#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testes da area de exclusao de desmonte.

O que se cadastra aqui vira decisao de tirar maquina do lugar antes do fogo:
a distancia tem que ser medida na grid da mina, e o dado gravado tem que ser
exatamente o que foi cadastrado.
"""
import importlib.util, json, os, shutil, sys, tempfile, unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "testes"))
from teste_servidor import sr, banco

_spec = importlib.util.spec_from_file_location(
    "desmontes", os.path.join(RAIZ, "servidor", "desmontes.py"))
dm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dm)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.malha = sr.Malha(banco())
        cls.proj = cls.malha.proj

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dm-")
        self.arq = os.path.join(self.dir, "desmontes.json")
        self.d = dm.Desmontes(self.arq, projecao=self.proj)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TesteCadastro(Base):
    def test_grid_e_geo_dao_o_mesmo_ponto(self):
        """Topografia publica em grid; o GPS le' em graus. Os dois caminhos
           tem que cair no mesmo lugar."""
        e, n = 665139.64, 7910269.13
        a = self.d.adicionar({"nome": "por grid", "grid_e": e, "grid_n": n})
        b = self.d.adicionar({"nome": "por geo", "lat": a["lat"], "lon": a["lon"]})
        self.assertAlmostEqual(a["lat"], b["lat"], places=6)
        self.assertAlmostEqual(a["grid_e"], b["grid_e"], delta=0.5)
        self.assertAlmostEqual(a["grid_n"], b["grid_n"], delta=0.5)

    def test_raio_padrao_e_400(self):
        a = self.d.adicionar({"nome": "x", "lat": -18.89, "lon": -43.43})
        self.assertEqual(a["raio_m"], 400.0)

    def test_recusa_dado_ruim(self):
        for dados, oque in [
                ({"lat": -18.9, "lon": -43.4}, "sem nome"),
                ({"nome": "x"}, "sem coordenada"),
                ({"nome": "x", "lat": -18.9, "lon": -43.4, "raio_m": "abc"}, "raio texto"),
                ({"nome": "x", "lat": -18.9, "lon": -43.4, "raio_m": 0}, "raio zero"),
                ({"nome": "x", "lat": 200, "lon": -43.4}, "latitude impossivel")]:
            with self.assertRaises(dm.ErroDeCadastro, msg=oque):
                self.d.adicionar(dados)

    def test_grava_e_recarrega(self):
        self.d.adicionar({"nome": "Bancada 880", "lat": -18.89, "lon": -43.43})
        outro = dm.Desmontes(self.arq, projecao=self.proj)
        self.assertEqual(len(outro.listar()), 1)
        self.assertEqual(outro.listar()[0]["nome"], "Bancada 880")

    def test_arquivo_corrompido_nao_derruba(self):
        with open(self.arq, "w") as f:
            f.write("{isto nao e json")
        outro = dm.Desmontes(self.arq, projecao=self.proj)
        self.assertEqual(outro.listar(), [])

    def test_encerrar_tira_do_ativo_mas_guarda(self):
        a = self.d.adicionar({"nome": "x", "lat": -18.89, "lon": -43.43})
        self.d.remover(a["id"])
        self.assertEqual(len(self.d.listar()), 0)
        self.assertEqual(len(self.d.listar(apenas_ativos=False)), 1)
        self.assertIn("removido_em", self.d.listar(apenas_ativos=False)[0])


class TesteAfetados(Base):
    def equip(self, nome, lat, lon, movel=True, tipo="caminhao"):
        return {"nome": nome, "tipo": tipo, "movel": movel, "tem_fix": True,
                "lat": lat, "lon": lon, "idade_s": 1}

    def test_quem_esta_dentro_do_raio(self):
        centro = self.d.adicionar({"nome": "fogo", "lat": -18.8935,
                                   "lon": -43.4325, "raio_m": 400})
        # 200 m ao norte: dentro. 1 km: fora.
        perto = self.equip("ERM-12", -18.8935 + 200/110540.0, -43.4325,
                           movel=False, tipo="repetidora")
        longe = self.equip("CA-1022", -18.8935 + 1000/110540.0, -43.4325)
        a = self.d.afetados(centro, [perto, longe])
        self.assertEqual([r["nome"] for r in a["dentro"]], ["ERM-12"])
        self.assertEqual(a["n_erms_dentro"], 1)
        self.assertEqual(a["n_moveis_dentro"], 0)
        self.assertAlmostEqual(a["dentro"][0]["distancia_m"], 200, delta=15)

    def test_avisa_quem_esta_logo_fora(self):
        centro = self.d.adicionar({"nome": "fogo", "lat": -18.8935,
                                   "lon": -43.4325, "raio_m": 400})
        borda = self.equip("TT-05", -18.8935 + 480/110540.0, -43.4325)
        a = self.d.afetados(centro, [borda])
        self.assertEqual(a["dentro"], [])
        self.assertEqual([r["nome"] for r in a["perto"]], ["TT-05"])

    def test_equipamento_sem_fix_nao_entra_na_conta(self):
        centro = self.d.adicionar({"nome": "fogo", "lat": -18.8935, "lon": -43.4325})
        cego = self.equip("CA-9", -18.8935, -43.4325)
        cego["tem_fix"] = False
        self.assertEqual(self.d.afetados(centro, [cego])["dentro"], [])

    def test_ordena_do_mais_proximo(self):
        centro = self.d.adicionar({"nome": "fogo", "lat": -18.8935, "lon": -43.4325})
        eqs = [self.equip("longe", -18.8935 + 300/110540.0, -43.4325),
               self.equip("perto", -18.8935 + 50/110540.0, -43.4325)]
        a = self.d.afetados(centro, eqs)
        self.assertEqual([r["nome"] for r in a["dentro"]], ["perto", "longe"])

    def test_locais_da_mina_dentro_do_raio(self):
        centro = self.d.adicionar({"nome": "fogo", "lat": -18.8935,
                                   "lon": -43.4325, "raio_m": 400})
        a = self.d.afetados(centro, [], self.malha.locais)
        for r in a["locais_dentro"]:
            self.assertLessEqual(r["distancia_m"], 400)

    def test_distancia_usa_a_grid_da_mina(self):
        """Nao e' aproximacao de graus: 400 m e' limite operacional."""
        centro = self.d.adicionar({"nome": "fogo", "grid_e": 665139.64,
                                   "grid_n": 7910269.13, "raio_m": 400})
        lat, lon = self.proj.para_wgs84(665139.64 + 300, 7910269.13)
        self.assertAlmostEqual(self.d.distancia_m(centro, lat, lon), 300, delta=1.0)
        lat, lon = self.proj.para_wgs84(665139.64, 7910269.13 + 250)
        self.assertAlmostEqual(self.d.distancia_m(centro, lat, lon), 250, delta=1.0)



class TesteListaColada(Base):
    """O formato que a topografia publica: NOME (E,N), uma linha por furo."""

    REAL = """CS_0020_091 (667401.967,7904915.048)
