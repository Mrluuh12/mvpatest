"""Testes do módulo SNMP declarativo.

O que erra num coletor SNMP quase nunca é o UDP: é o **mapeamento**. Contador
de 32 bits que vira a zero, uptime em centésimos lido como segundos, e a mesma
porta física virando dois sujeitos porque o nome e os contadores dela vivem em
tabelas diferentes. Os três estão guardados aqui.
"""

from __future__ import annotations

from typing import Any

import pytest

from plataforma.dicionario import POR_NOME
from plataforma.modulos.perfis_snmp import (
    BASICO,
    FATOR_UPTIME,
    PERFIS,
    REDE,
    SYS_UPTIME,
    Coluna,
    perfil_para,
)
from plataforma.modulos.snmp import MANIFESTO, ModuloSnmp, colher, sujeito_da_porta

IFX = "1.3.6.1.2.1.31.1.1.1"
IF = "1.3.6.1.2.1.2.2.1"
ALVO = {"chave": "sw-01", "papel": "switch", "ip": "10.0.0.1"}


class SessaoFalsa:
    """Responde o que o teste mandar; falha onde o teste mandar falhar."""

    def __init__(self, escalares=None, tabelas=None, quebrar: set[str] | None = None):
        self._escalares = escalares or {}
        self._tabelas = tabelas or {}
        self._quebrar = quebrar or set()

    async def escalares(self, alvo: str, oids: list[str]) -> dict[str, Any]:
        if "escalares" in self._quebrar:
            raise RuntimeError("sem resposta")
        return {o: v for o, v in self._escalares.items() if o in oids}

    async def tabela(self, alvo, oid, colunas) -> dict[str, dict[int, Any]]:
        if oid in self._quebrar:
            raise RuntimeError("noSuchObject")
        return self._tabelas.get(oid, {})


class TestPerfil:
    def test_toda_metrica_de_perfil_esta_no_dicionario(self) -> None:
        for p in PERFIS:
            for m in p.metricas():
                assert m in POR_NOME, f"{p.nome} publica {m!r}, que não é canônica"

    def test_coluna_e_metrica_ou_rotulo_nunca_os_dois(self) -> None:
        """Uma coluna que fosse as duas coisas viraria métrica com nome de
        porta — e ninguém entenderia o número depois."""
        with pytest.raises(ValueError, match="métrica"):
            Coluna(numero=1, metrica="iface_bytes_rx", rotulo="porta")
        with pytest.raises(ValueError, match="métrica"):
            Coluna(numero=1)

    def test_perfil_de_rede_usa_contador_de_64_bits(self) -> None:
        """ifInOctets é de 32 bits e vira a zero a cada 4 GB — 34 segundos num
        enlace de 1 Gb/s. Uma série sobre ele mostra quedas que não houve."""
        octetos = {
            c.numero
            for t in REDE.tabelas
            if t.oid == IFX
            for c in t.colunas
            if c.metrica in ("iface_bytes_rx", "iface_bytes_tx")
        }
        assert octetos == {6, 10}, "ifHCInOctets e ifHCOutOctets, da ifXTable"
        assert not any(t.oid == IF and c.numero in (10, 16)
                       for t in REDE.tabelas for c in t.colunas)

    def test_uptime_vem_em_centesimos(self) -> None:
        """Sem o fator, um equipamento de 3 dias vira 300 dias no painel."""
        assert REDE.fatores[SYS_UPTIME] == FATOR_UPTIME == 0.01

    def test_papel_sem_perfil_proprio_cai_no_basico(self) -> None:
        assert perfil_para("switch") is REDE
        assert perfil_para("ups") is BASICO
        assert perfil_para("qualquer-coisa") is BASICO

    def test_manifesto_nao_escreve(self) -> None:
        """SNMP de escrita em rede de mina é como se derruba uma frota."""
        assert MANIFESTO.somente_leitura


