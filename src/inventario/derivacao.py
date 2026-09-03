"""Derivação de ativo, papel e função de negócio a partir de nome e endereço.

O cadastro da mina já carrega o modelo de ativos — só que implícito, na
convenção que a equipe mantém há anos:

* o nome segue ``<FROTA>-<NÚMERO>-<PAPEL>`` (``CA-1001-RADIO RJT``);
* o **terceiro octeto** do IP diz o papel (``.99`` é rádio, ``.103`` é CLP);
* o **quarto octeto** diz o veículo — ``CA-1001`` é ``.1`` em todas as
  sub-redes de papel.

Derivar isso é código, não levantamento de campo. O que sobra para uma pessoa
é a exceção, e a exceção é relatada, nunca adivinhada.

Quando nome e endereço discordam, o resultado **não** é uma escolha silenciosa:
é uma divergência registrada. Discordância costuma ser erro de cadastro, e
achá-la é metade do valor de rodar isto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dialetos import limpar, papel_do_dialeto
from .modelo import Papel

#: ``<FROTA>-<NÚMERO>-<PAPEL>``. Cobre 78% dos 723 ativos reais; o resto é
#: relatado como fora de padrão.
#:
#: O número aceita de 1 a 4 dígitos de propósito. O cadastro atual só usa de 2
#: a 4, mas não existe regra que proíba ``CA-1`` — e um padrão restritivo
#: demais transforma um nome legítimo em exceção para tratamento manual.
PADRAO_NOME = re.compile(r"^([A-Z]{2,4})[-\s](\d{1,4})(?:[-\s](.*))?$")

#: Terceiro octeto -> papel, **apenas** onde o cadastro real mostra domínio
#: superior a 90%. Sub-redes ambíguas (``.101`` e ``.102`` misturam MEMS, GPS e
#: PTX-HUB; ``.96`` e ``.97`` são infra) ficam de fora de propósito: um mapa
#: que erra 30% das vezes é pior que mapa nenhum.
MAPA_SUBREDE: dict[int, Papel] = {
    98: Papel.IHM_BORDO,  # 96% PTX
    99: Papel.RADIO_MESH,  # 95% RADIO RJT
    100: Papel.RADIO_MESH,  # 100%
    103: Papel.PLC,  # 100% CLP/PLC
    107: Papel.ENDPOINT_IMX,  # 98% IMX
    108: Papel.ENDPOINT_IMX,  # 95% IMX
    110: Papel.CONVERSOR_CAN,  # 100%
}

#: Frota -> função de negócio provisória. Reduz o levantamento humano de 723
#: ativos para as exceções. Marcada sempre como ``DERIVADO``: o cadastro na
#: área ADM sobrepõe sem conflito.
FUNCAO_POR_FROTA: dict[str, str] = {
    "CA": "transporte_de_minerio",
    "EH": "carregamento",
    "PA": "carregamento",
    "PF": "perfuracao",
    "TT": "apoio_mina",
    "CP": "apoio_mina",
    "ERB": "rede_infraestrutura",
    "ERM": "rede_infraestrutura",
    "GST": "rede_infraestrutura",
}

FUNCAO_DESCONHECIDA = "desconhecido"


@dataclass(slots=True)
class Derivacao:
    """O que se conseguiu deduzir de um registro, e o que não se conseguiu."""

    nome_bruto: str
    nome_canonico: str
    frota: str | None = None
    numero: str | None = None
    ativo_id: str | None = None
    papel: Papel = Papel.DESCONHECIDO
    funcao_negocio: str = FUNCAO_DESCONHECIDA
    fonte_papel: str = "nenhuma"
    aderente_ao_padrao: bool = False
    divergencias: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _terceiro_octeto(ip: str | None) -> int | None:
    if not ip:
        return None
    partes = str(ip).strip().split(".")
    if len(partes) != 4:
        return None
    try:
        return int(partes[2])
    except ValueError:
        return None


def derivar(nome: str, ip: str | None = None) -> Derivacao:
    """Deriva ativo, papel e função a partir do nome e, se houver, do IP."""
    bruto = str(nome or "").strip()
    canonico = limpar(bruto)
    d = Derivacao(nome_bruto=bruto, nome_canonico=canonico)

    if not canonico:
        d.avisos.append("registro sem nome")
        return d

    casamento = PADRAO_NOME.match(canonico)
    if casamento:
        d.aderente_ao_padrao = True
        d.frota, d.numero, sufixo = casamento.groups()
        d.ativo_id = f"{d.frota}-{d.numero}"
        d.funcao_negocio = FUNCAO_POR_FROTA.get(d.frota, FUNCAO_DESCONHECIDA)
        if d.funcao_negocio == FUNCAO_DESCONHECIDA:
            d.avisos.append(f"frota {d.frota} sem função de negócio mapeada")
        papel_nome, reconhecido = papel_do_dialeto(sufixo or "")
    else:
        d.avisos.append("nome fora do padrão <FROTA>-<NÚMERO>-<PAPEL>")
        papel_nome, reconhecido = papel_do_dialeto(canonico)

    papel_rede = MAPA_SUBREDE.get(_terceiro_octeto(ip) or -1)

    # O nome é a fonte primária; a sub-rede corrobora ou cobre a lacuna.
    if reconhecido and papel_rede is not None and papel_rede != papel_nome:
        d.divergencias.append(
            f"nome indica {papel_nome.value}, sub-rede indica {papel_rede.value}"
        )
    if reconhecido:
        d.papel, d.fonte_papel = papel_nome, "nome"
    elif papel_rede is not None:
        d.papel, d.fonte_papel = papel_rede, "sub-rede"
        d.avisos.append("papel deduzido do endereço porque o nome não foi reconhecido")
    else:
        d.avisos.append(f"papel não reconhecido: {canonico!r}")

    return d
