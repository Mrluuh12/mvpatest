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


class Perfil(BaseModel):
    nome: str
    descricao: str
    #: Papéis do inventário que este perfil atende. Vazio = qualquer um.
    papeis: tuple[str, ...] = ()
    #: OID completo (com o `.0`) para valor canônico.
    escalares: dict[str, str] = Field(default_factory=dict)
    fatores: dict[str, float] = Field(default_factory=dict)
    tabelas: tuple[Tabela, ...] = ()

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


# -- MIB-II, o que todo equipamento gerenciável responde ---------------------

SYS_UPTIME = "1.3.6.1.2.1.1.3.0"

#: Centésimos de segundo. Sem este fator, 3 dias viram 300.
FATOR_UPTIME = 0.01

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
)

PERFIS: tuple[Perfil, ...] = (REDE, BASICO)


def perfil_para(papel: str) -> Perfil:
    """O perfil mais específico que atende este papel; senão, o básico."""
    for p in PERFIS:
        if papel in p.papeis:
            return p
    return BASICO


__all__ = ["BASICO", "PERFIS", "REDE", "Coluna", "Perfil", "Tabela", "perfil_para"]
