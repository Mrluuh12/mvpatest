#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A interface do PTX calcula a rota sozinha quando o servidor esta' fora do ar.
Este teste prova que essa rota de emergencia bate com a do servidor.

Precisa do node so' para rodar o JavaScript fora do navegador; sem node, o
teste e' pulado (o resto da suite nao depende dele).
"""
import json, math, os, random, shutil, subprocess, sys, tempfile, unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "testes"))
from teste_servidor import sr, banco          # reaproveita o carregamento


def ponto_ao_longo(ar, fracao):
    g = ar["grid"]
    total = sum(math.dist(g[i], g[i+1]) for i in range(len(g)-1))
    alvo, s = fracao*total, 0.0
    for i in range(len(g)-1):
        d = math.dist(g[i], g[i+1])
        if s + d >= alvo:
            t = (alvo - s)/d if d else 0.0
            return (g[i][0] + t*(g[i+1][0]-g[i][0]), g[i][1] + t*(g[i+1][1]-g[i][1]))
        s += d
    return g[-1]


@unittest.skipIf(shutil.which("node") is None, "node nao instalado")
class TesteRotaOffline(unittest.TestCase):
    N_CASOS = 60

    @classmethod
    def setUpClass(cls):
        cls.m = sr.Malha(banco())
        cls.dir = tempfile.mkdtemp(prefix="minanav-js-")
        with open(os.path.join(cls.dir, "malha.json"), "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": cls.m.estradas}, f)
        with open(os.path.join(cls.dir, "locais.json"), "w", encoding="utf-8") as f:
            json.dump({"total": len(cls.m.locais), "locais": cls.m.locais}, f)

        random.seed(7)
        abertas = [a for a in cls.m.arestas if not a["fechada"]]
        ativos = [o for o in cls.m.locais if o["ativa"]] or cls.m.locais
        cls.casos, cls.esperado = [], []
        for _ in range(cls.N_CASOS):
            ar = random.choice(abertas)
            e, n = ponto_ao_longo(ar, random.random())
            lat, lon = cls.m.proj.para_wgs84(e, n)
            destino = random.choice(ativos)["nome"]
            cls.casos.append({"lat": lat, "lon": lon, "destino": destino})
            cls.esperado.append(cls.m.rota_de_coordenada(lat, lon, destino))
        with open(os.path.join(cls.dir, "casos.json"), "w", encoding="utf-8") as f:
            json.dump(cls.casos, f)

        p = subprocess.run(
            ["node", os.path.join(RAIZ, "testes", "nucleo_rota_node.js"),
             os.path.join(cls.dir, "malha.json"),
             os.path.join(cls.dir, "locais.json"),
             os.path.join(cls.dir, "casos.json")],
            capture_output=True, text=True, timeout=180)
        if p.returncode != 0:
            raise AssertionError("node falhou: " + p.stderr[-2000:])
        cls.js = json.loads(p.stdout)

    def test_grafo_montado_igual(self):
        self.assertEqual(self.js["trechos"], len(self.m.arestas))
        self.assertEqual(self.js["nos"], len(self.m.nos))

    def test_mesma_decisao_de_alcancavel(self):
        for i, (esp, obt) in enumerate(zip(self.esperado, self.js["respostas"])):
            self.assertEqual(bool(esp.get("ok")), obt["ok"],
                             f"caso {i} ({self.casos[i]['destino']}): "
                             f"servidor={esp.get('ok')} terminal={obt['ok']}")

    def test_distancias_batem_com_o_servidor(self):
        piores = []
        for i, (esp, obt) in enumerate(zip(self.esperado, self.js["respostas"])):
            if not esp.get("ok"):
                continue
            rel = abs(obt["distancia_m"] - esp["distancia_m"]) / max(esp["distancia_m"], 1.0)
            piores.append((rel, i, esp["distancia_m"], obt["distancia_m"]))
        self.assertTrue(piores, "nenhum caso alcancavel para comparar")
        piores.sort(reverse=True)
        rel, i, a, b = piores[0]
        # a interface usa metrica local; o servidor usa a grid da mina.
        # 0,5% num trecho de 5 km e' 25 m: aceitavel para um degrade offline.
        self.assertLess(rel, 0.005,
                        f"caso {i}: servidor {a} m, terminal {b} m ({rel*100:.3f}%)")
        media = sum(p[0] for p in piores)/len(piores)
        self.assertLess(media, 0.001, f"erro medio {media*100:.4f}%")

    def test_geometria_nao_vem_vazia(self):
        for i, (esp, obt) in enumerate(zip(self.esperado, self.js["respostas"])):
            if esp.get("ok") and esp["distancia_m"] > 0:
                self.assertGreaterEqual(obt["pontos"], 2, f"caso {i} sem geometria")


if __name__ == "__main__":
    unittest.main(verbosity=2)
