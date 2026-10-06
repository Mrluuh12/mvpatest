#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Teste de ponta a ponta do cliente do PTX.

Sobe o servidor de rotas de verdade, cria uma serial virtual (pty) e escreve
sentencas NMEA nela, roda o PtxNav.exe no Mono apontado para as duas coisas e
confere pela interface HTTP local o que ele entendeu.

Cobre: leitura da serial, checksum, fix invalido, trava de seguranca por
velocidade, cache nas duas camadas do aufs, rota pelo servidor e degrade
quando o servidor cai.

Pulado se nao houver mono/mcs na maquina.
"""
import http.client, json, os, pty, shutil, subprocess, sys, tempfile, threading, time, unittest
import socket

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "testes"))
from teste_servidor import sr, banco

FALTA_MONO = shutil.which("mono") is None or shutil.which("mcs") is None


def porta_livre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def checksum(corpo):
    x = 0
    for ch in corpo:
        x ^= ord(ch)
    return "%02X" % x


def sentenca(corpo):
    return "$" + corpo + "*" + checksum(corpo) + "\r\n"


def graus_para_nmea(v, casas):
    hemi_neg = v < 0
    v = abs(v)
    g = int(v)
    m = (v - g) * 60.0
    return ("%0*d%08.5f" % (casas, g, m)), hemi_neg


def rmc(lat, lon, sog_nos=0.0, cog=0.0, valido=True):
    la, sul = graus_para_nmea(lat, 2)
    lo, oeste = graus_para_nmea(lon, 3)
    corpo = ("GPRMC,123519.00,%s,%s,%s,%s,%s,%.2f,%.1f,190826,,,A"
             % ("A" if valido else "V", la, "S" if sul else "N",
                lo, "W" if oeste else "E", sog_nos, cog))
    return sentenca(corpo)


def gga(lat, lon, qualidade=1, alt=800.0):
    la, sul = graus_para_nmea(lat, 2)
    lo, oeste = graus_para_nmea(lon, 3)
    corpo = ("GNGGA,123519.00,%s,%s,%s,%s,%d,09,0.9,%.1f,M,-6.0,M,,"
             % (la, "S" if sul else "N", lo, "W" if oeste else "E", qualidade, alt))
    return sentenca(corpo)


class Serial:
    """Serial virtual: escreve NMEA no mestre, o cliente le pelo escravo."""

    def __init__(self):
        self.mestre, escravo = pty.openpty()
        self.dev = os.ttyname(escravo)
        os.close(escravo)
        self.linhas = []
        self.parar = False
        self.trava = threading.Lock()
        self.t = threading.Thread(target=self._laco, daemon=True)
        self.t.start()

    def define(self, linhas):
        with self.trava:
            self.linhas = list(linhas)

    def _laco(self):
        while not self.parar:
            with self.trava:
                atuais = list(self.linhas)
            for ln in atuais:
                try:
                    os.write(self.mestre, ln.encode("ascii"))
                except OSError:
                    return
            time.sleep(0.5)

    def fecha(self):
        self.parar = True
        try: os.close(self.mestre)
        except OSError: pass


@unittest.skipIf(FALTA_MONO, "mono/mcs nao instalados")
class TesteCacheSemeado(unittest.TestCase):
    """PTX que nunca alcancou o servidor tem que abrir com o mapa mesmo assim,
       se o cache foi semeado na instalacao. Sem isso, terminal novo em area
       sem malha Rajant fica com a tela vazia ate alguem levar ele para perto
       do servidor."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="minanav-semente-")
        cls.exe = os.path.join(cls.dir, "PtxNav.exe")
        p = subprocess.run(["sh", os.path.join(RAIZ, "ptx", "compilar.sh"), cls.exe],
                           capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            raise AssertionError("compilacao falhou:\n" + p.stdout + p.stderr)

        # semeia o cache direto do banco, como faz o extrair_malha.py
        cls.cache = os.path.join(cls.dir, "cache")
        p = subprocess.run(
            [sys.executable, os.path.join(RAIZ, "servidor", "extrair_malha.py"),
             "--db", banco(), "--saida", os.path.join(cls.dir, "dados"),
             "--semear", cls.cache],
            capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            raise AssertionError("semeadura falhou:\n" + p.stdout + p.stderr)

        # servidor de rotas propositalmente inexistente
        cls.porta = porta_livre()
        cls.proc = subprocess.Popen(
            ["mono", cls.exe,
             "--servidor", "http://127.0.0.1:9",
             "--porta", str(cls.porta),
             "--serial", "/dev/naoexiste",
             "--cache", cls.cache,
             "--cache-persistente", os.path.join(cls.dir, "rr"),
             "--log", os.path.join(cls.dir, "log"), "--intervalo", "3600"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        fim = time.time() + 60
        while time.time() < fim:
            try:
                cls.pega_json("/estado", cls.porta)
                return
            except Exception:
                time.sleep(0.5)
        raise AssertionError("o cliente nao subiu")

    @classmethod
    def tearDownClass(cls):
        try: cls.proc.terminate(); cls.proc.wait(timeout=10)
        except Exception:
            try: cls.proc.kill()
            except Exception: pass
        try: cls.proc.stdout.close()
        except Exception: pass
        shutil.rmtree(cls.dir, ignore_errors=True)

    @classmethod
    def pega_json(cls, caminho, porta=None):
        c = http.client.HTTPConnection("127.0.0.1", porta or cls.porta, timeout=8)
        c.request("GET", caminho)
        r = c.getresponse(); b = r.read(); c.close()
        return json.loads(b.decode("utf-8"))

    def test_serve_o_mapa_sem_nunca_ter_visto_o_servidor(self):
        j = self.pega_json("/malha")
        self.assertEqual(len(j["features"]), 325)
        j = self.pega_json("/locais")
        self.assertGreater(j["total"], 0)
        j = self.pega_json("/areas")
        self.assertEqual(j["type"], "FeatureCollection")

    def test_estado_admite_que_o_servidor_esta_fora(self):
        j = self.pega_json("/estado")
        self.assertTrue(j["tem_cache"], "o cache semeado nao foi reconhecido")
        self.assertTrue(j["servidor"].startswith("offline")
                        or j["servidor"] == "nunca contatado", j["servidor"])

    def test_hash_semeado_e_o_mesmo_que_o_servidor_publicaria(self):
        """Se o hash bater, a primeira sincronizacao nao rebaixa nada:
           o servidor responde 304 e o cache semeado continua valendo."""
        import hashlib
        h = hashlib.sha256()
        with open(banco(), "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        self.assertEqual(self.pega_json("/estado")["hash"], h.hexdigest()[:16])


@unittest.skipIf(FALTA_MONO, "mono/mcs nao instalados")
class TesteDiagnostico(unittest.TestCase):
    """O laudo e o varredor de seriais rodam no terminal, muitas vezes com
       tudo errado. O que eles nao podem fazer, nunca, e' travar."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="minanav-diag-")
        cls.exe = os.path.join(cls.dir, "PtxNav.exe")
        p = subprocess.run(["sh", os.path.join(RAIZ, "ptx", "compilar.sh"), cls.exe],
                           capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            raise AssertionError("compilacao falhou:\n" + p.stdout + p.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_procurar_gps_termina_e_nao_pendura(self):
        """Ler serial e' bloqueante por natureza: um device que abre e nunca
           fala penduraria a varredura para sempre."""
        p = subprocess.run(
            ["mono", self.exe, "--procurar-gps"],
            capture_output=True, text=True, timeout=300)
        self.assertIn("PROCURANDO O GPS", p.stdout)
        # sem GPS nesta maquina: tem que sair reclamando, nao travar
        self.assertIn(p.returncode, (0, 1))
        if p.returncode == 1:
            self.assertIn("cat /dev/", p.stdout)

    def test_laudo_reprova_com_serial_e_servidor_errados(self):
        p = subprocess.run(
            ["mono", self.exe, "--testar",
             "--serial", "/dev/naoexiste-mesmo",
             "--servidor", "http://127.0.0.1:9",
             "--cache", os.path.join(self.dir, "ram"),
             "--cache-persistente", os.path.join(self.dir, "rr")],
            capture_output=True, text=True, timeout=300)
        self.assertEqual(p.returncode, 1)
        self.assertIn("nao existe", p.stdout)
        self.assertIn("--procurar-gps", p.stdout)
        self.assertIn("nao alcancei o servidor", p.stdout)

    def test_laudo_reprova_camada_persistente_na_raiz(self):
        """O furo do aufs: gravar em /media/realroot funciona mesmo sem o
           mount, e sumiria no reboot."""
        p = subprocess.run(
            ["mono", self.exe, "--testar",
             "--serial", "/dev/naoexiste-mesmo",
             "--servidor", "http://127.0.0.1:9",
             "--cache", os.path.join(self.dir, "ram"),
             "--cache-persistente", os.path.join(self.dir, "fingindo-ser-realroot")],
            capture_output=True, text=True, timeout=300)
        self.assertIn("camada em RAM do aufs", p.stdout)
        self.assertEqual(p.returncode, 1)


class TesteLancador(unittest.TestCase):
    """Detalhes do ptx_iniciar.sh que so' aparecem no equipamento."""

    CAMINHO = os.path.join(RAIZ, "ptx", "ptx_iniciar.sh")

    def test_e_posix_sh_sem_crlf(self):
        p = subprocess.run(["sh", "-n", self.CAMINHO], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(self.CAMINHO, "rb") as f:
            self.assertNotIn(b"\r\n", f.read(), "o PTX nao aceita CRLF")

    def test_chromium_recebe_no_sandbox(self):
        """O servico roda como root, e o Chromium se recusa a subir como root
           com o sandbox ligado: sai na hora e o supervisor reabre em laco."""
        with open(self.CAMINHO, encoding="utf-8") as f:
            texto = f.read()
        self.assertIn("--no-sandbox", texto)
        self.assertIn("--user-data-dir", texto)

    def test_saida_do_navegador_vai_para_arquivo(self):
        """Sem isso, navegador que morre no arranque nao deixa pista."""
        with open(self.CAMINHO, encoding="utf-8") as f:
            texto = f.read()
        self.assertIn("LOG_NAVEGADOR", texto)
        self.assertNotIn("$NAV_CMD >/dev/null 2>&1 &", texto)


@unittest.skipIf(FALTA_MONO, "mono/mcs nao instalados")
class TesteClientePtx(unittest.TestCase):
    LAT = -18.893508
    LON = -43.432549

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="minanav-ptx-")
        cls.exe = os.path.join(cls.dir, "PtxNav.exe")
        p = subprocess.run(["sh", os.path.join(RAIZ, "ptx", "compilar.sh"), cls.exe],
                           capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            raise AssertionError("compilacao falhou:\n" + p.stdout + p.stderr)

        # --- servidor de rotas de verdade, em processo separado
        # (subprocesso, e nao thread, para o teste poder mata-lo de verdade e
        #  ver o cliente degradar; com keep-alive uma thread continuaria
        #  respondendo pelas conexoes ja' abertas)
        cls.porta_srv = porta_livre()
        cls.srv_proc = subprocess.Popen(
            [sys.executable, os.path.join(RAIZ, "servidor", "servidor_rotas.py"),
             "--db", banco(), "--porta", str(cls.porta_srv),
             "--endereco", "127.0.0.1", "--intervalo", "3600",
             "--desmontes", os.path.join(cls.dir, "desmontes.json")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        cls.esperar_servidor()

        # --- serial virtual com posicao parada e valida
        cls.serial = Serial()
        cls.serial.define([rmc(cls.LAT, cls.LON, 0.0, 90.0), gga(cls.LAT, cls.LON)])

        # --- as duas camadas do aufs
        cls.cache_ram = os.path.join(cls.dir, "ram")
        cls.cache_persistente = os.path.join(cls.dir, "realroot")
        cls.log = os.path.join(cls.dir, "ptxnav.log")

        cls.porta = porta_livre()
        cls.proc = subprocess.Popen(
            ["mono", cls.exe,
             "--servidor", "http://127.0.0.1:%d" % cls.porta_srv,
             "--porta", str(cls.porta),
             "--serial", cls.serial.dev,
             "--baud", "9600",
             "--cache", cls.cache_ram,
             "--cache-persistente", cls.cache_persistente,
             "--log", cls.log,
             "--intervalo", "5",
             "--limite-parado", "3"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        cls.esperar_fix()

    @classmethod
    def tearDownClass(cls):
        try: cls.proc.terminate(); cls.proc.wait(timeout=10)
        except Exception:
            try: cls.proc.kill()
            except Exception: pass
        try: cls.proc.stdout.close()
        except Exception: pass
        cls.serial.fecha()
        cls.derruba_servidor()
        shutil.rmtree(cls.dir, ignore_errors=True)

    @classmethod
    def derruba_servidor(cls):
        if getattr(cls, "srv_proc", None) is None:
            return
        try: cls.srv_proc.terminate(); cls.srv_proc.wait(timeout=10)
        except Exception:
            try: cls.srv_proc.kill()
            except Exception: pass
        try: cls.srv_proc.stdout.close()
        except Exception: pass
        cls.srv_proc = None

    @classmethod
    def esperar_servidor(cls, limite=60):
        fim = time.time() + limite
        while time.time() < fim:
            if cls.srv_proc.poll() is not None:
                raise AssertionError("servidor morreu:\n" + cls.srv_proc.stdout.read())
            try:
                j = cls.pega_json("/api/saude", porta=cls.porta_srv)
                if j.get("ok"):
                    return
            except Exception:
                pass
            time.sleep(0.4)
        raise AssertionError("o servidor de rotas nao subiu a tempo")

    # ------------------------------------------------------------ auxiliares
    @classmethod
    def _pega(cls, caminho, porta=None, tempo=5):
        c = http.client.HTTPConnection("127.0.0.1", porta or cls.porta, timeout=tempo)
        c.request("GET", caminho)
        r = c.getresponse()
        corpo = r.read().decode("utf-8")
        c.close()
        return r.status, corpo

    @classmethod
    def pega_json(cls, caminho, porta=None):
        s, b = cls._pega(caminho, porta)
        return json.loads(b)

    @classmethod
    def esperar_fix(cls, limite=60):
        fim = time.time() + limite
        ultimo = ""
        while time.time() < fim:
            if cls.proc.poll() is not None:
                raise AssertionError("PtxNav morreu:\n" + cls.proc.stdout.read())
            try:
                j = cls.pega_json("/pos")
                if j.get("tem_fix"):
                    return j
                ultimo = json.dumps(j)
            except Exception as e:
                ultimo = str(e)
            time.sleep(0.5)
        raise AssertionError("nao chegou fix a tempo; ultimo estado: " + ultimo)

    def esperar(self, cond, limite=25, oque=""):
        fim = time.time() + limite
        while time.time() < fim:
            try:
                v = cond()
                if v: return v
            except Exception:
                pass
            time.sleep(0.4)
        self.fail("tempo esgotado esperando " + oque)

    # ---------------------------------------------------------------- testes
    def test_01_leu_a_posicao_da_serial(self):
        j = self.pega_json("/pos")
        self.assertTrue(j["tem_fix"])
        self.assertAlmostEqual(j["lat"], self.LAT, places=5)
        self.assertAlmostEqual(j["lon"], self.LON, places=5)
        self.assertEqual(j["callsign"], j["callsign"].upper())

    def test_02_estado_conta_linhas_boas(self):
        j = self.pega_json("/estado")
        self.assertGreater(j["linhas_boas"], 0)
        self.assertEqual(j["baud"], 9600)
        self.assertIn("/dev/pts", j["serial"])

    def test_03_descarta_linha_com_checksum_errado(self):
        antes = self.pega_json("/estado")["linhas_ruins"]
        ruim = "$GPRMC,123519.00,A,1853.61048,S,04325.95294,W,0.0,90.0,190826,,,A*00\r\n"
        self.serial.define([rmc(self.LAT, self.LON), gga(self.LAT, self.LON), ruim])
        self.esperar(lambda: self.pega_json("/estado")["linhas_ruins"] > antes,
                     oque="a linha corrompida ser contada")
        # e a posicao continua valida: linha ruim nao contamina o fix
        j = self.pega_json("/pos")
        self.assertTrue(j["tem_fix"])
        self.assertAlmostEqual(j["lat"], self.LAT, places=5)

    def test_04_trava_de_seguranca_por_velocidade(self):
        # parado: pode escolher destino
        self.serial.define([rmc(self.LAT, self.LON, 0.5, 90.0), gga(self.LAT, self.LON)])
        j = self.esperar(lambda: self.pega_json("/pos").get("parado") and self.pega_json("/pos"),
                         oque="o veiculo ser considerado parado")
        self.assertTrue(j["parado"])
        self.assertLess(j["sog_kmh"], 3.0)

        # 20 nos = 37 km/h: em movimento, a interface trava a escolha
        self.serial.define([rmc(self.LAT, self.LON, 20.0, 90.0), gga(self.LAT, self.LON)])
        j = self.esperar(lambda: (not self.pega_json("/pos")["parado"]) and self.pega_json("/pos"),
                         oque="o veiculo ser considerado em movimento")
        self.assertFalse(j["parado"])
        self.assertAlmostEqual(j["sog_kmh"], 20.0 * 1.852, delta=0.5)
        self.serial.define([rmc(self.LAT, self.LON, 0.0, 90.0), gga(self.LAT, self.LON)])

    def test_05_fix_invalido_quando_o_receptor_avisa(self):
        self.serial.define([rmc(self.LAT, self.LON, 0.0, 0.0, valido=False),
                            gga(self.LAT, self.LON, qualidade=0)])
        self.esperar(lambda: not self.pega_json("/pos")["tem_fix"],
                     oque="o fix ser invalidado com status V e qualidade 0")
        self.serial.define([rmc(self.LAT, self.LON), gga(self.LAT, self.LON)])
        self.esperar(lambda: self.pega_json("/pos")["tem_fix"], oque="o fix voltar")

    def test_06_fix_envelhece_quando_a_serial_emudece(self):
        self.serial.define([])                       # receptor calado
        self.esperar(lambda: not self.pega_json("/pos")["tem_fix"], limite=30,
                     oque="o fix vencer por idade (secao 2: 10 s)")
        j = self.pega_json("/pos")
        self.assertGreater(j["idade_s"], 9)
        self.serial.define([rmc(self.LAT, self.LON), gga(self.LAT, self.LON)])
        self.esperar(lambda: self.pega_json("/pos")["tem_fix"], limite=40,
                     oque="o fix voltar depois do silencio")

    def test_07_cache_gravado_nas_duas_camadas(self):
        self.esperar(lambda: self.pega_json("/estado")["tem_cache"], limite=40,
                     oque="a primeira sincronizacao")
        for camada in (self.cache_ram, self.cache_persistente):
            for nome in ("malha.json", "locais.json", "hash.txt"):
                p = os.path.join(camada, nome)
                self.assertTrue(os.path.exists(p), "faltou " + p)
                self.assertGreater(os.path.getsize(p), 0, "vazio: " + p)
        # o que foi gravado e' o mesmo nas duas camadas
        with open(os.path.join(self.cache_ram, "hash.txt")) as f: a = f.read()
        with open(os.path.join(self.cache_persistente, "hash.txt")) as f: b = f.read()
        self.assertEqual(a, b)
        self.assertFalse([f for f in os.listdir(self.cache_ram) if f.endswith(".tmp")],
                         "sobrou arquivo .tmp: a gravacao nao foi atomica")

    def test_08_serve_o_mapa_do_cache(self):
        j = self.pega_json("/malha")
        self.assertEqual(j["type"], "FeatureCollection")
        self.assertEqual(len(j["features"]), 325)
        j = self.pega_json("/locais")
        self.assertGreater(j["total"], 0)

    def test_09_rota_pelo_servidor(self):
        malha = sr.Malha(banco())
        ar = max((a for a in malha.arestas if not a["fechada"]),
                 key=lambda a: a["comprimento_m"])
        from urllib.parse import quote
        j = self.pega_json("/rota?para=" + quote(ar["para"]))
        self.assertTrue(j["ok"], j.get("erro"))
        self.assertGreater(j["distancia_m"], 0)
        self.assertIn("geometria", j)
        self.assertTrue(os.path.exists(os.path.join(self.cache_ram, "ultima_rota.json")))

    def test_10_sem_destino_reclama_em_portugues(self):
        j = self.pega_json("/rota")
        self.assertFalse(j["ok"])
        self.assertIn("destino", j["erro"])

    def test_99_servidor_fora_do_ar_nao_derruba_a_interface(self):
        self.derruba_servidor()
        try:
            j = self.pega_json("/rota?para=QUALQUER")
            self.assertFalse(j["ok"])
            self.assertTrue(j.get("offline"), "faltou marcar offline para a "
                                              "pagina calcular a rota sozinha")
            # a interface e o mapa continuam de pe'
            s, corpo = self._pega("/")
            self.assertEqual(s, 200)
            self.assertIn("<canvas", corpo)
            j = self.pega_json("/malha")
            self.assertEqual(len(j["features"]), 325)
        finally:
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
