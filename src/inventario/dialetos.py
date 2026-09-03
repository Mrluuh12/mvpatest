"""Normalização de dialetos do cadastro.

A armadilha dos dialetos já aconteceu um nível antes das métricas: aconteceu
nos nomes dos próprios ativos. No cadastro real convivem ``CLP`` e ``PLC`` para
a mesma coisa, e ``RADIO RJT`` / ``RADIO`` / ``RADIO-RAJANT`` para o mesmo
rádio. Se cada um virar uma série própria, em dois anos são painéis que não se
falam.

Este módulo é a porta: entra dialeto, sai vocabulário canônico. O que não for
reconhecido vira ``DESCONHECIDO`` **e é relatado** — nunca descartado em
silêncio.
"""

from __future__ import annotations

import re

from .modelo import Papel

#: Dialeto observado no cadastro -> papel canônico.
#: A contagem ao lado de cada entrada é quantas vezes ela aparece nos 723
#: ativos reais, para que a tabela possa ser conferida contra o inventário.
_DIALETOS: dict[str, Papel] = {
    # rádio da malha (Rajant) — 100 + 32 + 10 + 2
    "RADIO RJT": Papel.RADIO_MESH,
    "RADIO": Papel.RADIO_MESH,
    "RADIO-RAJANT": Papel.RADIO_MESH,
    "RADIO RAJANT": Papel.RADIO_MESH,
    "RAJANT": Papel.RADIO_MESH,
    # computador de bordo / IHM — 97
    "PTX": Papel.IHM_BORDO,
    "PTX RJT": Papel.IHM_BORDO,
    # concentrador embarcado — 33
    "PTX-HUB": Papel.HUB_PTX,
    # endpoint embarcado — 83
    "IMX": Papel.ENDPOINT_IMX,
    "IMX2": Papel.ENDPOINT_IMX,
    # posicionamento — 72 + 28 + 6
    "GPS-MM": Papel.GPS,
    "GPS-MM2": Papel.GPS,
    "GPS MM": Papel.GPS,
    "GPS-MM2-SLAVE": Papel.GPS,
    "MM2-SLAVE": Papel.GPS,
    "GPS": Papel.GPS,
    # telemetria de pneu (Michelin MEMS) — 46
    "MEMS": Papel.GATEWAY_PNEU,
    # controle de peso — 19
    "WEIGHT_CONTROL": Papel.SENSOR_PESO,
    "WEIGHT CONTROL": Papel.SENSOR_PESO,
    # controlador — 15 + 11, o dialeto mais caro do cadastro
    "CLP": Papel.PLC,
    "PLC": Papel.PLC,
    # conversor CAN — 13
    "ETH-CAN-CONVERTER": Papel.CONVERSOR_CAN,
    # roteamento — 15 + 7
    "MIKROTIK": Papel.ROTEADOR,
    "RCT-DVS-ROUTER": Papel.ROTEADOR,
    "OPS-ROUTER": Papel.ROTEADOR,
    # enlaces — 12 + 4 + 2
    "RADWIN": Papel.RADIO_PTMP,
    "INFINET": Papel.RADIO_PTP,
    "ASTRA": Papel.RADIO_PTP,
    "BASE ASTRA": Papel.RADIO_PTP,
    # câmeras — 10 + 8
    "CAMERA": Papel.CAMERA,
    "CS-DE-FISHEYE-CAMERA": Papel.CAMERA,
    "CS-NDE-FISHEYE-CAMERA": Papel.CAMERA,
    "FISHEYE-CAMERA": Papel.CAMERA,
    # energia — 5
    "UPS": Papel.UPS,
    # comutação — 6
    "SW": Papel.SWITCH,
    "SWITCH": Papel.SWITCH,
    "SW IE4000": Papel.SWITCH,
    "SWITCH IE4000": Papel.SWITCH,
    # diversos
    "PERIFERICO": Papel.PERIFERICO,
    "PERIFERICOS": Papel.PERIFERICO,
}

#: Prefixos que bastam para reconhecer o papel quando o sufixo varia
#: (``SW IE4000``, ``SW Mikrotik L1``, ``CAMERA-01``…).
_PREFIXOS: tuple[tuple[str, Papel], ...] = (
    ("SW ", Papel.SWITCH),
    ("SWITCH", Papel.SWITCH),
    ("CAMERA", Papel.CAMERA),
    ("RADIO", Papel.RADIO_MESH),
    ("GPS", Papel.GPS),
    ("PTX-HUB", Papel.HUB_PTX),
    ("PTX", Papel.IHM_BORDO),
    ("IMX", Papel.ENDPOINT_IMX),
    ("UPS", Papel.UPS),
)

_LIXO = re.compile(r"[\s_]+")


def limpar(bruto: str) -> str:
    """Colapsa espaços, unifica separadores e normaliza caixa."""
    return _LIXO.sub(" ", str(bruto).strip().upper()).strip()


def papel_do_dialeto(bruto: str) -> tuple[Papel, bool]:
    """Traduz um fragmento de nome para papel canônico.

    Devolve ``(papel, reconhecido)``. Quando ``reconhecido`` é ``False`` o papel
    vem ``DESCONHECIDO`` e o chamador deve relatar — a recusa precisa aparecer,
    não sumir.
    """
    texto = limpar(bruto)
    if not texto:
        return Papel.DESCONHECIDO, False

    if (papel := _DIALETOS.get(texto)) is not None:
        return papel, True

    # sufixo numérico solto: CAMERA-01, IMX2, GPS-MM2
    sem_sufixo = re.sub(r"[-\s]?\d+$", "", texto)
    if sem_sufixo != texto and (papel := _DIALETOS.get(sem_sufixo)) is not None:
        return papel, True

    for prefixo, papel in _PREFIXOS:
        if texto.startswith(prefixo):
            return papel, True

    return Papel.DESCONHECIDO, False
