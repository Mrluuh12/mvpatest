#!/usr/bin/env python3
"""
Foto aerea da mina como fundo do mapa.

O ortofoto vem em GeoTIFF, no sistema de coordenadas do levantamento. O mapa
da navegacao trabalha na grid do DISPATCH. Este modulo faz a ponte uma vez, no
servidor: le o TIFF, reprojeta para a grid e corta em ladrilhos prontos. O PTX
e o tablet so' desenham o que recebem — eles nao tem folego para reprojetar
nada, e a foto nao muda de uma semana para a outra.

Sem dependencia externa de proposito: o servidor roda como .exe na mina, onde
nao ha Python nem pip para instalar biblioteca de imagem. O leitor de TIFF e o
codificador JPEG estao aqui dentro. Se a Pillow existir na maquina (numa de
desenvolvimento, por exemplo), ela e' usada — e' mais rapida e le formatos que
este leitor nao alcanca, como TIFF com JPEG dentro.
"""

import math
import os
import struct
import sys
import threading
import time
import zlib
from itertools import accumulate

try:
    from PIL import Image          # opcional, nunca obrigatoria
except Exception:
    Image = None

try:
    import foto_rapida             # GDAL/numpy, quando existirem na maquina
except Exception:
    foto_rapida = None


# ---------------------------------------------------------------- TIFF

# Só as etiquetas que mudam a leitura. O resto do arquivo é ignorado de
# propósito: ortofoto traz dezenas de etiquetas de metadado que não afetam
# nem o pixel nem a posição dele no mundo.
ETIQUETAS = {
    256: "largura", 257: "altura", 258: "bits", 259: "compressao",
    262: "interpretacao", 273: "strip_offsets", 277: "amostras",
    278: "linhas_por_strip", 279: "strip_bytes", 284: "planar",
    317: "preditor", 320: "paleta", 322: "tile_largura", 323: "tile_altura",
    324: "tile_offsets", 325: "tile_bytes", 338: "extra_amostras",
    33550: "escala", 33922: "tiepoint", 34264: "transformacao",
    34735: "geokeys", 34736: "geodoubles", 34737: "geoascii",
}

# tipo TIFF -> (formato struct, bytes)
TIPOS = {1: ("B", 1), 2: ("c", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8),
         6: ("b", 1), 7: ("B", 1), 8: ("h", 2), 9: ("i", 4), 10: ("ii", 8),
         11: ("f", 4), 12: ("d", 8),
         16: ("Q", 8), 17: ("q", 8), 18: ("Q", 8)}

SEM_COMPRESSAO, LZW, JPEG_ANTIGO, JPEG, DEFLATE_ADOBE = 1, 5, 6, 7, 8
PACKBITS, DEFLATE = 32773, 32946

NOME_COMPRESSAO = {
    SEM_COMPRESSAO: "sem compressao", LZW: "LZW", JPEG_ANTIGO: "JPEG antigo",
    JPEG: "JPEG", DEFLATE_ADOBE: "Deflate", PACKBITS: "PackBits",
    DEFLATE: "Deflate",
}


class ErroTiff(Exception):
    pass


def _packbits(dados, esperado):
    saida = bytearray()
    i, n = 0, len(dados)
    while i < n and len(saida) < esperado:
        c = dados[i]; i += 1
        if c < 128:
            saida += dados[i:i + c + 1]; i += c + 1
        elif c > 128:
            if i >= n: break
            saida += bytes([dados[i]]) * (257 - c); i += 1
    return bytes(saida)


def _lzw(dados, esperado):
    """LZW do TIFF: codigos MSB primeiro, largura cresce um codigo antes
       (o 'early change' que o formato exige e quase todo mundo esquece)."""
    saida = bytearray()
    dic = {i: bytes([i]) for i in range(256)}
    proximo, largura = 258, 9
    anterior = None
    acumulado = bitsdisponiveis = 0
    for byte in dados:
        acumulado = (acumulado << 8) | byte
        bitsdisponiveis += 8
        while bitsdisponiveis >= largura:
            codigo = (acumulado >> (bitsdisponiveis - largura)) & ((1 << largura) - 1)
            bitsdisponiveis -= largura
            if codigo == 256:                       # limpa
                dic = {i: bytes([i]) for i in range(256)}
                proximo, largura, anterior = 258, 9, None
                continue
            if codigo == 257:                       # fim
                return bytes(saida)
            if anterior is None:
                entrada = dic[codigo]
            elif codigo in dic:
                entrada = dic[codigo]
                dic[proximo] = dic[anterior] + entrada[:1]; proximo += 1
            else:
                entrada = dic[anterior] + dic[anterior][:1]
                dic[proximo] = entrada; proximo += 1
            saida += entrada
            anterior = codigo
            if proximo + 1 >= (1 << largura) and largura < 12:
                largura += 1
            if len(saida) >= esperado:
                return bytes(saida)
    return bytes(saida)


def _desfaz_preditor(buf, largura, linhas, amostras, bits):
    """Preditor 2: cada pixel guarda a diferenca para o vizinho da esquerda.

    Desfazer isso e' uma soma acumulada ao longo da linha, por canal. Feita
    byte a byte em Python custava um terco de toda a conversao; com
    accumulate, a soma acontece em C. O modulo pode ficar para o fim porque
    somar e tirar resto comutam.
    """
    if bits != 8:
        raise ErroTiff(f"preditor horizontal com {bits} bits nao suportado")
    passo = largura * amostras
    b = bytearray(buf)
    for linha in range(linhas):
        base = linha * passo
        if base + passo > len(b):
            break
        fatia = b[base:base + passo]
        for canal in range(amostras):
            coluna = accumulate(fatia[canal::amostras])
            fatia[canal::amostras] = bytes([v & 0xFF for v in coluna])
        b[base:base + passo] = fatia
    return bytes(b)


