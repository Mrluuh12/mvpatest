#!/usr/bin/env python3
"""
GeoTIFF sintetico para os testes.

Escrito a mao, sem Pillow: o teste precisa rodar em qualquer maquina, e um
arquivo montado byte a byte aqui e' um controle melhor do leitor do que um
gerado pela mesma biblioteca que o le'.
"""

import struct
import zlib


def _ifd(entradas, e="<"):
    """entradas: lista de (etiqueta, tipo, valores). Devolve (ifd, extra, base)."""
    return entradas


def escrever(caminho, largura, altura, pixels, escala, canto, epsg=31983,
             compressao=1, por_faixa=None, amostras=3):
    """8 bits por amostra. Com amostras=4 a quarta banda e' alfa, como nos
       ortomosaicos "transparent" que o Pix4D e o ODM entregam.

       `canto` e' (X, Y) do canto superior esquerdo no sistema do EPSG;
       `escala` e' (m/px em X, m/px em Y)."""
    e = "<"
    linhas_faixa = por_faixa or altura
    faixas = []
    for y0 in range(0, altura, linhas_faixa):
        alt = min(linhas_faixa, altura - y0)
        bruto = pixels[y0 * largura * amostras:(y0 + alt) * largura * amostras]
        faixas.append(zlib.compress(bruto, 6) if compressao == 8 else bruto)

    corpo = bytearray()
    extra = bytearray()
    OFF_BASE = 8

    def guarda(dados):
        pos = OFF_BASE + len(extra)
        extra.extend(dados)
        if len(extra) % 2:
            extra.append(0)
        return pos

    # os dados das faixas vao depois do IFD; o deslocamento e' corrigido no fim
    marcador_offsets = []
    off_faixas = []

    geokeys = [1, 1, 0, 1, 3072, 0, 1, epsg]
    campos = [
        (256, 3, [largura]),
        (257, 3, [altura]),
        (258, 3, [8] * amostras),
        (259, 3, [compressao]),
        (262, 3, [2]),
        (273, 4, None),                       # offsets das faixas
        (277, 3, [amostras]),
        (278, 3, [linhas_faixa]),
        (279, 4, [len(f) for f in faixas]),
        (284, 3, [1]),
    ] + ([(338, 3, [2])] if amostras == 4 else []) + [   # 2 = alfa comum
        (33550, 12, [escala[0], escala[1], 0.0]),
        (33922, 12, [0.0, 0.0, 0.0, canto[0], canto[1], 0.0]),
        (34735, 3, geokeys),
    ]

    tam = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 11: 4, 12: 8}
    fmt = {1: "B", 3: "H", 4: "I", 12: "d"}

    # primeiro reserva o espaco do IFD para saber onde comecam os dados
    n = len(campos)
    tam_ifd = 2 + n * 12 + 4
    pos_dados_extra = 8 + tam_ifd

    valores_serializados = []
    extra = bytearray()
    OFF_BASE = pos_dados_extra
    for etiqueta, tipo, vals in campos:
        if vals is None:
            valores_serializados.append(None)
            continue
        b = b"".join(struct.pack(e + fmt[tipo], v) for v in vals)
        if len(b) <= 4:
            valores_serializados.append(("dentro", b.ljust(4, b"\0"), len(vals)))
        else:
            valores_serializados.append(("fora", guarda(b), len(vals)))

    inicio_faixas = OFF_BASE + len(extra)
    p = inicio_faixas
    for f in faixas:
        off_faixas.append(p)
        p += len(f)

    b_off = b"".join(struct.pack(e + "I", v) for v in off_faixas)
    if len(b_off) <= 4:
        val_off = ("dentro", b_off.ljust(4, b"\0"), len(off_faixas))
    else:
        val_off = ("fora", guarda(b_off), len(off_faixas))
        # guardar depois deslocou o inicio das faixas
        inicio_faixas = OFF_BASE + len(extra)
        off_faixas, p = [], inicio_faixas
        for f in faixas:
            off_faixas.append(p); p += len(f)
        b_off = b"".join(struct.pack(e + "I", v) for v in off_faixas)
        extra[val_off[1] - OFF_BASE:val_off[1] - OFF_BASE + len(b_off)] = b_off

    for i, (etiqueta, tipo, vals) in enumerate(campos):
        if vals is None:
            valores_serializados[i] = val_off

    ifd = bytearray(struct.pack(e + "H", n))
    for (etiqueta, tipo, _v), ser in zip(campos, valores_serializados):
        onde, dado, conta = ser
        ifd += struct.pack(e + "HHI", etiqueta, tipo, conta)
        ifd += dado if onde == "dentro" else struct.pack(e + "I", dado)
    ifd += struct.pack(e + "I", 0)

    with open(caminho, "wb") as f:
        f.write(b"II" + struct.pack(e + "HI", 42, 8))
        f.write(bytes(ifd))
        f.write(bytes(extra))
        for fa in faixas:
            f.write(fa)
    return caminho


def imagem_de_teste(largura, altura, marcas=()):
    """Fundo em gradiente com marcas de cor solida em pixels conhecidos."""
    px = bytearray(largura * altura * 3)
    for y in range(altura):
        for x in range(largura):
            k = (y * largura + x) * 3
            px[k] = (x * 255) // max(1, largura - 1)
            px[k + 1] = (y * 255) // max(1, altura - 1)
            px[k + 2] = 60
    for (mx, my, cor, raio) in marcas:
        for y in range(max(0, my - raio), min(altura, my + raio + 1)):
            for x in range(max(0, mx - raio), min(largura, mx + raio + 1)):
                k = (y * largura + x) * 3
                px[k:k + 3] = bytes(cor)
    return bytes(px)
