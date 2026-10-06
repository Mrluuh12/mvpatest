#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testes da camada de frota: leitura do GPS dos radios Rajant e rota ate um
equipamento em campo.

Nao precisa de radio nenhum: parte dos testes usa uma sessao falsa em
memoria, e parte fala BCAPI de verdade (TLS + protobuf) com o radio_falso.
"""
import http.client, importlib.util, json, os, sys, threading, time, unittest
from urllib.parse import quote

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "testes"))
sys.path.insert(0, os.path.join(RAIZ, "servidor"))
from teste_servidor import sr, banco

_spec = importlib.util.spec_from_file_location(
    "rajant_frota", os.path.join(RAIZ, "servidor", "rajant_frota.py"))
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)

try:
    import rajant_api  # noqa: F401
    TEM_PROTOBUF = True
except ImportError:
    TEM_PROTOBUF = False


class TesteLeituraNmea(unittest.TestCase):
    def test_hemisferio(self):
        # sem ler o sufixo, toda longitude do Brasil sairia do outro lado do mundo
        self.assertAlmostEqual(rf.nmea_para_graus("1853.61048S"), -18.893508, places=6)
        self.assertAlmostEqual(rf.nmea_para_graus("04325.95294W"), -43.432549, places=6)
        self.assertAlmostEqual(rf.nmea_para_graus("2743.8950N"), 27.731583, places=5)
        self.assertAlmostEqual(rf.nmea_para_graus("02258.1429E"), 22.969048, places=5)

    def test_entrada_ruim_nao_estoura(self):
        for ruim in (None, "", "   ", "abc", "1853", "S", ".5S"):
            self.assertIsNone(rf.nmea_para_graus(ruim), repr(ruim))


class TesteParseGps(unittest.TestCase):
    POS = {"gpsLat": "1853.61048S", "gpsLong": "04325.95294W", "gpsQuality": 1}

    def test_velocidade_em_nos(self):
        g = rf.parse_gps({"gps": {"gpsPos": self.POS,
                                  "gpsVel": {"gpsSpeedKnots": 20.0}}})
        self.assertAlmostEqual(g["vel_kmh"], 37.0, delta=0.1)

    def test_velocidade_ja_em_kmh_nao_e_multiplicada(self):
        """O state tem gpsSpeedKnots E gpsSpeedKph. Procurar por 'speed' e
           multiplicar por 1,852 sempre dobraria a leitura deste firmware —
           e e' esta velocidade que trava a interface em movimento."""
        g = rf.parse_gps({"gps": {"gpsPos": self.POS,
                                  "gpsVel": {"gpsSpeedKph": 37.04}}})
        self.assertAlmostEqual(g["vel_kmh"], 37.0, delta=0.1)

    def test_kph_vence_knots_quando_os_dois_vem(self):
        g = rf.parse_gps({"gps": {"gpsPos": self.POS,
                                  "gpsVel": {"gpsSpeedKph": 10.0,
                                             "gpsSpeedKnots": 5.399}}})
        self.assertAlmostEqual(g["vel_kmh"], 10.0, delta=0.1)

    def test_rumo(self):
        g = rf.parse_gps({"gps": {"gpsPos": self.POS,
                                  "gpsVel": {"gpsTrackDegreesTrue": 91.4}}})
        self.assertAlmostEqual(g["rumo"], 91.4, delta=0.1)
        g = rf.parse_gps({"gps": {"gpsPos": self.POS,
                                  "gpsVel": {"gpsTrackDegreesMag": 88.0}}})
        self.assertAlmostEqual(g["rumo"], 88.0, delta=0.1)

    def test_sem_fix_devolve_nada(self):
        self.assertIsNone(rf.parse_gps({}))
        self.assertIsNone(rf.parse_gps({"gps": {}}))
        self.assertIsNone(rf.parse_gps({"gps": {"gpsSwitch": {"enabled": False}}}))
        # qualidade 0 = sem fix
        self.assertIsNone(rf.parse_gps({"gps": {"gpsPos": dict(self.POS, gpsQuality=0)}}))
        # o RMC dizendo que a sentenca nao vale
        self.assertIsNone(rf.parse_gps({"gps": {"gpsRMC": {"gpsStatus": False},
                                                "gpsPos": self.POS}}))

    def test_campos_extras(self):
        g = rf.parse_gps({"gps": {"gpsPos": dict(self.POS, gpsAlt=812.5,
                                                 gpsSatsInView=9)}})
        self.assertAlmostEqual(g["altitude"], 812.5, delta=0.1)
        self.assertEqual(g["satelites"], 9)
        self.assertEqual(g["qualidade"], 1)


