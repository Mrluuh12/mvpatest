"""Testes da gravação de coleta.

A propriedade central defendida aqui: **guardar transições em vez de amostras
não perde informação**. A disponibilidade de qualquer janela continua
calculável — e a tabela fica três ordens de grandeza menor.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from inventario.modelo import Zona
from plataforma.coletor import alvos_do_inventario
from plataforma.db.coleta import disponibilidade, gravar_coleta
from plataforma.db.esquema import estado, saude_modulo, transicao
from plataforma.db.repositorio_pg import apagar_esquema, criar_engine, criar_esquema
from plataforma.modulos.contrato import Observacao, ResultadoColeta

# Banco separado de propósito: estas fixtures apagam o esquema entre os
# testes, e uma suíte que destrói o banco de desenvolvimento é uma suíte que
# as pessoas param de rodar.
URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma_teste",
)

pytestmark = pytest.mark.asyncio
T0 = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def engine():
    motor = criar_engine(URL)
    try:
        await apagar_esquema(motor)
        await criar_esquema(motor)
    except Exception as erro:  # noqa: BLE001
        await motor.dispose()
        pytest.skip(f"Postgres indisponível: {erro}")
    yield motor
    await apagar_esquema(motor)
    await motor.dispose()


def coleta(*, vivos: list[str], mortos: list[str], latencia: float = 4.2) -> ResultadoColeta:
    obs: list[Observacao] = []
    for s in vivos:
        obs += [
            Observacao(sujeito=s, metrica="ativo_alcancavel", valor=1.0),
            Observacao(sujeito=s, metrica="ativo_perda_pacote_pct", valor=0.0),
            Observacao(sujeito=s, metrica="ativo_latencia_ms", valor=latencia),
        ]
    for s in mortos:
        obs += [
            Observacao(sujeito=s, metrica="ativo_alcancavel", valor=0.0),
            Observacao(sujeito=s, metrica="ativo_perda_pacote_pct", valor=100.0),
        ]
    total = len(vivos) + len(mortos)
    obs.append(
        Observacao(sujeito="modulo:icmp", metrica="modulo_alvos_total", valor=total)
    )
    obs.append(
        Observacao(sujeito="modulo:icmp", metrica="modulo_ultima_coleta_ok_timestamp", valor=1.0)
    )
    return ResultadoColeta(
        observacoes=tuple(obs),
        alvos_total=total,
        alvos_falha=len(mortos),
        duracao_s=0.5,
    )


class TestGravacao:
    async def test_grava_estado_e_conta_alcancaveis(self, engine) -> None:
        async with engine.begin() as c:
            r = await gravar_coleta(c, "icmp", coleta(vivos=["a", "b"], mortos=["c"]), T0)
        assert r == {"estados": 3, "transicoes": 3, "alcancaveis": 2, "isolamento_suspeito": 0}

    async def test_latencia_de_quem_nao_respondeu_fica_nula_nao_zero(self, engine) -> None:
        """Nulo é "não sei"; zero é "medi e deu zero". Não são a mesma coisa."""
        async with engine.begin() as c:
            await gravar_coleta(c, "icmp", coleta(vivos=["a"], mortos=["c"]), T0)
        async with engine.connect() as c:
            linhas = {
                ln.sujeito: ln.latencia_ms
                for ln in (await c.execute(select(estado.c.sujeito, estado.c.latencia_ms))).all()
            }
        assert linhas["a"] == pytest.approx(4.2)
        assert linhas["c"] is None

    async def test_estado_estavel_nao_gera_transicao_nova(self, engine) -> None:
        """A economia que torna a tabela pequena — sem perder nada."""
        async with engine.begin() as c:
            await gravar_coleta(c, "icmp", coleta(vivos=["a"], mortos=[]), T0)
        for minuto in range(1, 6):
            async with engine.begin() as c:
                r = await gravar_coleta(
                    c, "icmp", coleta(vivos=["a"], mortos=[]), T0 + timedelta(minutes=minuto)
                )
            assert r["transicoes"] == 0

        async with engine.connect() as c:
            total = await c.scalar(select(func.count()).select_from(transicao))
        assert total == 1, "seis coletas, um único registro de mudança"

    async def test_ida_e_volta_geram_duas_transicoes(self, engine) -> None:
        async with engine.begin() as c:
            await gravar_coleta(c, "icmp", coleta(vivos=["a"], mortos=[]), T0)
        async with engine.begin() as c:
            await gravar_coleta(
                c, "icmp", coleta(vivos=[], mortos=["a"]), T0 + timedelta(minutes=1)
            )
        async with engine.begin() as c:
            await gravar_coleta(
                c, "icmp", coleta(vivos=["a"], mortos=[]), T0 + timedelta(minutes=3)
            )

        async with engine.connect() as c:
            linhas = (
                await c.execute(select(transicao.c.para).order_by(transicao.c.em))
            ).all()
        assert [ln.para for ln in linhas] == [True, False, True]


class TestSuspeitaDeIsolamento:
    async def test_falha_total_nao_registra_queda_de_todo_mundo(self, engine) -> None:
        """Se todos falham de uma vez, o mais provável é o coletor sem rede.

        Gravar 367 transições de queda seria fabricar 367 incidentes falsos a
        partir de uma única falha — a do próprio coletor.
        """
        async with engine.begin() as c:
            await gravar_coleta(
                c, "icmp", coleta(vivos=[f"d{i}" for i in range(8)], mortos=[]), T0
            )

        async with engine.begin() as c:
            r = await gravar_coleta(
                c,
                "icmp",
                coleta(vivos=[], mortos=[f"d{i}" for i in range(8)]),
                T0 + timedelta(minutes=1),
            )

        assert r["isolamento_suspeito"] == 1
        assert r["transicoes"] == 0, "queda de todo mundo não vira transição"

        async with engine.connect() as c:
            total = await c.scalar(select(func.count()).select_from(transicao))
            qualidades = {
                ln.qualidade
                for ln in (await c.execute(select(estado.c.qualidade))).all()
            }
        assert total == 8, "só as 8 subidas iniciais"
        assert qualidades == {"incerta"}, "o estado fica marcado como incerto"

    async def test_falha_parcial_registra_normalmente(self, engine) -> None:
        """Parte caindo é notícia de verdade; todo mundo caindo é suspeita."""
        async with engine.begin() as c:
            await gravar_coleta(
                c, "icmp", coleta(vivos=[f"d{i}" for i in range(8)], mortos=[]), T0
            )
        async with engine.begin() as c:
            r = await gravar_coleta(
                c,
                "icmp",
                coleta(vivos=[f"d{i}" for i in range(6)], mortos=["d6", "d7"]),
                T0 + timedelta(minutes=1),
            )
        assert r["isolamento_suspeito"] == 0
        assert r["transicoes"] == 2

    async def test_poucos_alvos_nao_disparam_a_suspeita(self, engine) -> None:
        """Com dois alvos, ambos caírem juntos é plausível."""
        async with engine.begin() as c:
            await gravar_coleta(c, "icmp", coleta(vivos=["a", "b"], mortos=[]), T0)
        async with engine.begin() as c:
            r = await gravar_coleta(
                c, "icmp", coleta(vivos=[], mortos=["a", "b"]), T0 + timedelta(minutes=1)
            )
        assert r["isolamento_suspeito"] == 0
        assert r["transicoes"] == 2


class TestSaudeDoModulo:
    async def test_carimbo_de_sucesso_avanca_so_quando_houve_sucesso(self, engine) -> None:
        """Módulo parado é indistinguível de parque saudável sem este carimbo."""
        async with engine.begin() as c:
            await gravar_coleta(c, "icmp", coleta(vivos=["a"], mortos=[]), T0)
        async with engine.connect() as c:
            ok_inicial = await c.scalar(select(saude_modulo.c.ultima_coleta_ok))
        assert ok_inicial == T0

        falha = ResultadoColeta(alvos_total=5, alvos_falha=5, rejeitadas=("estourou",))
        async with engine.begin() as c:
            await gravar_coleta(c, "icmp", falha, T0 + timedelta(minutes=1))

        async with engine.connect() as c:
            linha = (await c.execute(select(saude_modulo))).one()
        assert linha.ultima_coleta_ok == T0, "coleta falha não pode carimbar sucesso"
        assert linha.atualizado_em == T0 + timedelta(minutes=1)
        assert linha.alvos_falha == 5


class TestDisponibilidade:
    async def test_calculada_a_partir_das_transicoes(self, engine) -> None:
        """Vinte por cento fora do ar numa janela de dez minutos."""
        async with engine.begin() as c:
            await gravar_coleta(c, "icmp", coleta(vivos=["a"], mortos=[]), T0)
        async with engine.begin() as c:
            await gravar_coleta(
                c, "icmp", coleta(vivos=[], mortos=["a"]), T0 + timedelta(minutes=6)
            )
        async with engine.begin() as c:
            await gravar_coleta(
                c, "icmp", coleta(vivos=["a"], mortos=[]), T0 + timedelta(minutes=8)
            )
        async with engine.begin() as c:
            await gravar_coleta(
                c, "icmp", coleta(vivos=["a"], mortos=[]), T0 + timedelta(minutes=10)
            )

        async with engine.connect() as c:
            pct = await disponibilidade(c, "a", T0)
        assert pct == pytest.approx(80.0), "2 de 10 minutos fora"

    async def test_sujeito_sem_observacao_devolve_none_nao_zero(self, engine) -> None:
        """Número inventado aqui vira um SLA indefensável."""
        async with engine.connect() as c:
            assert await disponibilidade(c, "nunca-visto", T0) is None


class TestSelecaoDeAlvos:
    """Quem o coletor entrega a cada módulo.

    Duas regras se cruzam aqui, e a segunda é a que mais importa: a zona
    limita o que um módulo **alcança**, e por isso não se aplica igual a um
    módulo que nunca abre conexão com o equipamento. O que não se dobra em
    caso nenhum é a exclusão das zonas proibidas.
    """

    async def _semear(self, engine) -> None:
        from plataforma.db.esquema import dispositivo, identificador

        linhas = [
            ("k-corp", "RADIO-CORP", "radio_mesh", "corporativa", "10.0.0.1"),
            ("k-ot3", "RADIO-OT3", "radio_mesh", "ot_nivel3", "10.0.0.2"),
            ("k-clp", "CLP-OT2", "plc", "ot_nivel2", "10.0.0.3"),
            ("k-sw", "SWITCH", "switch", "corporativa", "10.0.0.4"),
        ]
        async with engine.begin() as c:
            for chave, nome, papel, zona, ip in linhas:
                await c.execute(
                    dispositivo.insert().values(
                        chave=chave, nome_bruto=nome, nome_canonico=nome,
                        papel=papel, zona=zona,
                    )
                )
                await c.execute(
                    identificador.insert().values(
                        dispositivo_chave=chave, tipo="ip", valor=ip
                    )
                )

    async def test_modulo_que_conecta_fica_preso_a_sua_zona(self, engine) -> None:

        await self._semear(engine)
        alvos = await alvos_do_inventario(engine, Zona.CORPORATIVA, ("radio_mesh",))
        assert {a["chave"] for a in alvos} == {"k-corp"}

    async def test_modulo_de_sistema_cobre_alem_da_propria_zona(self, engine) -> None:
        """Ele lê um Prometheus e atribui o que leu; nunca toca no rádio. Zona
        limita alcance, e quem não alcança não pode ser limitado por onde o
        equipamento está — o dado cruzou a fronteira no exportador."""

        await self._semear(engine)
        alvos = await alvos_do_inventario(
            engine, Zona.CORPORATIVA, ("radio_mesh",), conecta_no_alvo=False
        )
        assert {a["chave"] for a in alvos} == {"k-corp", "k-ot3"}

    async def test_zona_proibida_fica_de_fora_ate_do_modulo_de_sistema(
        self, engine
    ) -> None:
        """A linha que não se move: nem atribuir leitura a um controlador de
        processo a plataforma faz."""

        await self._semear(engine)
        alvos = await alvos_do_inventario(engine, Zona.CORPORATIVA, conecta_no_alvo=False)
        assert "k-clp" not in {a["chave"] for a in alvos}

    async def test_papel_declarado_recorta_os_alvos(self, engine) -> None:
        """Sem isso o módulo de rádio reportaria o switch como falha de
        cobertura a cada ciclo."""

        await self._semear(engine)
        alvos = await alvos_do_inventario(engine, Zona.CORPORATIVA)
        assert {a["chave"] for a in alvos} == {"k-corp", "k-sw"}
