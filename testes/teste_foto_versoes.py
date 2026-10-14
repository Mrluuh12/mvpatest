#!/usr/bin/env python3
"""
Atualizar a foto aerea sem mexer em nada: copiou o .tif para a pasta das
ortofotos, a foto se refaz sozinha e o servidor passa a servi-la.

O que se cobra aqui:
- cada conversao vai para uma versao propria, e o manifesto do topo so'
  aponta para ela quando esta' inteira;
- onde duas folhas se sobrepoem, a mais nova fica por cima;
- a vigia nao converte arquivo ainda sendo copiado, nao reconverte a cada
  minuto uma folha que nao abre, e nao entra em laco quando a conversao
  derruba o processo;
- o servidor no ar troca de foto sem reiniciar.
"""

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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

VERMELHO, VERDE, AZUL = (220, 40, 40), (40, 220, 40), (40, 40, 220)


def folha(caminho, malha, cor, data=None, frac=(0.0, 1.0)):
    """Um .tif de uma cor so', cobrindo a faixa `frac` (sul -> norte) da mina."""
    utm = mf.projecao_utm(23, "S")
    lim = mf.limites_da_malha(malha, folga=0)
    n0 = lim[1] + (lim[3] - lim[1]) * frac[0]
    n1 = lim[1] + (lim[3] - lim[1]) * frac[1]
    cantos = [utm.para_grid(*malha.proj.para_wgs84(e, n))
              for e in (lim[0], lim[2]) for n in (n0, n1)]
    X0 = min(c[0] for c in cantos); Y1 = max(c[1] for c in cantos)
    res = 16.0
    W = max(4, int((max(c[0] for c in cantos) - X0) / res))
    H = max(4, int((Y1 - min(c[1] for c in cantos)) / res))
    gg.escrever(caminho, W, H, bytes(cor) * (W * H), (res, res), (X0, Y1),
                epsg=31983, compressao=8, por_faixa=32)
    if data is not None:
        os.utime(caminho, (data, data))
    return caminho