@pytest.mark.asyncio
class TestColheita:
    async def test_escalar_com_fator(self) -> None:
        s = SessaoFalsa(escalares={SYS_UPTIME: 25_920_000})
        c = await colher(s, ALVO, REDE)
        (o,) = [x for x in c.observacoes if x.metrica == "ativo_uptime_s"]
        assert o.valor == 259_200, "3 dias em segundos, não em centésimos"
        assert o.sujeito == "sw-01"

    async def test_a_porta_e_um_sujeito_proprio(self) -> None:
        """48 portas disputando a mesma linha de leitura fariam a última
        vencer — o defeito do Rajant, multiplicado por 48."""
        s = SessaoFalsa(tabelas={
            IFX: {"1": {1: "Gi0/1", 6: 100}, "2": {1: "Gi0/2", 6: 200}},
        })
        c = await colher(s, ALVO, REDE)
        assert {o.sujeito for o in c.observacoes} == {"sw-01/Gi0/1", "sw-01/Gi0/2"}

    async def test_o_nome_da_porta_vale_para_todas_as_tabelas(self) -> None:
        """ifName está na ifXTable e ifOperStatus na ifTable, indexadas pelo
        mesmo ifIndex. Emitir tabela a tabela fazia a mesma porta virar
        `sw-01/Gi0/1` e `sw-01/1`, e ninguém casaria os dois depois."""
        s = SessaoFalsa(tabelas={
            IFX: {"1": {1: "Gi0/1", 6: 100}},
            IF: {"1": {8: 1}},
        })
        c = await colher(s, ALVO, REDE)
        assert {o.sujeito for o in c.observacoes} == {"sw-01/Gi0/1"}
        assert {o.metrica for o in c.observacoes} == {"iface_bytes_rx", "iface_status_oper"}

    async def test_sem_ifname_o_indice_serve_de_nome(self) -> None:
        """Agente antigo sem ifXTable: feio, mas estável entre tabelas."""
        s = SessaoFalsa(tabelas={IF: {"7": {8: 1}}})
        c = await colher(s, ALVO, REDE)
        assert [o.sujeito for o in c.observacoes] == ["sw-01/7"]

    async def test_tabela_ausente_nao_perde_o_escalar(self) -> None:
        """Um roteador pode não ter ifXTable. O uptime continua valendo."""
        s = SessaoFalsa(escalares={SYS_UPTIME: 100}, quebrar={IFX, IF})
        c = await colher(s, ALVO, REDE)
        assert [o.metrica for o in c.observacoes] == ["ativo_uptime_s"]
        assert len(c.falhas) == 2

    async def test_valor_nao_numerico_nao_vira_zero(self) -> None:
        """`noSuchObject` virando 0 seria uma leitura inventada."""
        s = SessaoFalsa(escalares={SYS_UPTIME: "noSuchObject"})
        c = await colher(s, ALVO, REDE)
        assert c.observacoes == []

    async def test_alvo_sem_ip_e_falha_declarada(self) -> None:
        c = await colher(SessaoFalsa(), {"chave": "x", "papel": "switch"}, REDE)
        assert c.observacoes == []
        assert "sem endereço IP" in c.falhas[0].motivo


@pytest.mark.asyncio
class TestModulo:
    async def test_alvo_mudo_conta_como_falha(self) -> None:
        m = ModuloSnmp(SessaoFalsa(quebrar={"escalares"}))
        r = await m.coletar([ALVO])
        assert (r.alvos_total, r.alvos_falha) == (1, 1)
        assert any("sem resposta" in x for x in r.rejeitadas)

    async def test_alvo_que_respondeu_so_nas_portas_nao_conta_falha(self) -> None:
        """O rótulo `dispositivo` na leitura de porta é o que liga a interface
        de volta ao aparelho — sem ele, um switch que só responde ifTable
        apareceria como mudo."""
        m = ModuloSnmp(SessaoFalsa(tabelas={IFX: {"1": {1: "Gi0/1", 6: 9}}}))
        r = await m.coletar([ALVO])
        assert r.alvos_falha == 0

    async def test_alvos_sao_consultados_em_paralelo(self) -> None:
        """Em série, um parque mudo custa a soma de todos os timeouts: 36
        alvos vezes 11 operações vezes 2 s dão oito minutos por ciclo, num
        módulo cujo intervalo é de dois. Medido contra o inventário real, a
        troca levou a coleta de ~8 min para 8,7 s."""
        import asyncio

        class Lenta(SessaoFalsa):
            def __init__(self):
                super().__init__(escalares={SYS_UPTIME: 1})
                self.simultaneos = 0
                self.pico = 0

            async def escalares(self, alvo, oids):
                self.simultaneos += 1
                self.pico = max(self.pico, self.simultaneos)
                await asyncio.sleep(0.05)
                self.simultaneos -= 1
                return await super().escalares(alvo, oids)

        s = Lenta()
        alvos = [{"chave": f"d{i}", "papel": "ups", "ip": f"10.0.0.{i}"} for i in range(12)]
        r = await ModuloSnmp(s, concorrencia=6).coletar(alvos)
        assert s.pico > 1, "os alvos foram consultados um de cada vez"
        assert s.pico <= 6, "o limite de concorrência não foi respeitado"
        assert r.alvos_falha == 0

    async def test_o_limite_de_concorrencia_existe(self) -> None:
        """Sem limite, 700 alvos abririam 700 sockets UDP e afogariam o
        próprio coletor."""
        assert ModuloSnmp(SessaoFalsa()).concorrencia > 0

    async def test_sujeito_da_porta_e_derivavel(self) -> None:
        assert sujeito_da_porta("sw-01", "Gi0/1") == "sw-01/Gi0/1"