class TesteTipos(unittest.TestCase):
    def test_prefixo_mais_longo_primeiro(self):
        # ERB/ERM tem que casar com a propria regra, nao com uma mais curta
        self.assertEqual(rf.tipo_do_nome("CA-1022"), "caminhao")
        self.assertEqual(rf.tipo_do_nome("ERB-12 Slipstream"), "torre")
        self.assertEqual(rf.tipo_do_nome("ERM-12 PTP CAM"), "repetidora")
        self.assertEqual(rf.tipo_do_nome("EH-07"), "escavadeira")
        self.assertEqual(rf.tipo_do_nome("PF-3"), "perfuratriz")
        self.assertEqual(rf.tipo_do_nome("10.188.99.195"), "generico")

    def test_movel_ou_fixo(self):
        self.assertIn("torre", rf.TIPOS_FIXOS)
        self.assertIn("repetidora", rf.TIPOS_FIXOS)
        self.assertNotIn("caminhao", rf.TIPOS_FIXOS)


class SessaoFalsa:
    """Sessao em memoria: nem TLS nem rede, so' a maquina de estados."""
    def __init__(self, estados, falhar_conexao=False):
        self.estados = estados
        self.i = 0
        self.falhar_conexao = falhar_conexao
        self.conexoes = 0
        self.fechada = 0

    def conectar(self):
        self.conexoes += 1
        if self.falhar_conexao:
            raise ConnectionError("radio inalcancavel")

    def ler_gps(self):
        e = self.estados[min(self.i, len(self.estados) - 1)]
        self.i += 1
        return e

    def fechar(self):
        self.fechada += 1


class TesteFrota(unittest.TestCase):
    POS = {"gpsLat": "1853.61048S", "gpsLong": "04325.95294W", "gpsQuality": 1}

    def frota_com(self, estados, ip="10.0.0.1", nome="CA-1022", **kw):
        sessoes = {}
        def fabrica(i):
            sessoes[i] = SessaoFalsa(estados, **kw)
            return sessoes[i]
        f = rf.Frota([ip], {ip: nome}, senha="x", intervalo=1,
                     fabrica_sessao=fabrica)
        return f, sessoes

    def esperar(self, cond, limite=10, oque=""):
        fim = time.time() + limite
        while time.time() < fim:
            v = cond()
            if v: return v
            time.sleep(0.05)
        self.fail("tempo esgotado esperando " + oque)

    def test_frota_registrada_antes_de_qualquer_fix(self):
        """Todo radio aparece na lista desde a largada, mesmo desligado:
           o operador ve' a frota inteira, nao so' quem ja' respondeu."""
        f, _ = self.frota_com([{}])
        lista = f.instantaneo()
        self.assertEqual(len(lista), 1)
        self.assertEqual(lista[0]["nome"], "CA-1022")
        self.assertEqual(lista[0]["tipo"], "caminhao")
        self.assertFalse(lista[0]["online"])
        self.assertFalse(lista[0]["tem_fix"])

    def test_le_posicao_e_publica(self):
        f, _ = self.frota_com([{"gps": {"gpsPos": self.POS,
                                        "gpsVel": {"gpsSpeedKnots": 0.0}}}])
        f.iniciar()
        try:
            r = self.esperar(lambda: (f.instantaneo()[0]["tem_fix"] and f.instantaneo()[0]),
                             oque="o primeiro fix")
            self.assertAlmostEqual(r["lat"], -18.893508, places=5)
            self.assertAlmostEqual(r["lon"], -43.432549, places=5)
            self.assertTrue(r["online"])
            self.assertTrue(r["parado"])
            self.assertIsNotNone(r["idade_s"])
        finally:
            f.encerrar()

    def test_radio_que_nao_conecta_fica_offline_sem_derrubar(self):
        f, _ = self.frota_com([{}], falhar_conexao=True)
        f.iniciar()
        try:
            r = self.esperar(lambda: (f.instantaneo()[0]["erro"] and f.instantaneo()[0]),
                             oque="o erro de conexao ser registrado")
            self.assertFalse(r["online"])
            self.assertFalse(r["tem_fix"])
            self.assertIn("inalcancavel", r["erro"])
        finally:
            f.encerrar()

    def test_busca_por_nome_ignora_caixa(self):
        f, _ = self.frota_com([{"gps": {"gpsPos": self.POS}}])
        self.assertIsNotNone(f.por_nome("ca-1022"))
        self.assertIsNotNone(f.por_nome("  CA-1022 "))
        self.assertIsNone(f.por_nome("CA-9999"))

    def test_batimento_mostra_que_a_frota_esta_viva(self):
        """"Fez uma coleta e parou" tem que ser visivel de fora: o resumo conta
           as leituras e a idade da ultima."""
        f, _ = self.frota_com([{"gps": {"gpsPos": self.POS}}])
        r0 = f.resumo()
        self.assertEqual(r0["leituras"], 0)
        self.assertIsNone(r0["idade_ultima_leitura_s"])
        f.iniciar()
        try:
            self.esperar(lambda: f.resumo()["leituras"] > 0, oque="a primeira leitura")
            n1 = f.resumo()["leituras"]
            self.esperar(lambda: f.resumo()["leituras"] > n1, oque="a leitura seguinte")
            r = f.resumo()
            self.assertIsNotNone(r["idade_ultima_leitura_s"])
            self.assertLess(r["idade_ultima_leitura_s"], 10)
        finally:
            f.encerrar()

    def test_resumo(self):
        f, _ = self.frota_com([{"gps": {"gpsPos": self.POS}}])
        r = f.resumo()
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["com_fix"], 0)
        self.assertTrue(r["ativa"])