def cor_no_meio(raiz, man):
    """Cor, no nivel mais fino da versao em uso, do ponto central da area."""
    nv, g, L = man["niveis"][-1], man["grid"], man["ladrilho"]
    px = ((g["E0"] + g["E1"]) / 2 - g["E0"]) / nv["m_por_px"]
    py = (g["N1"] - (g["N0"] + g["N1"]) / 2) / nv["m_por_px"]
    tx, ty = int(px // L), int(py // L)
    for ext in ("jpg", "png"):
        c = os.path.join(raiz, man["id"], str(nv["z"]), str(tx), f"{ty}.{ext}")
        if os.path.exists(c):
            return Image.open(c).convert("RGB").getpixel((int(px % L), int(py % L)))
    return None


def parecida(cor, alvo):
    return all(abs(a - b) < 40 for a, b in zip(cor, alvo))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="foto-versoes-")
        cls.db = os.path.join(cls.dir, "mina.db")
        gb.gerar(cls.db)
        cls.malha = sr.Malha(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self.caso = tempfile.mkdtemp(dir=self.dir)
        self.tifs = os.path.join(self.caso, "ortofotos")
        self.raiz = os.path.join(self.caso, "foto")
        os.makedirs(self.tifs)

    def publica(self):
        return mf.publicar_foto(mf.achar_tifs([self.tifs]), self.malha.proj,
                                self.raiz, limites=mf.limites_da_malha(self.malha),
                                mais_fino=32.0, registrar=lambda *a: None)


class TestePublicar(Base):

    def test_versao_nova_em_pasta_propria_e_o_topo_aponta_para_ela(self):
        folha(os.path.join(self.tifs, "a.tif"), self.malha, VERMELHO)
        man = self.publica()
        topo = mf.manifesto_em_uso(self.raiz)
        self.assertEqual(topo["id"], man["id"])
        self.assertRegex(topo["id"], mf.PADRAO_VERSAO)
        self.assertTrue(os.path.isdir(os.path.join(self.raiz, man["id"], "0")))
        self.assertEqual([f[0] for f in topo["fontes"]], ["a.tif"])
        # o app le o id com uma busca simples de texto: tem que vir primeiro
        with open(os.path.join(self.raiz, "manifesto.json"), encoding="utf-8") as f:
            self.assertTrue(f.read().lstrip("{ \n").startswith('"id"'))

    @unittest.skipIf(Image is None, "sem Pillow nesta maquina")
    def test_a_folha_mais_nova_fica_por_cima(self):
        """Voo novo de uma area e' para mostrar o que mudou ali. Se a velha
           ganhasse na sobreposicao, a atualizacao nao apareceria."""
        agora = time.time()
        velha = folha(os.path.join(self.tifs, "z_velha.tif"), self.malha,
                      VERMELHO, data=agora - 86400 * 30)
        nova = folha(os.path.join(self.tifs, "a_nova.tif"), self.malha,
                     VERDE, data=agora - 3600)
        self.assertTrue(parecida(cor_no_meio(self.raiz, self.publica()), VERDE),
                        "a folha velha ficou por cima da nova")
        # inverte as datas: agora a vermelha e' a nova
        os.utime(velha, (agora, agora))
        self.assertTrue(parecida(cor_no_meio(self.raiz, self.publica()), VERMELHO))

    def test_guarda_a_anterior_e_apaga_as_mais_velhas(self):
        folha(os.path.join(self.tifs, "a.tif"), self.malha, VERMELHO)
        os.makedirs(os.path.join(self.raiz, "0", "0"))      # formato antigo
        ids = [self.publica()["id"] for _ in range(3)]
        sobra = sorted(n for n in os.listdir(self.raiz)
                       if os.path.isdir(os.path.join(self.raiz, n)))
        self.assertEqual(sobra, sorted(ids[1:]),
                         "devia ficar so' a versao em uso e a anterior")

    def test_conversao_que_falha_nao_mexe_na_foto_em_uso(self):
        folha(os.path.join(self.tifs, "a.tif"), self.malha, VERMELHO)
        antes = self.publica()
        lixo = os.path.join(self.caso, "lixo")
        os.makedirs(lixo)
        with open(os.path.join(lixo, "quebrada.tif"), "wb") as f:
            f.write(b"isto nao e um TIFF")
        with self.assertRaises(Exception):
            mf.publicar_foto([os.path.join(lixo, "quebrada.tif")], self.malha.proj,
                             self.raiz, limites=mf.limites_da_malha(self.malha),
                             mais_fino=32.0, registrar=lambda *a: None)
        self.assertEqual(mf.manifesto_em_uso(self.raiz)["id"], antes["id"])
        self.assertEqual(sorted(n for n in os.listdir(self.raiz)
                                if os.path.isdir(os.path.join(self.raiz, n))),
                         [antes["id"]], "ficou meia versao no disco")


class TesteVigia(Base):

    def vigia(self):
        return mf.Vigia(self.tifs, self.raiz, lambda: self.malha,
                        lambda *a: None, opcoes={"mais_fino": 32.0})

    def test_pasta_vazia_ou_inexistente_nao_converte(self):
        v = self.vigia()
        self.assertEqual(v.rodada(), "vazia")
        shutil.rmtree(self.tifs)
        self.assertEqual(v.rodada(), "sem pasta")
        self.assertIsNone(mf.manifesto_em_uso(self.raiz))

    def test_arquivo_novo_vira_foto_depois_de_estabilizar(self):
        v = self.vigia()
        folha(os.path.join(self.tifs, "a.tif"), self.malha, VERMELHO)
        self.assertEqual(v.rodada(), "esperando",
                         "converteu na primeira olhada: o arquivo podia "
                         "estar sendo copiado ainda")
        self.assertEqual(v.rodada(), "converteu")
        self.assertEqual(v.rodada(), "em dia")
        self.assertEqual(v.estado()["em_uso"], mf.manifesto_em_uso(self.raiz)["id"])

    def test_arquivo_crescendo_nao_e_convertido(self):
        v = self.vigia()
        caminho = folha(os.path.join(self.tifs, "a.tif"), self.malha, VERMELHO)
        for i in range(3):
            with open(caminho, "ab") as f:               # a copia continua
                f.write(b"\0" * 1000)
            self.assertEqual(v.rodada(), "esperando")
        self.assertEqual(v.rodada(), "converteu")

    def test_folha_nova_ao_lado_das_velhas_refaz_a_foto(self):
        v = self.vigia()
        folha(os.path.join(self.tifs, "a.tif"), self.malha, VERMELHO,
              data=time.time() - 86400)
        v.rodada(); v.rodada()
        primeira = mf.manifesto_em_uso(self.raiz)["id"]
        folha(os.path.join(self.tifs, "b.tif"), self.malha, VERDE,
              frac=(0.4, 0.6))
        self.assertEqual(v.rodada(), "esperando")
        self.assertEqual(v.rodada(), "converteu")
        topo = mf.manifesto_em_uso(self.raiz)
        self.assertNotEqual(topo["id"], primeira)
        self.assertEqual(topo["origem"][0], "b.tif", "a mais nova vem primeiro")

    def test_folha_que_nao_abre_nao_reconverte_a_cada_minuto(self):
        v = self.vigia()
        folha(os.path.join(self.tifs, "a.tif"), self.malha, VERMELHO)
        with open(os.path.join(self.tifs, "quebrada.tif"), "wb") as f:
            f.write(b"isto nao e um TIFF")
        v.rodada()
        self.assertEqual(v.rodada(), "converteu")
        self.assertEqual(mf.manifesto_em_uso(self.raiz)["puladas"], ["quebrada.tif"])
        for _ in range(3):
            self.assertEqual(v.rodada(), "em dia")

    def test_so_arquivo_ruim_falha_uma_vez_e_para(self):
        v = self.vigia()
        with open(os.path.join(self.tifs, "quebrada.tif"), "wb") as f:
            f.write(b"isto nao e um TIFF")
        v.rodada()
        self.assertEqual(v.rodada(), "falhou")
        self.assertEqual(v.rodada(), "falhou antes")
        # servidor reiniciado: a marca no disco continua valendo
        self.assertEqual(self.vigia().rodada(), "falhou antes")

    def test_conversao_que_derruba_o_processo_desiste_na_segunda(self):
        """O servidor reinicia sozinho quando cai. Se a conversao fosse o que
           derrubou, ele cairia de novo a cada reinicio, para sempre."""
        folha(os.path.join(self.tifs, "a.tif"), self.malha, VERMELHO)
        assin = mf.assinatura(mf.achar_tifs([self.tifs]))
        os.makedirs(self.raiz)
        with open(os.path.join(self.raiz, mf.Vigia.MARCA), "w") as f:
            json.dump({"fontes": assin, "tentativas": 1}, f)
        v = self.vigia()
        v.rodada()
        self.assertEqual(v.rodada(), "converteu",
                         "uma queda so' (falta de luz) nao pode bastar para desistir")
        with open(os.path.join(self.raiz, mf.Vigia.MARCA), "w") as f:
            json.dump({"fontes": [["outra.tif", 1, 1]], "tentativas": 9}, f)
        folha(os.path.join(self.tifs, "b.tif"), self.malha, VERDE)
        assin = mf.assinatura(mf.achar_tifs([self.tifs]))
        with open(os.path.join(self.raiz, mf.Vigia.MARCA), "w") as f:
            json.dump({"fontes": assin, "tentativas": 2}, f)
        v = self.vigia()
        self.assertEqual(v.rodada(), "desistiu")

    def test_tirar_os_tif_depois_de_converter_nao_apaga_a_foto(self):
        v = self.vigia()
        folha(os.path.join(self.tifs, "a.tif"), self.malha, VERMELHO)
        v.rodada(); v.rodada()
        em_uso = mf.manifesto_em_uso(self.raiz)["id"]
        os.remove(os.path.join(self.tifs, "a.tif"))
        self.assertEqual(v.rodada(), "vazia")
        self.assertEqual(mf.manifesto_em_uso(self.raiz)["id"], em_uso)


class TesteServidorTrocaSemReiniciar(unittest.TestCase):
    """O servidor de verdade, com a vigia olhando a pasta a cada segundo."""

    @classmethod
    def setUpClass(cls):
        from teste_cliente_ptx import porta_livre
        cls.dir = tempfile.mkdtemp(prefix="foto-servidor-")
        cls.db = os.path.join(cls.dir, "mina.db")
        gb.gerar(cls.db)
        cls.malha = sr.Malha(cls.db)
        cls.tifs = os.path.join(cls.dir, "ortofotos")
        cls.raiz = os.path.join(cls.dir, "foto")
        os.makedirs(cls.tifs)
        cls.porta = porta_livre()
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(RAIZ, "servidor", "servidor_rotas.py"),
             "--db", cls.db, "--porta", str(cls.porta), "--endereco", "127.0.0.1",
             "--intervalo", "3600", "--foto", cls.raiz, "--ortofotos", cls.tifs,
             "--intervalo-ortofotos", "1", "--mais-fino", "32",
             "--desmontes", os.path.join(cls.dir, "d.json")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        fim = time.time() + 60
        while time.time() < fim:
            try:
                if cls.pede("/api/saude")[0] == 200:
                    break
            except Exception:
                time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate(); cls.proc.wait(timeout=10)
            cls.proc.stdout.close()
        except Exception:
            pass
        shutil.rmtree(cls.dir, ignore_errors=True)

    @classmethod
    def pede(cls, caminho):
        c = http.client.HTTPConnection("127.0.0.1", cls.porta, timeout=10)
        c.request("GET", caminho)
        r = c.getresponse()
        corpo = r.read()
        c.close()
        return r.status, corpo

    def espera_id(self, diferente_de=None, limite=60):
        fim = time.time() + limite
        while time.time() < fim:
            st, corpo = self.pede("/foto/manifesto.json")
            if st == 200:
                m = json.loads(corpo)
                if m.get("id") and m["id"] != diferente_de:
                    return m
            time.sleep(0.3)
        raise AssertionError("a foto nova nao apareceu no servidor")

    def test_copiar_o_tif_basta(self):
        self.assertEqual(self.pede("/foto/manifesto.json")[0], 404,
                         "ainda nao ha foto: o manifesto tinha que dar 404")
        folha(os.path.join(self.tifs, "a.tif"), self.malha, VERMELHO,
              data=time.time() - 86400)
        m = self.espera_id()
        nv = m["niveis"][0]
        k = f"{nv['z']}/0/0"
        ext = "png" if k in m.get("png", []) else "jpg"
        st, corpo = self.pede(f"/foto/{m['id']}/{k}.{ext}")
        self.assertEqual(st, 200, "o ladrilho da versao nova nao e' servido")
        saude = json.loads(self.pede("/api/saude")[1])
        self.assertEqual(saude["foto"]["em_uso"], m["id"])
        self.assertEqual(saude["foto"]["situacao"], "em dia")

        # voo novo de uma area, com o servidor no ar
        folha(os.path.join(self.tifs, "b.tif"), self.malha, VERDE, frac=(0.4, 0.6))
        m2 = self.espera_id(diferente_de=m["id"])
        self.assertEqual(m2["origem"][0], "b.tif")
        # quem ainda esta' na versao anterior continua sendo atendido
        self.assertEqual(self.pede(f"/foto/{m['id']}/{k}.{ext}")[0], 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
