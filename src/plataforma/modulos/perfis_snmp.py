"""Perfis SNMP: acrescentar um tipo de equipamento é configuração, não código.

É o que "módulo SNMP declarativo" quer dizer. Um perfil diz **quais OIDs ler**
e **para que métrica canônica cada um vai**; o módulo não conhece fabricante
nenhum. Suportar um switch novo é escrever quinze linhas aqui, não uma classe.

Duas decisões que o perfil materializa
--------------------------------------

**Contador de 64 bits, sempre que existir.** `ifInOctets` é de 32 bits e vira
a zero a cada 4 GB — num enlace de 1 Gb/s isso são 34 segundos. Uma série
montada sobre ele mostra quedas que nunca aconteceram. Por isso o perfil de
rede lê `ifHCInOctets` (ifXTable), e o dicionário canônico já pedia isso em
letras miúdas: *"usar contador de 64 bits"*.

**`sysUpTime` não está em segundos.** Está em centésimos, e é o erro clássico
de quem monta o primeiro coletor: um equipamento ligado há 3 dias vira 300
dias no painel. O fator está declarado, à vista.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from inventario.modelo import TipoAresta
from plataforma.dicionario import validar


class Coluna(BaseModel):
    """Uma coluna de tabela SNMP.

    Ou vira métrica, ou vira rótulo da linha (o nome da porta, por exemplo) —
    nunca as duas coisas, e nunca nenhuma.
    """

    numero: int
    metrica: str | None = None
    rotulo: str | None = None
    fator: float = 1.0

    @field_validator("metrica")
    @classmethod
    def _no_dicionario(cls, v: str | None) -> str | None:
        if v is not None:
            validar(v)
        return v

    def model_post_init(self, _ctx) -> None:
        if bool(self.metrica) == bool(self.rotulo):
            raise ValueError(
                f"coluna {self.numero}: declare métrica **ou** rótulo, não ambos "
                f"nem nenhum"
            )


class Tabela(BaseModel):
    """Uma tabela SNMP percorrida por índice — tipicamente uma linha por porta."""

    oid: str
    colunas: tuple[Coluna, ...]


class ColunaEnlace(BaseModel):
    """Uma coluna de tabela de vizinhança.

    Ou identifica o vizinho, ou mede o enlace até ele. Nada de tabela de
    vizinhos aqui vira métrica do próprio equipamento: SNR de um enlace não é
    "o SNR do rádio", é o SNR daquele par — e foi confundir isso que já fez a
    ficha de um rádio de malha mostrar um número que não existia.
    """

    numero: int
    #: ``identidade`` traz o MAC ou o nome do vizinho; ``nome`` é rótulo.
    papel: str | None = None
    #: Métrica canônica do enlace, quando a coluna é medida.
    medida: str | None = None
    fator: float = 1.0

    @field_validator("medida")
    @classmethod
    def _no_dicionario(cls, v: str | None) -> str | None:
        if v is not None:
            validar(v)
        return v

    @field_validator("papel")
    @classmethod
    def _papel_conhecido(cls, v: str | None) -> str | None:
        if v is not None and v not in {"identidade", "nome"}:
            raise ValueError(f"papel {v!r}: use 'identidade' ou 'nome'")
        return v

    def model_post_init(self, _ctx) -> None:
        if bool(self.medida) == bool(self.papel):
            raise ValueError(
                f"coluna {self.numero}: declare medida **ou** papel, não ambos "
                f"nem nenhum"
            )


class TabelaEnlace(BaseModel):
    """Tabela cujas linhas são **vizinhos**, não portas.

    É o que faltava para um rádio ponto a ponto entrar na plataforma sem código
    novo. A tabela de portas produz métrica do equipamento; esta produz relação
    — meia-aresta dirigida, com o que foi medido naquele enlace pendurado nela.

    Precisa de exatamente uma coluna de identidade: sem saber com quem, a
    medida não tem onde morar.
    """

    oid: str
    tipo: TipoAresta = TipoAresta.PEER_PTP
    colunas: tuple[ColunaEnlace, ...]

    def model_post_init(self, _ctx) -> None:
        identidades = [c for c in self.colunas if c.papel == "identidade"]
        if len(identidades) != 1:
            raise ValueError(
                f"tabela de enlace {self.oid}: precisa de exatamente uma coluna "
                f"de identidade, achei {len(identidades)}"
            )

    @property
    def coluna_identidade(self) -> int:
        return next(c.numero for c in self.colunas if c.papel == "identidade")


class Perfil(BaseModel):
    nome: str
    descricao: str
    #: Papéis do inventário que este perfil atende. Vazio = qualquer um.
    papeis: tuple[str, ...] = ()
    #: OID completo (com o `.0`) para valor canônico.
    escalares: dict[str, str] = Field(default_factory=dict)
    fatores: dict[str, float] = Field(default_factory=dict)
    tabelas: tuple[Tabela, ...] = ()
    #: Tabelas de vizinhança. Vazio para equipamento que não tem enlace de rádio.
    enlaces: tuple[TabelaEnlace, ...] = ()

    @field_validator("escalares")
    @classmethod
    def _escalares_conhecidos(cls, v: dict[str, str]) -> dict[str, str]:
        for metrica in v.values():
            validar(metrica)
        return v

    def metricas(self) -> tuple[str, ...]:
        nomes = list(self.escalares.values())
        nomes += [c.metrica for t in self.tabelas for c in t.colunas if c.metrica]
        return tuple(dict.fromkeys(nomes))

    def medidas_de_enlace(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                c.medida for t in self.enlaces for c in t.colunas if c.medida
            )
        )


# -- MIB-II, o que todo equipamento gerenciável responde ---------------------

SYS_UPTIME = "1.3.6.1.2.1.1.3.0"

#: Centésimos de segundo. Sem este fator, 3 dias viram 300.
FATOR_UPTIME = 0.01


# -- LLDP, a vizinhança que não depende de fabricante ------------------------

#: ``lldpRemTable`` (IEEE 802.1AB). Vale para switch, roteador e boa parte dos
#: rádios ponto a ponto — é o jeito padronizado de um equipamento dizer com
#: quem ele está diretamente ligado.
#:
#: A tabela é indexada por (lldpRemTimeMark, lldpRemLocalPortNum, lldpRemIndex),
#: índice de três partes. Isso não atrapalha: o índice serve só para agrupar as
#: células de uma linha, e a identidade do vizinho vem da coluna, não da chave.
LLDP_REM = "1.0.8802.1.1.2.1.4.1.1"

VIZINHANCA_LLDP = TabelaEnlace(
    oid=LLDP_REM,
    tipo=TipoAresta.ENLACE_FISICO,
    colunas=(
        # 5 = lldpRemChassisId. Com subtipo 4 é o MAC do vizinho, que é o
        # identificador que o inventário sabe resolver.
        ColunaEnlace(numero=5, papel="identidade"),
        ColunaEnlace(numero=9, papel="nome"),  # lldpRemSysName
    ),
)

BASICO = Perfil(
    nome="basico_mib2",
    descricao="Só o que qualquer agente MIB-II responde: há quanto tempo está de pé.",
    escalares={SYS_UPTIME: "ativo_uptime_s"},
    fatores={SYS_UPTIME: FATOR_UPTIME},
)

REDE = Perfil(
    nome="rede_mib2",
    descricao="Contadores e estado por porta, para switch e roteador.",
    papeis=("switch", "roteador"),
    escalares={SYS_UPTIME: "ativo_uptime_s"},
    fatores={SYS_UPTIME: FATOR_UPTIME},
    tabelas=(
        # ifXTable: nome legível da porta e os contadores de 64 bits.
        Tabela(
            oid="1.3.6.1.2.1.31.1.1.1",
            colunas=(
                Coluna(numero=1, rotulo="porta"),          # ifName
                Coluna(numero=6, metrica="iface_bytes_rx"),   # ifHCInOctets
                Coluna(numero=10, metrica="iface_bytes_tx"),  # ifHCOutOctets
                Coluna(numero=15, metrica="iface_velocidade_bps",
                       fator=1_000_000),                      # ifHighSpeed, em Mb/s
            ),
        ),
        # ifTable: estado e erros, que não têm equivalente de 64 bits.
        Tabela(
            oid="1.3.6.1.2.1.2.2.1",
            colunas=(
                Coluna(numero=7, metrica="iface_status_admin"),
                Coluna(numero=8, metrica="iface_status_oper"),
                Coluna(numero=13, metrica="iface_descartes_rx"),
                Coluna(numero=14, metrica="iface_erros_rx"),
                Coluna(numero=19, metrica="iface_descartes_tx"),
                Coluna(numero=20, metrica="iface_erros_tx"),
            ),
        ),
    ),
    # Switch também fala LLDP, e daí sai a topologia cabeada — sem isso o grafo
    # tem a malha de rádio e um buraco onde está a rede fixa.
    enlaces=(VIZINHANCA_LLDP,),
)


#: Rádio ponto a ponto e ponto-multiponto.
#:
#: O que este perfil **não** traz, e por quê: SNR, potência recebida e taxa de
#: modulação do enlace são justamente o que interessa num rádio PtP, e vivem em
#: MIB de fabricante — na Astra/InfiNet, na árvore MINT. Esses OIDs não estão
#: aqui porque não foram lidos de um rádio real, e OID chutado produz um módulo
#: que parece pronto e não coleta nada. O caminho para preencher está em
#: ``ferramentas/perfil_do_walk.py``: um ``snmpwalk`` contra um rádio, e as
#: colunas viram declaração.
#:
#: O que já vale hoje: há quanto tempo está de pé, o estado das interfaces, e
#: **com quem ele está falando** — que é o que faz o enlace aparecer no grafo,
#: no mapa e na aba Rede, com ou sem a medida de rádio.
PONTO_A_PONTO = Perfil(
    nome="radio_ptp",
    descricao="Rádio ponto a ponto: interfaces e vizinhança padronizada (LLDP).",
    papeis=("radio_ptp", "radio_ptmp"),
    escalares={SYS_UPTIME: "ativo_uptime_s"},
    fatores={SYS_UPTIME: FATOR_UPTIME},
    tabelas=(
        Tabela(
            oid="1.3.6.1.2.1.2.2.1",
            colunas=(
                Coluna(numero=7, metrica="iface_status_admin"),
                Coluna(numero=8, metrica="iface_status_oper"),
                Coluna(numero=13, metrica="iface_descartes_rx"),
                Coluna(numero=14, metrica="iface_erros_rx"),
            ),
        ),
    ),
    enlaces=(VIZINHANCA_LLDP,),
)


PERFIS: tuple[Perfil, ...] = (PONTO_A_PONTO, REDE, BASICO)


def perfil_para(papel: str) -> Perfil:
    """O perfil mais específico que atende este papel; senão, o básico."""
    for p in PERFIS:
        if papel in p.papeis:
            return p
    return BASICO


__all__ = [
    "BASICO",
    "LLDP_REM",
    "PERFIS",
    "REDE",
    "PONTO_A_PONTO",
    "VIZINHANCA_LLDP",
    "Coluna",
    "ColunaEnlace",
    "Perfil",
    "Tabela",
    "TabelaEnlace",
    "perfil_para",
]