CC_0910_071 (666889.001,7905699.105)
CC_0930_074 (666516.203,7905510.856)
CC_0835_074_P2 (665898.445,7905482.311)
CC_0830_053_P3 (666841.163,7904485.937)"""

    def test_le_o_formato_da_topografia(self):
        pontos, recusadas = dm.interpretar_pontos(self.REAL)
        self.assertEqual(len(pontos), 5)
        self.assertEqual(recusadas, [])
        self.assertEqual(pontos[0]["nome"], "CS_0020_091")
        self.assertAlmostEqual(pontos[0]["grid_e"], 667401.967, places=3)
        self.assertAlmostEqual(pontos[0]["grid_n"], 7904915.048, places=3)
        # nome com digito dentro nao pode virar coordenada
        self.assertEqual(pontos[3]["nome"], "CC_0835_074_P2")
        self.assertAlmostEqual(pontos[3]["grid_e"], 665898.445, places=3)

    def test_aceita_outros_separadores(self):
        pontos, _ = dm.interpretar_pontos(
            "A 667401.967 7904915.048\nB;666889.001;7905699.105\n"
            "666516.203, 7905510.856")
        self.assertEqual(len(pontos), 3)
        self.assertEqual(pontos[0]["nome"], "A")
        self.assertEqual(pontos[2]["nome"], "")

    def test_linha_ruim_e_recusada_e_nao_inventada(self):
        pontos, recusadas = dm.interpretar_pontos(
            "CS_0020_091 (667401.967,7904915.048)\nfurto de linha\nP9 (12,34)")
        self.assertEqual(len(pontos), 1)
        self.assertEqual(len(recusadas), 2)
        self.assertIn("pequena demais", recusadas[1]["motivo"])

    def test_cadastra_os_cinco_pontos(self):
        d = self.d.adicionar({"nome": "Desmonte da semana", "pontos": self.REAL})
        self.assertEqual(d["n_pontos"], 5)
        self.assertEqual(d["recusadas"], [])
        for p in d["pontos"]:
            self.assertIsNotNone(p["lat"])
            self.assertIsNotNone(p["grid_e"])
        # o centro fica no meio dos furos
        self.assertLess(abs(d["grid_e"] - 666709), 200)

    def test_raio_vale_de_CADA_furo(self):
        """Desmonte espalhado tem area maior que um circulo so'. Um ponto a
           300 m de UM furo esta' dentro, mesmo longe do centro."""
        d = self.d.adicionar({"nome": "x", "pontos": self.REAL, "raio_m": 400})
        # 300 m ao norte do primeiro furo
        lat, lon = self.proj.para_wgs84(667401.967, 7904915.048 + 300)
        self.assertAlmostEqual(self.d.distancia_m(d, lat, lon), 300, delta=2)
        # o centro do conjunto fica a mais de 400 m desse furo
        centro_lat, centro_lon = d["lat"], d["lon"]
        import math as _m
        self.assertGreater(
            _m.hypot(667401.967 - d["grid_e"], 7904915.048 - d["grid_n"]), 400)

    def test_afetados_usa_o_furo_mais_proximo(self):
        d = self.d.adicionar({"nome": "x", "pontos": self.REAL, "raio_m": 400})
        lat, lon = self.proj.para_wgs84(665898.445 + 100, 7905482.311)
        erm = {"nome": "ERM-40", "tipo": "repetidora", "movel": False,
               "tem_fix": True, "lat": lat, "lon": lon, "idade_s": 2}
        a = self.d.afetados(d, [erm])
        self.assertEqual([r["nome"] for r in a["dentro"]], ["ERM-40"])
        self.assertAlmostEqual(a["dentro"][0]["distancia_m"], 100, delta=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
