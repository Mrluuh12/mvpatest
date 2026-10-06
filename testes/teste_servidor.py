#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testes do servidor de rotas. Rodam sem o Modular.db real: um banco sintetico
e' montado a partir do dados/grafo.json, com o mesmo esquema e o mesmo formato
de BLOB do banco de producao.

    python3 -m unittest discover -s testes -v
"""
import http.client, importlib.util, json, math, os, struct, sys, tempfile, threading, unittest
from urllib.parse import quote

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "testes"))
import gerar_banco_sintetico as gerador

_spec = importlib.util.spec_from_file_location(
    "servidor_rotas", os.path.join(RAIZ, "servidor", "servidor_rotas.py"))
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)

_BANCO = None


def banco():
    """Usa o Modular.db de verdade se apontarem um (variavel MINA_DB ou
       dados/Modular.db). Sem ele, monta o sintetico a partir do grafo.json —
       mesmos 325 trechos e 318 nos, mesmo formato de BLOB."""
    global _BANCO
    if _BANCO is None:
        real = os.environ.get("MINA_DB") or os.path.join(RAIZ, "dados", "Modular.db")
        if os.path.exists(real):
            _BANCO = real
            print(f"[testes] usando o banco real: {real}")
        else:
            _BANCO = os.path.join(tempfile.mkdtemp(prefix="minanav-"), "Modular.db")
            gerador.gerar(_BANCO)
    return _BANCO


def banco_e_real():
    return os.path.abspath(banco()) not in (None,) and not banco().startswith(tempfile.gettempdir())


class TesteProjecao(unittest.TestCase):
    """Secao 3.4: o caso de regressao validado em campo."""

    def setUp(self):
        self.p = sr.Projecao(gerador.XML_PROJECAO)

    def test_regressao_de_campo(self):
        lat, lon = self.p.para_wgs84(665139.64, 7910269.13)
        self.assertAlmostEqual(lat, -18.893508, delta=1e-4)
        self.assertAlmostEqual(lon, -43.432549, delta=1e-4)

    def test_ida_e_volta(self):
        for e, n in [(665139.64, 7910269.13), (667029.824, 7905236.66),
                     (666693.0, 7909560.0)]:
            lat, lon = self.p.para_wgs84(e, n)
            e2, n2 = self.p.para_grid(lat, lon)
            self.assertLess(math.dist((e, n), (e2, n2)), 1e-3)  # sub-milimetro


class TesteGeometria(unittest.TestCase):
    """Secao 3.2: tamanhos conferidos na analise binaria."""

    def test_tamanhos_do_blob(self):
        self.assertEqual(len(gerador.blob_ponto(1, 2, 3)), 29)
        self.assertEqual(len(gerador.blob_polilinha([(0, 0, 0)] * 4)), 105)
        self.assertEqual(len(gerador.blob_poligono([(0, 0, 0)] * 9)), 229)

    def test_decodifica_os_tres_tipos(self):
        cod, pts = sr.decodificar(gerador.blob_ponto(10.5, 20.5, 30.5))
        self.assertEqual((cod, len(pts)), (1001, 1))
        self.assertEqual(pts[0], (10.5, 20.5, 30.5))
        cod, pts = sr.decodificar(gerador.blob_polilinha([(1, 2, 3), (4, 5, 6)]))
        self.assertEqual((cod, len(pts)), (1002, 2))
        cod, pts = sr.decodificar(gerador.blob_poligono([(1, 2, 3)] * 5))
        self.assertEqual((cod, len(pts)), (1003, 5))

    def test_blob_truncado_nao_estoura(self):
        self.assertEqual(sr.decodificar(b""), (None, []))
        self.assertEqual(sr.decodificar(None), (None, []))
        cod, pts = sr.decodificar(gerador.blob_polilinha([(1, 2, 3)] * 4)[:40])
        self.assertEqual(cod, 1002)
        self.assertEqual(len(pts), 1)          # so' o que coube, sem excecao

    def test_projecao_em_segmento(self):
        # pe da perpendicular no meio
        x, y, t = sr.projeta_no_segmento(5, 3, 0, 0, 10, 0)
        self.assertEqual((x, y), (5, 0)); self.assertAlmostEqual(t, 0.5)
        # antes do inicio: prende no extremo A
        x, y, t = sr.projeta_no_segmento(-7, 2, 0, 0, 10, 0)
        self.assertEqual((x, y, t), (0, 0, 0.0))
        # depois do fim: prende no extremo B
        x, y, t = sr.projeta_no_segmento(99, 2, 0, 0, 10, 0)
        self.assertEqual((x, y, t), (10, 0, 1.0))
        # segmento degenerado nao divide por zero
        self.assertEqual(sr.projeta_no_segmento(1, 1, 4, 4, 4, 4), (4, 4, 0.0))


class TesteMalha(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = sr.Malha(banco())

    def test_carregou_o_grafo_da_mina(self):
        self.assertEqual(len(self.m.arestas), 325)   # secao 3.3
        self.assertEqual(len(self.m.nos), 318)
        self.assertTrue(self.m.locais)

    def test_trechos_fechados_fora_do_roteamento(self):
        fechados = [a for a in self.m.arestas if a["fechada"]]
        self.assertTrue(fechados, "o grafo de teste precisa ter trecho fechado")
        for a in fechados:
            vizinhos = [v for v, _, _ in self.m.adj.get(a["de"], [])]
            self.assertNotIn(a["para"], vizinhos)
            # mas continua desenhavel no mapa
            self.assertTrue(any(f["properties"]["id"] == a["id"]
                                for f in self.m.estradas))

    def test_componentes_desconexos_dao_erro_e_nao_excecao(self):
        a, b = self._nos_de_componentes_diferentes()
        r = self.m.rota(a, b)
        self.assertFalse(r["ok"])
        self.assertIn("sem caminho", r["erro"])
        self.assertIn("desconexas", r["detalhe"])

    def test_no_desconhecido_da_erro_claro(self):
        r = self.m.rota("NAO-EXISTE", next(iter(self.m.nos)))
        self.assertFalse(r["ok"])
        self.assertIn("origem desconhecido", r["erro"])

    # ---------------------------------------------------- ancoragem
    def _aresta_longa(self):
        abertas = [a for a in self.m.arestas if not a["fechada"]]
        return max(abertas, key=lambda a: a["comprimento_m"])

    @staticmethod
    def _ponto_ao_longo(ar, fracao):
        """Ponto a uma fracao do comprimento, andando pela polilinha.
           As vias da mina sao curvas: interpolar entre o primeiro e o ultimo
           vertice cairia fora da estrada."""
        g = ar["grid"]
        total = sum(math.dist(g[i], g[i+1]) for i in range(len(g)-1))
        alvo, s = fracao*total, 0.0
        for i in range(len(g)-1):
            d = math.dist(g[i], g[i+1])
            if s + d >= alvo:
                t = (alvo - s)/d if d else 0.0
                return (g[i][0] + t*(g[i+1][0]-g[i][0]),
                        g[i][1] + t*(g[i+1][1]-g[i][1]))
            s += d
        return g[-1]

    @staticmethod
    def _comprimento_real(ar):
        g = ar["grid"]
        return sum(math.dist(g[i], g[i+1]) for i in range(len(g)-1))

    def _nos_de_componentes_diferentes(self):
        vistos, comps = set(), []
        for no in self.m.nos:
            if no in vistos: continue
            pilha, comp = [no], []
            vistos.add(no)
            while pilha:
                u = pilha.pop(); comp.append(u)
                for v, _, _ in self.m.adj.get(u, []):
                    if v not in vistos:
                        vistos.add(v); pilha.append(v)
            comps.append(comp)
        comps.sort(key=len, reverse=True)
        self.assertGreater(len(comps), 1, "esperava mais de um componente")
        return comps[0][0], comps[1][0]

    def test_ancora_no_meio_do_trecho_nao_no_no(self):
        ar = self._aresta_longa()
        alvo = self._ponto_ao_longo(ar, 0.4)     # veiculo em cima da via, a 40%
        anc = self.m.ancorar_trecho(*alvo)
        self.assertEqual(anc["aresta"]["id"], ar["id"])
        self.assertLess(anc["dist_m"], 0.01)     # cai exatamente sobre a via
        # o no' mais proximo, ao contrario, esta' a centenas de metros
        _, dist_no = self.m.ancorar(*alvo)
        self.assertGreater(dist_no, anc["dist_m"] + 100)

    def test_ancoragem_ignora_trecho_fechado(self):
        fechada = max((a for a in self.m.arestas if a["fechada"]),
                      key=lambda a: a["comprimento_m"])
        meio = ((fechada["grid"][0][0]+fechada["grid"][-1][0])/2,
                (fechada["grid"][0][1]+fechada["grid"][-1][1])/2)
        anc = self.m.ancorar_trecho(*meio)
        self.assertFalse(anc["aresta"]["fechada"])

    def test_rota_do_meio_do_trecho_nao_manda_voltar(self):
        """O defeito que a secao 4 mandou corrigir: com o veiculo a 40% de um
           trecho longo, ancorar no no' mais proximo obriga a desandar."""
        ar = self._aresta_longa()
        L = self._comprimento_real(ar)
        pos = self._ponto_ao_longo(ar, 0.4)
        lat, lon = self.m.proj.para_wgs84(*pos)

        # destino = o extremo 'para', logo a frente: a resposta certa e' 60% de L
        r = self.m.rota_de_coordenada(lat, lon, ar["para"])
        self.assertTrue(r["ok"], r.get("erro"))
        self.assertAlmostEqual(r["distancia_m"], 0.6*L, delta=max(1.0, 0.02*L))

        # a ancoragem antiga (no' mais proximo) manda percorrer o trecho inteiro,
        # e ainda por cima ignora o caminho de volta ate esse no'
        no_antigo, _ = self.m.ancorar(*pos)
        antiga = self.m.rota(no_antigo, ar["para"])
        self.assertTrue(antiga["ok"])
        percorrido_de_verdade = antiga["distancia_m"] + (
            0.4*L if no_antigo == ar["de"] else 0.6*L)
        self.assertLess(r["distancia_m"], percorrido_de_verdade)

    def _destino_alcancavel_a_partir_de(self, no):
        """Um no' a alguns saltos de distancia, para a rota ter comprimento."""
        vistos = {no}; borda = [no]; ordem = [no]
        for _ in range(6):
            nova = []
            for u in borda:
                for v, _, _ in self.m.adj.get(u, []):
                    if v not in vistos:
                        vistos.add(v); nova.append(v); ordem.append(v)
            if not nova: break
            borda = nova
        return ordem[-1]

    def test_geometria_da_rota_comeca_na_posicao_ancorada(self):
        ar = self._aresta_longa()
        meio = self._ponto_ao_longo(ar, 0.5)
        pos = (meio[0] + 30, meio[1] + 30)       # 30 m fora da via, como um GPS real
        lat, lon = self.m.proj.para_wgs84(*pos)
        destino = self._destino_alcancavel_a_partir_de(ar["para"])
        r = self.m.rota_de_coordenada(lat, lon, destino)
        self.assertTrue(r["ok"], r.get("erro"))
        co = r["geometria"]["coordinates"]
        self.assertAlmostEqual(co[0][0], r["ancorou_lon"], places=6)
        self.assertAlmostEqual(co[0][1], r["ancorou_lat"], places=6)
        # comprimento da linha desenhada bate com a distancia informada
        comp = 0.0
        for i in range(len(co)-1):
            a = self.m.proj.para_grid(co[i][1], co[i][0])
            b = self.m.proj.para_grid(co[i+1][1], co[i+1][0])
            comp += math.dist(a, b)
        self.assertAlmostEqual(comp, r["distancia_m"], delta=max(1.0, 0.01*comp))

    def test_nos_virtuais_nao_vazam_na_resposta(self):
        ar = self._aresta_longa()
        lat, lon = self.m.proj.para_wgs84(*self._ponto_ao_longo(ar, 0.25))
        destino = self._destino_alcancavel_a_partir_de(ar["para"])
        r = self.m.rota_de_coordenada(lat, lon, destino)
        self.assertTrue(r["ok"])
        self.assertFalse([n for n in r["nos"] if n.startswith("@")])
        self.assertEqual(r["n_nos"], len(r["nos"]))

    def test_origem_e_destino_no_mesmo_trecho(self):
        """Nao pode dar a volta pelo no' quando o destino esta' logo a frente."""
        ar = self._aresta_longa()
        L = self._comprimento_real(ar)
        origem = self._ponto_ao_longo(ar, 0.30)
        destino_grid = self._ponto_ao_longo(ar, 0.70)
        self.m.locais.append({"id": -1, "nome": "ALVO-NO-TRECHO", "tipo": "Teste",
                              "lat": 0, "lon": 0, "elevacao": 0,
                              "grid_e": destino_grid[0], "grid_n": destino_grid[1],
                              "ativa": True})
        self.m.locais_por_nome["ALVO-NO-TRECHO"] = self.m.locais[-1]
        try:
            lat, lon = self.m.proj.para_wgs84(*origem)
            r = self.m.rota_de_coordenada(lat, lon, "ALVO-NO-TRECHO")
            self.assertTrue(r["ok"], r.get("erro"))
            self.assertAlmostEqual(r["distancia_m"], 0.40*L, delta=max(2.0, 0.03*L))
            # nao passou por no' nenhum: o destino estava no mesmo trecho
            self.assertEqual(r["nos"], [])
        finally:
            self.m.locais.pop(); self.m.locais_por_nome.pop("ALVO-NO-TRECHO")

    def test_destino_desconhecido_da_erro_e_nao_excecao(self):
        lat, lon = -18.9, -43.42
        r = self.m.rota_de_coordenada(lat, lon, "LUGAR-QUE-NAO-EXISTE")
        self.assertFalse(r["ok"])
        self.assertIn("destino desconhecido", r["erro"])

    def test_aviso_quando_o_veiculo_esta_longe_da_malha(self):
        r = self.m.rota_de_coordenada(-18.80, -43.30, next(iter(self.m.nos)))
        self.assertIn("aviso", r)
        self.assertGreater(r["dist_ate_malha_m"], 500)


