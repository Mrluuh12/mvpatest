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

from plataforma import relatorios
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
        r = await relatorios.gerar(conexao, "disponibilidade_frota", INICIO, FIM)
        assert r.linhas[0]["disponibilidade_pct"] == 100.0

    async def test_metade_do_periodo_caido(self, conexao) -> None:
        meio = INICIO + timedelta(hours=12)
        await semear(conexao, ("CA-1", "CA", "britagem", [("d1", False, ((False, meio),))]))
        r = await relatorios.gerar(conexao, "disponibilidade_frota", INICIO, FIM)
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
        r = await relatorios.gerar(conexao, "disponibilidade_frota", INICIO, FIM)
        assert r.linhas[0]["disponibilidade_pct"] == 100.0
        assert r.linhas[0]["medidos"] == 1
        assert any("fora da média" in n for n in r.notas)

    async def test_agrupa_por_frota_e_funcao(self, conexao) -> None:
        await semear(
            conexao,
            ("CA-1", "CA", "britagem", [("a", True, ())]),
            ("CA-2", "CA", "transporte", [("b", True, ())]),
        )
        r = await relatorios.gerar(conexao, "disponibilidade_frota", INICIO, FIM)
        assert {(x["frota"], x["funcao"]) for x in r.linhas} == {
            ("CA", "britagem"), ("CA", "transporte")
        }

    async def test_ativo_sem_funcao_cadastrada_e_dito(self, conexao) -> None:
        await semear(conexao, ("CA-1", "CA", None, [("d1", True, ())]))
        r = await relatorios.gerar(conexao, "disponibilidade_frota", INICIO, FIM)
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
        (linha,) = (await relatorios.gerar(conexao, "disponibilidade_frota", INICIO, FIM)).linhas
        assert linha["disponibilidade_pct"] == pytest.approx(75.0, abs=0.1)
        assert linha["pior_pct"] == pytest.approx(50.0, abs=0.1)


class TestCobertura:
    async def test_separa_vigiado_de_medido(self, conexao) -> None:
        """Um papel com estado e sem métrica está sendo vigiado, não medido —
        e a diferença decide se dá para responder "por quê"."""
        await semear(conexao, ("CA-1", "CA", "britagem", [("d1", True, ())]))
        r = await relatorios.gerar(conexao, "cobertura", INICIO, FIM)
        (linha,) = [x for x in r.linhas if x["papel"] == "radio_mesh"]
        assert linha["com_estado"] == 1
        assert linha["com_metrica"] == 0
        # A ressalva encurtou; o que ela precisa manter é a distinção entre
        # estar de pé e ter medida — é ela que decide se dá para responder
        # "por quê" quando alguém pergunta.
        assert any("estar de pé" in n and "medição" in n for n in r.notas)


class TestSaida:
    async def test_o_csv_leva_as_ressalvas_no_topo(self, conexao) -> None:
        """Quem abrir a planilha três semanas depois precisa das mesmas
        ressalvas que quem viu a tela."""
        await semear(
            conexao,
            ("CA-1", "CA", "britagem", [("bom", True, ()), ("nunca", None, ())]),
        )
        r = await relatorios.gerar(conexao, "disponibilidade_frota", INICIO, FIM)
        csv = relatorios.para_csv(r)
        assert csv.startswith("# Disponibilidade por frota")
        assert "# ressalva: " in csv
        assert "Frota,Função de negócio" in csv, "cabeçalho é o rótulo, não o nome interno"
        # O valor sai cru de propósito: "94,32%" como texto quebra fórmula, e a
        # planilha existe para se calcular em cima dela. A conferência é só
        # sobre os dados: as linhas de comentário levam prosa, e prosa é
        # formatada mesmo — inclusive com percentual escrito por extenso.
        dados = [ln for ln in csv.splitlines() if not ln.startswith("#")]
        assert any(",100.0," in ln for ln in dados)
        assert not any("%" in ln for ln in dados), "dado formatado quebra fórmula"

    async def test_relatorio_inexistente_e_recusado(self, conexao) -> None:
        with pytest.raises(KeyError):
            await relatorios.gerar(conexao, "inventado", INICIO, FIM)

    async def test_todo_relatorio_tem_rotulo_curto_e_descricao(self) -> None:
        """A primeira linha do docstring dava rótulo de oito palavras dentro
        de um botão de três."""
        for nome, d in relatorios.RELATORIOS.items():
            assert 0 < len(d.rotulo) <= 20, f"{nome}: rótulo longo demais"
            assert d.descricao.endswith("."), f"{nome}: descrição sem frase inteira"


