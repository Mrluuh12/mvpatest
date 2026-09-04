"""Testes dos relatórios.

A propriedade que importa não é a aritmética: é a **ressalva**. Um relatório
que não declara o que ficou de fora vira número citado errado numa reunião, e
o erro só aparece meses depois, quando já virou meta.

Em especial: equipamento nunca sondado **não entra como zero**. Contá-lo assim
rebaixaria a frota inteira por falta de coleta — e a falta de coleta é problema
de quem opera a plataforma, não da mina.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from plataforma.db import relatorios
from plataforma.db.esquema import ativo, campo, dispositivo, estado, transicao
from plataforma.db.repositorio_pg import apagar_esquema, criar_engine, criar_esquema

URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma_teste",
)

pytestmark = pytest.mark.asyncio
FIM = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
INICIO = FIM - timedelta(hours=24)


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


async def semear(c, *maquinas) -> None:
    """maquinas = (ativo_id, frota, funcao, [(chave, vivo_agora, mudancas)])"""
    for ativo_id, frota, funcao, pecas in maquinas:
        await c.execute(
            ativo.insert().values(
                ativo_id=ativo_id, frota=frota, numero="1", site="mina"
            )
        )
        if funcao:
            await c.execute(
                campo.insert().values(
                    sujeito=f"ativo:{ativo_id}", nome="funcao_negocio",
                    origem="cadastrado", natureza="intencao",
                    valor=funcao, em=INICIO,
                )
            )
        for chave, vivo, mudancas in pecas:
            await c.execute(
                dispositivo.insert().values(
                    chave=chave, nome_bruto=chave, nome_canonico=chave,
                    papel="radio_mesh", zona="corporativa", ativo_id=ativo_id,
                )
            )
            for para, em in mudancas:
                await c.execute(
                    transicao.insert().values(
                        sujeito=chave, de=not para, para=para, em=em
                    )
                )
            if vivo is not None:
                await c.execute(
                    estado.insert().values(
                        sujeito=chave, alcancavel=vivo, qualidade="boa", visto_em=FIM
                    )
                )


class TestDisponibilidade:
    async def test_sempre_de_pe_da_cem_por_cento(self, conexao) -> None:
        await semear(conexao, ("CA-1", "CA", "britagem", [("d1", True, ())]))
        r = await relatorios.disponibilidade_por_frota(conexao, INICIO, FIM)
        assert r.linhas[0]["disponibilidade_pct"] == 100.0

    async def test_metade_do_periodo_caido(self, conexao) -> None:
        meio = INICIO + timedelta(hours=12)
        await semear(conexao, ("CA-1", "CA", "britagem", [("d1", False, ((False, meio),))]))
        r = await relatorios.disponibilidade_por_frota(conexao, INICIO, FIM)
        assert r.linhas[0]["disponibilidade_pct"] == pytest.approx(50.0, abs=0.1)

    async def test_nunca_sondado_fica_fora_da_media_e_a_ressalva_diz(
        self, conexao
    ) -> None:
        """Contá-lo como zero rebaixaria a frota por falta de coleta — que é
        problema de quem opera a plataforma, não da mina."""
        await semear(
            conexao,
            ("CA-1", "CA", "britagem", [("bom", True, ()), ("nunca", None, ())]),
        )
        r = await relatorios.disponibilidade_por_frota(conexao, INICIO, FIM)
        assert r.linhas[0]["disponibilidade_pct"] == 100.0
        assert r.linhas[0]["dispositivos_medidos"] == 1
        assert any("fora da média" in n for n in r.notas)

    async def test_agrupa_por_frota_e_funcao(self, conexao) -> None:
        await semear(
            conexao,
            ("CA-1", "CA", "britagem", [("a", True, ())]),
            ("CA-2", "CA", "transporte", [("b", True, ())]),
        )
        r = await relatorios.disponibilidade_por_frota(conexao, INICIO, FIM)
        assert {(x["frota"], x["funcao"]) for x in r.linhas} == {
            ("CA", "britagem"), ("CA", "transporte")
        }

    async def test_ativo_sem_funcao_cadastrada_e_dito(self, conexao) -> None:
        await semear(conexao, ("CA-1", "CA", None, [("d1", True, ())]))
        r = await relatorios.disponibilidade_por_frota(conexao, INICIO, FIM)
        assert r.linhas[0]["funcao"] == "não definida"
        assert any("não definida" in n for n in r.notas)

    async def test_o_pior_aparece_ao_lado_da_media(self, conexao) -> None:
        """Média de frota esconde a máquina que está mal. As duas juntas não."""
        meio = INICIO + timedelta(hours=12)
        await semear(
            conexao,
            ("CA-1", "CA", "britagem",
             [("bom", True, ()), ("ruim", False, ((False, meio),))]),
        )
        (linha,) = (await relatorios.disponibilidade_por_frota(conexao, INICIO, FIM)).linhas
        assert linha["disponibilidade_pct"] == pytest.approx(75.0, abs=0.1)
        assert linha["pior_pct"] == pytest.approx(50.0, abs=0.1)


class TestCobertura:
    async def test_separa_vigiado_de_medido(self, conexao) -> None:
        """Um papel com estado e sem métrica está sendo vigiado, não medido —
        e a diferença decide se dá para responder "por quê"."""
        await semear(conexao, ("CA-1", "CA", "britagem", [("d1", True, ())]))
        r = await relatorios.cobertura_da_coleta(conexao, INICIO, FIM)
        (linha,) = [x for x in r.linhas if x["papel"] == "radio_mesh"]
        assert linha["com_estado"] == 1
        assert linha["com_metrica"] == 0
        assert any("vigiado, não medido" in n for n in r.notas)


class TestSaida:
    async def test_o_csv_leva_as_ressalvas_no_topo(self, conexao) -> None:
        """Quem abrir a planilha três semanas depois precisa das mesmas
        ressalvas que quem viu a tela."""
        await semear(
            conexao,
            ("CA-1", "CA", "britagem", [("bom", True, ()), ("nunca", None, ())]),
        )
        r = await relatorios.disponibilidade_por_frota(conexao, INICIO, FIM)
        csv = relatorios.para_csv(r)
        assert csv.startswith("# Disponibilidade por frota")
        assert "# ressalva: " in csv
        assert "frota,funcao,ativos" in csv

    async def test_relatorio_inexistente_e_recusado(self, conexao) -> None:
        with pytest.raises(KeyError):
            await relatorios.gerar(conexao, "inventado", INICIO, FIM)

    async def test_todo_relatorio_tem_rotulo_curto_e_descricao(self) -> None:
        """A primeira linha do docstring dava rótulo de oito palavras dentro
        de um botão de três."""
        for nome, d in relatorios.RELATORIOS.items():
            assert 0 < len(d.rotulo) <= 20, f"{nome}: rótulo longo demais"
            assert d.descricao.endswith("."), f"{nome}: descrição sem frase inteira"
