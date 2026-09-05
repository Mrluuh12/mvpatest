"""Testes das séries dos gráficos.

A plataforma não guarda série — ela pergunta para quem tem. O que precisa
valer aqui é que ela **saiba de quem perguntar**, e que diga a verdade quando
não há de quem: um gráfico que desenha uma linha reta a partir de um ponto só
parece informação, e é pior que gráfico nenhum.

A segunda propriedade é sobre a natureza do dado. Disponibilidade não é uma
série amostrada: a tabela guarda o instante exato de cada mudança, então a
resposta certa são as faixas, não pontos de dez em dez minutos que perderiam
uma queda de dois.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from plataforma import series
from plataforma.db.esquema import estado, transicao
from plataforma.db.repositorio_pg import apagar_esquema, criar_engine, criar_esquema

URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma_teste",
)
T0 = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


class TestJanela:
    @pytest.mark.parametrize(
        "texto,esperado", [("30m", 1800), ("6h", 21600), ("7d", 604800)]
    )
    def test_formas_aceitas(self, texto: str, esperado: int) -> None:
        assert series.segundos_da_janela(texto) == esperado

    @pytest.mark.parametrize("texto", ["meia-hora", "", "6", "6x", "0m", "365d"])
    def test_recusa_diz_o_que_serve(self, texto: str) -> None:
        with pytest.raises(series.JanelaInvalida):
            series.segundos_da_janela(texto)

    def test_passo_nunca_desce_abaixo_do_util(self) -> None:
        """Passo menor que o intervalo de coleta não traz detalhe: devolve o
        mesmo valor repetido e faz o Prometheus trabalhar à toa."""
        assert series.passo_para(60) == 15
        assert series.passo_para(21600) == 90

    def test_passo_cabe_na_largura_do_desenho(self) -> None:
        for janela in ("30m", "6h", "24h", "7d", "90d"):
            s = series.segundos_da_janela(janela)
            assert s / series.passo_para(s) <= series.PONTOS_ALVO + 1


class TestOrigem:
    def test_metrica_do_rajant_tem_serie(self) -> None:
        assert series.tem_serie("rf_snr_db")

    def test_disponibilidade_tem_serie_pelas_transicoes(self) -> None:
        assert series.tem_serie("ativo_alcancavel")

    def test_metrica_de_snmp_passou_a_ter(self) -> None:
        """Este teste já afirmou o contrário, e a mudança é o ponto.

        Enquanto só o exportador Rajant alimentava um Prometheus, tráfego de
        porta de switch não podia virar linha. Agora a própria plataforma
        publica o que coleta, e o que mudou não foi o dado — foi ele passar a
        chegar a quem guarda série.
        """
        assert series.tem_serie("iface_bytes_rx")
        assert series.LOCAIS["iface_bytes_rx"].taxa, "contador cru é rampa, não tráfego"

    def test_metrica_que_ninguem_publica_continua_sem_serie(self) -> None:
        s = series.sem_serie("disp_ventilador_rpm")
        assert s.tipo == "ausente"
        assert "última leitura" in s.motivo

    def test_metrica_fora_do_dicionario_e_dita_como_tal(self) -> None:
        assert "dicionário" in series.sem_serie("inventada_por_alguem").motivo


def _resposta(valores: list[list]) -> httpx.MockTransport:
    def lidar(pedido: httpx.Request) -> httpx.Response:
        corpo = {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [{"metric": {"ip": "10.0.0.1"}, "values": valores}],
            },
        }
        return httpx.Response(200, content=json.dumps(corpo),
                              headers={"content-type": "application/json"})

    return httpx.MockTransport(lidar)


@pytest.mark.asyncio
class TestPrometheus:
    async def test_traz_os_pontos_e_a_consulta_que_os_produziu(self) -> None:
        """Gráfico que não pode ser conferido é gráfico em que ninguém confia
        depois da primeira surpresa."""
        s = await series.de_prometheus(
            "http://p:9090", "rf_snr_db", "10.0.0.1", 3600,
            transporte=_resposta([[100, "21.5"], [190, "22.0"]]),
        )
        assert s.tipo == "numerica"
        assert s.unidade == "dB"
        assert s.pontos == [[100.0, 21.5], [190.0, 22.0]]
        assert 'rajant_peer_snr_db{ip="10.0.0.1"}' in s.consulta
        assert s.agregacao == "pior_entre_vizinhos"

    async def test_nan_nao_vira_zero(self) -> None:
        """O Prometheus diz "sem dado" com NaN. Zero seria uma leitura, e no
        desenho um mergulho ao chão que nunca aconteceu."""
        s = await series.de_prometheus(
            "http://p:9090", "rf_snr_db", "10.0.0.1", 3600,
            transporte=_resposta([[100, "21.5"], [190, "NaN"], [280, "22.0"]]),
        )
        assert [p[1] for p in s.pontos] == [21.5, 22.0]

    async def test_pontos_saem_ordenados_no_tempo(self) -> None:
        s = await series.de_prometheus(
            "http://p:9090", "rf_snr_db", "10.0.0.1", 3600,
            transporte=_resposta([[300, "3"], [100, "1"], [200, "2"]]),
        )
        assert [p[0] for p in s.pontos] == [100.0, 200.0, 300.0]


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


@pytest.mark.asyncio
class TestTransicoes:
    async def _semear(self, c, *, qualidade="boa", vivo_agora=False, mudancas=()):
        for para, em in mudancas:
            await c.execute(
                transicao.insert().values(sujeito="d1", de=not para, para=para, em=em)
            )
        await c.execute(
            estado.insert().values(
                sujeito="d1", alcancavel=vivo_agora, qualidade=qualidade,
                visto_em=T0 + timedelta(hours=6),
            )
        )

    async def test_devolve_faixas_e_nao_amostras(self, conexao) -> None:
        """A tabela guarda o instante exato da mudança. Amostrar de dez em dez
        minutos perderia uma queda de dois — o gráfico mentiria por omissão."""
        queda = T0 + timedelta(hours=2)
        volta = queda + timedelta(minutes=2)
        await self._semear(
            conexao, vivo_agora=True,
            mudancas=((False, queda), (True, volta)),
        )
        s = await series.de_transicoes(
            conexao, "d1", 6 * 3600, agora=T0 + timedelta(hours=6)
        )
        assert s.tipo == "estados"
        curtas = [f for f in s.faixas if f["fim"] - f["inicio"] == 120]
        assert len(curtas) == 1, "a queda de 2 minutos tem de estar lá, inteira"
        assert curtas[0]["alcancavel"] is False

    async def test_a_faixa_corrente_herda_a_incerteza(self, conexao) -> None:
        """Sob suspeita de isolamento, "sem resposta" ainda não é afirmação de
        queda — e o desenho precisa poder mostrar a diferença."""
        await self._semear(conexao, qualidade="incerta", vivo_agora=False)
        s = await series.de_transicoes(conexao, "d1", 3600, agora=T0 + timedelta(hours=6))
        assert s.faixas[-1]["incerta"] is True

    async def test_as_faixas_cobrem_a_janela_inteira_sem_buraco(self, conexao) -> None:
        await self._semear(
            conexao, vivo_agora=True,
            mudancas=((False, T0 + timedelta(hours=1)), (True, T0 + timedelta(hours=3))),
        )
        fim = T0 + timedelta(hours=6)
        s = await series.de_transicoes(conexao, "d1", 6 * 3600, agora=fim)
        assert s.faixas[0]["inicio"] == T0.timestamp()
        assert s.faixas[-1]["fim"] == fim.timestamp()
        for a, b in zip(s.faixas, s.faixas[1:], strict=False):
            assert a["fim"] == b["inicio"], "faixas emendadas, sem vão"

    async def test_equipamento_nunca_sondado_diz_isso(self, conexao) -> None:
        s = await series.de_transicoes(conexao, "nunca-visto", 3600)
        assert s.tipo == "ausente"
        assert "nunca foi sondado" in s.motivo


class TestSeriesDaPlataforma:
    """As métricas que a própria plataforma publica no Prometheus.

    O que estes testes guardam é a honestidade da consulta: contador vira taxa,
    agregação é declarada, e onde agregar não faria sentido a resposta é pedir
    a dimensão em vez de inventar um número.
    """

    def test_toda_metrica_local_existe_no_dicionario(self) -> None:
        from plataforma.dicionario import POR_NOME

        fora = [m for m in series.LOCAIS if m not in POR_NOME]
        assert not fora, f"métrica publicada fora do vocabulário: {fora}"

    def test_contador_de_bytes_vira_taxa(self) -> None:
        """Um contador de bytes desenhado cru é uma rampa que só sobe, e
        ninguém consegue ler tráfego nela."""
        assert series.LOCAIS["iface_bytes_rx"].taxa
        assert series.LOCAIS["iface_bytes_rx"].unidade == "B/s"

    def test_janela_da_taxa_tem_piso(self) -> None:
        """Medido contra um Prometheus real raspando a cada 15 s: janela de
        60 s deu 15,1 MB/s onde o tráfego era 12,5, porque extrapolou de poucas
        amostras. A janela curta não é mais fiel, é mais barulhenta — e com
        raspagem de 60 s ficaria vazia."""
        assert series._janela_de_taxa(15) == series.PISO_JANELA_TAXA_S
        assert series._janela_de_taxa(2520) == 2520 * 4

    def test_estado_de_porta_pede_a_porta_em_vez_de_somar(self) -> None:
        local = series.LOCAIS["iface_status_oper"]
        assert local.agregacao_impossivel
        assert "escolha uma porta" in local.agregacao_impossivel

    def test_metrica_de_interface_declara_a_dimensao(self) -> None:
        assert series.dimensao_de("iface_bytes_rx") == "porta"
        assert series.dimensao_de("disp_cpu_pct") == ""