class Tiff:
    """Leitor de TIFF/BigTIFF por blocos: nunca carrega a imagem inteira.

    Ortofoto de mina tem gigabytes. Abrir tudo na memoria derrubaria o
    servidor; e para montar um ladrilho de 256 px so' interessam os poucos
    blocos que caem embaixo dele.
    """

    # Ortofoto costuma vir em faixas de UMA linha. Guardando so' um punhado
    # delas, cada linha era decodificada de novo a cada coluna de ladrilhos —
    # dezessete vezes a mesma conta. O limite passa a ser de memoria, nao de
    # quantidade: cabe a mina inteira de faixas decodificadas.
    MAX_BYTES_GUARDADOS = 96 * 1024 * 1024

    def __init__(self, caminho):
        self.caminho = caminho
        self.arq = open(caminho, "rb")
        self.tags = {}
        self._cache = {}
        self._ordem_cache = []
        self._bytes_cache = 0
        try:
            self._ler_cabecalho()
            self._ler_ifd(self.primeiro_ifd)
            self._interpretar()
        except Exception:
            # numa pasta com dezenove folhas, uma que nao abre nao pode deixar
            # o arquivo pendurado atras dela
            self.fechar()
            raise

    # -- cabecalho e etiquetas ------------------------------------------
    def _ler_cabecalho(self):
        cab = self.arq.read(16)
        if len(cab) < 8:
            raise ErroTiff("arquivo pequeno demais para ser um TIFF")
        if cab[:2] == b"II":
            self.e = "<"
        elif cab[:2] == b"MM":
            self.e = ">"
        else:
            raise ErroTiff("nao e' um TIFF (nem II nem MM no comeco)")
        versao = struct.unpack(self.e + "H", cab[2:4])[0]
        if versao == 42:
            self.grande = False
            self.primeiro_ifd = struct.unpack(self.e + "I", cab[4:8])[0]
        elif versao == 43:                      # BigTIFF: ortofoto acima de 4 GB
            self.grande = True
            tam_off = struct.unpack(self.e + "H", cab[4:6])[0]
            if tam_off != 8:
                raise ErroTiff("BigTIFF com deslocamento diferente de 8 bytes")
            self.primeiro_ifd = struct.unpack(self.e + "Q", cab[8:16])[0]
        else:
            raise ErroTiff(f"versao de TIFF desconhecida: {versao}")

    def _ler_ifd(self, pos):
        self.arq.seek(pos)
        if self.grande:
            n = struct.unpack(self.e + "Q", self.arq.read(8))[0]
            tam_entrada, fmt_cont, fmt_val, cabe = 20, "Q", "Q", 8
        else:
            n = struct.unpack(self.e + "H", self.arq.read(2))[0]
            tam_entrada, fmt_cont, fmt_val, cabe = 12, "I", "I", 4
        bruto = self.arq.read(n * tam_entrada)
        for i in range(n):
            ent = bruto[i * tam_entrada:(i + 1) * tam_entrada]
            etiqueta, tipo = struct.unpack(self.e + "HH", ent[:4])
            conta = struct.unpack(self.e + fmt_cont, ent[4:4 + cabe])[0]
            if tipo not in TIPOS:
                continue
            fmt, tam = TIPOS[tipo]
            total = tam * conta
            campo = ent[4 + cabe:]
            if total <= cabe:
                dados = campo[:total]
            else:
                off = struct.unpack(self.e + fmt_val, campo[:cabe])[0]
                guardado = self.arq.tell()
                self.arq.seek(off)
                dados = self.arq.read(total)
                self.arq.seek(guardado)
            self.tags[etiqueta] = self._valores(tipo, conta, dados)

    def _valores(self, tipo, conta, dados):
        fmt, tam = TIPOS[tipo]
        if tipo == 2:
            return dados.split(b"\0")[0].decode("latin-1")
        if tipo in (5, 10):                     # racional: par de inteiros
            vals = struct.unpack(self.e + fmt * conta, dados[:8 * conta])
            return [vals[i] / vals[i + 1] if vals[i + 1] else 0.0
                    for i in range(0, len(vals), 2)]
        vals = list(struct.unpack(self.e + fmt * conta, dados[:tam * conta]))
        return vals

    def t(self, etiqueta, padrao=None):
        v = self.tags.get(etiqueta, padrao)
        if isinstance(v, list) and len(v) == 1:
            return v[0]
        return v

    # -- geometria dos blocos -------------------------------------------
    def _interpretar(self):
        self.largura = int(self.t(256, 0))
        self.altura = int(self.t(257, 0))
        if not self.largura or not self.altura:
            raise ErroTiff("TIFF sem largura/altura")
        self.amostras = int(self.t(277, 1))
        bits = self.tags.get(258, [8])
        self.bits = int(bits[0] if isinstance(bits, list) else bits)
        self.compressao = int(self.t(259, 1))
        self.interpretacao = int(self.t(262, 1))
        self.planar = int(self.t(284, 1))
        self.preditor = int(self.t(317, 1))
        self.paleta = self.tags.get(320)

        # ExtraSamples: 1 = alfa pre-multiplicado, 2 = alfa comum. Sem a
        # etiqueta, uma quarta banda num RGB e' alfa na pratica.
        extras = self.tags.get(338)
        extras = extras if isinstance(extras, list) else ([extras] if extras else [])
        self.canal_alfa = -1
        if self.amostras >= 4:
            self.canal_alfa = self.amostras - len(extras) if extras else 3
            if not (0 <= self.canal_alfa < self.amostras):
                self.canal_alfa = 3
        elif self.amostras == 2:            # cinza + alfa
            self.canal_alfa = 1
        self.tem_alfa = self.canal_alfa >= 0

        if 322 in self.tags:                     # organizado em ladrilhos
            self.bl_largura = int(self.t(322))
            self.bl_altura = int(self.t(323))
            self.offsets = self._lista(324)
            self.bytes_bloco = self._lista(325)
            self.por_ladrilho = True
        else:                                    # organizado em faixas
            self.bl_largura = self.largura
            self.bl_altura = int(self.t(278, self.altura) or self.altura)
            self.offsets = self._lista(273)
            self.bytes_bloco = self._lista(279)
            self.por_ladrilho = False
        self.cols = max(1, (self.largura + self.bl_largura - 1) // self.bl_largura)
        self.linhas_bloco = max(1, (self.altura + self.bl_altura - 1) // self.bl_altura)

    def _lista(self, etiqueta):
        v = self.tags.get(etiqueta, [])
        return v if isinstance(v, list) else [v]

    def legivel_aqui(self):
        """Diz se este leitor da' conta do arquivo, e por que nao, quando nao da'."""
        if self.compressao in (JPEG, JPEG_ANTIGO) and Image is None:
            return False, ("TIFF com JPEG dentro; este leitor nao decodifica JPEG. "
                           "Converta antes com  gdal_translate -co COMPRESS=DEFLATE")
        if self.compressao not in (SEM_COMPRESSAO, LZW, DEFLATE, DEFLATE_ADOBE,
                                   PACKBITS, JPEG, JPEG_ANTIGO):
            return False, f"compressao {self.compressao} nao suportada"
        if self.planar != 1:
            return False, "amostras em planos separados (planar=2) nao suportado"
        if self.bits not in (8, 16):
            return False, f"{self.bits} bits por amostra nao suportado"
        return True, ""

    # -- pixels ----------------------------------------------------------
    def _bloco(self, ic, il):
        chave = (ic, il)
        if chave in self._cache:
            return self._cache[chave]
        indice = il * self.cols + ic if self.por_ladrilho else il
        if indice >= len(self.offsets):
            return None
        self.arq.seek(self.offsets[indice])
        bruto = self.arq.read(self.bytes_bloco[indice])

        alt = self.bl_altura
        if not self.por_ladrilho:
            alt = min(self.bl_altura, self.altura - il * self.bl_altura)
        esperado = self.bl_largura * alt * self.amostras * (self.bits // 8)

        c = self.compressao
        if c == SEM_COMPRESSAO:
            dados = bruto
        elif c in (DEFLATE, DEFLATE_ADOBE):
            dados = zlib.decompress(bruto)
        elif c == PACKBITS:
            dados = _packbits(bruto, esperado)
        elif c == LZW:
            dados = _lzw(bruto, esperado)
        elif c in (JPEG, JPEG_ANTIGO):
            if Image is None:
                raise ErroTiff("TIFF com JPEG dentro e sem Pillow nesta maquina")
            import io
            im = Image.open(io.BytesIO(bruto)).convert("RGB")
            dados = im.tobytes()
        else:
            raise ErroTiff(f"compressao {c} nao suportada")

        if self.preditor == 2:
            dados = _desfaz_preditor(dados, self.bl_largura, alt,
                                     self.amostras, self.bits)

        bloco = (dados, alt)
        self._cache[chave] = bloco
        self._ordem_cache.append(chave)
        self._bytes_cache += len(dados)
        while self._bytes_cache > self.MAX_BYTES_GUARDADOS and self._ordem_cache:
            velho = self._ordem_cache.pop(0)
            antigo_ = self._cache.pop(velho, None)
            if antigo_ is not None:
                self._bytes_cache -= len(antigo_[0])
        return bloco

    def amostra(self, px, py):
        """Cor RGB do pixel, ou None fora da imagem."""
        if px < 0 or py < 0 or px >= self.largura or py >= self.altura:
            return None
        ic, il = px // self.bl_largura, py // self.bl_altura
        b = self._bloco(ic, il)
        if b is None:
            return None
        dados, alt = b
        x, y = px - ic * self.bl_largura, py - il * self.bl_altura
        if y >= alt:
            return None
        passo = self.bits // 8
        i = ((y * self.bl_largura) + x) * self.amostras * passo
        if i + self.amostras * passo > len(dados):
            return None

        # Caminho rapido para o caso de sempre: 8 bits, RGB ou RGBA. Sao
        # centenas de milhoes de chamadas numa conversao; montar uma lista e
        # um range a cada pixel custava mais que todo o resto junto.
        if passo == 1 and self.amostras >= 3 and not self.paleta:
            if self.canal_alfa >= 0 and dados[i + self.canal_alfa] < 128:
                return None
            return (dados[i], dados[i + 1], dados[i + 2])

        if passo == 2:                          # 16 bits: cabe em 8 na tela
            vals = [dados[i + k * 2 + (1 if self.e == "<" else 0)]
                    for k in range(self.amostras)]
        else:
            vals = [dados[i] for k in range(self.amostras)] if False else \
                   [dados[i + k] for k in range(self.amostras)]

        if self.interpretacao == 3 and self.paleta:   # paleta de cores
            n = len(self.paleta) // 3
            idx = vals[0]
            return (self.paleta[idx] >> 8, self.paleta[n + idx] >> 8,
                    self.paleta[2 * n + idx] >> 8)
        if self.tem_alfa and len(vals) > self.canal_alfa:
            # ortomosaico "transparent" traz o fora-do-voo com alfa zero. Sem
            # olhar essa banda, a borda do levantamento vira retangulo preto
            # em cima do mapa.
            if vals[self.canal_alfa] < 128:
                return None
        if self.amostras >= 3:
            return (vals[0], vals[1], vals[2])
        cinza = vals[0]
        if self.interpretacao == 0:             # 0 = branco neste TIFF
            cinza = 255 - cinza
        return (cinza, cinza, cinza)

    def fechar(self):
        try:
            self.arq.close()
        except Exception:
            pass


# ------------------------------------------------- georreferenciamento

class Georref:
    """De pixel para coordenada do levantamento, e de volta."""

    def __init__(self, a, b, c, d, e, f, fonte=""):
        # X = a*px + b*py + c ;  Y = d*px + e*py + f
        self.a, self.b, self.c = a, b, c
        self.d, self.e, self.f = d, e, f
        self.fonte = fonte
        det = a * e - b * d
        if abs(det) < 1e-12:
            raise ErroTiff("georreferenciamento degenerado (escala zero?)")
        self.det = det

    def para_crs(self, px, py):
        return (self.a * px + self.b * py + self.c,
                self.d * px + self.e * py + self.f)

    def para_pixel(self, X, Y):
        x, y = X - self.c, Y - self.f
        return ((x * self.e - y * self.b) / self.det,
                (y * self.a - x * self.d) / self.det)

    @property
    def metros_por_pixel(self):
        return (math.hypot(self.a, self.d) + math.hypot(self.b, self.e)) / 2.0


def georref_de(tif, caminho):
    """Le a posicao do TIFF, ou do world file ao lado dele."""
    if 34264 in tif.tags:                       # matriz completa 4x4
        m = tif.tags[34264]
        if len(m) >= 8:
            return Georref(m[0], m[1], m[3], m[4], m[5], m[7],
                           "ModelTransformation")
    if 33550 in tif.tags and 33922 in tif.tags:
        esc = tif.tags[33550]
        tie = tif.tags[33922]
        if len(esc) >= 2 and len(tie) >= 6:
            sx, sy = float(esc[0]), float(esc[1])
            i, j, _k, X, Y, _Z = [float(v) for v in tie[:6]]
            # o eixo Y do mundo cresce para o norte; o da imagem, para baixo
            return Georref(sx, 0.0, X - i * sx, 0.0, -sy, Y + j * sy,
                           "ModelPixelScale + ModelTiepoint")
    mundo = _world_file(caminho)
    if mundo:
        a, d, b, e, c, f = mundo
        return Georref(a, b, c, d, e, f, "world file")
    return None


def _world_file(caminho):
    """.tfw/.tifw ao lado: seis numeros, um por linha."""
    base, ext = os.path.splitext(caminho)
    for cand in (base + ".tfw", base + ".tifw", base + ".TFW", caminho + ".aux"):
        if not os.path.exists(cand):
            continue
        try:
            with open(cand) as f:
                nums = [float(l.strip()) for l in f if l.strip()]
            if len(nums) >= 6:
                return nums[:6]
        except Exception:
            pass
    return None


# ------------------------------------------------------ sistema de coordenadas

WGS84_A, WGS84_E2 = 6378137.0, 0.00669437999014


def utm_de_epsg(codigo):
    """(zona, hemisferio, datum) do codigo EPSG, ou None se nao for UTM."""
    c = int(codigo)
    if 32601 <= c <= 32660:
        return c - 32600, "N", "WGS84"
    if 32701 <= c <= 32760:
        return c - 32700, "S", "WGS84"
    if 31965 <= c <= 31976:                     # SIRGAS 2000, zonas 11N a 22N
        return c - 31954, "N", "SIRGAS2000"
    if 31977 <= c <= 31985:                     # SIRGAS 2000, zonas 17S a 25S
        return c - 31960, "S", "SIRGAS2000"
    if 29168 <= c <= 29172:
        return c - 29150, "N", "SAD69"
    if 29187 <= c <= 29195:
        return c - 29170, "S", "SAD69"
    if 5531 <= c <= 5535:                       # SAD69(96), zonas 21S a 25S
        return c - 5510, "S", "SAD69"
    if 22521 <= c <= 22525:
        return c - 22500, "S", "CorregoAlegre"
    return None


def epsg_do_tiff(tif):
    """Codigo EPSG nas GeoKeys, quando ha."""
    chaves = tif.tags.get(34735)
    if not chaves or len(chaves) < 4:
        return None
    n = int(chaves[3])
    achado = {}
    for i in range(n):
        base = 4 + i * 4
        if base + 3 >= len(chaves):
            break
        chave, loc, _conta, valor = chaves[base:base + 4]
        if loc == 0:
            achado[int(chave)] = int(valor)
    return achado.get(3072) or achado.get(2048)


def projecao_utm(zona, hemisferio, dE=0.0, dN=0.0):
    """A mesma transversa de Mercator do servidor, com os parametros do UTM.

    `dE`/`dN` deslocam a origem falsa. E' assim que se acerta a diferenca de
    datum: em vez de aplicar parametros de SAD69 tirados de tabela, o desvio
    e' medido no proprio banco do DISPATCH (ver `desvio_do_datum`).
    """
    from servidor_rotas import TransversaMercator
    return TransversaMercator(
        A=WGS84_A, e2=WGS84_E2, phi0=0.0,
        lam0=math.radians(-183 + 6 * zona), k0=0.9996,
        baseE=500000.0 + dE,
        baseN=(0.0 if hemisferio == "N" else 10000000.0) + dN)


def desvio_do_datum(proj_grid, zona, hemisferio):
    """Quanto a numeracao da mina difere do UTM WGS84, medido no banco.

    O DISPATCH guarda a origem da grid duas vezes: em latitude/longitude e em
    coordenada projetada. Se a mina trabalha num datum antigo, os dois nao
    batem — e a diferenca entre eles E' o deslocamento do datum, no lugar
    exato onde a mina fica. Melhor que os parametros de tabela: sai do
    documento que manda no sistema.
    """
    lat0 = math.degrees(proj_grid.phi0)
    lon0 = math.degrees(proj_grid.lam0)
    E84, N84 = projecao_utm(zona, hemisferio).para_grid(lat0, lon0)
    return (proj_grid.baseE - E84, proj_grid.baseN - N84)


# ------------------------------------------------------------- JPEG

# Tabelas do anexo K da norma. Sao as mesmas que qualquer codificador usa;
# escrever as proprias so' trocaria bytes por risco.
Q_LUMA = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
          14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
          18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
          49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]
Q_CROMA = [17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99,
           24, 26, 56, 99, 99, 99, 99, 99, 47, 66, 99, 99, 99, 99, 99, 99] + [99] * 32

ZIGUEZAGUE = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
              12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
              35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
              58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63]

BITS_DC_LUMA = [0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
BITS_DC_CROMA = [0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
VAL_DC = list(range(12))
BITS_AC_LUMA = [0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d]
BITS_AC_CROMA = [0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77]
VAL_AC_LUMA = [
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
    0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08,
    0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0, 0x24, 0x33, 0x62, 0x72,
    0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45,
    0x46, 0x47, 0x48, 0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x73, 0x74, 0x75,
    0x76, 0x77, 0x78, 0x79, 0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3,
    0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6,
    0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9,
    0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
    0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf1, 0xf2, 0xf3, 0xf4,
    0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa]
VAL_AC_CROMA = [
    0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41,
    0x51, 0x07, 0x61, 0x71, 0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91,
    0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0, 0x15, 0x62, 0x72, 0xd1,
    0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
    0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44,
    0x45, 0x46, 0x47, 0x48, 0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58,
    0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x73, 0x74,
    0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
    0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a,
    0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4,
    0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7,
    0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
    0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf2, 0xf3, 0xf4,
    0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa]

_COS = [[math.cos((2 * x + 1) * u * math.pi / 16) * (0.353553390593 if u == 0
         else 0.5) for u in range(8)] for x in range(8)]


def _tabela_huffman(bits, valores):
    """Codigo canonico: comprimento crescente, valor crescente."""
    tab, codigo, k = {}, 0, 0
    for comprimento in range(1, 17):
        for _ in range(bits[comprimento - 1]):
            tab[valores[k]] = (codigo, comprimento)
            codigo += 1
            k += 1
        codigo <<= 1
    return tab


def _quantizacao(qualidade):
    q = max(1, min(100, int(qualidade)))
    escala = 5000 // q if q < 50 else 200 - 2 * q
    def ajusta(t):
        return [max(1, min(255, (v * escala + 50) // 100)) for v in t]
    return ajusta(Q_LUMA), ajusta(Q_CROMA)


class _Bits:
    def __init__(self):
        self.saida = bytearray()
        self.acc = self.n = 0

    def escreve(self, codigo, comprimento):
        self.acc = (self.acc << comprimento) | codigo
        self.n += comprimento
        while self.n >= 8:
            b = (self.acc >> (self.n - 8)) & 0xFF
            self.n -= 8
            self.saida.append(b)
            if b == 0xFF:
                self.saida.append(0x00)     # byte stuffing exigido pela norma

    def termina(self):
        if self.n:
            self.escreve((1 << (8 - self.n)) - 1, 8 - self.n)
        return bytes(self.saida)


def _categoria(v):
    a = abs(v)
    n = 0
    while a:
        a >>= 1
        n += 1
    return n


def _complemento(v, cat):
    """Valor negativo vira o complemento de um, na largura da categoria.

    Sem a mascara o numero entra negativo no acumulador de bits e embaralha
    tudo o que vem depois: o arquivo ainda abre, e a imagem sai lixo.
    """
    return (v - 1) & ((1 << cat) - 1) if v < 0 else v


def _dct8(bloco):
    """DCT separavel: 8 multiplicacoes por amostra em vez de 64."""
    tmp = [0.0] * 64
    for y in range(8):
        linha = bloco[y * 8:y * 8 + 8]
        for u in range(8):
            s = 0.0
            for x in range(8):
                s += linha[x] * _COS[x][u]
            tmp[y * 8 + u] = s
    saida = [0.0] * 64
    for u in range(8):
        col = [tmp[y * 8 + u] for y in range(8)]
        for v in range(8):
            s = 0.0
            for y in range(8):
                s += col[y] * _COS[y][v]
            saida[v * 8 + u] = s
    return saida


def codificar_jpeg(pixels, largura, altura, qualidade=78):
    """JPEG base, 4:4:4. `pixels` e' uma sequencia de bytes RGB."""
    q_luma, q_croma = _quantizacao(qualidade)
    hdc_l = _tabela_huffman(BITS_DC_LUMA, VAL_DC)
    hdc_c = _tabela_huffman(BITS_DC_CROMA, VAL_DC)
    hac_l = _tabela_huffman(BITS_AC_LUMA, VAL_AC_LUMA)
    hac_c = _tabela_huffman(BITS_AC_CROMA, VAL_AC_CROMA)

    # RGB -> YCbCr uma vez so'
    n = largura * altura
    Y = [0.0] * n; CB = [0.0] * n; CR = [0.0] * n
    for i in range(n):
        r = pixels[i * 3]; g = pixels[i * 3 + 1]; b = pixels[i * 3 + 2]
        Y[i] = 0.299 * r + 0.587 * g + 0.114 * b - 128.0
        CB[i] = -0.168736 * r - 0.331264 * g + 0.5 * b
        CR[i] = 0.5 * r - 0.418688 * g - 0.081312 * b

    bits = _Bits()
    anterior = [0, 0, 0]
    canais = ((Y, q_luma, hdc_l, hac_l), (CB, q_croma, hdc_c, hac_c),
              (CR, q_croma, hdc_c, hac_c))

    for by in range(0, altura, 8):
        for bx in range(0, largura, 8):
            for ci, (canal, quant, hdc, hac) in enumerate(canais):
                bloco = []
                for y in range(8):
                    sy = min(by + y, altura - 1)
                    for x in range(8):
                        sx = min(bx + x, largura - 1)
                        bloco.append(canal[sy * largura + sx])
                coef = _dct8(bloco)
                zz = []
                for k in range(64):
                    idx = ZIGUEZAGUE[k]
                    v = coef[idx] / quant[idx]
                    zz.append(int(v + 0.5) if v >= 0 else -int(0.5 - v))

                dif = zz[0] - anterior[ci]
                anterior[ci] = zz[0]
                cat = _categoria(dif)
                cod, comp = hdc[cat]
                bits.escreve(cod, comp)
                if cat:
                    bits.escreve(_complemento(dif, cat), cat)

                zeros = 0
                for k in range(1, 64):
                    v = zz[k]
                    if v == 0:
                        zeros += 1
                        continue
                    while zeros > 15:
                        cod, comp = hac[0xF0]
                        bits.escreve(cod, comp)
                        zeros -= 16
                    cat = _categoria(v)
                    cod, comp = hac[(zeros << 4) | cat]
                    bits.escreve(cod, comp)
                    bits.escreve(_complemento(v, cat), cat)
                    zeros = 0
                if zeros:
                    cod, comp = hac[0x00]
                    bits.escreve(cod, comp)

    def marca(cod, corpo=b""):
        if not corpo:
            return bytes([0xFF, cod])
        return bytes([0xFF, cod]) + struct.pack(">H", len(corpo) + 2) + corpo

    out = bytearray(marca(0xD8))
    out += marca(0xE0, b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0")
    out += marca(0xDB, bytes([0]) + bytes(q_luma[ZIGUEZAGUE[k]] for k in range(64)))
    out += marca(0xDB, bytes([1]) + bytes(q_croma[ZIGUEZAGUE[k]] for k in range(64)))
    out += marca(0xC0, bytes([8]) + struct.pack(">HH", altura, largura) +
                 bytes([3, 1, 0x11, 0, 2, 0x11, 1, 3, 0x11, 1]))
    for cls, ident, b_, v_ in ((0, 0, BITS_DC_LUMA, VAL_DC),
                               (0, 1, BITS_DC_CROMA, VAL_DC),
                               (1, 0, BITS_AC_LUMA, VAL_AC_LUMA),
                               (1, 1, BITS_AC_CROMA, VAL_AC_CROMA)):
        out += marca(0xC4, bytes([(cls << 4) | ident]) + bytes(b_) + bytes(v_))
    out += marca(0xDA, bytes([3, 1, 0x00, 2, 0x11, 3, 0x11, 0, 63, 0]))
    out += bits.termina()
    out += marca(0xD9)
    return bytes(out)


# -------------------------------------------------------------- PNG

def codificar_png(pixels, largura, altura):
    """PNG RGBA, para o ladrilho que a foto cobre so' em parte.

    O ortomosaico tem contorno recortado, e sao dezenove folhas encostando uma
    na outra. Sem alfa, cada borda dessas apareceria como uma faixa cinza
    riscando o mapa. JPEG nao tem alfa; para esses ladrilhos vale trocar o
    tamanho do arquivo pela borda limpa.
    """
    linhas = bytearray()
    passo = largura * 4
    for y in range(altura):
        linhas.append(0)                       # sem filtro: simples e rapido
        linhas += pixels[y * passo:(y + 1) * passo]

    def bloco(tipo, dados):
        return (struct.pack(">I", len(dados)) + tipo + dados +
                struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + bloco(b"IHDR", struct.pack(">IIBBBBB", largura, altura,
                                         8, 6, 0, 0, 0))
            + bloco(b"IDAT", zlib.compress(bytes(linhas), 6))
            + bloco(b"IEND", b""))


# ------------------------------------------------------------ mosaico

LADRILHO = 256


def _lattice(func, x0, y0, passo, n):
    """Amostra a transformacao numa grade esparsa.

    Converter cada pixel custaria duas projecoes completas por ponto — sao
    dezenas de milhoes numa ortofoto inteira. A transformacao e' suave nesta
    escala, entao 9x9 pontos por ladrilho e uma interpolacao bilinear entre
    eles ficam abaixo de um decimo de pixel.
    """
    return [[func(x0 + i * passo, y0 + j * passo) for i in range(n)]
            for j in range(n)]


def _interpola(grade, fx, fy, passo, n):
    gi = min(int(fx / passo), n - 2)
    gj = min(int(fy / passo), n - 2)
    tx = (fx - gi * passo) / passo
    ty = (fy - gj * passo) / passo
    a = grade[gj][gi]; b = grade[gj][gi + 1]
    c = grade[gj + 1][gi]; d = grade[gj + 1][gi + 1]
    return (a[0] + (b[0] - a[0]) * tx + (c[0] - a[0]) * ty
            + (a[0] - b[0] - c[0] + d[0]) * tx * ty,
            a[1] + (b[1] - a[1]) * tx + (c[1] - a[1]) * ty
            + (a[1] - b[1] - c[1] + d[1]) * tx * ty)


class Conversor:
    """Grid da mina -> coordenada da foto. Identidade quando ja' e' a mesma."""

    def __init__(self, proj_grid, proj_foto):
        self.proj_grid = proj_grid
        self.proj_foto = proj_foto

    def __call__(self, E, N):
        if self.proj_foto is None:              # foto ja' na grid da mina
            return (E, N)
        lat, lon = self.proj_grid.para_wgs84(E, N)
        if self.proj_foto == "wgs84":
            return (lon, lat)
        return self.proj_foto.para_grid(lat, lon)


# Teto do que se gera e do que o tablet baixa. Com o GDAL a conversao e' dez
# vezes mais rapida, entao cabe muito mais detalhe no mesmo tempo de espera.
LIMITE_LADRILHOS = 6000
LIMITE_LADRILHOS_RAPIDO = 40000


class FotoFonte:
    """Um .tif aberto, com a posicao dele ja' resolvida na grid da mina."""

    def __init__(self, caminho, proj_grid, proj_foto):
        self.caminho = caminho
        self.nome = os.path.basename(caminho)
        self.tif = Tiff(caminho)
        ok, motivo = self.tif.legivel_aqui()
        if not ok:
            self.tif.fechar()
            raise ErroTiff(motivo)
        self.geo = georref_de(self.tif, caminho)
        if self.geo is None:
            self.tif.fechar()
            raise ErroTiff("nao diz onde fica: sem GeoTIFF nem world file")
        self.para_foto = Conversor(proj_grid, proj_foto)
        de_foto = _inverso(proj_grid, proj_foto)
        cantos = [de_foto(*self.geo.para_crs(px, py))
                  for px, py in ((0, 0), (self.tif.largura, 0),
                                 (0, self.tif.altura),
                                 (self.tif.largura, self.tif.altura))]
        self.E0 = min(c[0] for c in cantos); self.E1 = max(c[0] for c in cantos)
        self.N0 = min(c[1] for c in cantos); self.N1 = max(c[1] for c in cantos)

        # A geometria continua vindo daqui; o GDAL entra so' como leitor de
        # pixel. Assim os dois caminhos pousam a foto no mesmo lugar.
        # Um handle de GDAL POR THREAD. Um dataset do rasterio nao aceita
        # leituras simultaneas; compartilhar um so' entre os trabalhadores
        # daria dado embaralhado, que e' pior que lento.
        self._local = threading.local()
        self._abertos = []
        self._trava_abrir = threading.Lock()
        self.tem_rapido = foto_rapida is not None and foto_rapida.disponivel()
        if self.tem_rapido and self.rapido is None:
            self.tem_rapido = False

    @property
    def rapido(self):
        if not getattr(self, "tem_rapido", True):
            return None
        ds = getattr(self._local, "ds", None)
        if ds is None:
            if foto_rapida is None or not foto_rapida.disponivel():
                return None
            try:
                ds = foto_rapida.abrir(self.caminho)
            except Exception:
                return None
            self._local.ds = ds
            with self._trava_abrir:
                self._abertos.append(ds)
        return ds

    def cruza(self, e0, n0, e1, n1):
        return not (e1 < self.E0 or e0 > self.E1 or n1 < self.N0 or n0 > self.N1)

    def fechar(self):
        self.tif.fechar()
        with self._trava_abrir:
            for ds in self._abertos:
                foto_rapida.fecha(ds)
            self._abertos = []
        self._local = threading.local()


def abrir_fontes(caminhos, proj_grid, crs="auto", sem_datum=False,
                 registrar=print):
    """Abre os .tif que servem e diz, um por um, o que aconteceu com cada."""
    fontes = []
    for caminho in caminhos:
        try:
            tif = Tiff(caminho)
            proj = _proj_da_foto(tif, crs, proj_grid, sem_datum, lambda *a: None)
            tif.fechar()
            if proj == "?":
                raise ErroTiff("sistema de coordenadas desconhecido; use --crs")
            f = FotoFonte(caminho, proj_grid, proj)
        except Exception as e:
            registrar(f"  {os.path.basename(caminho)}: PULADO -- {e}")
            continue
        registrar(f"  {f.nome}: {f.tif.largura}x{f.tif.altura} px, "
                  f"{f.geo.metros_por_pixel:.2f} m/px"
                  + (", com alfa" if f.tif.tem_alfa else ""))
        fontes.append(f)
    return fontes


def gerar_mosaico(caminhos, proj_grid, saida, proj_foto=None, limites=None,
                  ladrilho=LADRILHO, qualidade=85, mais_fino=None,
                  registrar=print, crs="auto", sem_datum=False, fundos=None):
    """Corta as fotos em ladrilhos ja' na grid da mina. Devolve o manifesto.

    Aceita varios .tif: levantamento de mina costuma vir em folhas separadas,
    uma por area, e o operador nao quer escolher qual delas esta' vendo. Elas
    entram todas no mesmo mosaico; onde duas se sobrepoem, vale a primeira que
    cobre o ponto.
    """
    if isinstance(caminhos, str):
        caminhos = [caminhos]
    if isinstance(fundos, str):
        fundos = [fundos]
    fundos = fundos or []
    if proj_foto is not None:
        fontes = []
        for c in caminhos:
            fontes.append(FotoFonte(c, proj_grid, proj_foto))
    else:
        fontes = abrir_fontes(caminhos, proj_grid, crs, sem_datum, registrar)
    if not fontes:
        raise ErroTiff("nenhum .tif utilizavel")

    # O fundo entra DEPOIS na lista, entao perde para o ortofoto em todo pixel
    # que os dois cobrem — ele existe para tapar buraco, nao para competir.
    # A area coberta e' a de todo mundo (senao o fundo nao teria onde entrar);
    # a resolucao, so' a do levantamento, para uma imagem grossa de tapar
    # buraco nao rebaixar o detalhe do mapa inteiro.
    if fundos:
        de_fundo = abrir_fontes(fundos, proj_grid, crs, sem_datum, registrar)
        for f in de_fundo:
            registrar(f"  fundo: {f.nome}, {f.geo.metros_por_pixel:.1f} m/px")
        fontes = fontes + de_fundo
    E0 = min(f.E0 for f in fontes); E1 = max(f.E1 for f in fontes)
    N0 = min(f.N0 for f in fontes); N1 = max(f.N1 for f in fontes)
    if limites:
        E0 = max(E0, limites[0]); N0 = max(N0, limites[1])
        E1 = min(E1, limites[2]); N1 = min(N1, limites[3])
    if E1 <= E0 or N1 <= N0:
        for f in fontes:
            f.fechar()
        raise ErroTiff("a foto nao cobre nada da area da malha -- confira o "
                       "sistema de coordenadas (--crs)")

    largura_m, altura_m = E1 - E0, N1 - N0

    def piramide(base):
        niveis, z, res = [], 0, base
        while True:
            cols = int(math.ceil(largura_m / (res * ladrilho)))
            linhas = int(math.ceil(altura_m / (res * ladrilho)))
            niveis.append({"z": z, "m_por_px": res, "cols": cols,
                           "linhas": linhas})
            if cols <= 1 and linhas <= 1 or z > 12:
                break
            res *= 2
            z += 1
        niveis.reverse()
        for i, nv in enumerate(niveis):
            nv["z"] = i
        return niveis

    da_mina = [f for f in fontes if f.caminho not in fundos]
    base = mais_fino or max(0.25, round(min(f.geo.metros_por_pixel
                                            for f in da_mina), 3))
    niveis = piramide(base)
    # Ortofoto de mina tem detalhe que nenhuma tela de tablet mostra, e cada
    # ladrilho e' tempo de conversao aqui e megabyte de rede la'. Quando passa
    # do teto, o nivel mais fino recua.
    teto = (LIMITE_LADRILHOS_RAPIDO
            if (foto_rapida is not None and foto_rapida.disponivel()
                and all(f.rapido is not None for f in fontes))
            else LIMITE_LADRILHOS)
    while sum(n["cols"] * n["linhas"] for n in niveis) > teto:
        base *= 2
        niveis = piramide(base)
    if not mais_fino and base > max(0.25, min(f.geo.metros_por_pixel
                                              for f in da_mina)) + 1e-9:
        registrar(f"nivel mais fino em {base:.2f} m/px para caber em "
                  f"{teto} ladrilhos (--mais-fino force outro)")

    total = sum(n["cols"] * n["linhas"] for n in niveis)
    rapido = (foto_rapida is not None and foto_rapida.disponivel()
              and all(f.rapido is not None for f in fontes))
    # Trabalhadores em paralelo so' no caminho rapido: o leitor de Python puro
    # tem um cache de blocos compartilhado, que nao aguenta concorrencia.
    trabalhadores = min(8, (os.cpu_count() or 2)) if rapido else 1
    registrar(f"{len(fontes)} foto(s), {len(niveis)} niveis, ate {total} ladrilhos")
    if rapido:
        registrar(f"leitura pelo GDAL (rasterio), {trabalhadores} em paralelo")
    else:
        registrar("leitura em Python puro. Para acelerar muito: "
                  "pip install rasterio numpy pillow")

    escritos = 0
    com_alfa = []
    tentados = 0
    comeco = time.time()
    proximo_aviso = 25
    for nv in niveis:
        res = nv["m_por_px"]
        deste_nivel = 0
        tarefas = []
        for ty in range(nv["linhas"]):
            for tx in range(nv["cols"]):
                e0 = E0 + tx * ladrilho * res
                n1 = N1 - ty * ladrilho * res
                e1 = e0 + ladrilho * res
                n0 = n1 - ladrilho * res
                aqui = [f for f in fontes if f.cruza(e0, n0, e1, n1)]
                if aqui:
                    tarefas.append((tx, ty, e0, n1, aqui))

        def constroi(tarefa):
            tx, ty, e0, n1, aqui = tarefa
            if rapido:
                saiu = _um_ladrilho_rapido(aqui, e0, n1, res, ladrilho,
                                           qualidade)
                if saiu is not None:
                    return tx, ty, saiu
                return tx, ty, None
            return tx, ty, _um_ladrilho(aqui, e0, n1, res, ladrilho, qualidade)

        if trabalhadores > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=trabalhadores) as equipe:
                resultados = list(equipe.map(constroi, tarefas))
        else:
            resultados = [constroi(t) for t in tarefas]

        # gravar e contar numa thread so': o disco nao ganha nada com fila
        for tx, ty, saiu in resultados:
            tentados += 1
            # Sem isto, a conversao fica meia hora calada e quem esta' na
            # frente do computador nao sabe se travou.
            if tentados >= proximo_aviso:
                proximo_aviso = tentados + 25
                gasto = time.time() - comeco
                resta = (total - tentados) * gasto / tentados
                registrar(f"    {tentados}/{total} ladrilhos, "
                          f"{gasto / 60:.0f} min feitos, "
                          f"~{resta / 60:.0f} min pela frente")
            if saiu is None:
                continue
            dados, ext = saiu
            pasta = os.path.join(saida, str(nv["z"]), str(tx))
            os.makedirs(pasta, exist_ok=True)
            with open(os.path.join(pasta, f"{ty}.{ext}"), "wb") as f:
                f.write(dados)
            if ext == "png":
                com_alfa.append(f"{nv['z']}/{tx}/{ty}")
            escritos += 1
            deste_nivel += 1
        registrar(f"  nivel {nv['z']}: {res:.2f} m/px, {deste_nivel} ladrilhos")

    for f in fontes:
        f.fechar()
    manifesto = {
        "versao": 1,
        "origem": [f.nome for f in fontes],
        "grid": {"E0": round(E0, 2), "N0": round(N0, 2),
                 "E1": round(E1, 2), "N1": round(N1, 2)},
        # A tela desenha em graus, nao na grid. Tres cantos bastam: entre a
        # grid e o grau a deformacao e' desprezivel nos poucos quilometros de
        # uma mina, e com tres pontos a tela monta a transformacao inteira.
        "cantos": {"no": _lonlat(proj_grid, E0, N1),
                   "ne": _lonlat(proj_grid, E1, N1),
                   "so": _lonlat(proj_grid, E0, N0)},
        "ladrilho": ladrilho,
        "formato": "jpg",
        # os da borda do voo tem alfa e sao PNG; a tela precisa saber quais
        "png": com_alfa,
        "niveis": niveis,
        "ladrilhos": escritos,
    }
    os.makedirs(saida, exist_ok=True)
    with open(os.path.join(saida, "manifesto.json"), "w", encoding="utf-8") as f:
        import json
        json.dump(manifesto, f, ensure_ascii=False, indent=1)
    registrar(f"{escritos} ladrilhos em {saida}, "
              f"em {(time.time() - comeco) / 60:.0f} min")
    return manifesto


def _lonlat(proj, E, N):
    lat, lon = proj.para_wgs84(E, N)
    return [round(lon, 8), round(lat, 8)]


def _inverso(proj_grid, proj_foto):
    """Coordenada da foto -> grid da mina."""
    def f(X, Y):
        if proj_foto is None:
            return (X, Y)
        if proj_foto == "wgs84":
            return proj_grid.para_grid(Y, X)
        lat, lon = proj_foto.para_wgs84(X, Y)
        return proj_grid.para_grid(lat, lon)
    return f


def _grade_cheia(grade, passo, n, lado):
    """Espalha a grade de controle para todos os pixels do ladrilho, em numpy.

    E' a mesma interpolacao bilinear do caminho lento, feita de uma vez para
    os 65 mil pixels em vez de um por um.
    """
    np = foto_rapida.np
    LX = np.array([[p[0] for p in linha] for linha in grade], dtype=np.float64)
    LY = np.array([[p[1] for p in linha] for linha in grade], dtype=np.float64)
    u = np.arange(lado, dtype=np.float64) / passo
    i0 = np.clip(u.astype(np.int64), 0, n - 2); tu = (u - i0)[None, :]
    j0 = i0; tv = (u - j0)[:, None]
    jj = j0[:, None]; ii = i0[None, :]

    def bilinear(L):
        a = L[jj, ii]; b = L[jj, ii + 1]
        c = L[jj + 1, ii]; d = L[jj + 1, ii + 1]
        return a + (b - a) * tu + (c - a) * tv + (a - b - c + d) * tu * tv

    return bilinear(LX), bilinear(LY)


def _um_ladrilho_rapido(fontes, e_base, n_base, res, lado, qualidade):
    """O mesmo ladrilho do caminho lento, com o GDAL lendo os pixels.

    A geometria e' identica — a mesma grade de controle, a mesma projecao, o
    mesmo datum. O que muda e' que a janela vem decodificada em C e ja'
    reduzida ao tamanho util, e a amostragem acontece em numpy.
    """
    np = foto_rapida.np
    passo = max(1, lado // 8)
    n = lado // passo + 1

    cor = np.zeros((lado, lado, 3), dtype=np.uint8)
    pronto = np.zeros((lado, lado), dtype=bool)

    for f in fontes:
        if f.rapido is None:
            return None                      # sem GDAL aqui: cai no lento
        def mundo(i, j, f=f):
            X, Y = f.para_foto(e_base + i * res, n_base - j * res)
            return f.geo.para_pixel(X, Y)
        grade = _lattice(mundo, 0, 0, passo, n)
        FX, FY = _grade_cheia(grade, passo, n, lado)

        larg, alt = f.tif.largura, f.tif.altura
        dentro = (FX >= 0) & (FX < larg) & (FY >= 0) & (FY < alt) & (~pronto)
        if not dentro.any():
            continue
        x0 = int(max(0, np.floor(FX[dentro].min())))
        x1 = int(min(larg, np.ceil(FX[dentro].max()) + 1))
        y0 = int(max(0, np.floor(FY[dentro].min())))
        y1 = int(min(alt, np.ceil(FY[dentro].max()) + 1))
        if x1 <= x0 or y1 <= y0:
            continue

        # ler ja' no tamanho util: um pixel de origem por pixel de saida
        sw = min(x1 - x0, lado + 2)
        sh = min(y1 - y0, lado + 2)
        try:
            bloco = foto_rapida.ler(f.rapido, x0, y0, x1 - x0, y1 - y0, sw, sh)
        except Exception:
            return None
        bh, bw = bloco.shape[0], bloco.shape[1]

        ax = np.clip(((FX - x0) * (bw / float(x1 - x0))).astype(np.int64),
                     0, bw - 1)
        ay = np.clip(((FY - y0) * (bh / float(y1 - y0))).astype(np.int64),
                     0, bh - 1)
        amostra = bloco[ay, ax]
        opaco = dentro & (amostra[:, :, 3] >= 128)
        if not opaco.any():
            continue
        cor[opaco] = amostra[:, :, :3][opaco]
        pronto |= opaco

    cobertos = int(pronto.sum())
    total = lado * lado
    if cobertos < total * 0.01:
        return None
    if cobertos == total:
        dados = foto_rapida.codifica_jpeg(cor, qualidade)
        if dados is None:
            dados = codificar_jpeg(cor.tobytes(), lado, lado, qualidade)
        return dados, "jpg"
    rgba = np.dstack([cor, np.where(pronto, 255, 0).astype(np.uint8)])
    dados = foto_rapida.codifica_png(rgba)
    if dados is None:
        dados = codificar_png(rgba.tobytes(), lado, lado)
    return dados, "png"


def _um_ladrilho(fontes, e_base, n_base, res, lado, qualidade):
    """Um ladrilho como (bytes, extensao), ou None quando nao ha foto ali.

    Cheio vira JPEG; cobertura parcial vira PNG com alfa. O contorno do
    ortomosaico e' recortado e as folhas encostam umas nas outras — pintar o
    que falta de cinza riscaria o mapa em cada uma dessas bordas.
    """
    passo = max(1, lado // 8)
    n = lado // passo + 1

    # uma grade de controle por foto: sao poucas as que cruzam cada ladrilho
    grades = []
    for f in fontes:
        def mundo(i, j, f=f):
            X, Y = f.para_foto(e_base + i * res, n_base - j * res)
            return f.geo.para_pixel(X, Y)
        grade = _lattice(mundo, 0, 0, passo, n)
        xs = [p[0] for l in grade for p in l]
        ys = [p[1] for l in grade for p in l]
        if (max(xs) < 0 or min(xs) > f.tif.largura or
                max(ys) < 0 or min(ys) > f.tif.altura):
            continue
        grades.append((f, grade))
    if not grades:
        return None

    px = bytearray(lado * lado * 4)
    cobertos = 0
    for j in range(lado):
        for i in range(lado):
            cor = None
            for f, grade in grades:
                fx, fy = _interpola(grade, i, j, passo, n)
                cor = f.tif.amostra(int(fx), int(fy))
                if cor is not None:
                    break
            k = (j * lado + i) * 4
            if cor is not None:
                px[k:k + 3] = bytes(cor)
                px[k + 3] = 255
                cobertos += 1
    total = lado * lado
    if cobertos < total * 0.01:
        return None
    if cobertos == total:
        rgb = bytearray(total * 3)
        for p in range(total):
            rgb[p * 3:p * 3 + 3] = px[p * 4:p * 4 + 3]
        return codificar_jpeg(bytes(rgb), lado, lado, qualidade), "jpg"
    return codificar_png(bytes(px), lado, lado), "png"


# ---------------------------------------------------------------- CLI

def _proj_da_foto(tif, escolha, proj_grid=None, sem_datum=False, registrar=print):
    """Decide em que sistema estao as coordenadas do TIFF.

       Devolve None (ja' e' a grid da mina), "wgs84", ou uma projecao UTM.
    """
    if escolha == "grid":
        registrar("  sistema   : grid da mina (informado na linha de comando)")
        return None
    if escolha and escolha.startswith("epsg:"):
        codigo = int(escolha.split(":", 1)[1])
    elif escolha and escolha.startswith("utm:"):
        z = escolha.split(":", 1)[1]
        zona, hemi = int(z[:-1]), z[-1].upper()
        registrar(f"  sistema   : UTM {zona}{hemi} (informado na linha de comando)")
        return projecao_utm(zona, hemi)
    elif escolha and escolha.startswith("utmmina:"):
        z = escolha.split(":", 1)[1]
        zona, hemi = int(z[:-1]), z[-1].upper()
        return _com_datum(zona, hemi, "informado", proj_grid, sem_datum, registrar)
    else:
        codigo = epsg_do_tiff(tif)
        if not codigo:
            registrar("  sistema   : o TIFF nao diz. Use --crs epsg:31983, "
                      "--crs utm:23S ou --crs grid")
            return "?"
    if codigo in (4326, 4674, 4979):
        registrar(f"  sistema   : graus (EPSG:{codigo})")
        return "wgs84"
    utm = utm_de_epsg(codigo)
    if not utm:
        registrar(f"  sistema   : EPSG:{codigo} nao reconhecido")
        return "?"
    zona, hemi, datum = utm
    registrar(f"  sistema   : UTM {zona}{hemi}, datum {datum} (EPSG:{codigo})")
    if datum in ("SAD69", "CorregoAlegre"):
        return _com_datum(zona, hemi, datum, proj_grid, sem_datum, registrar)
    return projecao_utm(zona, hemi)


def _com_datum(zona, hemi, datum, proj_grid, sem_datum, registrar):
    """Acerta o datum antigo pelo desvio medido no banco do DISPATCH."""
    if sem_datum or proj_grid is None:
        return projecao_utm(zona, hemi)
    dE, dN = desvio_do_datum(proj_grid, zona, hemi)
    if math.hypot(dE, dN) < 5.0:
        registrar(f"  datum     : {datum} declarado, mas a grid da mina esta' "
                  f"em WGS84 (o banco nao mostra desvio). Se a foto sair "
                  f"deslocada, o .tif esta' rotulado errado.")
        return projecao_utm(zona, hemi)
    registrar(f"  datum     : corrigido em {dE:.1f} m leste e {dN:.1f} m norte, "
              f"medidos no proprio Modular.db. Use --sem-datum para desligar.")
    return projecao_utm(zona, hemi, dE, dN)


def conferir(caminho, proj_grid=None, crs="auto", sem_datum=False,
             registrar=print):
    """Diz o que ha dentro do arquivo, sem converter nada."""
    tif = Tiff(caminho)
    registrar(f"=== {os.path.basename(caminho)} ===")
    registrar(f"  tamanho   : {tif.largura} x {tif.altura} px, "
              f"{tif.amostras} amostra(s) de {tif.bits} bits")
    registrar(f"  compressao: {NOME_COMPRESSAO.get(tif.compressao, tif.compressao)}"
              + (" com preditor horizontal" if tif.preditor == 2 else ""))
    registrar(f"  blocos    : {'ladrilhos' if tif.por_ladrilho else 'faixas'} de "
              f"{tif.bl_largura} x {tif.bl_altura}")
    ok, motivo = tif.legivel_aqui()
    registrar(f"  leitura   : {'ok' if ok else 'NAO -- ' + motivo}")

    geo = georref_de(tif, caminho)
    if geo is None:
        registrar("  posicao   : NAO -- sem GeoTIFF e sem world file ao lado")
        tif.fechar()
        return False
    registrar(f"  posicao   : {geo.fonte}, {geo.metros_por_pixel:.3f} m/px")
    proj_foto = _proj_da_foto(tif, crs, proj_grid, sem_datum, registrar)

    X0, Y0 = geo.para_crs(0, 0)
    X1, Y1 = geo.para_crs(tif.largura, tif.altura)
    registrar(f"  cantos    : {min(X0, X1):.1f} a {max(X0, X1):.1f} (X), "
              f"{min(Y0, Y1):.1f} a {max(Y0, Y1):.1f} (Y)")

    if proj_grid is not None and proj_foto != "?":
        de_foto = _inverso(proj_grid, proj_foto)
        cantos = [de_foto(*geo.para_crs(px, py))
                  for px, py in ((0, 0), (tif.largura, 0),
                                 (0, tif.altura), (tif.largura, tif.altura))]
        E0 = min(c[0] for c in cantos); E1 = max(c[0] for c in cantos)
        N0 = min(c[1] for c in cantos); N1 = max(c[1] for c in cantos)
        registrar(f"  na grid   : E {E0:.0f} a {E1:.0f}, N {N0:.0f} a {N1:.0f}"
                  f"  ({(E1 - E0) / 1000:.2f} x {(N1 - N0) / 1000:.2f} km)")
    tif.fechar()
    return ok


def limites_da_malha(malha, folga=500.0):
    """Retangulo que contem a malha, com folga. Ladrilho fora disso e' peso
       morto: ninguem navega onde nao ha estrada."""
    es = [n["grid_e"] for n in malha.nos.values()]
    ns = [n["grid_n"] for n in malha.nos.values()]
    if not es:
        return None
    return (min(es) - folga, min(ns) - folga, max(es) + folga, max(ns) + folga)


def achar_tifs(caminhos):
    """Aceita arquivos, pastas, ou uma mistura. Pasta vira todos os .tif dela.

    O levantamento chega como uma pasta cheia de folhas, uma por area; obrigar
    a listar dezenove nomes na linha de comando seria trabalho a toa.
    """
    achados = []
    for c in caminhos:
        if os.path.isdir(c):
            for nome in sorted(os.listdir(c)):
                if nome.lower().endswith((".tif", ".tiff")):
                    achados.append(os.path.join(c, nome))
        elif os.path.isfile(c):
            achados.append(c)
        else:
            print(f"nao achei: {c}")
    return achados


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Prepara a foto aerea da mina para o mapa da navegacao")
    ap.add_argument("tif", nargs="+",
                    help="arquivos .tif, ou a pasta que os contem")
    ap.add_argument("--db", help="Modular.db, para saber a grid da mina")
    ap.add_argument("--saida", help="pasta dos ladrilhos (ativa a conversao)")
    ap.add_argument("--crs", default="auto",
                    help="auto | grid | epsg:31983 | utm:23S")
    ap.add_argument("--qualidade", type=int, default=85,
                    help="qualidade do JPEG, 1 a 100 (padrao 85)")
    ap.add_argument("--mais-fino", type=float, default=None, metavar="M",
                    help="metros por pixel do nivel mais detalhado")
    ap.add_argument("--sem-recorte", action="store_true",
                    help="nao recorta pela area da malha")
    ap.add_argument("--fundo", action="append", metavar="TIF", default=[],
                    help="imagem de fundo para tapar onde nao ha ortofoto. "
                         "Perde para o levantamento em todo pixel coberto "
                         "pelos dois. Pode repetir")
    ap.add_argument("--sem-datum", action="store_true",
                    help="nao corrige a diferenca de datum entre a foto e a "
                         "grid da mina")
    a = ap.parse_args(argv)

    aqui = os.path.dirname(os.path.abspath(__file__))
    if aqui not in sys.path:
        sys.path.insert(0, aqui)

    arquivos = achar_tifs(a.tif)
    if not arquivos:
        print("nenhum .tif encontrado.")
        return 1

    proj_grid, malha = None, None
    if a.db:
        import servidor_rotas
        malha = servidor_rotas.Malha(a.db)
        proj_grid = malha.proj

    print(f"=== {len(arquivos)} arquivo(s) ===")
    servem = 0
    for arq in arquivos:
        try:
            if conferir(arq, proj_grid, a.crs, a.sem_datum):
                servem += 1
        except Exception as e:
            print(f"=== {os.path.basename(arq)} ===")
            print(f"  NAO SERVE: {e}")
        print()
    if not servem:
        print("nenhum arquivo utilizavel.")
        return 1
    print(f"{servem} de {len(arquivos)} servem.")

    if not a.saida:
        print("\nPara gerar os ladrilhos:  --db Modular.db --saida foto")
        return 0
    if proj_grid is None:
        print("\nERRO: a conversao precisa do --db para saber a grid da mina.")
        return 1

    limites = None if a.sem_recorte else limites_da_malha(malha)
    print()
    gerar_mosaico(arquivos, proj_grid, a.saida, limites=limites,
                  qualidade=a.qualidade, mais_fino=a.mais_fino, crs=a.crs,
                  sem_datum=a.sem_datum, fundos=achar_tifs(a.fundo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
