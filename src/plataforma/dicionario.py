"""Dicionário canônico de métricas.

O vocabulário é fechado e a porta é estreita: um módulo publica com estes
nomes ou não publica. É o que faz `avg by (funcao_negocio) (rf_rssi_dbm)`
devolver Rajant, RADWIN e InfiNet no mesmo painel, sem um único caso especial
por fabricante — e o que evita que, em dois anos, existam vinte painéis que não
se falam porque cada módulo batizou a mesma grandeza do seu jeito.

Métrica nova não é linha de plugin. É revisão deste arquivo, deliberada,
porque nome canônico é contrato.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class Familia(StrEnum):
    DISPONIBILIDADE = "disponibilidade"
    INTERFACE = "interface"
    RF = "rf"
    MALHA = "malha"
    DISPOSITIVO = "dispositivo"
    GEO = "geo"
    SO = "so"
    SERVICO = "servico"
    OT = "ot"
    MODULO = "modulo"


class Tipo(StrEnum):
    MEDIDA = "medida"  # valor instantâneo (gauge)
    CONTADOR = "contador"  # monotônico, pode dar a volta
    ESTADO = "estado"  # enumeração discreta


class Metrica(BaseModel):
    nome: str
    familia: Familia
    tipo: Tipo
    unidade: str = ""
    descricao: str = ""


def _m(nome: str, familia: Familia, tipo: Tipo, unidade: str = "", descricao: str = "") -> Metrica:
    return Metrica(nome=nome, familia=familia, tipo=tipo, unidade=unidade, descricao=descricao)


_D = Familia.DISPONIBILIDADE
_I = Familia.INTERFACE
_R = Familia.RF
_ML = Familia.MALHA
_DV = Familia.DISPOSITIVO
_G = Familia.GEO
_SO = Familia.SO
_SV = Familia.SERVICO
_OT = Familia.OT
_MD = Familia.MODULO

METRICAS: tuple[Metrica, ...] = (
    # -- disponibilidade: o único sinal universal deste parque -------------
    _m("ativo_alcancavel", _D, Tipo.ESTADO, "", "1 responde, 0 não responde"),
    _m("ativo_latencia_ms", _D, Tipo.MEDIDA, "ms", "ida e volta média"),
    _m("ativo_perda_pacote_pct", _D, Tipo.MEDIDA, "%", "perda na janela de sondagem"),
    _m("ativo_jitter_ms", _D, Tipo.MEDIDA, "ms", "variação da latência"),
    _m("ativo_uptime_s", _D, Tipo.CONTADOR, "s"),
    # -- interface ---------------------------------------------------------
    _m("iface_status_oper", _I, Tipo.ESTADO),
    _m("iface_status_admin", _I, Tipo.ESTADO),
    _m("iface_bytes_rx", _I, Tipo.CONTADOR, "B", "usar contador de 64 bits"),
    _m("iface_bytes_tx", _I, Tipo.CONTADOR, "B", "usar contador de 64 bits"),
    _m("iface_pacotes_rx", _I, Tipo.CONTADOR),
    _m("iface_pacotes_tx", _I, Tipo.CONTADOR),
    _m("iface_erros_rx", _I, Tipo.CONTADOR),
    _m("iface_erros_tx", _I, Tipo.CONTADOR),
    _m("iface_descartes_rx", _I, Tipo.CONTADOR),
    _m("iface_descartes_tx", _I, Tipo.CONTADOR),
    _m("iface_velocidade_bps", _I, Tipo.MEDIDA, "bps"),
    _m("iface_utilizacao_pct", _I, Tipo.MEDIDA, "%"),
    # -- rádio -------------------------------------------------------------
    _m("rf_rssi_dbm", _R, Tipo.MEDIDA, "dBm", "quanta energia chega — quantidade, não qualidade"),
    _m("rf_snr_db", _R, Tipo.MEDIDA, "dB"),
    _m("rf_cinr_db", _R, Tipo.MEDIDA, "dB", "sinal sobre ruído mais interferência"),
    _m("rf_ruido_dbm", _R, Tipo.MEDIDA, "dBm", "sinal sem ruído é meio diagnóstico"),
    _m("rf_potencia_tx_dbm", _R, Tipo.MEDIDA, "dBm"),
    _m("rf_mcs_indice", _R, Tipo.MEDIDA, "", "a modulação em uso é o termômetro real"),
    _m("rf_frequencia_mhz", _R, Tipo.MEDIDA, "MHz"),
    _m("rf_largura_canal_mhz", _R, Tipo.MEDIDA, "MHz"),
    _m("rf_capacidade_estimada_mbps", _R, Tipo.MEDIDA, "Mbps"),
    _m("rf_retransmissoes_pct", _R, Tipo.MEDIDA, "%"),
    _m("rf_distancia_m", _R, Tipo.MEDIDA, "m"),
    _m("rf_clientes_associados", _R, Tipo.MEDIDA),
    # -- malha -------------------------------------------------------------
    _m("malha_peers_ativos", _ML, Tipo.MEDIDA),
    _m("malha_custo_link", _ML, Tipo.MEDIDA),
    _m("malha_custo_caminho_total", _ML, Tipo.MEDIDA),
    _m("malha_saltos", _ML, Tipo.MEDIDA),
    _m(
        "malha_trocas_peer_taxa",
        _ML,
        Tipo.MEDIDA,
        "1/h",
        "calculada pela plataforma a partir do grafo — módulo não publica, "
        "porque exigiria memória e coletor é sem estado",
    ),
    # -- dispositivo -------------------------------------------------------
    _m("disp_cpu_pct", _DV, Tipo.MEDIDA, "%"),
    _m("disp_memoria_pct", _DV, Tipo.MEDIDA, "%"),
    _m("disp_temperatura_c", _DV, Tipo.MEDIDA, "°C"),
    _m("disp_ventilador_rpm", _DV, Tipo.MEDIDA, "rpm"),
    _m("disp_energia_fonte", _DV, Tipo.ESTADO),
    _m("disp_bateria_pct", _DV, Tipo.MEDIDA, "%"),
    _m("disp_tensao_v", _DV, Tipo.MEDIDA, "V"),
    # -- posição -----------------------------------------------------------
    _m("geo_latitude", _G, Tipo.MEDIDA, "°"),
    _m("geo_longitude", _G, Tipo.MEDIDA, "°"),
    _m("geo_altitude_m", _G, Tipo.MEDIDA, "m"),
    _m("geo_velocidade_kmh", _G, Tipo.MEDIDA, "km/h"),
    # -- sistema operacional e serviço -------------------------------------
    _m("so_cpu_pct", _SO, Tipo.MEDIDA, "%"),
    _m("so_memoria_pct", _SO, Tipo.MEDIDA, "%"),
    _m("so_disco_uso_pct", _SO, Tipo.MEDIDA, "%"),
    _m("so_disco_io_ms", _SO, Tipo.MEDIDA, "ms"),
    _m("servico_disponivel", _SV, Tipo.ESTADO),
    _m("servico_tempo_resposta_ms", _SV, Tipo.MEDIDA, "ms"),
    _m("cert_dias_expiracao", _SV, Tipo.MEDIDA, "d"),
    # -- OT ----------------------------------------------------------------
    _m("ot_tag_valor", _OT, Tipo.MEDIDA, "", "acompanha sempre ot_qualidade"),
    _m("ot_qualidade", _OT, Tipo.ESTADO, "", "valor sem código de qualidade não é dado"),
    _m("ot_gateway_latencia_ms", _OT, Tipo.MEDIDA, "ms"),
    _m("ot_conexao_status", _OT, Tipo.ESTADO),
    # -- auto-observação: obrigatória em todo módulo ------------------------
    _m("modulo_ultima_coleta_ok_timestamp", _MD, Tipo.MEDIDA, "s"),
    _m("modulo_alvos_total", _MD, Tipo.MEDIDA),
    _m("modulo_alvos_falha", _MD, Tipo.MEDIDA),
    _m("modulo_duracao_coleta_s", _MD, Tipo.MEDIDA, "s"),
    _m("modulo_amostras_rejeitadas_total", _MD, Tipo.CONTADOR),
)

POR_NOME: dict[str, Metrica] = {m.nome: m for m in METRICAS}

#: Sem estas cinco, um módulo é opaco: quando ele morre, as métricas
#: simplesmente param, e ausência de dado ruim é indistinguível de ausência de
#: problema.
OBRIGATORIAS_DE_MODULO: frozenset[str] = frozenset(
    m.nome for m in METRICAS if m.familia is Familia.MODULO
)

#: Calculadas pela plataforma. Um módulo que as declarasse estaria mentindo
#: sobre a própria natureza — não tem memória para computá-las.
DERIVADAS: frozenset[str] = frozenset({"malha_trocas_peer_taxa"})


class MetricaDesconhecida(ValueError):
    """Levantada quando alguém publica um nome fora do dicionário.

    A mensagem sugere o nome mais parecido de propósito: recusa que não ajuda
    a corrigir vira meia hora de alguém achando que o coletor quebrou.
    """

    def __init__(self, nome: str) -> None:
        sugestao = sugerir(nome)
        extra = f" — você quis dizer {sugestao!r}?" if sugestao else ""
        super().__init__(f"métrica {nome!r} não existe no dicionário canônico{extra}")
        self.nome = nome
        self.sugestao = sugestao


def sugerir(nome: str) -> str | None:
    """Nome canônico mais próximo, por sufixo ou subcadeia."""
    alvo = nome.strip().lower()
    if not alvo:
        return None
    candidatos = [c for c in POR_NOME if c.endswith(alvo) or alvo in c]
    if not candidatos:
        candidatos = [c for c in POR_NOME if c.split("_", 1)[-1].startswith(alvo.split("_")[0])]
    return min(candidatos, key=len) if candidatos else None


def validar(nome: str) -> Metrica:
    if (metrica := POR_NOME.get(nome)) is None:
        raise MetricaDesconhecida(nome)
    return metrica
