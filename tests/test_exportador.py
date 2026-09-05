"""Testes da exportação para o Prometheus.

O que estes testes defendem não é o formato do texto — é o que **não** sai
nele. Republicar o que já veio de um Prometheus criaria duas verdades sobre o
mesmo número, e publicar leitura velha faz um coletor parado parecer uma linha
reta saudável. As duas omissões são o produto; o resto é serialização.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import insert

from plataforma.db.esquema import ativo, campo, dispositivo, estado, identificador, leitura
from plataforma.db.repositorio_pg import apagar_esquema, criar_engine, criar_esquema
from plataforma.exportador import PREFIXO, exportar, formatar, manifestos

URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma_teste",
)

pytestmark = pytest.mark.asyncio
AGORA = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
CHAVE = "mac:AA:BB:CC:00:00:01"


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


async def _parque(motor) -> None:
    async with motor.begin() as c:
        await c.execute(insert(ativo).values(ativo_id="ERB-01", frota="ERB", numero="01"))
        await c.execute(
            insert(dispositivo).values(
                chave=CHAVE, nome_bruto="SW-01", nome_canonico="SW-01",
                papel="switch", zona="corporativa", ativo_id="ERB-01",
            )
        )
        await c.execute(
            insert(identificador).values(
                dispositivo_chave=CHAVE, tipo="ip", valor="10.0.0.1"
            )
        )
        # Duas origens para a mesma função: a correção humana tem de vencer.
        await c.execute(
            insert(campo).values(
                sujeito="ativo:ERB-01", nome="funcao_negocio", origem="derivado",
                natureza="intencao", valor="chute_automatico", em=AGORA,
            )
        )
        await c.execute(
            insert(campo).values(
                sujeito="ativo:ERB-01", nome="funcao_negocio", origem="cadastrado",
                natureza="intencao", valor="rede_infraestrutura", em=AGORA,
            )
        )


async def _ler(motor, **valores) -> None:
    async with motor.begin() as c:
        await c.execute(insert(leitura).values(**valores))


async def _exportar(motor):
    async with motor.connect() as c:
        return await exportar(c, agora=AGORA)


def _por_metrica(exp, nome: str) -> list:
    return [a for a in exp.amostras if a.metrica == nome]


class TestOmissoes:
    async def test_o_que_veio_de_outro_prometheus_nao_volta(self, engine) -> None:
        """O laço que este teste impede: o módulo Rajant lê do Prometheus do
        exportador; se republicássemos, o mesmo número entraria de novo com
        outro nome e passariam a existir duas verdades sobre ele."""
        await _parque(engine)
        await _ler(
            engine, sujeito=CHAVE, metrica="rf_snr_db", valor=23.0, qualidade="boa",
            rotulos={}, modulo="rajant", em=AGORA,
        )
        exp = await _exportar(engine)
        assert _por_metrica(exp, "rf_snr_db") == []
        assert exp.omitidas_externas == 1

    async def test_leitura_velha_nao_sai_e_e_contada(self, engine) -> None:
        """Uma amostra raspada vale como se fosse de agora. Deixar a velha
        sair transformaria coletor parado em linha reta saudável — o defeito
        que o gráfico existe para não cometer."""
        await _parque(engine)
        await _ler(
            engine, sujeito=CHAVE, metrica="disp_cpu_pct", valor=40.0, qualidade="boa",
            rotulos={}, modulo="snmp", em=AGORA - timedelta(hours=2),
        )
        exp = await _exportar(engine)
        assert _por_metrica(exp, "disp_cpu_pct") == []
        assert exp.omitidas_velhas == 1

    async def test_dentro_da_validade_sai(self, engine) -> None:
        await _parque(engine)
        await _ler(
            engine, sujeito=CHAVE, metrica="disp_cpu_pct", valor=40.0, qualidade="boa",
            rotulos={}, modulo="snmp", em=AGORA - timedelta(seconds=30),
        )
        exp = await _exportar(engine)
        assert len(_por_metrica(exp, "disp_cpu_pct")) == 1


class TestRotulos:
    async def test_a_identidade_de_negocio_viaja_com_o_numero(self, engine) -> None:
        """É o que faz `sum by (funcao_negocio)` responder. Sem isto a métrica
        é um número solto com um IP do lado."""
        await _parque(engine)
        await _ler(
            engine, sujeito=CHAVE, metrica="disp_cpu_pct", valor=40.0, qualidade="boa",
            rotulos={}, modulo="snmp", em=AGORA,
        )
        exp = await _exportar(engine)
        r = _por_metrica(exp, "disp_cpu_pct")[0].rotulos
        assert r["ativo"] == "ERB-01"
        assert r["frota"] == "ERB"
        assert r["zona"] == "corporativa"
        assert r["papel"] == "switch"
        assert r["ip"] == "10.0.0.1"

    async def test_a_correcao_humana_vence_a_derivacao(self, engine) -> None:
        """A precedência não é detalhe do cadastro: se o gráfico mostrasse o
        valor derivado, a correção feita pela tela não chegaria a lugar
        nenhum."""
        await _parque(engine)
        await _ler(
            engine, sujeito=CHAVE, metrica="disp_cpu_pct", valor=1.0, qualidade="boa",
            rotulos={}, modulo="snmp", em=AGORA,
        )
        exp = await _exportar(engine)
        assert _por_metrica(exp, "disp_cpu_pct")[0].rotulos["funcao_negocio"] == (
            "rede_infraestrutura"
        )

    async def test_porta_se_liga_ao_dispositivo_pelo_rotulo_e_nao_pelo_texto(
        self, engine
    ) -> None:
        """O sujeito de uma porta é ``chave/porta`` e nome de porta tem barra
        ("Gi0/1"): partir a string erra em algum fabricante. O dono vem
        declarado pelo módulo."""
        await _parque(engine)
        await _ler(
            engine, sujeito=f"{CHAVE}/Gi0/1", metrica="iface_bytes_rx", valor=99.0,
            qualidade="boa", rotulos={"porta": "Gi0/1", "dispositivo": CHAVE, "fonte": "snmp"},
            modulo="snmp", em=AGORA,
        )
        exp = await _exportar(engine)
        r = _por_metrica(exp, "iface_bytes_rx")[0].rotulos
        assert r["dispositivo"] == CHAVE
        assert r["porta"] == "Gi0/1"
        assert r["ativo"] == "ERB-01", "a porta herda a identidade do equipamento"
        assert "fonte" not in r, "rótulo interno não vira dimensão"

    async def test_sujeito_desconhecido_sai_cru_em_vez_de_sumir(self, engine) -> None:
        await _parque(engine)
        await _ler(
            engine, sujeito="coisa:estranha", metrica="disp_cpu_pct", valor=7.0,
            qualidade="boa", rotulos={}, modulo="snmp", em=AGORA,
        )
        exp = await _exportar(engine)
        assert _por_metrica(exp, "disp_cpu_pct")[0].rotulos["sujeito"] == "coisa:estranha"


class TestEstado:
    async def _estado(self, motor, **v) -> None:
        async with motor.begin() as c:
            await c.execute(insert(estado).values(sujeito=CHAVE, visto_em=AGORA, **v))

    async def test_latencia_e_perda_saem_de_estado(self, engine) -> None:
        """Não moram em `leitura` — moram em `estado`, porque viram transição.
        Se o exportador só olhasse `leitura`, o gráfico de latência continuaria
        impossível, que era metade do problema."""
        await _parque(engine)
        await self._estado(engine, alcancavel=True, latencia_ms=4.2, perda_pct=0.0,
                           jitter_ms=0.3, qualidade="boa")
        exp = await _exportar(engine)
        assert _por_metrica(exp, "ativo_latencia_ms")[0].valor == 4.2
        assert _por_metrica(exp, "ativo_alcancavel")[0].valor == 1.0

    async def test_medida_ausente_nao_vira_zero(self, engine) -> None:
        """Latência zero afirmaria resposta instantânea — número plausível e
        errado, que meses depois aparece num relatório inexplicável."""
        await _parque(engine)
        await self._estado(engine, alcancavel=False, latencia_ms=None, perda_pct=100.0,
                           jitter_ms=None, qualidade="incerta")
        exp = await _exportar(engine)
        assert _por_metrica(exp, "ativo_latencia_ms") == []
        assert _por_metrica(exp, "ativo_jitter_ms") == []
        assert _por_metrica(exp, "ativo_alcancavel")[0].rotulos["qualidade"] == "incerta"


class TestFormato:
    async def test_ressalva_da_disponibilidade_vai_no_help(self, engine) -> None:
        """Quem consulta de fora não pode descobrir por acidente que esta série
        é amostrada e o registro exato está noutro lugar."""
        await _parque(engine)
        async with engine.begin() as c:
            await c.execute(
                insert(estado).values(
                    sujeito=CHAVE, alcancavel=True, qualidade="boa", visto_em=AGORA
                )
            )
        texto = formatar(await _exportar(engine))
        linha = next(x for x in texto.splitlines() if "HELP" in x and "alcancavel" in x)
        assert "transições" in linha

    async def test_aspas_no_rotulo_nao_quebram_a_linha(self, engine) -> None:
        await _parque(engine)
        await _ler(
            engine, sujeito=CHAVE, metrica="disp_cpu_pct", valor=1.0, qualidade="boa",
            rotulos={"porta": 'a"b\\c'}, modulo="snmp", em=AGORA,
        )
        texto = formatar(await _exportar(engine))
        assert r'porta="a\"b\\c"' in texto

    async def test_o_exportador_publica_a_propria_saude(self, engine) -> None:
        """Mesma regra dos módulos: quem não publica a própria saúde falha em
        silêncio, e silêncio é indistinguível de tudo bem."""
        await _parque(engine)
        texto = formatar(await _exportar(engine))
        for nome in ("amostras", "omitidas_velhas", "omitidas_externas", "duracao_s"):
            assert f"{PREFIXO}exportador_{nome}" in texto


class TestManifestos:
    async def test_so_o_rajant_declara_serie_externa(self, engine) -> None:
        ms = manifestos()
        assert ms["rajant"].serie_externa is True
        assert not ms["icmp"].serie_externa
        assert not ms["snmp"].serie_externa