@unittest.skipUnless(TEM_PROTOBUF, "rajant-api nao instalado")
class TesteBcapiDeVerdade(unittest.TestCase):
    """Fala BCAPI de verdade com um radio falso: TLS, framing de 8 bytes,
       sha384(senha+desafio), filtro 'gps' e protobuf."""

    @classmethod
    def setUpClass(cls):
        from radio_falso import RadioFalso
        cls.RadioFalso = RadioFalso
        cls.radio = RadioFalso(senha="segredo", gps={
            "lat": "1853.61048S", "lon": "04325.95294W",
            "nos": 20.0, "rumo": 91.0, "alt": 812.0})

    @classmethod
    def tearDownClass(cls):
        cls.radio.fecha()

    def test_sessao_completa(self):
        s = rf.SessaoBC("127.0.0.1", self.radio.porta, "VIEW", "segredo")
        s.conectar()
        try:
            estado = s.ler_gps()
            self.assertIn(["gps"], self.radio.filtros,
                          "o servidor tem que pedir so' a subarvore gps")
            g = rf.parse_gps(estado)
            self.assertAlmostEqual(g["lat"], -18.893508, places=5)
            self.assertAlmostEqual(g["lon"], -43.432549, places=5)
            self.assertAlmostEqual(g["vel_kmh"], 37.0, delta=0.2)
            self.assertAlmostEqual(g["rumo"], 91.0, delta=0.2)
        finally:
            s.fechar()

    def test_senha_errada_e_recusada(self):
        s = rf.SessaoBC("127.0.0.1", self.radio.porta, "VIEW", "errada")
        with self.assertRaises(PermissionError):
            s.conectar()
        s.fechar()

    def test_frota_inteira_contra_o_radio_falso(self):
        ip = "127.0.0.1"
        f = rf.Frota([ip], {ip: "CA-1022"}, senha="segredo", intervalo=1,
                     fabrica_sessao=lambda i: rf.SessaoBC(i, self.radio.porta,
                                                          "VIEW", "segredo"))
        f.iniciar()
        try:
            fim = time.time() + 15
            r = None
            while time.time() < fim:
                r = f.instantaneo()[0]
                if r["tem_fix"]:
                    break
                time.sleep(0.1)
            self.assertTrue(r["tem_fix"], "a frota nao pegou o fix do radio")
            self.assertAlmostEqual(r["lat"], -18.893508, places=5)
            self.assertEqual(r["tipo"], "caminhao")
            self.assertFalse(r["parado"])       # 37 km/h
        finally:
            f.encerrar()


