"""Testes do contrato de módulo, do registro e do coletor ICMP.

O que estes testes defendem, em uma frase: **a plataforma tem que saber a
diferença entre "perguntei e está ruim" e "não consegui perguntar"**. Quase
todo caso aqui é uma variação disso.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from inventario.modelo import Zona
from plataforma.dicionario import MetricaDesconhecida, sugerir, validar  # noqa: F401
from plataforma.modulos.contrato import (
    Alvo,
    Descoberta,
    Manifesto,
    Observacao,
    ResultadoColeta,
    filtrar_observacoes,
)
from plataforma.modulos.icmp import ModuloIcmp, Sonda, extrair_resposta, montar_echo
from plataforma.modulos.registro import (
    Agendador,
    Registro,
    ZonaIncompativel,
    executar_ciclo,
)


class TestDicionario:
    def test_metrica_conhecida_passa(self) -> None:
        assert validar("rf_rssi_dbm").familia.value == "rf"

    def test_metrica_desconhecida_sugere_o_nome_certo(self) -> None:
        """Recusa que não ajuda a corrigir custa uma tarde de alguém."""
        with pytest.raises(MetricaDesconhecida) as erro:
            validar("rssi_dbm")
        assert erro.value.sugestao == "rf_rssi_dbm"
        assert "rf_rssi_dbm" in str(erro.value)

    @pytest.mark.parametrize(
        ("errado", "certo"),
        [
            ("bateria_pct", "disp_bateria_pct"),
            ("latencia_ms", "ativo_latencia_ms"),
            ("cpu_pct", "so_cpu_pct"),
        ],
    )
    def test_sugestoes_uteis(self, errado: str, certo: str) -> None:
        assert sugerir(errado) == certo


class TestManifesto:
    def test_zona_de_controlador_e_invalida_em_qualquer_manifesto(self) -> None:
        """Não é configuração, é impossibilidade. Lá vivem os 32 CLPs."""
        for zona in (Zona.OT_NIVEL0, Zona.OT_NIVEL1, Zona.OT_NIVEL2):
            with pytest.raises(ValidationError, match="impossibilidade"):
                Manifesto(nome="perigoso", zona_permitida=(zona,))

    def test_zona_ot_nivel3_e_permitida(self) -> None:
        """O nível 3 é o historiador — leitura ali é legítima."""
        assert Manifesto(nome="hist", zona_permitida=(Zona.OT_NIVEL3,))

    def test_manifesto_sem_zona_e_recusado(self) -> None:
        with pytest.raises(ValidationError):
            Manifesto(nome="vago", zona_permitida=())

    def test_metrica_fora_do_dicionario_impede_o_carregamento(self) -> None:
        with pytest.raises(ValidationError):
            Manifesto(nome="dialeto", produz_metricas=("rssi",))

    def test_modulo_nao_pode_declarar_metrica_derivada(self) -> None:
        """`malha_trocas_peer_taxa` exige memória; coletor é sem estado."""
        with pytest.raises(ValidationError, match="calculada pela plataforma"):
            Manifesto(nome="mentiroso", produz_metricas=("malha_trocas_peer_taxa",))

    def test_quem_descobre_sozinho_precisa_declarar_entidades(self) -> None:
        with pytest.raises(ValidationError, match="fonte de inventário"):
            Manifesto(nome="descobridor", descoberta=Descoberta.PROPRIA)

        assert Manifesto(
            nome="descobridor",
            alvo=Alvo.SISTEMA,
            descoberta=Descoberta.PROPRIA,
            produz_entidades=("radio_mesh",),
        )


class TestObservacao:
    def test_observacao_nao_pode_carregar_nome_fora_do_dicionario(self) -> None:
        """O Pydantic embrulha a exceção, mas a sugestão sobrevive na mensagem."""
        with pytest.raises(ValidationError, match="não existe no dicionário"):
            Observacao(sujeito="x", metrica="rssi_dbm", valor=1)

    def test_filtro_preserva_a_sugestao_que_o_embrulho_perderia(self) -> None:
        _, recusadas = filtrar_observacoes(
            [{"sujeito": "a", "metrica": "rssi_dbm", "valor": 1}]
        )
        assert recusadas and "rf_rssi_dbm" in recusadas[0]
        assert not recusadas[0].startswith("observação inválida"), (
            "a recusa por dicionário precisa ser específica, não genérica"
        )

    def test_observacao_malformada_e_recusada_separadamente(self) -> None:
        _, recusadas = filtrar_observacoes(
            [{"sujeito": "a", "metrica": "ativo_alcancavel", "valor": "não-é-número"}]
        )
        assert recusadas and recusadas[0].startswith("observação inválida")

    def test_recusa_e_devolvida_nunca_engolida(self) -> None:
        aceitas, recusadas = filtrar_observacoes(
            [
                {"sujeito": "a", "metrica": "ativo_alcancavel", "valor": 1},
                {"sujeito": "a", "metrica": "alcancavel", "valor": 1},
            ]
        )
        assert len(aceitas) == 1
        assert len(recusadas) == 1
        assert "ativo_alcancavel" in recusadas[0], "a recusa precisa dizer o nome certo"


class ModuloFalso:
    def __init__(self, manifesto: Manifesto, resultado: ResultadoColeta | None = None) -> None:
        self.manifesto = manifesto
        self._resultado = resultado

    async def coletar(self, alvos: list[dict[str, Any]]) -> ResultadoColeta:
        if self._resultado is None:
            raise RuntimeError("o equipamento pegou fogo")
        return self._resultado


class ModuloLento:
    def __init__(self, manifesto: Manifesto) -> None:
        self.manifesto = manifesto
        self.chamadas = 0

    async def coletar(self, alvos: list[dict[str, Any]]) -> ResultadoColeta:
        self.chamadas += 1
        await asyncio.sleep(0.01)
        return ResultadoColeta(alvos_total=len(alvos))


class TestRegistro:
    def test_recusa_modulo_que_nao_opera_na_zona_do_coletor(self) -> None:
        """O par zona-do-módulo × zona-do-coletor é o que impede alcance indevido."""
        registro = Registro(zona_do_coletor=Zona.OT_NIVEL3)
        modulo = ModuloFalso(Manifesto(nome="corporativo", zona_permitida=(Zona.CORPORATIVA,)))
        with pytest.raises(ZonaIncompativel, match="ot_nivel3"):
            registro.registrar(modulo)

    def test_aceita_quando_as_zonas_batem(self) -> None:
        registro = Registro(zona_do_coletor=Zona.CORPORATIVA)
        registro.registrar(ModuloFalso(Manifesto(nome="ok")))
        assert "ok" in registro and len(registro) == 1


@pytest.mark.asyncio
class TestIsolamentoDeFalha:
    async def test_modulo_que_estoura_nao_derruba_o_ciclo(self) -> None:
        modulo = ModuloFalso(Manifesto(nome="explosivo"))
        resultado, erro = await executar_ciclo(modulo, [{"ip": "10.0.0.1"}])
        assert isinstance(erro, RuntimeError)
        assert resultado.rejeitadas, "a falha precisa aparecer, não sumir"

    async def test_falha_do_modulo_marca_alvos_falha_igual_ao_total(self) -> None:
        """A distinção que evita centenas de incidentes falsos.

        Coletor morto significa "não consegui perguntar", não "todos os
        equipamentos estão mal".
        """
        modulo = ModuloFalso(Manifesto(nome="explosivo"))
        alvos = [{"ip": f"10.0.0.{i}"} for i in range(5)]
        resultado, _ = await executar_ciclo(modulo, alvos)
        assert resultado.alvos_falha == resultado.alvos_total == 5
        assert not resultado.completa


@pytest.mark.asyncio
class TestAgendador:
    async def _agendador(self, modulo, alvos=None):
        registro = Registro()
        registro.registrar(modulo)
        recebido: list[tuple[str, ResultadoColeta]] = []

        async def fonte(_nome: str) -> list[dict[str, Any]]:
            return alvos if alvos is not None else []

        async def escoadouro(nome: str, resultado: ResultadoColeta) -> None:
            recebido.append((nome, resultado))

        return Agendador(registro, fonte, escoadouro), recebido

    async def test_emite_as_cinco_series_obrigatorias(self) -> None:
        modulo = ModuloFalso(Manifesto(nome="bom"), ResultadoColeta(alvos_total=3))
        agendador, _ = await self._agendador(modulo)
        resultado = await agendador.rodar_uma_vez("bom")
        nomes = {o.metrica for o in resultado.observacoes if o.sujeito == "modulo:bom"}
        assert nomes == {
            "modulo_ultima_coleta_ok_timestamp",
            "modulo_alvos_total",
            "modulo_alvos_falha",
            "modulo_duracao_coleta_s",
            "modulo_amostras_rejeitadas_total",
        }

    async def test_modulo_quebrado_ainda_se_reporta_mas_sem_carimbo_de_sucesso(
        self,
    ) -> None:
        """Módulo que morre em silêncio faz as métricas pararem — e ninguém nota."""
        agendador, _ = await self._agendador(ModuloFalso(Manifesto(nome="ruim")))
        resultado = await agendador.rodar_uma_vez("ruim")
        nomes = {o.metrica for o in resultado.observacoes}
        assert "modulo_alvos_falha" in nomes, "o módulo quebrado continua se reportando"
        assert "modulo_ultima_coleta_ok_timestamp" not in nomes, (
            "coleta que falhou não carimba sucesso"
        )
        assert agendador.falhas["ruim"] == 1

    async def test_falha_da_fonte_de_alvos_tambem_e_um_ciclo_com_falha(self) -> None:
        registro = Registro()
        registro.registrar(ModuloFalso(Manifesto(nome="m"), ResultadoColeta()))

        async def fonte(_nome: str) -> list[dict[str, Any]]:
            raise ConnectionError("banco fora")

        async def escoadouro(_n: str, _r: ResultadoColeta) -> None:
            return None

        agendador = Agendador(registro, fonte, escoadouro)
        resultado = await agendador.rodar_uma_vez("m")
        assert agendador.falhas["m"] == 1
        assert resultado.rejeitadas

    async def test_laco_roda_e_para_sem_vazar_tarefa(self) -> None:
        modulo = ModuloLento(Manifesto(nome="lento", intervalo_metricas_s=1))
        agendador, recebido = await self._agendador(modulo)
        await agendador.iniciar()
        await asyncio.sleep(0.05)
        await agendador.parar()
        assert modulo.chamadas >= 1
        assert recebido


class TestIcmpProtocolo:
    def test_pacote_tem_cabecalho_valido(self) -> None:
        pacote = montar_echo(0x1234, 7)
        assert pacote[0] == 8  # echo request
        assert len(pacote) == 8 + len(b"plataforma")

    def test_resposta_malformada_nao_explode(self) -> None:
        assert extrair_resposta(b"", socket_bruto=False) is None
        assert extrair_resposta(b"\x00\x01", socket_bruto=False) is None
        assert extrair_resposta(b"\x45" + b"\x00" * 5, socket_bruto=True) is None


class TestSonda:
    def test_jitter_com_uma_amostra_e_none_nao_zero(self) -> None:
        """Zero afirmaria estabilidade que não foi observada."""
        sonda = Sonda(alvo="10.0.0.1", enviados=1, latencias_ms=[3.0])
        assert sonda.jitter_ms is None

    def test_jitter_com_duas_amostras(self) -> None:
        sonda = Sonda(alvo="10.0.0.1", enviados=2, latencias_ms=[3.0, 5.0])
        assert sonda.jitter_ms == pytest.approx(2.0)

    def test_alvo_mudo_tem_perda_total_e_latencia_ausente(self) -> None:
        sonda = Sonda(alvo="10.0.0.1", enviados=3)
        assert sonda.perda_pct == 100.0
        assert sonda.latencia_media_ms is None
        assert not sonda.alcancavel


@pytest.mark.asyncio
class TestModuloIcmp:
    async def test_alvo_que_responde_publica_latencia(self) -> None:
        modulo = ModuloIcmp(tentativas=2, timeout_s=0.6)
        resultado = await modulo.coletar([{"ip": "127.0.0.1", "chave": "nome:local"}])
        por_metrica = {o.metrica: o.valor for o in resultado.observacoes}
        assert por_metrica["ativo_alcancavel"] == 1.0
        assert por_metrica["ativo_perda_pacote_pct"] == 0.0
        assert por_metrica["ativo_latencia_ms"] >= 0.0
        assert resultado.alvos_falha == 0

    async def test_alvo_mudo_publica_zero_de_alcance_e_nenhuma_latencia(self) -> None:
        """A regra que protege todo relatório de disponibilidade futuro.

        Latência zero afirmaria resposta instantânea de um equipamento que não
        respondeu — número plausível e errado, a pior categoria de falha.
        """
        modulo = ModuloIcmp(tentativas=1, timeout_s=0.3)
        resultado = await modulo.coletar([{"ip": "10.255.255.1", "chave": "nome:mudo"}])
        por_metrica = {o.metrica: o.valor for o in resultado.observacoes}
        assert por_metrica["ativo_alcancavel"] == 0.0
        assert por_metrica["ativo_perda_pacote_pct"] == 100.0
        assert "ativo_latencia_ms" not in por_metrica
        assert "ativo_jitter_ms" not in por_metrica
        assert resultado.alvos_falha == 1

    async def test_alvo_sem_ip_e_ignorado_sem_quebrar(self) -> None:
        modulo = ModuloIcmp(tentativas=1, timeout_s=0.2)
        resultado = await modulo.coletar([{"chave": "nome:sem-ip"}, {"ip": ""}])
        assert resultado.alvos_total == 0
        assert resultado.observacoes == ()

    async def test_manifesto_do_icmp_e_somente_leitura_e_corporativo(self) -> None:
        manifesto = ModuloIcmp().manifesto
        assert manifesto.somente_leitura
        assert manifesto.zona_permitida == (Zona.CORPORATIVA,)