class TesteConferirBanco(unittest.TestCase):
    """O DISPATCH tem varios .db e so' um carrega a topologia. Apontar para o
       errado dava "no such table: GeographicRegion", que nao ajuda ninguem."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="minanav-db-")
        cls.bom = banco()
        cls.errado = os.path.join(cls.dir, "Modular.db")
        c = __import__("sqlite3").connect(cls.errado)
        c.executescript("CREATE TABLE Equipment(Id INTEGER);"
                        "CREATE TABLE Shift(Id INTEGER);")
        c.commit(); c.close()
        cls.naoeh = os.path.join(cls.dir, "naoeh.db")
        with open(cls.naoeh, "wb") as f:
            f.write(b"isto nao e sqlite")

    def test_reconhece_o_banco_bom(self):
        i = sr.inspecionar_banco(self.bom)
        self.assertTrue(i["serve"], i.get("motivo"))
        self.assertEqual(i["contagem"]["Road"], 325)

    def test_explica_o_banco_sem_topologia(self):
        i = sr.inspecionar_banco(self.errado)
        self.assertFalse(i["serve"])
        self.assertIn("GeographicRegion", i["motivo"])
        self.assertIn("Equipment", i["algumas_tabelas"])

    def test_arquivo_que_nao_e_sqlite(self):
        i = sr.inspecionar_banco(self.naoeh)
        self.assertFalse(i["serve"])
        self.assertIn("SQLite", i["motivo"])

    def test_arquivo_inexistente(self):
        i = sr.inspecionar_banco(os.path.join(self.dir, "nao-existe.db"))
        self.assertFalse(i["serve"])
        self.assertFalse(i["existe"])

    def test_procura_acha_o_certo_no_meio_dos_errados(self):
        import shutil as _sh
        pasta = os.path.join(self.dir, "varredura")
        os.makedirs(os.path.join(pasta, "sub"), exist_ok=True)
        _sh.copy(self.bom, os.path.join(pasta, "sub", "Modular.db"))
        _sh.copy(self.errado, os.path.join(pasta, "Modular.db"))
        achados, vistos = sr.procurar_bancos(pasta)
        bons = [i for i in achados if i["serve"]]
        self.assertEqual(len(bons), 1)
        self.assertTrue(bons[0]["caminho"].endswith(os.path.join("sub", "Modular.db")))
        self.assertGreaterEqual(vistos, 2)

    def test_servidor_sobe_e_explica_em_vez_de_morrer(self):
        """Banco errado nao pode derrubar o servidor: a frota Rajant continua
           funcionando, e o erro tem que dizer o que fazer."""
        repo = sr.Repositorio(self.errado, 3600)
        self.assertIsNone(repo.malha)
        self.assertIn("GeographicRegion", repo.erro)
        self.assertIn("--procurar-db", repo.erro)


class TesteApiHttp(unittest.TestCase):
    """Sobe o servidor de verdade e conversa com ele por HTTP."""

    @classmethod
    def setUpClass(cls):
        sr.Handler.repo = sr.Repositorio(banco(), 3600)
        cls.srv = sr.ThreadingHTTPServer(("127.0.0.1", 0), sr.Handler)
        cls.porta = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def pega(self, caminho, cabecalhos=None):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=10)
        c.request("GET", caminho, headers=cabecalhos or {})
        r = c.getresponse(); corpo = r.read(); c.close()
        return r.status, dict(r.getheaders()), corpo

    def json_de(self, caminho, cabecalhos=None):
        s, h, b = self.pega(caminho, cabecalhos)
        return s, h, json.loads(b.decode("utf-8"))

    def test_saude(self):
        s, _, j = self.json_de("/api/saude")
        self.assertEqual(s, 200); self.assertTrue(j["ok"]); self.assertTrue(j["hash"])

    def test_versao_e_304_com_etag(self):
        s, h, j = self.json_de("/api/versao")
        self.assertEqual(s, 200)
        etag = h["ETag"]
        self.assertEqual(j["trechos"], 325)
        s2, _, b2 = self.pega("/api/malha", {"If-None-Match": etag})
        self.assertEqual(s2, 304)
        self.assertEqual(len(b2), 0)          # secao 4: 0 bytes na segunda vez

    def test_malha_areas_e_locais(self):
        s, _, j = self.json_de("/api/malha")
        self.assertEqual(s, 200)
        self.assertEqual(j["type"], "FeatureCollection")
        self.assertEqual(len(j["features"]), 325)
        self.assertTrue(all(f["geometry"]["type"] == "LineString" for f in j["features"]))
        s, _, j = self.json_de("/api/areas")
        self.assertEqual(j["features"][0]["geometry"]["type"], "Polygon")
        s, _, j = self.json_de("/api/locais")
        self.assertTrue(j["total"] > 0)
        um = j["locais"][0]["nome"][:3]
        s, _, jf = self.json_de("/api/locais?q=" + um)
        self.assertTrue(all(um.upper() in o["nome"].upper() for o in jf["locais"]))
        self.assertLessEqual(jf["total"], j["total"])

    def test_rota_por_coordenada(self):
        malha = sr.Handler.repo.malha
        ar = max((a for a in malha.arestas if not a["fechada"]),
                 key=lambda a: a["comprimento_m"])
        lat, lon = malha.proj.para_wgs84(*ar["grid"][0])
        s, _, j = self.json_de(
            f"/api/rota?de_lat={lat}&de_lon={lon}&para={quote(ar['para'])}")
        self.assertEqual(s, 200)
        self.assertTrue(j["ok"])
        self.assertGreater(j["distancia_m"], 0)
        self.assertEqual(j["geometria"]["type"], "LineString")
        self.assertIsNotNone(j["ancorou_em"])

    def test_nome_com_acento_e_espaco(self):
        """Nomes reais da mina tem acento e espaco (BOX-LUBRIFICACAO,
           LEI-PRACA FEIJAO-01). Tem que atravessar a URL inteiros."""
        malha = sr.Handler.repo.malha
        acentuados = [n for n in malha.nos if not n.isascii()]
        if not acentuados:
            self.skipTest("o banco em uso nao tem nome acentuado")
        alvo = acentuados[0]
        s, _, j = self.json_de("/api/locais?q=" + quote(alvo[:8]))
        ar = next(a for a in malha.arestas
                  if not a["fechada"] and alvo in (a["de"], a["para"]))
        outro = a_outro = ar["de"] if ar["para"] == alvo else ar["para"]
        s, _, j = self.json_de(
            f"/api/rota?de={quote(outro)}&para={quote(alvo)}")
        self.assertEqual(s, 200)
        self.assertTrue(j["ok"], j.get("erro"))
        self.assertEqual(j["destino"], alvo)      # voltou com os acentos certos

    def test_rota_sem_parametro_da_400(self):
        s, _, j = self.json_de("/api/rota")
        self.assertEqual(s, 400); self.assertFalse(j["ok"])
        s, _, j = self.json_de("/api/rota?para=X")
        self.assertEqual(s, 400)
        s, _, j = self.json_de("/api/rota?de_lat=abc&de_lon=1&para=X")
        self.assertEqual(s, 400)

    def test_rota_impossivel_da_404_com_erro_legivel(self):
        s, _, j = self.json_de("/api/rota?de_lat=-18.9&de_lon=-43.42&para=INEXISTENTE")
        self.assertEqual(s, 404)
        self.assertFalse(j["ok"])
        self.assertTrue(j["erro"])

    def test_rota_entre_nos_nomeados(self):
        malha = sr.Handler.repo.malha
        ar = next(a for a in malha.arestas if not a["fechada"])
        s, _, j = self.json_de(
            f"/api/rota?de={quote(ar['de'])}&para={quote(ar['para'])}")
        self.assertEqual(s, 200); self.assertTrue(j["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
