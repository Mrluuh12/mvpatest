#!/usr/bin/env python3
"""
Atalho de velocidade para a conversao da foto aerea.

O `mapa_foto.py` sabe fazer tudo sozinho, sem instalar nada — e' o que
permite converter na propria maquina da mina, onde so' existe o .exe. So' que
decodificar LZW em Python custa caro: uma mina inteira leva mais de uma hora.

Quando `rasterio` e `numpy` estao disponiveis, quem decodifica passa a ser o
GDAL, em C, lendo so' a janela pedida e ja' reduzida. A geometria, o datum e o
formato de saida continuam sendo os do `mapa_foto`: muda quem le' o pixel, nao
onde ele vai parar. Ha um teste comparando os dois caminhos.

    pip install rasterio numpy
"""

import os

# Cache de blocos do GDAL. As folhas vem em faixas de UMA linha; com o cache
# pequeno de fabrica, o mesmo trecho e' decodificado de novo a cada ladrilho
# vizinho. Precisa ser definido antes de o GDAL subir.
os.environ.setdefault("GDAL_CACHEMAX", "1024")          # MB

try:
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import Window
    _ERRO = None
except Exception as e:                     # pragma: sem as bibliotecas
    np = rasterio = Resampling = Window = None
    _ERRO = str(e)


def disponivel():
    return rasterio is not None


def por_que_nao():
    return _ERRO or ""


def abrir(caminho):
    return rasterio.open(caminho)


def dimensoes(ds):
    return ds.width, ds.height, ds.count


def transformacao(ds):
    """Os seis numeros do georreferenciamento, na ordem do world file."""
    t = ds.transform
    return (t.a, t.b, t.c, t.d, t.e, t.f)


def codigo_epsg(ds):
    try:
        return ds.crs.to_epsg() if ds.crs else None
    except Exception:
        return None


def ler(ds, x0, y0, larg, alt, larg_saida, alt_saida):
    """Janela da imagem, ja' reduzida pelo GDAL, como RGBA uint8.

    Reduzir na leitura e' o que faz a diferenca: para um ladrilho de 256 px
    nao adianta trazer 4000 linhas do arquivo e jogar fora 15 de cada 16.
    """
    x0 = max(0, min(int(x0), ds.width - 1))
    y0 = max(0, min(int(y0), ds.height - 1))
    larg = max(1, min(int(larg), ds.width - x0))
    alt = max(1, min(int(alt), ds.height - y0))
    larg_saida = max(1, int(larg_saida))
    alt_saida = max(1, int(alt_saida))

    janela = Window(x0, y0, larg, alt)
    reamostragem = (Resampling.average if larg > larg_saida * 1.5
                    else Resampling.nearest)
    dados = ds.read(window=janela, out_shape=(ds.count, alt_saida, larg_saida),
                    resampling=reamostragem, boundless=False)
    dados = np.transpose(dados, (1, 2, 0))          # (linha, coluna, banda)

    if dados.dtype != np.uint8:                     # 16 bits cabe em 8 na tela
        maximo = float(dados.max()) or 1.0
        dados = (dados.astype(np.float32) * (255.0 / maximo)).astype(np.uint8)

    n = dados.shape[2]
    if n == 1:
        cinza = dados[:, :, 0]
        rgba = np.dstack([cinza, cinza, cinza,
                          np.full(cinza.shape, 255, np.uint8)])
    elif n == 2:
        cinza = dados[:, :, 0]
        rgba = np.dstack([cinza, cinza, cinza, dados[:, :, 1]])
    elif n == 3:
        rgba = np.dstack([dados, np.full(dados.shape[:2], 255, np.uint8)])
    else:
        rgba = dados[:, :, :4]
    return np.ascontiguousarray(rgba)


def fecha(ds):
    try:
        ds.close()
    except Exception:
        pass


try:
    from PIL import Image
except Exception:                          # pragma: sem a biblioteca
    Image = None


def codifica_disponivel():
    return Image is not None


def codifica_jpeg(rgb, qualidade):
    """JPEG a partir de um array (linha, coluna, 3). None se nao der."""
    if Image is None:
        return None
    import io
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, "JPEG", quality=int(qualidade),
                                     optimize=False, subsampling=0)
    return buf.getvalue()


def codifica_png(rgba):
    """PNG com alfa a partir de um array (linha, coluna, 4)."""
    if Image is None:
        return None
    import io
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", compress_level=6)
    return buf.getvalue()
