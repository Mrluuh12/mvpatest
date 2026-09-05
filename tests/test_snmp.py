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


LLDP = "1.0.8802.1.1.2.1.4.1.1"
RADIO = {"chave": "nome:ERM-09", "papel": "radio_ptp", "ip": "10.0.0.9"}


@pytest.mark.asyncio
class TestTabelaDeEnlace:
    """A peça que faltava para um rádio ponto a ponto entrar sem código novo.

    Tabela de portas produz métrica do equipamento; tabela de vizinhança produz
    **relação** — meia-aresta dirigida, com o medido naquele enlace pendurado
    nela. Confundir as duas foi o que já fez a ficha de um rádio mostrar "o SNR"
    de um equipamento que tem um SNR por vizinho.
    """

    def test_precisa_de_exatamente_uma_coluna_de_identidade(self) -> None:
        from plataforma.modulos.perfis_snmp import ColunaEnlace, TabelaEnlace

        with pytest.raises(ValueError, match="exatamente uma coluna"):
            TabelaEnlace(oid=LLDP, colunas=(ColunaEnlace(numero=9, papel="nome"),))
        with pytest.raises(ValueError, match="exatamente uma coluna"):
            TabelaEnlace(
                oid=LLDP,
                colunas=(
                    ColunaEnlace(numero=5, papel="identidade"),
                    ColunaEnlace(numero=6, papel="identidade"),
                ),
            )

    def test_coluna_e_medida_ou_papel_nunca_os_dois(self) -> None:
        from plataforma.modulos.perfis_snmp import ColunaEnlace

        with pytest.raises(ValueError, match="não ambos"):
            ColunaEnlace(numero=5, papel="identidade", medida="rf_snr_db")
        with pytest.raises(ValueError, match="nem nenhum"):
            ColunaEnlace(numero=5)

    def test_medida_de_enlace_passa_pelo_dicionario(self) -> None:
        from plataforma.modulos.perfis_snmp import ColunaEnlace

        with pytest.raises(ValueError, match="dicionário"):
            ColunaEnlace(numero=6, medida="snr_do_radio")

    async def test_a_linha_da_tabela_vira_meia_aresta_com_medida(self) -> None:
        from plataforma.modulos.perfis_snmp import ColunaEnlace, Perfil, TabelaEnlace

        perfil = Perfil(
            nome="teste", descricao="",
            enlaces=(
                TabelaEnlace(
                    oid=LLDP,
                    colunas=(
                        ColunaEnlace(numero=5, papel="identidade"),
                        ColunaEnlace(numero=9, papel="nome"),
                        ColunaEnlace(numero=11, medida="rf_snr_db"),
                    ),
                ),
            ),
        )
        s = SessaoFalsa(tabelas={LLDP: {
            "0.1.1": {5: b"\x00\x04\x07\x00\x85\x90", 9: "ERM-05", 11: 27},
        }})
        c = await colher(s, RADIO, perfil)
        (rel,) = c.relacoes
        assert rel.origem == "nome:ERM-09"
        assert rel.destino == "mac:00:04:07:00:85:90"
        assert rel.medidas == {"rf_snr_db": 27.0}
        assert rel.atributos["nome_do_vizinho"] == "ERM-05"

    async def test_vizinho_sem_identidade_nao_vira_aresta(self) -> None:
        """Aresta para um equipamento inventado é pior que aresta ausente."""
        from plataforma.modulos.perfis_snmp import ColunaEnlace, Perfil, TabelaEnlace

        perfil = Perfil(
            nome="t", descricao="",
            enlaces=(TabelaEnlace(
                oid=LLDP, colunas=(ColunaEnlace(numero=5, papel="identidade"),)
            ),),
        )
        s = SessaoFalsa(tabelas={LLDP: {"1": {5: ""}, "2": {}}})
        c = await colher(s, RADIO, perfil)
        assert c.relacoes == []

    async def test_vizinhanca_que_falhou_e_marcada_parcial(self) -> None:
        """Vizinhança não lida não autoriza fechar aresta: ausência aí quer
        dizer "não perguntei", não "deixou de existir"."""
        from plataforma.modulos.perfis_snmp import ColunaEnlace, Perfil, TabelaEnlace

        perfil = Perfil(
            nome="t", descricao="",
            enlaces=(TabelaEnlace(
                oid=LLDP, colunas=(ColunaEnlace(numero=5, papel="identidade"),)
            ),),
        )
        c = await colher(SessaoFalsa(quebrar={LLDP}), RADIO, perfil)
        assert c.vizinhanca_parcial is True
        assert any("vizinhança" in f.motivo for f in c.falhas)


