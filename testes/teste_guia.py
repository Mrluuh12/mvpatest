#!/usr/bin/env python3
"""
Instrucoes de manobra: o bloco GUIA da tela, rodado no node.

A geometria e' montada aqui em metros e convertida para graus perto da mina,
para cada caso ter resposta conhecida: um cruzamento em T com virada a
direita tem que virar "vire a direita" na distancia certa, uma dobra de
estrada no meio de um trecho nao pode virar instrucao, e assim por diante.
"""
import json, math, os, shutil, subprocess, tempfile, unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
LAT0, LON0 = -18.915, -43.42


def ll(x, y):
    """metros (leste, norte) a partir de um ponto da mina -> [lon, lat]"""
    k = math.cos(math.radians(LAT0))
    return [LON0 + x / (111320 * k), LAT0 + y / 110540]


def trecho(*pts):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "LineString", "coordinates": [ll(*p) for p in pts]}}


def roda(casos):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(casos, f)
        nome = f.name
    try:
        r = subprocess.run([NODE, os.path.join(RAIZ, "testes", "guia_node.js"), nome],
                           capture_output=True, text=True, timeout=30)
        if r.returncode:
            raise AssertionError(r.stderr)
        return json.loads(r.stdout)
    finally:
        os.remove(nome)


# Um cruzamento em (0, 300): chega-se do sul; saidas para leste, oeste e norte.
CRUZ = (0, 300)
MALHA_T = {"features": [
    trecho((0, 0), CRUZ),                 # vem do sul
    trecho(CRUZ, (400, 300)),             # leste
    trecho(CRUZ, (-400, 300)),            # oeste
    trecho(CRUZ, (30, 700)),              # quase em frente (desvio leve)
]}


@unittest.skipIf(NODE is None, "sem node nesta maquina")
class TesteGuia(unittest.TestCase):

    def test_reconhece_o_cruzamento_pelo_numero_de_trechos(self):
        r = roda([{"f": "grau", "malha": MALHA_T, "ponto": ll(*CRUZ)},
                  {"f": "grau", "malha": MALHA_T, "ponto": ll(0, 150)}])
        self.assertEqual(r[0], 4)
        self.assertEqual(r[1], 0, "meio de trecho nao e' cruzamento")

    def test_pontas_que_nao_fecham_no_milimetro_sao_o_mesmo_cruzamento(self):
        """Trecho desenhado a mao termina a um metro do outro. Comparando
           coordenada exata, o cruzamento sumia e a voz nao falava nada."""
        malha = {"features": [
            trecho((0, 0), CRUZ),
            trecho((1.2, 300.4), (400, 300)),
            trecho((-0.8, 299.3), (-400, 300)),
        ]}
        rota = [ll(0, 0), ll(*CRUZ), ll(1.2, 300.4), ll(400, 300)]
        r = roda([{"f": "grau", "malha": malha, "ponto": ll(*CRUZ)},
                  {"f": "manobras", "rota": rota, "malha": malha}])
        self.assertEqual(r[0], 3)
        self.assertEqual([x["texto"] for x in r[1]], ["vire à direita"])

    def test_virada_a_direita_no_cruzamento(self):
        rota = [ll(0, 0), ll(*CRUZ), ll(400, 300)]
        m = roda([{"f": "manobras", "rota": rota, "malha": MALHA_T}])[0]
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["texto"], "vire à direita")
        self.assertAlmostEqual(m[0]["dist"], 300, delta=2)

    def test_virada_a_esquerda(self):
        rota = [ll(0, 0), ll(*CRUZ), ll(-400, 300)]
        m = roda([{"f": "manobras", "rota": rota, "malha": MALHA_T}])[0]
        self.assertEqual(m[0]["texto"], "vire à esquerda")

    def test_quase_em_frente_no_cruzamento_nao_vira_instrucao(self):
        """Cinco graus de desvio num cruzamento: falar 'vire' ali confunde."""
        rota = [ll(0, 0), ll(*CRUZ), ll(30, 700)]
        m = roda([{"f": "manobras", "rota": rota, "malha": MALHA_T}])[0]
        self.assertEqual(m, [])

    def test_curva_no_meio_do_trecho_nao_e_manobra(self):
        """Estrada de mina faz curva sem cruzamento nenhum. Se cada curva
           virasse 'vire a direita', o operador deixaria de ouvir a voz."""
        malha = {"features": [trecho((0, 0), (0, 300), (400, 300))]}
        rota = [ll(0, 0), ll(0, 300), ll(400, 300)]
        m = roda([{"f": "manobras", "rota": rota, "malha": malha}])[0]
        self.assertEqual(m, [])

    def test_retorno(self):
        malha = {"features": [trecho((0, 0), CRUZ), trecho(CRUZ, (40, 0)),
                              trecho(CRUZ, (0, 700))]}
        rota = [ll(0, 0), ll(*CRUZ), ll(40, 0)]
        m = roda([{"f": "manobras", "rota": rota, "malha": malha}])[0]
        self.assertEqual(m[0]["tipo"], "retorno")

    def test_progresso_projeta_no_trecho_e_nao_no_vertice(self):
        """Com trechos de centenas de metros, medir pelo vertice mais proximo
           erraria a distancia que falta por centenas de metros."""
        rota = [ll(0, 0), ll(0, 1000)]
        lon, lat = ll(12, 400)                 # 12 m ao lado, a 400 m do inicio
        p = roda([{"f": "progresso", "rota": rota, "lon": lon, "lat": lat}])[0]
        self.assertAlmostEqual(p["ao_longo"], 400, delta=2)
        self.assertAlmostEqual(p["desvio"], 12, delta=1)
        self.assertAlmostEqual(p["total"], 1000, delta=2)

    def test_proxima_manobra_e_a_que_esta_a_frente(self):
        lista = [{"dist": 100, "texto": "a"}, {"dist": 500, "texto": "b"}]
        r = roda([{"f": "proxima", "lista": lista, "ao_longo": 250},
                  {"f": "proxima", "lista": lista, "ao_longo": 600}])
        self.assertEqual(r[0]["texto"], "b")
        self.assertIsNone(r[1])

    def test_frases(self):
        man = {"texto": "vire à direita"}
        r = roda([{"f": "frase", "man": man, "dist": 203},
                  {"f": "frase", "man": man, "dist": 30},
                  {"f": "frase", "man": man, "dist": 1260}])
        self.assertEqual(r[0], "Em 200 metros, vire à direita.")
        self.assertEqual(r[1], "Vire à direita.")
        self.assertEqual(r[2], "Em 1,3 quilômetros, vire à direita.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