class TestCatalogo:
    """O catálogo é a parte em que se perde ou se ganha a comparação com o
    SolarWinds, que traz mais de cem modelos prontos. A resposta aqui não é
    competir em número: é cada relatório declarar o que ficou de fora dele."""

    async def test_todo_relatorio_declara_categoria_e_parametros_tipados(self) -> None:
        for nome, d in relatorios.RELATORIOS.items():
            assert d.categoria in relatorios.Categoria, nome
            for pr in d.parametros:
                assert pr.tipo in relatorios.TipoParam, f"{nome}/{pr.nome}"
                assert pr.rotulo, f"{nome}/{pr.nome} sem rótulo"

    async def test_relatorio_de_series_e_marcado_indisponivel_com_motivo(self) -> None:
        """Sem Prometheus ele geraria uma tabela vazia, que parece 'não
        aconteceu nada' — a mentira mais fácil de contar por omissão."""
        sem = {x["nome"]: x for x in relatorios.catalogo(com_series=False)}
        assert sem["previsao_interfaces"]["disponivel"] is False
        assert "PLATAFORMA_PROMETHEUS" in sem["previsao_interfaces"]["motivo"]
        assert sem["quedas"]["disponivel"] is True, "quedas sai do banco, não do Prometheus"

    async def test_parametro_recusado_diz_qual_campo(self) -> None:
        """'422' sozinho manda a pessoa adivinhar qual campo estava errado."""
        d = relatorios.RELATORIOS["piores_disponibilidades"]
        with pytest.raises(ValueError, match="Quantas linhas"):
            d.ler_parametros({"limite": "muitas"})

    async def test_escolha_invalida_lista_as_validas(self) -> None:
        d = relatorios.RELATORIOS["top_interfaces"]
        with pytest.raises(ValueError, match="entrada_mbps"):
            d.ler_parametros({"ordenar": "inventado"})

    async def test_janela_padrao_do_relatorio_de_series_e_menor(self) -> None:
        """Sete dias serve para disponibilidade e deixa a previsão vazia,
        porque a série é mais nova que a janela."""
        assert relatorios.RELATORIOS["previsao_interfaces"].janela_padrao == "24h"
        assert relatorios.RELATORIOS["disponibilidade_frota"].janela_padrao == "7d"


class TestUnidades:
    """A unidade errada é um erro que não parece erro."""

    async def test_soma_de_tempo_parado_e_equipamento_hora_e_nao_duracao(
        self, conexao
    ) -> None:
        """654 equipamentos parados um dia dão 654 "dias" — lido como duração
        isso é absurdo na cara. Não é duração: é volume de indisponibilidade."""
        r = await relatorios.gerar(conexao, "disponibilidade_frota", INICIO, FIM)
        col = next(c for c in r.colunas if c.nome == "fora_do_ar_eqh")
        assert col.unidade == "equip·h"
        assert col.tipo is relatorios.TipoColuna.NUMERO

    async def test_percentual_nunca_soma_no_rodape(self, conexao) -> None:
        """Somar uma coluna de percentuais dá 4.700%.

        Percorre os relatórios que saem do banco; os que dependem do Prometheus
        ficam de fora porque exigiriam a série, e a regra que se defende aqui é
        do modelo de coluna, não da origem do dado.
        """
        for nome, d in relatorios.RELATORIOS.items():
            if d.exige_series:
                continue
            r = await relatorios.gerar(conexao, nome, INICIO, FIM)
            for c in r.colunas:
                if c.tipo is relatorios.TipoColuna.PERCENTUAL:
                    assert not c.somavel, f"{nome}/{c.nome}"

    async def test_todo_relatorio_do_banco_gera_sem_estourar(self, conexao) -> None:
        """Banco vazio é o caso que mais aparece numa instalação nova, e é
        onde um relatório costuma dividir por zero."""
        for nome, d in relatorios.RELATORIOS.items():
            if d.exige_series:
                continue
            r = await relatorios.gerar(conexao, nome, INICIO, FIM)
            assert r.colunas, f"{nome} não declarou colunas"

    async def test_ausencia_vira_travessao_e_nunca_zero(self) -> None:
        from plataforma.relatorios.formatos import formatar_valor

        assert formatar_valor(None, relatorios.TipoColuna.NUMERO) == "—"
        assert formatar_valor(0, relatorios.TipoColuna.NUMERO) == "0"

    async def test_duracao_vira_algo_que_se_le_de_relance(self) -> None:
        from plataforma.relatorios.formatos import duracao

        assert duracao(45) == "45 s"
        assert duracao(3842) == "1 h 04 min"
        assert duracao(200000) == "2 d 07 h"


class TestPrevisao:
    """A previsão linear é o método do SolarWinds — e herda o defeito dele."""

    async def test_tendencia_de_reta_perfeita_tem_r2_um(self) -> None:
        from plataforma.relatorios.capacidade import tendencia

        a, _, r2 = tendencia([(0.0, 0.0), (10.0, 10.0), (20.0, 20.0)])
        assert a == pytest.approx(1.0)
        assert r2 == pytest.approx(1.0)

    async def test_pontos_sem_reta_tem_r2_baixo(self) -> None:
        """É o R² que separa "satura em 12 dias" de "os pontos não descrevem
        reta nenhuma". Sem ele as duas projeções parecem igualmente boas."""
        from plataforma.relatorios.capacidade import tendencia

        _, _, r2 = tendencia([(0.0, 5.0), (1.0, 90.0), (2.0, 3.0), (3.0, 80.0)])
        assert r2 < 0.5

    async def test_dias_ate_nao_inventa_data_quando_a_pergunta_nao_se_aplica(self) -> None:
        from plataforma.relatorios.capacidade import dias_ate

        assert dias_ate(atual=95, por_dia=1, limiar=90) is None, "já passou"
        assert dias_ate(atual=10, por_dia=0, limiar=90) is None, "não cresce"
        assert dias_ate(atual=10, por_dia=-1, limiar=90) is None, "está caindo"
        assert dias_ate(atual=10, por_dia=1e-9, limiar=90) is None, "fora do horizonte"
        assert dias_ate(atual=80, por_dia=2, limiar=90) == 5.0
