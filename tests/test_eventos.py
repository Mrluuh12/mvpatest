"""Testes do canal de eventos — o que o equipamento conta por conta própria.

Aqui a plataforma não escolhe quando nem quanto recebe, e é isso que muda o
que precisa ser testado. Não é a aritmética: é **não perder o aviso do dia
ruim** e **não deixar um equipamento defeituoso derrubar a plataforma junto**.

É também o único canal em que a origem não é confiável: syslog sobre UDP não
autentica nada. O teste guarda que isso fique registrado ao lado do evento, em
vez de o evento ser lido como prova.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from inventario.modelo import Zona
from plataforma.db import eventos as guarda
from plataforma.db.esquema import dispositivo, identificador
from plataforma.db.repositorio_pg import apagar_esquema, criar_engine, criar_esquema
from plataforma.eventos import SEVERIDADES, Evento, analisar_syslog, descrever_trap

URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma_teste",
)
AGORA = datetime(2026, 9, 4, 11, 20, 5, tzinfo=UTC)


class TestAnalise:
    def test_cisco_com_numero_de_sequencia(self) -> None:
        """A Cisco enfia `45: ` antes do carimbo, e isso quebra parser
        ingênuo — que então joga a mensagem inteira no campo errado."""
        e = analisar_syslog(
            b"<189>45: Sep  4 11:20:01 SW-01 %LINK-3-UPDOWN: Interface Gi0/2, down",
            "10.0.0.1", AGORA,
        )
        assert e.remetente == "SW-01"
        assert e.mensagem.startswith("%LINK-3-UPDOWN")
        assert e.severidade == "atencao"
        assert e.facilidade == "local7"

    def test_rfc5424(self) -> None:
        e = analisar_syslog(
            b"<14>1 2026-09-04T11:20:03Z rt-01 bgpd 42 ID1 - vizinho caiu",
            "10.0.0.2", AGORA,
        )
        assert (e.remetente, e.mensagem) == ("rt-01", "vizinho caiu")
        assert e.atributos["aplicacao"] == "bgpd"
        assert e.em == datetime(2026, 9, 4, 11, 20, 3, tzinfo=UTC)

    def test_mensagem_torta_vira_evento_e_nao_lixo(self) -> None:
        """Equipamento que fala errado ainda está falando. Descartar em
        silêncio é perder o aviso que interessa justamente no dia ruim."""
        e = analisar_syslog(b"isto nao e syslog nenhum", "10.0.0.3", AGORA)
        assert e.mensagem == "isto nao e syslog nenhum"
        assert e.atributos["formato"] == "sem_pri"

    def test_severidade_sai_do_pri(self) -> None:
        for n, nome in enumerate(SEVERIDADES):
            e = analisar_syslog(f"<{8 + n}>teste".encode(), "10.0.0.1", AGORA)
            assert e.severidade == nome

    def test_relogio_do_remetente_e_comparado_com_o_nosso(self) -> None:
        """Equipamento com relógio errado em horas produz histórico que não
        casa com nenhum outro, e o defeito passa despercebido até alguém
        tentar correlacionar."""
        e = analisar_syslog(
            b"<14>1 2026-09-04T09:20:03Z rt-01 x 1 ID1 - t", "10.0.0.2", AGORA
        )
        assert e.relogio_divergente_s == pytest.approx(7202, abs=2)

    def test_5424_com_campo_faltando_ainda_entrega_a_hora(self) -> None:
        """No campo aparece 5424 fora do padrão. Perder o carimbo por causa de
        um campo a menos é descartar o dado bom junto com a formatação ruim."""
        e = analisar_syslog(
            b"<14>1 2026-09-04T09:20:03Z rt-01 faltou campo", "10.0.0.2", AGORA
        )
        assert e.em == datetime(2026, 9, 4, 9, 20, 3, tzinfo=UTC)
        assert e.remetente == "rt-01"
        assert e.atributos["formato"] == "rfc5424_torto"

    def test_sem_carimbo_a_mensagem_nao_perde_a_primeira_palavra(self) -> None:
        e = analisar_syslog(b"<14>porta Gi0/1 caiu", "10.0.0.1", AGORA)
        assert e.mensagem == "porta Gi0/1 caiu"
        assert e.remetente == ""


class TestTrap:
    def test_generico_vira_frase(self) -> None:
        nome, sev, frase = descrever_trap("1.3.6.1.6.3.1.1.5.3", {"ifIndex": "7"})
        assert (nome, sev) == ("linkDown", "erro")
        assert "porta caiu" in frase and "7" in frase

    def test_desconhecido_nao_vira_lixo(self) -> None:
        """Um OID que ninguém traduziu ainda é melhor que evento descartado —
        é assim que se descobre o que traduzir."""
        nome, sev, frase = descrever_trap("1.3.6.1.4.1.9.9.41.2.0.1", {})
        assert nome == "1.3.6.1.4.1.9.9.41.2.0.1"
        assert sev == "informativo"
        assert "não traduzido" in frase


class TestVazao:
    def test_deixa_passar_ate_o_limite(self) -> None:
        v = guarda.Vazao(limite=3)
        assert [v.aceita("10.0.0.1", AGORA) for _ in range(4)] == [
            True, True, True, False
        ]

    def test_uma_origem_em_tempestade_nao_cala_as_outras(self) -> None:
        """Sem isso, um equipamento defeituoso enche a tabela e leva junto o
        que importa."""
        v = guarda.Vazao(limite=2)
        for _ in range(5):
            v.aceita("ruidoso", AGORA)
        assert v.aceita("quieto", AGORA) is True

    def test_a_janela_vira_no_minuto(self) -> None:
        v = guarda.Vazao(limite=1)
        assert v.aceita("a", AGORA) is True
        assert v.aceita("a", AGORA) is False
        assert v.aceita("a", AGORA + timedelta(minutes=1)) is True

    def test_o_descarte_e_contado_e_devolvido_uma_vez_so(self) -> None:
        """Quem chama grava o resumo — gravá-lo duas vezes seria pior que
        nenhum."""
        v = guarda.Vazao(limite=1)
        for _ in range(4):
            v.aceita("a", AGORA)
        assert v.descartes() == {"a": 3}
        assert v.descartes() == {}


@pytest_asyncio.fixture
async def motor():
    engine = criar_engine(URL)
    try:
        await apagar_esquema(engine)
        await criar_esquema(engine)
    except Exception as erro:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"Postgres indisponível: {erro}")
    async with engine.begin() as c:
        await c.execute(
            dispositivo.insert().values(
                chave="sw1", nome_bruto="SW-01", nome_canonico="SW-01",
                papel="switch", zona="corporativa",
            )
        )
        await c.execute(
            identificador.insert().values(
                dispositivo_chave="sw1", tipo="ip", valor="10.0.0.1"
            )
        )
    yield engine
    await apagar_esquema(engine)
    await engine.dispose()


@pytest.mark.asyncio
class TestGravacao:
    async def test_resolve_a_origem_pelo_ip(self, motor) -> None:
        async with motor.begin() as c:
            r = await guarda.gravar(c, [Evento(origem_ip="10.0.0.1", mensagem="oi")])
        assert (r["gravados"], r["sem_dono"]) == (1, 0)
        async with motor.connect() as c:
            (item,) = await guarda.ultimos(c)
        assert item["sujeito"] == "sw1"

    async def test_ip_desconhecido_e_guardado_como_achado(self, motor) -> None:
        """Alguma coisa na rede está falando e a planilha não a conhece. É
        achado de inventário, não erro — e some se for descartado."""
        async with motor.begin() as c:
            r = await guarda.gravar(c, [Evento(origem_ip="10.9.9.9", mensagem="?")])
        assert r["sem_dono"] == 1
        async with motor.connect() as c:
            (item,) = await guarda.ultimos(c)
        assert item["sujeito"] is None
        assert item["origem_ip"] == "10.9.9.9"

    async def test_ip_disputado_nao_pendura_o_evento_no_errado(self, motor) -> None:
        async with motor.begin() as c:
            await c.execute(
                dispositivo.insert().values(
                    chave="sw2", nome_bruto="SW-02", nome_canonico="SW-02",
                    papel="switch", zona="corporativa",
                )
            )
            await c.execute(
                identificador.insert().values(
                    dispositivo_chave="sw2", tipo="ip", valor="10.0.0.1"
                )
            )
            await guarda.gravar(c, [Evento(origem_ip="10.0.0.1", mensagem="x")])
        async with motor.connect() as c:
            (item,) = await guarda.ultimos(c)
        assert item["sujeito"] is None

    async def test_a_confianca_fica_ao_lado_do_evento(self, motor) -> None:
        """Syslog sobre UDP não autentica nada. Um evento não é prova; é o que
        alguém disse — e a tela precisa poder mostrar essa diferença."""
        async with motor.begin() as c:
            await guarda.gravar(c, [Evento(origem_ip="10.0.0.1", mensagem="x")])
        async with motor.connect() as c:
            (item,) = await guarda.ultimos(c)
        assert item["confianca"] == "ip_de_origem"

    async def test_filtra_por_severidade_minima(self, motor) -> None:
        async with motor.begin() as c:
            await guarda.gravar(c, [
                Evento(origem_ip="10.0.0.1", severidade="depuracao", mensagem="d"),
                Evento(origem_ip="10.0.0.1", severidade="critico", mensagem="c"),
            ])
        async with motor.connect() as c:
            graves = await guarda.ultimos(c, severidade_minima="erro")
        assert [e["severidade"] for e in graves] == ["critico"]

    async def test_limpeza_apaga_o_que_passou_da_retencao(self, motor) -> None:
        """Evento não se agrega nem se resume: acumula para sempre se ninguém
        apagar."""
        velho = datetime.now(UTC) - timedelta(days=60)
        async with motor.begin() as c:
            await guarda.gravar(c, [
                Evento(origem_ip="10.0.0.1", mensagem="velho", recebido_em=velho),
                Evento(origem_ip="10.0.0.1", mensagem="novo"),
            ])
        async with motor.begin() as c:
            assert await guarda.limpar(c, dias=30) == 1
        async with motor.connect() as c:
            assert [e["mensagem"] for e in await guarda.ultimos(c)] == ["novo"]


@pytest.mark.asyncio
class TestReceptor:
    async def test_zona_proibida_e_recusada_no_construtor(self, motor) -> None:
        """Nem escutar o que um controlador de processo diz a plataforma faz."""
        from plataforma.receptor import Receptor

        with pytest.raises(ValueError, match="impossibilidade"):
            Receptor(motor, Zona.OT_NIVEL2)

    async def test_evento_de_outra_zona_e_marcado_nao_recusado(self, motor) -> None:
        """Ou a rede está fazendo ponte onde não deveria, ou alguém está
        forjando. Recusar em silêncio some com a pergunta."""
        from plataforma.receptor import Receptor

        r = Receptor(motor, Zona.CORPORATIVA)
        e = Evento(origem_ip="10.0.0.1", mensagem="x")
        r.marcar_zona([e], {"10.0.0.1": "ot_nivel3"})
        assert e.confianca == "ip_de_outra_zona"
        assert e.atributos["zona_do_cadastro"] == "ot_nivel3"

    async def test_zona_igual_nao_marca_nada(self, motor) -> None:
        from plataforma.receptor import Receptor

        r = Receptor(motor, Zona.CORPORATIVA)
        e = Evento(origem_ip="10.0.0.1", mensagem="x")
        r.marcar_zona([e], {"10.0.0.1": "corporativa"})
        assert e.confianca == "ip_de_origem"