class TestIdentidadeDoVizinho:
    """O defeito que só um agente de verdade mostrou.

    ``lldpRemChassisId`` não chega como ``bytes``: chega como ``OctetString`` do
    pysnmp. Passar isso por ``str()`` decodifica os seis octetos do MAC como se
    fossem texto, e o vizinho ``00:04:07:00:85:90`` virava identidade vazia.
    """

    def test_octetstring_do_pysnmp_vira_mac(self) -> None:
        from pysnmp.proto.api import v2c

        from plataforma.modulos.snmp import identidade_do_vizinho

        bruto = v2c.OctetString(b"\x00\x04\x07\x00\x85\x90")
        assert identidade_do_vizinho(bruto) == "mac:00:04:07:00:85:90"

    @pytest.mark.parametrize(
        "bruto",
        [b"\x00\x04\x07\x00\x85\x90", "00:04:07:00:85:90", "00-04-07-00-85-90",
         "0x000407008590", "000407008590"],
    )
    def test_as_quatro_formas_de_escrever_um_mac_dao_a_mesma_identidade(
        self, bruto
    ) -> None:
        from plataforma.modulos.snmp import identidade_do_vizinho

        assert identidade_do_vizinho(bruto) == "mac:00:04:07:00:85:90"

    def test_nome_de_sistema_vira_identidade_por_nome(self) -> None:
        from plataforma.modulos.snmp import identidade_do_vizinho

        assert identidade_do_vizinho("ERM-05-RADIO") == "nome:ERM-05-RADIO"

    @pytest.mark.parametrize("vazio", [None, "", "   "])
    def test_vazio_nao_vira_identidade(self, vazio) -> None:
        from plataforma.modulos.snmp import identidade_do_vizinho

        assert identidade_do_vizinho(vazio) is None


@pytest.mark.asyncio
class TestVizinhancaCompleta:
    """``relacoes_completas`` é a permissão para fechar aresta. Nega por
    omissão, como o resto da plataforma."""

    async def test_so_e_completa_quando_todo_alvo_respondeu(self) -> None:
        from plataforma.modulos.perfis_snmp import PONTO_A_PONTO

        s = SessaoFalsa(
            escalares={SYS_UPTIME: 100},
            tabelas={LLDP: {"1": {5: b"\x00\x04\x07\x00\x85\x90"}}},
        )
        mod = ModuloSnmp(s)
        r = await mod.coletar([RADIO])
        assert r.relacoes_completas is True
        assert len(r.relacoes) == 1
        assert PONTO_A_PONTO.enlaces, "o perfil de PtP declara vizinhança"

    async def test_alvo_mudo_torna_a_vizinhanca_parcial(self) -> None:
        s = SessaoFalsa(quebrar={"escalares"})
        r = await ModuloSnmp(s).coletar([RADIO])
        assert r.relacoes_completas is False
        assert r.alvos_falha == 1

    async def test_sem_alvo_nenhum_nao_e_vizinhanca_completa(self) -> None:
        """Zero alvos e zero relações fechariam a malha inteira se isso
        contasse como leitura completa."""
        r = await ModuloSnmp(SessaoFalsa()).coletar([])
        assert r.relacoes_completas is False


class TestFerramentaDoWalk:
    """A ferramenta que faz "acrescentar equipamento é configuração" custar
    minutos em vez de uma MIB que ninguém acha."""

    def _tool(self):
        """A ferramenta é um script, não um pacote: carrega por caminho.

        O registro em ``sys.modules`` antes de executar não é cerimônia — sem
        ele o ``@dataclass`` do próprio arquivo não consegue resolver o módulo
        em que foi declarado e estoura.
        """
        import importlib.util
        import sys
        from pathlib import Path

        if "perfil_do_walk" in sys.modules:
            return sys.modules["perfil_do_walk"]
        caminho = Path(__file__).resolve().parents[1] / "ferramentas/perfil_do_walk.py"
        spec = importlib.util.spec_from_file_location("perfil_do_walk", caminho)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_acha_tabela_com_indice_de_tres_partes(self) -> None:
        """A ``lldpRemTable`` é indexada por três componentes. A primeira versão
        parava no primeiro corte que desse um número e por isso só achava
        tabelas de índice simples — justo a de vizinhança passava batido."""
        t = self._tool()
        linhas = []
        for idx in ("0.1.1", "0.2.1"):
            linhas += [
                (f"1.0.8802.1.1.2.1.4.1.1.5.{idx}", "Hex-STRING", "00 04 07 00 85 90"),
                (f"1.0.8802.1.1.2.1.4.1.1.9.{idx}", "STRING", "ERM-05"),
            ]
        achadas = t.agrupar(linhas, 2)
        assert "1.0.8802.1.1.2.1.4.1.1" in achadas
        assert sorted(achadas["1.0.8802.1.1.2.1.4.1.1"]) == [5, 9]

    def test_escalar_nao_vira_tabela(self) -> None:
        t = self._tool()
        assert t.agrupar([("1.3.6.1.2.1.1.3.0", "Timeticks", "1")], 2) == {}

    def test_a_coluna_de_mac_e_apontada_como_identidade(self) -> None:
        t = self._tool()
        col = t.Coluna(numero=5, tipos={"Hex-STRING"}, amostras=["00 04 07 00 85 90"])
        assert t.parece_identidade(col)
        assert not t.parece_identidade(
            t.Coluna(numero=11, tipos={"INTEGER"}, amostras=["27"])
        )

    def test_a_medida_sai_comentada_porque_o_nome_e_decisao(self) -> None:
        """O dicionário canônico recusa nome inventado, e é assim que se
        descobre que falta decidir o nome antes de coletar."""
        t = self._tool()
        cols = {
            5: t.Coluna(numero=5, tipos={"Hex-STRING"}, amostras=["00 04 07 00 85 90"]),
            11: t.Coluna(numero=11, tipos={"INTEGER"}, amostras=["27"]),
        }
        texto = t.esqueleto("1.2.3", cols)
        assert 'ColunaEnlace(numero=5, papel="identidade")' in texto
        assert "# ColunaEnlace(numero=11" in texto
