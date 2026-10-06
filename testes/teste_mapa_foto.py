#!/usr/bin/env python3
"""
Foto aerea: leitura do GeoTIFF, JPEG e o encaixe na grid da mina.

O que mais importa aqui e' o teste de ponta a ponta: uma marca num pixel
conhecido da foto tem que aparecer no ladrilho e no pixel previstos depois de
atravessar UTM, WGS84 e a grid do DISPATCH. Errar a projecao por 50 metros
poe a estrada em cima da bancada, e ninguem percebe olhando so' o codigo.
"""

import importlib.util
import io
import math
import os
import shutil
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "servidor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapa_foto as mf
import servidor_rotas as sr
import gerar_geotiff as gg
import gerar_banco_sintetico as gb

try:
    from PIL import Image
except Exception:
    Image = None


class TesteLeitorTiff(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="foto-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _arquivo(self, **kw):
        px = gg.imagem_de_teste(60, 40, [(10, 5, (255, 0, 0), 2)])
        p = os.path.join(self.dir, "t.tif")
        gg.escrever(p, 60, 40, px, (0.5, 0.5), (600000.0, 7900000.0), **kw)
        return p, px

    def test_le_sem_compressao(self):
        p, px = self._arquivo(compressao=1)
        t = mf.Tiff(p)
        self.assertEqual((t.largura, t.altura, t.amostras), (60, 40, 3))
        self.assertEqual(t.amostra(10, 5), (255, 0, 0))
        self.assertEqual(t.amostra(0, 0), tuple(px[0:3]))
        t.fechar()

    def test_le_deflate_em_varias_faixas(self):
        """Ortofoto real vem em faixas ou ladrilhos; ler so' a primeira daria
           uma imagem certa no topo e preta no resto."""
        p, _ = self._arquivo(compressao=8, por_faixa=8)
        t = mf.Tiff(p)
        self.assertEqual(t.amostra(10, 5), (255, 0, 0))
        self.assertEqual(t.amostra(59, 39), (255, 255, 60))
        t.fechar()

    def test_fora_da_imagem_nao_estoura(self):
        p, _ = self._arquivo()
        t = mf.Tiff(p)
        for x, y in ((-1, 0), (0, -1), (60, 0), (0, 40), (9999, 9999)):
            self.assertIsNone(t.amostra(x, y))
        t.fechar()

    def test_acha_a_posicao_e_o_epsg(self):
        p, _ = self._arquivo()
        t = mf.Tiff(p)
        geo = mf.georref_de(t, p)
        self.assertAlmostEqual(geo.para_crs(0, 0)[0], 600000.0, places=3)
        self.assertAlmostEqual(geo.para_crs(0, 0)[1], 7900000.0, places=3)
        # o eixo Y do mundo cresce para o norte, o da imagem para baixo
        self.assertLess(geo.para_crs(0, 10)[1], geo.para_crs(0, 0)[1])
        self.assertAlmostEqual(geo.metros_por_pixel, 0.5, places=6)
        self.assertEqual(mf.epsg_do_tiff(t), 31983)
        t.fechar()

    def test_world_file_quando_nao_ha_geotiff(self):
        p, _ = self._arquivo()
        # arquivo sem as etiquetas geo, com .tfw ao lado
        px = gg.imagem_de_teste(10, 10)
        p2 = os.path.join(self.dir, "sem_geo.tif")
        gg.escrever(p2, 10, 10, px, (1.0, 1.0), (0.0, 0.0))
        t = mf.Tiff(p2)
        del t.tags[33550], t.tags[33922]
        with open(os.path.join(self.dir, "sem_geo.tfw"), "w") as f:
            f.write("0.25\n0\n0\n-0.25\n700000.5\n7800000.5\n")
        geo = mf.georref_de(t, p2)
        self.assertEqual(geo.fonte, "world file")
        self.assertAlmostEqual(geo.para_crs(0, 0)[0], 700000.5, places=3)
        t.fechar()

    def test_pixel_e_coordenada_sao_reversiveis(self):
        p, _ = self._arquivo()
        t = mf.Tiff(p)
        geo = mf.georref_de(t, p)
        for px, py in ((0, 0), (13, 27), (59.5, 39.5)):
            X, Y = geo.para_crs(px, py)
            a, b = geo.para_pixel(X, Y)
            self.assertAlmostEqual(a, px, places=6)
            self.assertAlmostEqual(b, py, places=6)
        t.fechar()


class TesteAlfa(unittest.TestCase):
    """Ortomosaico "transparent" traz o fora-do-voo com alfa zero. Ignorar
       essa banda pinta a borda do levantamento de preto em cima do mapa —
       e as folhas que a mina entrega sao todas assim."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="alfa-")
        self.W = self.H = 24
        px = bytearray()
        for y in range(self.H):
            for x in range(self.W):
                dentro = 6 <= x < 18 and 6 <= y < 18
                px += bytes([200, 100, 50, 255 if dentro else 0])
        self.arq = os.path.join(self.dir, "t.tif")
        gg.escrever(self.arq, self.W, self.H, bytes(px), (1.0, 1.0),
                    (600000.0, 7900000.0), compressao=8, amostras=4)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_reconhece_a_banda_alfa(self):
        t = mf.Tiff(self.arq)
        self.assertEqual(t.amostras, 4)
        self.assertTrue(t.tem_alfa)
        self.assertEqual(t.canal_alfa, 3)
        t.fechar()

    def test_pixel_transparente_conta_como_sem_foto(self):
        t = mf.Tiff(self.arq)
        self.assertEqual(t.amostra(12, 12), (200, 100, 50))
        self.assertIsNone(t.amostra(1, 1), "o fora-do-voo virou pixel opaco")
        self.assertIsNone(t.amostra(23, 23))
        t.fechar()


@unittest.skipIf(Image is None, "sem Pillow nesta maquina")
class TesteContraOutraBiblioteca(unittest.TestCase):
    """O leitor foi escrito a mao; conferir contra uma implementacao
       independente e' o que separa 'passa nos meus testes' de 'le' TIFF'."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="foto-pil-")
        self.W, self.H = 71, 53
        self.im = Image.new("RGB", (self.W, self.H))
        p = self.im.load()
        for y in range(self.H):
            for x in range(self.W):
                p[x, y] = ((x * 7) % 256, (y * 11) % 256, ((x + y) * 3) % 256)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _confere(self, nome, **kw):
        p = os.path.join(self.dir, nome + ".tif")
        self.im.save(p, **kw)
        t = mf.Tiff(p)
        ok, motivo = t.legivel_aqui()
        self.assertTrue(ok, f"{nome}: {motivo}")
        ref = Image.open(p).convert("RGB").load()
        for y in range(self.H):
            for x in range(self.W):
                self.assertEqual(t.amostra(x, y), ref[x, y],
                                 f"{nome}: pixel {x},{y}")
        t.fechar()

    def test_lzw(self):
        self._confere("lzw", compression="tiff_lzw")

    def test_packbits(self):
        self._confere("packbits", compression="packbits")

    def test_deflate(self):
        self._confere("deflate", compression="tiff_deflate")

    def test_lzw_com_preditor(self):
        """LZW com preditor horizontal e' o que o gdal_translate faz por
           padrao; sem desfazer o preditor a imagem sai em faixas borradas."""
        self._confere("pred_lzw", compression="tiff_lzw", tiffinfo={317: 2})

    def test_deflate_com_preditor(self):
        self._confere("pred_defl", compression="tiff_deflate", tiffinfo={317: 2})


class TesteJpeg(unittest.TestCase):
    def _imagem(self, W, H):
        px = bytearray()
        for y in range(H):
            for x in range(W):
                px += bytes([(x * 4) % 256, (y * 4) % 256,
                             255 if (x // 8 + y // 8) % 2 else 30])
        return bytes(px)

    def test_gera_arquivo_com_a_estrutura_certa(self):
        d = mf.codificar_jpeg(self._imagem(16, 16), 16, 16)
        self.assertEqual(d[:2], b"\xff\xd8")
        self.assertEqual(d[-2:], b"\xff\xd9")

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_a_imagem_volta_parecida(self):
        """Codifica aqui, decodifica com outra biblioteca. Um erro de sinal no
           acumulador de bits gera um arquivo que ABRE e mostra lixo — so' a
           comparacao pixel a pixel pega isso."""
        W = H = 32
        orig = self._imagem(W, H)
        dado = mf.codificar_jpeg(orig, W, H, qualidade=85)
        dec = Image.open(io.BytesIO(dado)).convert("RGB").tobytes()
        self.assertEqual(len(dec), len(orig))
        erro = [abs(dec[i] - orig[i]) for i in range(len(orig))]
        mse = sum(e * e for e in erro) / len(erro)
        psnr = 10 * math.log10(255 * 255 / mse) if mse else 99
        self.assertGreater(psnr, 30, "a imagem decodificada nao parece a original")

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_qualidade_maior_gera_arquivo_maior(self):
        img = self._imagem(32, 32)
        pequeno = mf.codificar_jpeg(img, 32, 32, qualidade=40)
        grande = mf.codificar_jpeg(img, 32, 32, qualidade=95)
        self.assertLess(len(pequeno), len(grande))


class TesteCoordenadas(unittest.TestCase):
    def test_epsg_de_utm(self):
        self.assertEqual(mf.utm_de_epsg(31983), (23, "S", "SIRGAS2000"))
        self.assertEqual(mf.utm_de_epsg(32723), (23, "S", "WGS84"))
        self.assertEqual(mf.utm_de_epsg(32633), (33, "N", "WGS84"))
        self.assertEqual(mf.utm_de_epsg(29193), (23, "S", "SAD69"))
        self.assertIsNone(mf.utm_de_epsg(4326))

    def test_utm_vai_e_volta(self):
        p = mf.projecao_utm(23, "S")
        lat, lon = p.para_wgs84(600000.0, 7900000.0)
        self.assertAlmostEqual(lon, -43.0, delta=1.5)
        E, N = p.para_grid(lat, lon)
        self.assertAlmostEqual(E, 600000.0, places=2)
        self.assertAlmostEqual(N, 7900000.0, places=2)

    def test_meridiano_central_da_zona(self):
        for zona, lon in ((23, -45.0), (22, -51.0), (24, -39.0)):
            p = mf.projecao_utm(zona, "S")
            self.assertAlmostEqual(math.degrees(p.lam0), lon, places=6)


class TesteDatum(unittest.TestCase):
    """A mina trabalha num datum antigo e o DISPATCH guarda isso sem dizer.

       O banco tem a origem da grid duas vezes: em graus e em coordenada
       projetada. Quando a mina usa SAD69, as duas nao batem — e a diferenca
       e' o deslocamento do datum, medido no lugar exato onde a mina fica.
       Ignorar isso poe a foto uns 60 m fora da estrada."""

    def _grid_com_desvio(self, dE, dN):
        """Uma grid como a do DISPATCH: origem em graus, base deslocada."""
        lat0, lon0 = -18.93882038, -43.41417951
        E, N = mf.projecao_utm(23, "S").para_grid(lat0, lon0)
        return sr.TransversaMercator(
            A=mf.WGS84_A, e2=mf.WGS84_E2,
            phi0=math.radians(lat0), lam0=math.radians(lon0),
            k0=0.99994276, baseE=E + dE, baseN=N + dN)

    def test_epsg_sad69_96(self):
        self.assertEqual(mf.utm_de_epsg(5533), (23, "S", "SAD69"))
        self.assertEqual(mf.utm_de_epsg(5531), (21, "S", "SAD69"))

    def test_mede_o_desvio_guardado_no_banco(self):
        grid = self._grid_com_desvio(44.54, 44.90)
        dE, dN = mf.desvio_do_datum(grid, 23, "S")
        self.assertAlmostEqual(dE, 44.54, places=2)
        self.assertAlmostEqual(dN, 44.90, places=2)

    def test_grid_ja_em_wgs84_nao_ganha_desvio(self):
        grid = self._grid_com_desvio(0.0, 0.0)
        dE, dN = mf.desvio_do_datum(grid, 23, "S")
        self.assertLess(math.hypot(dE, dN), 0.01)

    def test_a_correcao_leva_a_foto_para_o_lugar_certo(self):
        """Com o desvio aplicado, a coordenada da foto cai onde a mina a
           numera; sem ele, fica 63 m fora."""
        grid = self._grid_com_desvio(44.54, 44.90)
        X, Y = 665057.6, 7910816.5        # canto de uma folha real, em SAD69
        sem = grid.para_grid(*mf.projecao_utm(23, "S").para_wgs84(X, Y))
        com = grid.para_grid(*mf.projecao_utm(23, "S", 44.54, 44.90)
                             .para_wgs84(X, Y))
        self.assertAlmostEqual(math.hypot(sem[0] - com[0], sem[1] - com[1]),
                               63.2, delta=0.5)
        # a numeracao da mina e a da foto sao a mesma; a grid so' gira um pouco
        self.assertLess(math.hypot(com[0] - X, com[1] - Y), 80,
                        "a foto corrigida devia cair perto da propria "
                        "coordenada dela")

    def test_sem_datum_desliga_a_correcao(self):
        grid = self._grid_com_desvio(44.54, 44.90)
        classe = type("T", (), {"tags": {34735: [1, 1, 0, 1, 3072, 0, 1, 5533]},
                                "amostras": 4})
        falso = classe()
        com = mf._proj_da_foto(falso, "auto", grid, False, lambda *a: None)
        sem = mf._proj_da_foto(falso, "auto", grid, True, lambda *a: None)
        self.assertNotAlmostEqual(com.baseE, sem.baseE, places=1)
        self.assertAlmostEqual(sem.baseE, 500000.0, places=6)


class TesteMosaico(unittest.TestCase):
    """De ponta a ponta: foto em UTM -> ladrilhos na grid da mina."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="mosaico-")
        cls.db = os.path.join(cls.dir, "mina.db")
        gb.gerar(cls.db)
        cls.malha = sr.Malha(cls.db)
        cls.utm = mf.projecao_utm(23, "S")
        cls.res = 4.0
        lim = mf.limites_da_malha(cls.malha, folga=0)
        cls.centro = ((lim[0] + lim[2]) / 2, (lim[1] + lim[3]) / 2)
        cantos = [cls.utm.para_grid(*cls.malha.proj.para_wgs84(e, n))
                  for e in (lim[0] - 300, lim[2] + 300)
                  for n in (lim[1] - 300, lim[3] + 300)]
        cls.X0 = min(c[0] for c in cantos); cls.Y1 = max(c[1] for c in cantos)
        W = int((max(c[0] for c in cantos) - cls.X0) / cls.res)
        H = int((cls.Y1 - min(c[1] for c in cantos)) / cls.res)
        Xc, Yc = cls.utm.para_grid(*cls.malha.proj.para_wgs84(*cls.centro))
        cls.marca = (int((Xc - cls.X0) / cls.res), int((cls.Y1 - Yc) / cls.res))
        px = gg.imagem_de_teste(W, H, [(cls.marca[0], cls.marca[1], (255, 0, 0), 4)])
        cls.tif = os.path.join(cls.dir, "orto.tif")
        gg.escrever(cls.tif, W, H, px, (cls.res, cls.res), (cls.X0, cls.Y1),
                    epsg=31983, compressao=8, por_faixa=32)
        cls.saida = os.path.join(cls.dir, "foto")
        cls.man = mf.gerar_mosaico(cls.tif, cls.malha.proj, cls.saida,
                                   proj_foto=cls.utm,
                                   limites=mf.limites_da_malha(cls.malha),
                                   mais_fino=8.0, registrar=lambda *a: None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _ladrilho(self, z, tx, ty):
        for ext in ("jpg", "png"):
            c = os.path.join(self.saida, str(z), str(tx), f"{ty}.{ext}")
            if os.path.exists(c):
                return c
        return None

    def test_manifesto_descreve_o_que_foi_escrito(self):
        m = self.man
        self.assertEqual(m["formato"], "jpg")
        self.assertGreater(m["ladrilhos"], 0)
        self.assertLess(m["grid"]["E0"], m["grid"]["E1"])
        self.assertLess(m["grid"]["N0"], m["grid"]["N1"])
        # niveis em ordem, cada um com o dobro da resolucao do anterior
        for a, b in zip(m["niveis"], m["niveis"][1:]):
            self.assertEqual(b["z"], a["z"] + 1)
            self.assertAlmostEqual(b["m_por_px"], a["m_por_px"] / 2, places=6)

    def test_manifesto_traz_os_cantos_em_graus(self):
        """A tela desenha em graus; sem os cantos ela nao sabe onde por a
           foto, e a grid da mina nao ajuda porque e' girada."""
        c = self.man["cantos"]
        for nome in ("no", "ne", "so"):
            lon, lat = c[nome]
            self.assertTrue(-180 <= lon <= 180 and -90 <= lat <= 90)
        self.assertGreater(c["ne"][0], c["no"][0], "o canto NE fica a leste do NO")
        self.assertLess(c["so"][1], c["no"][1], "o canto SO fica ao sul do NO")
        # os cantos tem que bater com a grid declarada
        g = self.man["grid"]
        lat, lon = self.malha.proj.para_wgs84(g["E0"], g["N1"])
        self.assertAlmostEqual(c["no"][0], lon, places=6)
        self.assertAlmostEqual(c["no"][1], lat, places=6)

    def test_os_arquivos_prometidos_existem(self):
        for nv in self.man["niveis"]:
            achados = 0
            for tx in range(nv["cols"]):
                for ty in range(nv["linhas"]):
                    for ext in ("jpg", "png"):
                        if os.path.exists(os.path.join(self.saida, str(nv["z"]),
                                                       str(tx), f"{ty}.{ext}")):
                            achados += 1
            self.assertGreater(achados, 0, f"nivel {nv['z']} sem nenhum ladrilho")

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_a_marca_cai_no_lugar_certo_da_grid(self):
        """O teste que vale: a foto atravessou UTM, WGS84 e a grid do
           DISPATCH. Se a projecao estiver trocada, a marca some — e em campo
           isso seria a estrada desenhada em cima da bancada."""
        nv = self.man["niveis"][-1]
        L = self.man["ladrilho"]; g = self.man["grid"]
        Ec, Nc = self.centro
        gx = (Ec - g["E0"]) / nv["m_por_px"]
        gy = (g["N1"] - Nc) / nv["m_por_px"]
        tx, ty = int(gx // L), int(gy // L)
        ix, iy = int(gx) - tx * L, int(gy) - ty * L
        caminho = self._ladrilho(nv["z"], tx, ty)
        self.assertIsNotNone(caminho,
                             f"nao ha ladrilho onde a marca deveria estar: "
                             f"{nv['z']}/{tx}/{ty}")
        r, v, a = Image.open(caminho).convert("RGB").getpixel((ix, iy))
        self.assertGreater(r, 180, "o vermelho da marca nao esta' aqui")
        self.assertLess(v, 80)
        self.assertLess(a, 80)

    def test_foto_longe_da_malha_e_recusada(self):
        """Ortofoto de outra mina, ou EPSG errado: melhor parar do que
           desenhar o mapa no lugar errado."""
        px = gg.imagem_de_teste(40, 40)
        longe = os.path.join(self.dir, "longe.tif")
        gg.escrever(longe, 40, 40, px, (1.0, 1.0), (200000.0, 1000000.0),
                    epsg=31983, compressao=1)
        with self.assertRaises(mf.ErroTiff):
            mf.gerar_mosaico(longe, self.malha.proj,
                             os.path.join(self.dir, "x"), proj_foto=self.utm,
                             limites=mf.limites_da_malha(self.malha),
                             registrar=lambda *a: None)


class TesteVariasFolhas(unittest.TestCase):
    """O levantamento vem em folhas separadas, uma por area. Todas tem que
       entrar no mesmo mosaico: o operador nao escolhe qual esta' vendo."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="folhas-")
        cls.db = os.path.join(cls.dir, "mina.db")
        gb.gerar(cls.db)
        cls.malha = sr.Malha(cls.db)
        cls.utm = mf.projecao_utm(23, "S")
        lim = mf.limites_da_malha(cls.malha, folga=0)
        cls.lim = lim
        # duas folhas lado a lado, cada uma cobrindo metade da mina no sentido
        # norte-sul, com cores diferentes para saber de onde veio cada pixel
        cls.pasta = os.path.join(cls.dir, "tifs")
        os.makedirs(cls.pasta)
        meio = (lim[1] + lim[3]) / 2
        cls.cores = [(220, 40, 40), (40, 220, 40)]
        for i, (n0, n1) in enumerate(((meio, lim[3]), (lim[1], meio))):
            cantos = [cls.utm.para_grid(*cls.malha.proj.para_wgs84(e, n))
                      for e in (lim[0], lim[2]) for n in (n0, n1)]
            X0 = min(c[0] for c in cantos); Y1 = max(c[1] for c in cantos)
            res = 8.0
            W = max(4, int((max(c[0] for c in cantos) - X0) / res))
            H = max(4, int((Y1 - min(c[1] for c in cantos)) / res))
            px = bytes(cls.cores[i]) * (W * H)
            gg.escrever(os.path.join(cls.pasta, f"folha{i}.tif"), W, H, px,
                        (res, res), (X0, Y1), epsg=31983, compressao=8,
                        por_faixa=32)
        cls.saida = os.path.join(cls.dir, "foto")
        cls.man = mf.gerar_mosaico(
            mf.achar_tifs([cls.pasta]), cls.malha.proj, cls.saida,
            limites=mf.limites_da_malha(cls.malha), mais_fino=16.0,
            registrar=lambda *a: None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_acha_os_tif_da_pasta(self):
        achados = mf.achar_tifs([self.pasta])
        self.assertEqual(len(achados), 2)
        self.assertTrue(all(a.endswith(".tif") for a in achados))

    def test_o_manifesto_lista_as_folhas(self):
        self.assertEqual(len(self.man["origem"]), 2)

    def test_o_mosaico_cobre_as_duas_folhas(self):
        """A area coberta tem que ser a soma, nao a da primeira folha."""
        g = self.man["grid"]
        altura = g["N1"] - g["N0"]
        esperado = min(self.lim[3], g["N1"]) - max(self.lim[1], g["N0"])
        self.assertGreater(altura, (self.lim[3] - self.lim[1]) * 0.8,
                           "o mosaico ficou so' com uma das folhas")

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_as_duas_cores_aparecem_no_mosaico(self):
        """Prova de que os pixels de ambas as folhas foram amostrados."""
        vistas = set()
        nv = self.man["niveis"][-1]
        for tx in range(nv["cols"]):
            for ty in range(nv["linhas"]):
                caminho = None
                for ext in ("jpg", "png"):
                    c = os.path.join(self.saida, str(nv["z"]), str(tx),
                                     f"{ty}.{ext}")
                    if os.path.exists(c):
                        caminho = c
                        break
                if caminho is None:
                    continue
                im = Image.open(caminho).convert("RGBA")
                for x in range(0, im.width, 8):
                    for y in range(0, im.height, 8):
                        r, v, a, alfa = im.getpixel((x, y))
                        if alfa < 128:
                            continue
                        if r > 150 and v < 100 and a < 100:
                            vistas.add("vermelho")
                        elif v > 150 and r < 100 and a < 100:
                            vistas.add("verde")
        self.assertEqual(vistas, {"vermelho", "verde"},
                         f"faltou folha no mosaico: {vistas}")

    def test_arquivo_ruim_e_pulado_e_o_resto_continua(self):
        """Uma folha corrompida no meio de dezenove nao pode derrubar a
           conversao inteira."""
        pasta = os.path.join(self.dir, "com_lixo")
        os.makedirs(pasta, exist_ok=True)
        shutil.copy2(os.path.join(self.pasta, "folha0.tif"),
                     os.path.join(pasta, "boa.tif"))
        with open(os.path.join(pasta, "quebrada.tif"), "wb") as f:
            f.write(b"isto nao e um TIFF")
        avisos = []
        man = mf.gerar_mosaico(
            mf.achar_tifs([pasta]), self.malha.proj,
            os.path.join(self.dir, "saida2"),
            limites=mf.limites_da_malha(self.malha), mais_fino=32.0,
            registrar=avisos.append)
        self.assertEqual(man["origem"], ["boa.tif"])
        self.assertTrue(any("PULADO" in a for a in avisos),
                        "a folha quebrada devia aparecer no relato")


def fronteira(y, lado):
    """Onde uma folha termina e a outra comeca: linha ondulada, nao reta."""
    return lado / 2 + 60 * math.sin(y * 0.03)


class TesteFolhasQueSeEncaixam(unittest.TestCase):
    """As folhas do levantamento nao sao retangulos: os contornos se encaixam,
       um recorte no outro.

       Duas coisas diferentes sao cobradas aqui:

       - onde as folhas se encaixam, o mosaico nao pode ter buraco. E' o que o
         teste da emenda anda conferindo, ponto a ponto ao longo da juncao;
       - onde a folha ACABA (o contorno externo recortado, como no visualizador
         do Windows), o ladrilho tem que sair com alfa, e nao preenchido. Com
         preenchimento, cada contorno desses viraria uma moldura solida por
         cima do mapa — e sao dezenove contornos. Quem cobra isso sao os dois
         testes de borda: sem alfa, a lista de PNG do manifesto vem vazia."""

    LADO = 640          # pixels de cada folha
    RES = 4.0           # metros por pixel

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="encaixe-")
        cls.db = os.path.join(cls.dir, "mina.db")
        gb.gerar(cls.db)
        cls.malha = sr.Malha(cls.db)
        cls.utm = mf.projecao_utm(23, "S")

        lim = mf.limites_da_malha(cls.malha, folga=0)
        cantos = [cls.utm.para_grid(*cls.malha.proj.para_wgs84(e, n))
                  for e in (lim[0], lim[2]) for n in (lim[1], lim[3])]
        X0 = min(c[0] for c in cantos); Y1 = max(c[1] for c in cantos)
        L, R = cls.LADO, cls.RES

        pasta = os.path.join(cls.dir, "tifs")
        os.makedirs(pasta)
        for nome, cor, esquerda in (("oeste.tif", (210, 60, 60), True),
                                    ("leste.tif", (60, 60, 210), False)):
            px = bytearray()
            for y in range(L):
                b = fronteira(y, L)
                for x in range(L):
                    dentro = (x < b + 8) if esquerda else (x > b - 8)
                    px += bytes([cor[0], cor[1], cor[2], 255 if dentro else 0])
            gg.escrever(os.path.join(pasta, nome), L, L, bytes(px), (R, R),
                        (X0, Y1), epsg=31983, compressao=8, por_faixa=64,
                        amostras=4)

        cls.X0, cls.Y1 = X0, Y1
        cls.saida = os.path.join(cls.dir, "foto")
        cls.man = mf.gerar_mosaico(mf.achar_tifs([pasta]), cls.malha.proj,
                                   cls.saida, mais_fino=4.0,
                                   registrar=lambda *a: None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _pixel(self, E, N):
        """Cor no mosaico, no nivel mais fino, para um ponto da grid."""
        nv = self.man["niveis"][-1]
        L = self.man["ladrilho"]; g = self.man["grid"]
        gx = (E - g["E0"]) / nv["m_por_px"]
        gy = (g["N1"] - N) / nv["m_por_px"]
        tx, ty = int(gx // L), int(gy // L)
        ix, iy = int(gx) - tx * L, int(gy) - ty * L
        for ext in ("jpg", "png"):
            c = os.path.join(self.saida, str(nv["z"]), str(tx), f"{ty}.{ext}")
            if os.path.exists(c):
                return Image.open(c).convert("RGBA").getpixel((ix, iy))
        return None

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_a_emenda_entre_as_folhas_nao_tem_buraco(self):
        """Anda em cima da juncao e cobra pixel de foto em todo ponto. A
           checagem de cinza aqui e' precaucao: na faixa de sobreposicao
           sempre ha uma folha cobrindo, entao o preenchimento nao apareceria
           mesmo. Quem pega o preenchimento sao os testes de borda."""
        de_foto = mf._inverso(self.malha.proj, self.utm)
        vistas = set()
        vazios = cinzas = 0
        # anda ao longo da fronteira ondulada, de cima a baixo
        for k in range(40):
            ypx = 40 + k * (self.LADO - 80) / 39.0
            xpx = fronteira(ypx, self.LADO)
            E, N = de_foto(self.X0 + xpx * self.RES, self.Y1 - ypx * self.RES)
            p = self._pixel(E, N)
            if p is None or p[3] < 200:
                vazios += 1
                continue
            # cinza de preenchimento nao vale como emenda fechada: as duas
            # folhas sao cores fortes, o preenchimento e' neutro
            if max(p[:3]) - min(p[:3]) < 60:
                cinzas += 1
                continue
            vistas.add("oeste" if p[0] > p[2] else "leste")
        self.assertEqual(vazios, 0,
                         f"{vazios} pontos vazios em cima da emenda: as folhas "
                         f"nao fecharam")
        self.assertEqual(cinzas, 0,
                         f"{cinzas} pontos de preenchimento em cima da emenda: "
                         f"a junta entre folhas virou faixa cinza")
        self.assertTrue(vistas, "nenhum ponto da emenda foi amostrado")

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_as_duas_folhas_estao_no_mosaico(self):
        de_foto = mf._inverso(self.malha.proj, self.utm)
        achadas = set()
        for xpx, quem in ((self.LADO * 0.15, "oeste"), (self.LADO * 0.85, "leste")):
            E, N = de_foto(self.X0 + xpx * self.RES,
                           self.Y1 - self.LADO * 0.5 * self.RES)
            p = self._pixel(E, N)
            self.assertIsNotNone(p, f"sem ladrilho no meio da folha {quem}")
            self.assertGreater(p[3], 200, f"folha {quem} saiu transparente")
            achadas.add("oeste" if p[0] > p[2] else "leste")
        self.assertEqual(achadas, {"oeste", "leste"})

    def test_o_manifesto_lista_os_ladrilhos_com_alfa(self):
        """A tela pede .png nesses e .jpg no resto; sem a lista ela erraria a
           extensao e a borda do voo nao carregaria."""
        self.assertTrue(self.man["png"],
                        "nenhum ladrilho de borda: o recorte do voo sumiu")
        for chave in self.man["png"]:
            z, tx, ty = chave.split("/")
            caminho = os.path.join(self.saida, z, tx, f"{ty}.png")
            self.assertTrue(os.path.exists(caminho),
                            f"o manifesto promete {chave}.png e nao ha arquivo")

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_ladrilho_de_borda_tem_pixel_transparente(self):
        chave = self.man["png"][0]
        z, tx, ty = chave.split("/")
        im = Image.open(os.path.join(self.saida, z, tx, f"{ty}.png"))
        self.assertEqual(im.mode, "RGBA")
        alfas = [im.getpixel((x, y))[3]
                 for x in range(0, im.width, 8) for y in range(0, im.height, 8)]
        self.assertTrue(any(a < 128 for a in alfas),
                        "ladrilho de borda sem nenhum pixel transparente")
        self.assertTrue(any(a > 200 for a in alfas),
                        "ladrilho de borda inteiramente transparente")


class TesteCaminhoRapido(unittest.TestCase):
    """Com rasterio instalado, quem decodifica e' o GDAL — dez vezes mais
       rapido. So' vale se pousar o pixel exatamente onde o caminho lento
       pousa: a geometria, o datum e o recorte tem que ser os mesmos.
       Um erro aqui seria a foto inteira deslocada, e ninguem compara ladrilho
       a ladrilho na mao."""

    @classmethod
    def setUpClass(cls):
        if mf.foto_rapida is None or not mf.foto_rapida.disponivel():
            raise unittest.SkipTest("sem rasterio nesta maquina")
        cls.dir = tempfile.mkdtemp(prefix="rapido-")
        cls.db = os.path.join(cls.dir, "mina.db")
        gb.gerar(cls.db)
        cls.malha = sr.Malha(cls.db)
        utm = mf.projecao_utm(23, "S")
        lim = mf.limites_da_malha(cls.malha, folga=0)
        cls.centro = ((lim[0] + lim[2]) / 2, (lim[1] + lim[3]) / 2)
        cantos = [utm.para_grid(*cls.malha.proj.para_wgs84(e, n))
                  for e in (lim[0], lim[2]) for n in (lim[1], lim[3])]
        X0 = min(c[0] for c in cantos); Y1 = max(c[1] for c in cantos)
        res = 4.0
        W = int((max(c[0] for c in cantos) - X0) / res)
        H = int((Y1 - min(c[1] for c in cantos)) / res)
        Xc, Yc = utm.para_grid(*cls.malha.proj.para_wgs84(*cls.centro))
        marca = (int((Xc - X0) / res), int((Y1 - Yc) / res))
        # marcas pequenas e espalhadas: e' a posicao delas que denuncia
        # qualquer deslocamento entre os dois caminhos
        marcas = [(marca[0], marca[1], (255, 0, 255), 3),
                  (int(W * 0.30), int(H * 0.25), (255, 0, 255), 3),
                  (int(W * 0.65), int(H * 0.70), (255, 0, 255), 3)]
        px = gg.imagem_de_teste(W, H, marcas)
        tif = os.path.join(cls.dir, "orto.tif")
        gg.escrever(tif, W, H, px, (res, res), (X0, Y1), epsg=31983,
                    compressao=8, por_faixa=32)

        limites = mf.limites_da_malha(cls.malha)
        cls.saida_r = os.path.join(cls.dir, "rapido")
        cls.man_r = mf.gerar_mosaico([tif], cls.malha.proj, cls.saida_r,
                                     proj_foto=utm, limites=limites,
                                     mais_fino=8.0, registrar=lambda *a: None)
        # o mesmo, com o atalho desligado
        guardado = mf.foto_rapida
        mf.foto_rapida = None
        try:
            cls.saida_l = os.path.join(cls.dir, "lento")
            cls.man_l = mf.gerar_mosaico([tif], cls.malha.proj, cls.saida_l,
                                         proj_foto=utm, limites=limites,
                                         mais_fino=8.0, registrar=lambda *a: None)
        finally:
            mf.foto_rapida = guardado

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_os_dois_caminhos_geram_o_mesmo_mosaico(self):
        for chave in ("grid", "ladrilho", "niveis", "formato"):
            self.assertEqual(self.man_r[chave], self.man_l[chave],
                             f"{chave} diferente entre os dois caminhos")
        self.assertEqual(self.man_r["cantos"], self.man_l["cantos"])

    def test_os_mesmos_ladrilhos_existem(self):
        def conjunto(saida, man):
            achados = set()
            for nv in man["niveis"]:
                for tx in range(nv["cols"]):
                    for ty in range(nv["linhas"]):
                        for ext in ("jpg", "png"):
                            if os.path.exists(os.path.join(
                                    saida, str(nv["z"]), str(tx), f"{ty}.{ext}")):
                                achados.add((nv["z"], tx, ty))
            return achados
        self.assertEqual(conjunto(self.saida_r, self.man_r),
                         conjunto(self.saida_l, self.man_l))

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_a_marca_cai_no_mesmo_pixel_nos_dois(self):
        """O teste que importa: a mesma marca, no mesmo lugar, nos dois."""
        nv = self.man_r["niveis"][-1]
        L = self.man_r["ladrilho"]; g = self.man_r["grid"]
        Ec, Nc = self.centro
        gx = (Ec - g["E0"]) / nv["m_por_px"]
        gy = (g["N1"] - Nc) / nv["m_por_px"]
        tx, ty = int(gx // L), int(gy // L)
        ix, iy = int(gx) - tx * L, int(gy) - ty * L
        for saida, quem in ((self.saida_r, "rapido"), (self.saida_l, "lento")):
            caminho = None
            for ext in ("jpg", "png"):
                c = os.path.join(saida, str(nv["z"]), str(tx), f"{ty}.{ext}")
                if os.path.exists(c):
                    caminho = c
                    break
            self.assertIsNotNone(caminho, f"{quem}: sem ladrilho da marca")
            r, v, a = Image.open(caminho).convert("RGB").getpixel((ix, iy))
            self.assertGreater(r, 170, f"{quem}: a marca nao esta' aqui")
            self.assertLess(v, 90, f"{quem}: cor errada")
            self.assertGreater(a, 170, f"{quem}: cor errada")

    def _marcas(self, saida, man):
        """Onde as marcas vermelhas cairam, em pixel do mosaico."""
        nv = man["niveis"][-1]
        L = man["ladrilho"]
        pontos = []
        for tx in range(nv["cols"]):
            for ty in range(nv["linhas"]):
                caminho = None
                for ext in ("jpg", "png"):
                    c = os.path.join(saida, str(nv["z"]), str(tx), f"{ty}.{ext}")
                    if os.path.exists(c):
                        caminho = c
                        break
                if caminho is None:
                    continue
                im = Image.open(caminho).convert("RGB")
                for x in range(im.width):
                    for y in range(im.height):
                        r, v, a = im.getpixel((x, y))
                        # o fundo do gradiente tem azul fixo em 60; so' a
                        # marca passa de 200
                        if r > 150 and v < 90 and a > 200:
                            pontos.append((tx * L + x, ty * L + y))
        return pontos

    def _agrupa(self, pontos):
        """Centro de cada mancha vermelha."""
        grupos = []
        for p in pontos:
            for g in grupos:
                if abs(g[0][0] - p[0]) < 30 and abs(g[0][1] - p[1]) < 30:
                    g.append(p)
                    break
            else:
                grupos.append([p])
        centros = [(sum(q[0] for q in g) / len(g), sum(q[1] for q in g) / len(g))
                   for g in grupos]
        return sorted(centros)

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_as_marcas_caem_na_mesma_posicao_nos_dois(self):
        """O teste que importa. Comparar cor media nao serve: numa imagem
           suave, deslocar seis pixels quase nao muda valor nenhum — foi o que
           deixou passar a primeira versao deste teste. Aqui se mede ONDE a
           marca caiu, e um pixel de diferenca ja' aparece."""
        a = self._agrupa(self._marcas(self.saida_r, self.man_r))
        b = self._agrupa(self._marcas(self.saida_l, self.man_l))
        self.assertTrue(a, "nenhuma marca achada no caminho rapido")
        self.assertEqual(len(a), len(b),
                         f"marcas em quantidade diferente: {len(a)} x {len(b)}")
        for (ax, ay), (bx, by) in zip(a, b):
            d = math.hypot(ax - bx, ay - by)
            self.assertLess(d, 1.5,
                            f"marca deslocada {d:.1f} px entre os caminhos "
                            f"({ax:.1f},{ay:.1f}) x ({bx:.1f},{by:.1f})")


class TesteFundo(unittest.TestCase):
    """Imagem de fundo para tapar onde o levantamento nao chega.

       A regra que importa: onde ha ortofoto, e' o ortofoto que aparece. Um
       fundo de satelite tem dezenas de metros por pixel e a data que tiver;
       deixar ele por cima da cava seria trocar o levantamento da semana por
       uma imagem velha e borrada — em mina, isso e' pior que buraco preto."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="fundo-")
        cls.db = os.path.join(cls.dir, "mina.db")
        gb.gerar(cls.db)
        cls.malha = sr.Malha(cls.db)
        cls.utm = mf.projecao_utm(23, "S")
        lim = mf.limites_da_malha(cls.malha, folga=0)
        cls.lim = lim

        def escreve(nome, e0, n0, e1, n1, res, cor, amostras=3):
            cantos = [cls.utm.para_grid(*cls.malha.proj.para_wgs84(e, n))
                      for e in (e0, e1) for n in (n0, n1)]
            X0 = min(c[0] for c in cantos); Y1 = max(c[1] for c in cantos)
            W = max(4, int((max(c[0] for c in cantos) - X0) / res))
            H = max(4, int((Y1 - min(c[1] for c in cantos)) / res))
            px = bytes(cor) * (W * H)
            caminho = os.path.join(cls.dir, nome)
            gg.escrever(caminho, W, H, px, (res, res), (X0, Y1), epsg=31983,
                        compressao=8, por_faixa=64, amostras=amostras)
            return caminho

        # levantamento: so' a metade norte, fino e vermelho
        meio = (lim[1] + lim[3]) / 2
        cls.orto = escreve("orto.tif", lim[0], meio, lim[2], lim[3], 4.0,
                           (220, 40, 40))
        # fundo: a mina toda, grosso e azul
        cls.fundo = escreve("fundo.tif", lim[0], lim[1], lim[2], lim[3], 20.0,
                            (40, 40, 220))

        cls.saida = os.path.join(cls.dir, "foto")
        cls.man = mf.gerar_mosaico([cls.orto], cls.malha.proj, cls.saida,
                                   fundos=[cls.fundo], mais_fino=8.0,
                                   registrar=lambda *a: None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_a_resolucao_vem_do_levantamento_e_nao_do_fundo(self):
        """O fundo tem 20 m/px. Se ele mandasse na escolha, o mapa inteiro
           perderia detalhe por causa da imagem de tapar buraco."""
        fino = self.man["niveis"][-1]["m_por_px"]
        self.assertLessEqual(fino, 8.0)

    def _cor(self, E, N):
        nv = self.man["niveis"][-1]
        L = self.man["ladrilho"]; g = self.man["grid"]
        gx = (E - g["E0"]) / nv["m_por_px"]; gy = (g["N1"] - N) / nv["m_por_px"]
        tx, ty = int(gx // L), int(gy // L)
        ix, iy = int(gx) - tx * L, int(gy) - ty * L
        for ext in ("jpg", "png"):
            c = os.path.join(self.saida, str(nv["z"]), str(tx), f"{ty}.{ext}")
            if os.path.exists(c):
                return Image.open(c).convert("RGBA").getpixel((ix, iy))
        return None

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_onde_ha_levantamento_o_fundo_nao_aparece(self):
        E = (self.lim[0] + self.lim[2]) / 2
        N = self.lim[1] + (self.lim[3] - self.lim[1]) * 0.75    # metade norte
        p = self._cor(E, N)
        self.assertIsNotNone(p, "sem ladrilho onde ha levantamento")
        self.assertGreater(p[0], 150, "o fundo cobriu o levantamento")
        self.assertLess(p[2], 110)

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_onde_nao_ha_levantamento_o_fundo_preenche(self):
        E = (self.lim[0] + self.lim[2]) / 2
        N = self.lim[1] + (self.lim[3] - self.lim[1]) * 0.25    # metade sul
        p = self._cor(E, N)
        self.assertIsNotNone(p, "o fundo nao gerou ladrilho onde faltava foto")
        self.assertGreater(p[3], 200, "ficou transparente em vez de preencher")
        self.assertGreater(p[2], 150, "nao e' o fundo que esta' aqui")
        self.assertLess(p[0], 110)


class TesteRotaDaFoto(unittest.TestCase):
    """O servidor entregando os ladrilhos. Nao precisa de mosaico de verdade:
       o que se testa aqui e' a rota, o tipo do conteudo e a recusa de
       caminho que sobe de pasta."""

    @classmethod
    def setUpClass(cls):
        import json as _json
        import subprocess
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from teste_servidor import banco
        from teste_cliente_ptx import porta_livre

        cls.dir = tempfile.mkdtemp(prefix="rota-foto-")
        cls.foto = os.path.join(cls.dir, "foto")
        os.makedirs(os.path.join(cls.foto, "0", "0"))
        with open(os.path.join(cls.foto, "manifesto.json"), "w") as f:
            _json.dump({"versao": 1, "ladrilho": 256, "formato": "jpg",
                        "grid": {"E0": 0, "N0": 0, "E1": 1, "N1": 1},
                        "cantos": {"no": [0, 0], "ne": [1, 0], "so": [0, -1]},
                        "niveis": [{"z": 0, "m_por_px": 1, "cols": 1,
                                    "linhas": 1}]}, f)
        cls.conteudo = b"\xff\xd8ladrilho de mentira\xff\xd9"
        with open(os.path.join(cls.foto, "0", "0", "0.jpg"), "wb") as f:
            f.write(cls.conteudo)
        with open(os.path.join(cls.dir, "segredo.txt"), "w") as f:
            f.write("nao deve sair pela rota da foto")

        cls.porta = porta_livre()
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(RAIZ, "servidor", "servidor_rotas.py"),
             "--db", banco(), "--porta", str(cls.porta), "--endereco", "127.0.0.1",
             "--intervalo", "3600", "--foto", cls.foto,
             "--desmontes", os.path.join(cls.dir, "d.json")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        import http.client, time as _t
        fim = _t.time() + 60
        while _t.time() < fim:
            try:
                c = http.client.HTTPConnection("127.0.0.1", cls.porta, timeout=5)
                c.request("GET", "/api/saude")
                c.getresponse().read()
                c.close()
                break
            except Exception:
                _t.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate(); cls.proc.wait(timeout=10)
        except Exception:
            pass
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _pede(self, caminho):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=10)
        c.request("GET", caminho)
        r = c.getresponse()
        corpo = r.read()
        tipo = r.getheader("Content-Type") or ""
        cache = r.getheader("Cache-Control") or ""
        c.close()
        return r.status, tipo, cache, corpo

    def test_serve_o_manifesto(self):
        st, tipo, _c, corpo = self._pede("/foto/manifesto.json")
        self.assertEqual(st, 200)
        self.assertIn("json", tipo)
        import json as _json
        self.assertEqual(_json.loads(corpo)["ladrilho"], 256)

    def test_serve_o_ladrilho_como_imagem(self):
        st, tipo, cache, corpo = self._pede("/foto/0/0/0.jpg")
        self.assertEqual(st, 200)
        self.assertEqual(tipo, "image/jpeg")
        self.assertEqual(corpo, self.conteudo)
        # ladrilho nao muda depois de gerado: o tablet baixa uma vez so'
        self.assertIn("max-age=31536000", cache)

    def test_ladrilho_que_nao_existe_da_404(self):
        st, _t, _c, _b = self._pede("/foto/7/7/7.jpg")
        self.assertEqual(st, 404)

    def test_nao_deixa_subir_de_pasta(self):
        """A rota entrega arquivo de disco; sem esta trava, uma URL com '..'
           leria qualquer coisa da maquina do servidor."""
        for caminho in ("/foto/../segredo.txt", "/foto/0/../../segredo.txt",
                        "/foto/%2e%2e/segredo.txt"):
            st, _t, _c, corpo = self._pede(caminho)
            self.assertNotEqual(st, 200, f"{caminho} devolveu conteudo")
            self.assertNotIn(b"nao deve sair", corpo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
