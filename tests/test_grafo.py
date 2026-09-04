"""Testes do canal de fatos e do grafo temporal.

A tabela `aresta` sempre teve validade e restrição de exclusão; até aqui só
recebia o que veio da planilha. Aqui ela passa a receber o que a rede está
fazendo, e três propriedades precisam valer.

A segunda é a que evita gravar uma mentira permanente: **aresta fechada é
fato datado**. Se uma leitura parcial fizesse a plataforma fechar tudo, a
história registraria que a malha inteira se desfez num instante — e isso não
se desfaz depois.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from inventario.modelo import TipoAresta
from plataforma.db.esquema import aresta, dispositivo, identificador
from plataforma.db.grafo import conciliar, resolver_identidades, vizinhos
from plataforma.db.repositorio_pg import apagar_esquema, criar_engine, criar_esquema
from plataforma.modulos.contrato import Relacao

URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma_teste",
)

pytestmark = pytest.mark.asyncio
T0 = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
MESH = TipoAresta.PEER_MESH


@pytest_asyncio.fixture
async def conexao():
    motor = criar_engine(URL)
    try:
        await apagar_esquema(motor)
        await criar_esquema(motor)
    except Exception as erro:  # noqa: BLE001
        await motor.dispose()
        pytest.skip(f"Postgres indisponível: {erro}")
    async with motor.begin() as c:
        yield c
    await apagar_esquema(motor)
    await motor.dispose()


async def semear(c, *itens) -> None:
    """itens = (chave, nome, mac, ip)"""
    for chave, nome, mac, ip in itens:
        await c.execute(
            dispositivo.insert().values(
                chave=chave, nome_bruto=nome, nome_canonico=nome,
                papel="radio_mesh", zona="corporativa",
            )
        )
        for tipo, valor in (("nome", nome), ("mac", mac), ("ip", ip)):
            if valor:
                await c.execute(
                    identificador.insert().values(
                        dispositivo_chave=chave, tipo=tipo, valor=valor
                    )
                )


def rel(origem: str, destino: str) -> Relacao:
    return Relacao(origem=origem, destino=destino, tipo=MESH)


async def abertas(c) -> set[tuple[str, str]]:
    linhas = (
        await c.execute(
            select(aresta.c.origem_chave, aresta.c.destino_chave)
            .where(aresta.c.tipo == MESH.value)
            .where(func.upper_inf(aresta.c.validade))
        )
    ).all()
    return {(ln.origem_chave, ln.destino_chave) for ln in linhas}


class TestConciliacao:
    async def test_abre_o_que_apareceu(self, conexao) -> None:
        r = await conciliar(conexao, MESH, (rel("a", "b"),), T0, completo=True)
        assert r.abertas == 1
        assert await abertas(conexao) == {("a", "b")}

    async def test_rodar_de_novo_nao_faz_nada(self, conexao) -> None:
        """É isto que faz a tabela crescer com as mudanças, não com os ciclos.
        Sem idempotência, 149 rádios a cada minuto seriam 200 mil linhas/dia
        dizendo a mesma coisa."""
        rs = (rel("a", "b"), rel("a", "c"))
        await conciliar(conexao, MESH, rs, T0, completo=True)
        segunda = await conciliar(conexao, MESH, rs, T0 + timedelta(minutes=1), completo=True)
        assert (segunda.abertas, segunda.fechadas) == (0, 0)
        assert segunda.inalteradas == 2
        assert len(await abertas(conexao)) == 2

    async def test_fecha_o_que_sumiu(self, conexao) -> None:
        await conciliar(conexao, MESH, (rel("a", "b"), rel("a", "c")), T0, completo=True)
        r = await conciliar(
            conexao, MESH, (rel("a", "b"),), T0 + timedelta(minutes=5), completo=True
        )
        assert (r.abertas, r.fechadas) == (0, 1)
        assert await abertas(conexao) == {("a", "b")}

    async def test_leitura_incompleta_nao_fecha_nada(self, conexao) -> None:
        """O Prometheus caiu. A ausência do vizinho significa "não perguntei",
        não "o enlace caiu" — e aresta fechada é fato datado, que fica."""
        await conciliar(conexao, MESH, (rel("a", "b"), rel("a", "c")), T0, completo=True)
        r = await conciliar(conexao, MESH, (), T0 + timedelta(minutes=5), completo=False)
        assert r.fechadas == 0
        assert r.fechamento_suspenso is True
        assert len(await abertas(conexao)) == 2

    async def test_so_fecha_aresta_de_quem_foi_lido(self, conexao) -> None:
        """Uma coleta completa de 22 rádios não pode fechar as arestas dos
        outros 127 só por não os ter mencionado."""
        await conciliar(conexao, MESH, (rel("a", "b"), rel("x", "y")), T0, completo=True)
        r = await conciliar(
            conexao, MESH, (), T0 + timedelta(minutes=5),
            completo=True, observadores={"a"},
        )
        assert r.fechadas == 1
        assert await abertas(conexao) == {("x", "y")}

    async def test_aresta_fechada_guarda_quando_valeu(self, conexao) -> None:
        fim = T0 + timedelta(hours=2)
        await conciliar(conexao, MESH, (rel("a", "b"),), T0, completo=True)
        await conciliar(conexao, MESH, (), fim, completo=True, observadores={"a"})
        (ln,) = (await conexao.execute(select(aresta).where(aresta.c.tipo == MESH.value))).all()
        assert ln.validade.lower == T0
        assert ln.validade.upper == fim


class TestIdentidade:
    async def test_resolve_por_mac_com_qualquer_separador(self, conexao) -> None:
        """O cadastro guarda 00:01:B9:66:A1:AE; o equipamento pode publicar
        com hífen ou sem nada. Perder o enlace por causa de um hífen seria
        um defeito difícil de enxergar."""
        await semear(conexao, ("k1", "RADIO-1", "00:01:B9:66:A1:AE", "10.0.0.1"))
        r = await resolver_identidades(
            conexao, {"mac:00-01-b9-66-a1-ae", "mac:0001B966A1AE"}
        )
        assert set(r.por_identidade.values()) == {"k1"}
        assert len(r.por_identidade) == 2

    async def test_resolve_por_ip_sem_prefixo(self, conexao) -> None:
        await semear(conexao, ("k1", "RADIO-1", "00:01:B9:66:A1:AE", "10.0.0.1"))
        r = await resolver_identidades(conexao, {"10.0.0.1"})
        assert r.por_identidade == {"10.0.0.1": "k1"}

    async def test_identidade_disputada_nao_vira_aresta(self, conexao) -> None:
        """Pendurar o enlace no equipamento errado é pior do que não tê-lo."""
        await semear(
            conexao,
            ("k1", "RADIO-1", "AA:AA:AA:AA:AA:AA", "10.0.0.9"),
            ("k2", "RADIO-2", "BB:BB:BB:BB:BB:BB", "10.0.0.9"),
        )
        r = await resolver_identidades(conexao, {"10.0.0.9"})
        assert r.por_identidade == {}
        assert r.ambiguas == {"10.0.0.9"}

    async def test_vizinho_fora_do_cadastro_e_contado(self, conexao) -> None:
        """A malha acha rádio que a planilha não tem. É achado de inventário."""
        await semear(conexao, ("k1", "RADIO-1", "AA:AA:AA:AA:AA:AA", "10.0.0.1"))
        r = await resolver_identidades(conexao, {"mac:FF:FF:FF:FF:FF:FF"})
        assert r.desconhecidas == {"mac:FF:FF:FF:FF:FF:FF"}
        assert r.ambiguas == set()


class TestConsultaTemporal:
    async def test_o_grafo_de_ontem_continua_la(self, conexao) -> None:
        """É a pergunta que justifica guardar validade em vez de sobrescrever."""
        meio = T0 + timedelta(hours=1)
        await conciliar(conexao, MESH, (rel("a", "b"),), T0, completo=True)
        await conciliar(
            conexao, MESH, (rel("a", "c"),), meio, completo=True, observadores={"a"}
        )
        antes = await vizinhos(conexao, "a", T0 + timedelta(minutes=30))
        depois = await vizinhos(conexao, "a", meio + timedelta(minutes=30))
        assert [v["destino"] for v in antes] == ["b"]
        assert [v["destino"] for v in depois] == ["c"]

    async def test_antes_de_existir_nao_havia_vizinho(self, conexao) -> None:
        await conciliar(conexao, MESH, (rel("a", "b"),), T0, completo=True)
        assert await vizinhos(conexao, "a", T0 - timedelta(days=1)) == []