class TesteApiEquipamentos(unittest.TestCase):
    """A API do servidor de rotas com a frota ligada."""

    @classmethod
    def setUpClass(cls):
        cls.malha = sr.Malha(banco())
        # dois equipamentos: um em cima da malha, um sem fix
        ar = max((a for a in cls.malha.arestas if not a["fechada"]),
                 key=lambda a: a["comprimento_m"])
        g = ar["grid"]
        meio = ((g[0][0]+g[-1][0])/2, (g[0][1]+g[-1][1])/2)
        lat, lon = cls.malha.proj.para_wgs84(*meio)
        cls.eq_lat, cls.eq_lon, cls.aresta = lat, lon, ar

        def para_nmea(v, casas):
            neg = v < 0; v = abs(v); d = int(v)
            return "%0*d%08.5f" % (casas, d, (v-d)*60), neg
        la, sul = para_nmea(lat, 2); lo, oeste = para_nmea(lon, 3)
        estado = {"gps": {"gpsPos": {"gpsLat": la + ("S" if sul else "N"),
                                     "gpsLong": lo + ("W" if oeste else "E"),
                                     "gpsQuality": 1},
                          "gpsVel": {"gpsSpeedKnots": 0.0}}}

        ips = ["10.0.0.1", "10.0.0.2"]
        nomes = {"10.0.0.1": "CA-1022", "10.0.0.2": "ERB-12"}
        estados = {"10.0.0.1": [estado], "10.0.0.2": [{}]}
        frota = rf.Frota(ips, nomes, senha="x", intervalo=1,
                         fabrica_sessao=lambda i: SessaoFalsa(estados[i]))
        frota.iniciar()
        fim = time.time() + 10
        while time.time() < fim and not frota.por_nome("CA-1022")["tem_fix"]:
            time.sleep(0.1)
        cls.frota = frota

        sr.Handler.repo = sr.Repositorio(banco(), 3600)
        sr.Handler.frota = frota
        cls.srv = sr.ThreadingHTTPServer(("127.0.0.1", 0), sr.Handler)
        cls.porta = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()
        cls.frota.encerrar()
        sr.Handler.frota = None

    def pega(self, caminho):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=10)
        c.request("GET", caminho)
        r = c.getresponse(); b = r.read(); c.close()
        return r.status, json.loads(b.decode("utf-8"))

    def test_lista_equipamentos(self):
        s, j = self.pega("/api/equipamentos")
        self.assertEqual(s, 200)
        self.assertTrue(j["ok"])
        self.assertEqual(j["total"], 2)
        nomes = [e["nome"] for e in j["equipamentos"]]
        self.assertIn("CA-1022", nomes)
        self.assertIn("ERB-12", nomes)

    def test_filtros(self):
        s, j = self.pega("/api/equipamentos?moveis=1")
        self.assertEqual([e["nome"] for e in j["equipamentos"]], ["CA-1022"])
        s, j = self.pega("/api/equipamentos?com_fix=1")
        self.assertEqual([e["nome"] for e in j["equipamentos"]], ["CA-1022"])
        s, j = self.pega("/api/equipamentos?tipo=torre")
        self.assertEqual([e["nome"] for e in j["equipamentos"]], ["ERB-12"])

    def test_saude_traz_o_resumo_da_frota(self):
        s, j = self.pega("/api/saude")
        self.assertEqual(j["frota"]["total"], 2)
        self.assertEqual(j["frota"]["com_fix"], 1)

    def test_rota_ate_equipamento(self):
        no = self.aresta["de"]
        d = self.malha.nos[no]
        s, j = self.pega(f"/api/rota?de_lat={d['lat']}&de_lon={d['lon']}"
                         f"&para_equip=CA-1022")
        self.assertEqual(s, 200, j)
        self.assertTrue(j["ok"], j.get("erro"))
        self.assertTrue(j["destino_movel"])
        self.assertGreater(j["distancia_m"], 0)
        self.assertEqual(j["equipamento"]["nome"], "CA-1022")
        # a rota termina onde o equipamento esta'
        self.assertAlmostEqual(j["destino_lat"], self.eq_lat, places=5)
        self.assertAlmostEqual(j["destino_lon"], self.eq_lon, places=5)
        self.assertGreaterEqual(len(j["geometria"]["coordinates"]), 2)

    def test_equipamento_sem_fix_da_erro_claro(self):
        s, j = self.pega("/api/rota?de_lat=-18.9&de_lon=-43.42&para_equip=ERB-12")
        self.assertEqual(s, 404)
        self.assertFalse(j["ok"])
        self.assertIn("sem posicao GPS", j["erro"])

    def test_equipamento_desconhecido(self):
        s, j = self.pega("/api/rota?de_lat=-18.9&de_lon=-43.42&para_equip=CA-9999")
        self.assertEqual(s, 404)
        self.assertIn("desconhecido", j["erro"])

    def test_rota_ate_equipamento_exige_origem(self):
        s, j = self.pega("/api/rota?para_equip=CA-1022")
        self.assertEqual(s, 400)
        self.assertIn("de_lat", j["erro"])


class TesteFrotaDesligada(unittest.TestCase):
    """Sem frota configurada o servidor continua servindo rotas normalmente."""

    @classmethod
    def setUpClass(cls):
        sr.Handler.repo = sr.Repositorio(banco(), 3600)
        sr.Handler.frota = None
        sr.Handler.frota_erro = None
        cls.srv = sr.ThreadingHTTPServer(("127.0.0.1", 0), sr.Handler)
        cls.porta = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def pega(self, caminho):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=10)
        c.request("GET", caminho)
        r = c.getresponse(); b = r.read(); c.close()
        return r.status, json.loads(b.decode("utf-8"))

    def test_equipamentos_explica_que_nao_esta_configurada(self):
        s, j = self.pega("/api/equipamentos")
        self.assertEqual(s, 503)
        self.assertFalse(j["ok"])
        self.assertIn("frota", j["erro"])

    def test_malha_continua_funcionando(self):
        s, j = self.pega("/api/malha")
        self.assertEqual(s, 200)
        self.assertEqual(len(j["features"]), 325)


if __name__ == "__main__":
    unittest.main(verbosity=2)
