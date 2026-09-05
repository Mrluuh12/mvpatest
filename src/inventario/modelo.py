"""Modelo de domínio do inventário.

Duas regras sustentam este módulo:

1. **Todo valor carrega a sua origem.** Um campo preenchido por derivação
   automática, por um módulo de coleta, ou por uma pessoa na área ADM não são
   a mesma coisa — e a plataforma precisa saber a diferença para não sobrescrever
   trabalho humano ao rodar a semeadura de novo.

2. **A precedência depende da natureza do campo.** Para campos de *intenção*
   (função de negócio, criticidade), quem manda é o cadastro humano. Para campos
   de *observação* (firmware, modelo, MAC), quem manda é o equipamento — uma
   pessoa digitando a versão de firmware é pior evidência do que lê-la do
   próprio aparelho.

A divergência entre o que foi cadastrado e o que foi descoberto não é um erro a
esconder: é um achado a relatar.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Origem e precedência
# --------------------------------------------------------------------------


class Origem(StrEnum):
    """De onde veio um valor."""

    DERIVADO = "derivado"  # inferido pela plataforma a partir de nome/endereço
    DESCOBERTO = "descoberto"  # reportado por um módulo de coleta
    CADASTRADO = "cadastrado"  # declarado por uma pessoa na área ADM


class Natureza(StrEnum):
    """O que o campo representa — decide quem ganha um conflito."""

    INTENCAO = "intencao"  # o que se quer que seja: função, criticidade, apelido
    OBSERVACAO = "observacao"  # o que de fato é: firmware, modelo, MAC


PRECEDENCIA: dict[Natureza, dict[Origem, int]] = {
    Natureza.INTENCAO: {
        Origem.CADASTRADO: 3,
        Origem.DESCOBERTO: 2,
        Origem.DERIVADO: 1,
    },
    Natureza.OBSERVACAO: {
        Origem.DESCOBERTO: 3,
        Origem.CADASTRADO: 2,
        Origem.DERIVADO: 1,
    },
}


class Valor(BaseModel):
    """Um valor com procedência."""

    valor: Any
    origem: Origem
    em: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def vence(self, outro: Valor | None, natureza: Natureza) -> bool:
        """True se este valor deve prevalecer sobre ``outro``."""
        if outro is None:
            return True
        tabela = PRECEDENCIA[natureza]
        meu, dele = tabela[self.origem], tabela[outro.origem]
        if meu != dele:
            return meu > dele
        return self.em > outro.em  # mesma origem: o mais recente ganha


def conciliar(
    atual: Valor | None, novo: Valor, natureza: Natureza
) -> tuple[Valor, bool]:
    """Concilia dois valores. Devolve (vencedor, houve_divergencia).

    Divergência é quando os dois lados afirmam coisas diferentes com origens
    diferentes — o caso que interessa relatar (o cadastro diz um modelo, o
    equipamento diz outro).
    """
    if atual is None:
        return novo, False
    divergencia = atual.valor != novo.valor and atual.origem != novo.origem
    vencedor = novo if novo.vence(atual, natureza) else atual
    return vencedor, divergencia


# --------------------------------------------------------------------------
# Identidade
# --------------------------------------------------------------------------


class TipoIdentificador(StrEnum):
    MAC = "mac"
    SERIE = "serie"
    NOME = "nome"
    IP = "ip"
    ASSET_ID = "asset_id"


#: Ordem de confiança para casar duas observações do mesmo dispositivo.
#: O IP fica de fora de propósito: há 26 IPs repetidos no cadastro real, e um
#: identificador que se repete não identifica nada. Ele só serve de desempate.
PRECEDENCIA_IDENTIDADE: tuple[TipoIdentificador, ...] = (
    TipoIdentificador.MAC,
    TipoIdentificador.SERIE,
    TipoIdentificador.NOME,
)


class Identificador(BaseModel):
    tipo: TipoIdentificador
    valor: str


# --------------------------------------------------------------------------
# Vocabulário canônico
# --------------------------------------------------------------------------


class Papel(StrEnum):
    """O que o dispositivo é. Vocabulário fechado — dialeto não entra aqui."""

    RADIO_MESH = "radio_mesh"
    RADIO_PTP = "radio_ptp"
    RADIO_PTMP = "radio_ptmp"
    IHM_BORDO = "ihm_bordo"
    HUB_PTX = "hub_ptx"
    GATEWAY_PNEU = "gateway_pneu"
    GPS = "gps"
    ENDPOINT_IMX = "endpoint_imx"
    PLC = "plc"
    CONVERSOR_CAN = "conversor_can"
    SENSOR_PESO = "sensor_peso"
    ROTEADOR = "roteador"
    SWITCH = "switch"
    CAMERA = "camera"
    UPS = "ups"
    SERVIDOR = "servidor"
    PERIFERICO = "periferico"
    DESCONHECIDO = "desconhecido"


class Zona(StrEnum):
    """Zona de rede. Governa o que a coleta e a ação podem fazer."""

    CORPORATIVA = "corporativa"
    INDUSTRIAL_DMZ = "industrial_dmz"
    OT_NIVEL3 = "ot_nivel3"
    OT_NIVEL2 = "ot_nivel2"
    OT_NIVEL1 = "ot_nivel1"
    OT_NIVEL0 = "ot_nivel0"


#: Zonas onde nenhum módulo pode declarar operação. Não é configuração: é
#: impossibilidade. Um manifesto que as declare é recusado no carregamento.
ZONAS_PROIBIDAS: frozenset[Zona] = frozenset(
    {Zona.OT_NIVEL0, Zona.OT_NIVEL1, Zona.OT_NIVEL2}
)


class TipoAresta(StrEnum):
    PEER_MESH = "peer_mesh"
    PEER_PTP = "peer_ptp"
    ASSOCIACAO_PTMP = "associacao_ptmp"
    ENLACE_FISICO = "enlace_fisico"
    DEPENDENCIA_L3 = "dependencia_l3"
    DEPENDENCIA_SERVICO = "dependencia_servico"
    ALIMENTACAO = "alimentacao"
    EMBARCADO_EM = "embarcado_em"


# --------------------------------------------------------------------------
# Controle de acesso
# --------------------------------------------------------------------------


class Permissao(StrEnum):
    VER = "ver"
    EDITAR_PAINEL = "editar_painel"
    #: Rodar sonda de diagnóstico: ping, traceroute, SNMP walk, teste de
    #: porta. É **separada** de EXECUTAR_ACAO de propósito — quem pode
    #: perguntar "este endereço responde?" não deveria por isso poder
    #: reiniciar o rádio de um caminhão em operação. Conflatar as duas seria
    #: dar poder de parar máquina a quem só precisava diagnosticar.
    DIAGNOSTICAR = "diagnosticar"
    EXECUTAR_ACAO = "executar_acao"
    APROVAR_ACAO = "aprovar_acao"
    CADASTRAR_ATIVO = "cadastrar_ativo"
    EDITAR_ATIVO = "editar_ativo"
    GERIR_MODULOS = "gerir_modulos"
    GERIR_CREDENCIAIS = "gerir_credenciais"
    GERIR_USUARIOS = "gerir_usuarios"
    GERIR_DICIONARIO = "gerir_dicionario"


class PapelUsuario(StrEnum):
    ADMINISTRADOR = "administrador"
    ENGENHEIRO = "engenheiro"
    OPERADOR = "operador"
    CAMPO = "campo"
    LEITOR = "leitor"


#: O administrador é o topo: cadastro de ativos, módulos, credenciais, usuários
#: e dicionário vivem nele. Os demais papéis são recortes deliberados.
MATRIZ_PAPEIS: dict[PapelUsuario, frozenset[Permissao]] = {
    PapelUsuario.ADMINISTRADOR: frozenset(Permissao),
    PapelUsuario.ENGENHEIRO: frozenset(
        {
            Permissao.VER,
            Permissao.EDITAR_PAINEL,
            Permissao.DIAGNOSTICAR,
            Permissao.EXECUTAR_ACAO,
            Permissao.CADASTRAR_ATIVO,
            Permissao.EDITAR_ATIVO,
            Permissao.GERIR_MODULOS,
        }
    ),
    PapelUsuario.OPERADOR: frozenset(
        {
            Permissao.VER,
            Permissao.EDITAR_PAINEL,
            Permissao.DIAGNOSTICAR,
            Permissao.EXECUTAR_ACAO,
        }
    ),
    # Quem está em campo diagnostica — é o trabalho dele. Executar ação
    # continua separado.
    PapelUsuario.CAMPO: frozenset(
        {Permissao.VER, Permissao.DIAGNOSTICAR, Permissao.EXECUTAR_ACAO}
    ),
    PapelUsuario.LEITOR: frozenset({Permissao.VER}),
}


class Concessao(BaseModel):
    """Um papel vale dentro de um conjunto de zonas, não em toda a plataforma.

    É a composição que faz o modelo servir numa mina: a mesma pessoa pode ser
    operadora na zona corporativa e apenas leitora em OT.
    """

    papel: PapelUsuario
    zonas: frozenset[Zona]

    def pode(self, permissao: Permissao, zona: Zona) -> bool:
        if zona not in self.zonas:
            return False
        return permissao in MATRIZ_PAPEIS[self.papel]


class Usuario(BaseModel):
    login: str
    nome: str
    concessoes: tuple[Concessao, ...] = ()
    ativo: bool = True

    def pode(self, permissao: Permissao, zona: Zona) -> bool:
        """Autorização é negada por padrão e concedida por composição."""
        if not self.ativo:
            return False
        return any(c.pode(permissao, zona) for c in self.concessoes)


# --------------------------------------------------------------------------
# Entidades
# --------------------------------------------------------------------------


class Dispositivo(BaseModel):
    """O que tem endereço. Mede."""

    chave: str
    nome_bruto: str
    nome_canonico: str
    papel: Papel
    zona: Zona = Zona.CORPORATIVA
    identificadores: tuple[Identificador, ...] = ()
    ativo_id: str | None = None
    campos: dict[str, Valor] = Field(default_factory=dict)

    def identidade(self) -> tuple[TipoIdentificador, str] | None:
        """O identificador mais confiável disponível, na ordem de precedência."""
        por_tipo = {i.tipo: i.valor for i in self.identificadores}
        for tipo in PRECEDENCIA_IDENTIDADE:
            if valor := por_tipo.get(tipo):
                return tipo, valor
        return None


class Ativo(BaseModel):
    """A entidade de negócio: o caminhão, a torre. Agrega, nunca mede."""

    ativo_id: str
    frota: str
    numero: str
    funcao_negocio: Valor
    site: str = "mina"
    dispositivos: tuple[str, ...] = ()


class Aresta(BaseModel):
    origem_chave: str
    destino_chave: str
    tipo: TipoAresta
    direcional: bool = True
