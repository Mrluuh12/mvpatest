#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Teste da interface de navegacao num navegador de verdade.

Sobe a pilha inteira — radio Rajant falso, servidor de rotas, serial virtual,
PtxNav.exe — e abre a pagina no Chromium para conferir o que o operador ve':
mapa desenhado, frota no seletor, rota tracada ate um local e ate um
equipamento, e a trava de seguranca quando o veiculo anda.

E' tambem a prova do caminho preferido da secao 6 da especificacao: navegador
em modo quiosque apontando para o localhost.

Pulado se faltar playwright, chromium ou mono.
"""
import json, os, shutil, subprocess, sys, time, unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "testes"))
from teste_servidor import sr, banco
from teste_cliente_ptx import Serial, rmc, gga, porta_livre, FALTA_MONO

CHROMIUM = "/opt/pw-browsers/chromium"
try:
    from playwright.sync_api import sync_playwright
    TEM_PW = True
except ImportError:
    TEM_PW = False

TEM_CHROMIUM = os.path.exists(CHROMIUM)
try:
    import rajant_api  # noqa: F401
    TEM_PROTOBUF = True
except ImportError:
    TEM_PROTOBUF = False


@unittest.skipIf(not TEM_PW or not TEM_CHROMIUM, "playwright/chromium ausentes")
@unittest.skipIf(FALTA_MONO, "mono/mcs nao instalados")
@unittest.skipUnless(TEM_PROTOBUF, "rajant-api nao instalado")
class TesteInterface(unittest.TestCase):
    NOME_EQUIP = "CA-1022"

    @classmethod
    def setUpClass(cls):
        import tempfile
        from radio_falso import RadioFalso

        cls.dir = tempfile.mkdtemp(prefix="minanav-ui-")
        cls.malha = sr.Malha(banco())

        # --- posicoes: o veiculo num no', o equipamento no meio de um trecho
        ar = max((a for a in cls.malha.arestas if not a["fechada"]),
                 key=lambda a: a["comprimento_m"])
        cls.no_origem = ar["de"]
        d = cls.malha.nos[cls.no_origem]
        cls.LAT, cls.LON = d["lat"], d["lon"]
        g = ar["grid"]
        meio = ((g[0][0] + g[-1][0]) / 2, (g[0][1] + g[-1][1]) / 2)
        eq_lat, eq_lon = cls.malha.proj.para_wgs84(*meio)

        def nmea(v, casas):
            neg = v < 0; v = abs(v); i = int(v)
            return "%0*d%08.5f" % (casas, i, (v - i) * 60), neg
        la, sul = nmea(eq_lat, 2); lo, oeste = nmea(eq_lon, 3)

        # --- radio falso na porta escolhida
        porta_radio = porta_livre()
        cls.radio = RadioFalso(senha="segredo", porta=porta_radio, gps={
            "lat": la + ("S" if sul else "N"),
            "lon": lo + ("W" if oeste else "E"),
            "nos": 0.0, "rumo": 45.0, "alt": 800.0})

        cfg = os.path.join(cls.dir, "rajant.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"ips": ["127.0.0.1"],
                       "nomes": {"127.0.0.1": cls.NOME_EQUIP},
                       "senha": "segredo", "porta": porta_radio,
                       "intervalo": 2}, f)

        # --- servidor de rotas com a frota ligada
        cls.porta_srv = porta_livre()
        cls.srv_proc = subprocess.Popen(
            [sys.executable, os.path.join(RAIZ, "servidor", "servidor_rotas.py"),
             "--db", banco(), "--porta", str(cls.porta_srv),
             "--endereco", "127.0.0.1", "--intervalo", "3600",
             "--rajant-config", cfg,
             # arquivo proprio: desmonte cadastrado num teste nao pode
             # aparecer no seguinte
             "--desmontes", os.path.join(cls.dir, "desmontes.json")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        # --- cliente do PTX com serial virtual
        cls.exe = os.path.join(cls.dir, "PtxNav.exe")
        p = subprocess.run(["sh", os.path.join(RAIZ, "ptx", "compilar.sh"), cls.exe],
                           capture_output=True, text=True, timeout=300)
        assert p.returncode == 0, p.stdout + p.stderr

        cls.serial = Serial()
        cls.serial.define([rmc(cls.LAT, cls.LON, 0.0, 45.0), gga(cls.LAT, cls.LON)])
        cls.porta = porta_livre()
        cls.proc = subprocess.Popen(
            ["mono", cls.exe,
             "--servidor", "http://127.0.0.1:%d" % cls.porta_srv,
             "--porta", str(cls.porta), "--serial", cls.serial.dev, "--baud", "9600",
             "--cache", os.path.join(cls.dir, "ram"),
             "--cache-persistente", os.path.join(cls.dir, "rr"),
             "--log", os.path.join(cls.dir, "log"), "--intervalo", "5"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROMIUM)
        cls.pagina = cls.browser.new_page(viewport={"width": 1024, "height": 768})
        cls.pagina.goto("http://127.0.0.1:%d/" % cls.porta, timeout=60000)
        cls.esperar_pronto(cls.pagina)
        cls.pagina.click("#abEntrar")     # sai da tela de boas-vindas

    @classmethod
    def tearDownClass(cls):
        for f in (lambda: cls.browser.close(), lambda: cls.pw.stop()):
            try: f()
            except Exception: pass
        for p in (getattr(cls, "proc", None), getattr(cls, "srv_proc", None)):
            if p is None: continue
            try: p.terminate(); p.wait(timeout=10)
            except Exception:
                try: p.kill()
                except Exception: pass
            try: p.stdout.close()
            except Exception: pass
        try: cls.serial.fecha()
        except Exception: pass
        try: cls.radio.fecha()
        except Exception: pass
        shutil.rmtree(cls.dir, ignore_errors=True)

    @classmethod
    def esperar_pronto(cls, pagina, limite=90):
        """Espera o mapa, o GPS e a frota chegarem na pagina."""
        fim = time.time() + limite
        while time.time() < fim:
            estado = pagina.evaluate(
                "() => ({malha: !!(window.malha && malha.features.length),"
                " pos: !!(window.pos && pos.tem_fix),"
                " frota: (window.frota || []).length})")
            if estado["malha"] and estado["pos"] and estado["frota"]:
                return estado
            time.sleep(0.5)
        raise AssertionError("a interface nao ficou pronta: " + json.dumps(estado))

    def espera(self, expr, limite=30, oque=""):
        fim = time.time() + limite
        while time.time() < fim:
            v = self.pagina.evaluate(expr)
            if v: return v
            time.sleep(0.3)
        self.fail("tempo esgotado esperando " + (oque or expr))

    # ------------------------------------------------------------- testes
    def test_01_pagina_carrega_sem_recurso_externo(self):
        """Nada de CDN: no PTX nao ha internet."""
        html = self.pagina.content()
        self.assertNotIn("http://unpkg", html)
        self.assertNotIn("https://", html.split("<script>")[0])
        self.assertEqual(self.pagina.title(), "Navegacao da mina")

    def test_02_mapa_desenhado_no_canvas(self):
        pintados = self.pagina.evaluate("""() => {
            var c = document.getElementById('tela');
            var d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            var n = 0;
            for (var i = 0; i < d.length; i += 4)
                if (d[i] > 30 || d[i+1] > 30 || d[i+2] > 30) n++;
            return n;
        }""")
        self.assertGreater(pintados, 5000,
                           "o canvas ficou praticamente vazio: mapa nao desenhou")

    def test_03_busca_so_tem_frota(self):
        """A busca lista equipamento e ERM. Os 940 locais da mina ficam fora:
           o operador procura maquina, nao ponto topografico."""
        valores = self.pagina.evaluate(
            "() => Array.from(document.querySelectorAll('#destino option'))"
            ".map(function(o){return o.value;})")
        self.assertIn("E:" + self.NOME_EQUIP, valores)
        self.assertFalse([v for v in valores if v.startswith("L:")],
                         "local topografico vazou para a busca")
        grupos = self.pagina.evaluate(
            "() => Array.from(document.querySelectorAll('#destino optgroup'))"
            ".map(function(g){return g.label;})")
        self.assertTrue(grupos, "seletor sem grupo nenhum")

    def test_04_rodape_mostra_a_distancia(self):
        self.pagina.select_option("#destino", "E:" + self.NOME_EQUIP)
        self.pagina.click("#ir")
        self.espera("() => !!(window.rota && rota.ok)", oque="a rota")
        self.assertGreater(self.pagina.evaluate("() => rota.distancia_m"), 0)
        texto = self.pagina.inner_text("#dist")
        self.assertTrue(texto.endswith("km") or texto.endswith("m"), texto)
        self.assertEqual(self.pagina.evaluate(
            "() => getComputedStyle(document.getElementById('rodape')).display"),
            "block")

    def test_05_traca_rota_ate_um_equipamento(self):
        self.pagina.select_option("#destino", "E:" + self.NOME_EQUIP)
        self.pagina.click("#ir")
        self.espera("() => !!(window.rota && rota.ok && rota.destino_equip)",
                    oque="a rota ate o equipamento")
        r = self.pagina.evaluate(
            "() => ({d: rota.distancia_m, eq: rota.destino_equip,"
            " movel: rota.destino_movel, pontos: rota.geometria.coordinates.length,"
            " nome: rota.equipamento ? rota.equipamento.nome : null})")
        self.assertEqual(r["eq"], self.NOME_EQUIP)
        self.assertTrue(r["movel"])
        self.assertEqual(r["nome"], self.NOME_EQUIP)
        self.assertGreater(r["d"], 0)
        self.assertGreaterEqual(r["pontos"], 2)
        self.assertIn(self.NOME_EQUIP, self.pagina.inner_text("#sub"))

    def test_06b_em_movimento_o_mapa_continua_visivel_e_acompanha(self):
        """Em movimento o operador precisa se ver andando no mapa, na escala
           que ele escolheu. A faixa de distancia fica por cima, nao no lugar
           do mapa: antes era uma cobertura de tela cheia e o mapa sumia."""
        self.pagina.evaluate("() => { esc = 12000; seguir = false; desenha(); }")
        escolhido = self.pagina.evaluate("() => esc")

        self.serial.define([rmc(self.LAT, self.LON, 18.0, 90.0),
                            gga(self.LAT, self.LON)])
        self.espera("() => window.pos && pos.tem_fix && !pos.parado",
                    oque="o veiculo em movimento")
        self.espera("() => getComputedStyle(document.getElementById('movimento'))"
                    ".display !== 'none'", oque="a faixa de movimento")

        # a faixa nao pode engolir a tela
        alto = self.pagina.evaluate(
            "() => document.getElementById('movimento').getBoundingClientRect().height")
        tela = self.pagina.evaluate("() => window.innerHeight")
        self.assertLess(alto, tela * 0.5,
                        "a faixa de movimento cobriu o mapa")

        # o mapa continua sendo desenhado por baixo dela
        pintado = self.pagina.evaluate("""() => {
            var c = document.getElementById('tela');
            var d = c.getContext('2d').getImageData(0, Math.round(c.height * 0.6),
                                                    c.width, 10).data;
            var n = 0;
            for (var i = 0; i < d.length; i += 4)
                if (d[i] || d[i+1] || d[i+2]) n++;
            return n;
        }""")
        self.assertGreater(pintado, 50, "o mapa nao esta' sendo desenhado")

        # acompanha o veiculo, e sem mexer na escala escolhida
        self.assertTrue(self.pagina.evaluate("() => seguir"),
                        "o mapa nao passou a acompanhar o veiculo")
        self.assertAlmostEqual(self.pagina.evaluate("() => esc"), escolhido,
                               delta=1.0, msg="a escala do operador foi trocada")

    def test_06_trava_de_seguranca_em_movimento(self):
        """Secao 6: em movimento, so' direcao e distancia, sem interacao."""
        self.serial.define([rmc(self.LAT, self.LON, 20.0, 45.0),
                            gga(self.LAT, self.LON)])
        self.espera("() => window.pos && pos.tem_fix && !pos.parado",
                    oque="o cliente ver o veiculo em movimento")
        self.espera("() => getComputedStyle(document.getElementById('movimento'))"
                    ".display !== 'none'", oque="a faixa de movimento")
        self.assertTrue(self.pagina.evaluate(
            "() => document.getElementById('destino').disabled"),
            "o seletor tinha que estar travado com o veiculo andando")
        self.assertTrue(self.pagina.evaluate(
            "() => document.getElementById('ir').disabled"))
        self.assertTrue(self.pagina.inner_text("#mvDist").strip())

        # parou: a interface volta ao normal
        self.serial.define([rmc(self.LAT, self.LON, 0.0, 45.0),
                            gga(self.LAT, self.LON)])
        self.espera("() => window.pos && pos.parado", oque="o veiculo parar")
        self.espera("() => getComputedStyle(document.getElementById('movimento'))"
                    ".display === 'none'", oque="a faixa de movimento sumir")
        self.assertFalse(self.pagina.evaluate(
            "() => document.getElementById('destino').disabled"))

    def test_07_aviso_quando_o_gps_cai(self):
        self.serial.define([])
        self.espera("() => document.querySelectorAll('#avisos .aviso').length > 0",
                    limite=40, oque="o aviso de GPS")
        texto = self.pagina.inner_text("#avisos")
        self.assertIn("GPS", texto.upper())
        self.serial.define([rmc(self.LAT, self.LON, 0.0, 45.0),
                            gga(self.LAT, self.LON)])
        self.espera("() => window.pos && pos.tem_fix", limite=40,
                    oque="o GPS voltar")

    def test_08_zoom_e_centrar(self):
        antes = self.pagina.evaluate("() => window.esc")
        self.pagina.click("#mais")
        self.assertGreater(self.pagina.evaluate("() => window.esc"), antes)
        self.pagina.click("#menos")
        self.pagina.click("#eu")
        centrado = self.pagina.evaluate(
            "() => ({cx: window.cx, cy: window.cy, lat: pos.lat, lon: pos.lon})")
        self.assertAlmostEqual(centrado["cx"], centrado["lon"], places=6)
        self.assertAlmostEqual(centrado["cy"], centrado["lat"], places=6)

    def test_09_tema_dia_e_noite(self):
        self.pagina.click("#tema")
        self.assertEqual(self.pagina.evaluate("() => document.body.className"), "dia")
        self.pagina.click("#tema")
        self.assertEqual(self.pagina.evaluate("() => document.body.className"), "")

    def test_10_alvos_de_toque_tem_pelo_menos_48px(self):
        """Operador de luva: secao 6 pede alvo minimo de 48 px."""
        alvos = self.pagina.evaluate("""() => {
            var ids = ['ir','limpar','mais','menos','eu','tema','destino'], r = {};
            for (var i = 0; i < ids.length; i++) {
                var e = document.getElementById(ids[i]);
                var b = e.getBoundingClientRect();
                r[ids[i]] = [Math.round(b.width), Math.round(b.height)];
            }
            return r;
        }""")
        for nome, (w, h) in alvos.items():
            if nome == "limpar" and w == 0:
                continue                      # escondido quando nao ha rota
            self.assertGreaterEqual(h, 48, f"{nome} tem {h}px de altura")
            self.assertGreaterEqual(w, 48, f"{nome} tem {w}px de largura")



    def test_11_mapa_mostra_so_frota_por_padrao(self):
        """Os 940 locais poluiam o mapa. Por padrao ficam escondidos; a frota
           (equipamento e ERM) e' o que aparece."""
        self.assertFalse(self.pagina.evaluate("() => window.mostrarLocais"))
        # o menu traz eles de volta
        self.pagina.click("#btMenu")
        self.pagina.click("#chPontos")
        self.assertTrue(self.pagina.evaluate("() => window.mostrarLocais"))
        self.pagina.click("#chPontos")
        self.assertFalse(self.pagina.evaluate("() => window.mostrarLocais"))
        self.pagina.click("#fecharMenu")
        # o mapa volta a desenhar os pontos, mas a busca segue so' com frota
        n = self.pagina.evaluate(
            "() => Array.from(document.querySelectorAll('#destino option'))"
            ".filter(function(o){return o.value.substring(0,2)==='L:';}).length")
        self.assertEqual(n, 0)
        self.assertGreater(self.pagina.evaluate("() => (window.locais||[]).length"), 0,
                           "os locais tem que continuar carregados para desenhar")

    def test_12_equipamento_desenhado_no_mapa(self):
        """O equipamento tem que aparecer no canvas, com o nome."""
        pintou = self.pagina.evaluate("""() => {
            var antes = document.getElementById('tela');
            var g = antes.getContext('2d');
            var d0 = g.getImageData(0, 0, antes.width, antes.height).data;
            var n0 = 0;
            for (var i = 0; i < d0.length; i += 4) if (d0[i+1] > 100) n0++;
            return n0;
        }""")
        self.assertGreater(pintou, 0, "nada colorido no mapa")
        # a frota chegou e tem o nosso equipamento
        nomes = self.pagina.evaluate(
            "() => (window.frota || []).map(function(e){return e.nome;})")
        self.assertIn(self.NOME_EQUIP, nomes)



    def test_13_abertura_mostra_o_estado_antes_de_entrar(self):
        """O operador sobe no equipamento e ve' de cara se da' para navegar."""
        p = self.browser.new_page(viewport={"width": 1024, "height": 768})
        try:
            p.goto("http://127.0.0.1:%d/" % self.porta, timeout=60000)
            self.esperar_pronto(p)
            self.assertEqual(p.evaluate(
                "() => getComputedStyle(document.getElementById('abertura')).display"),
                "flex")
            self.assertIn("BEM-VINDO", p.inner_text("#abSub"))
            fim = time.time() + 20
            while time.time() < fim:
                if "OK" in p.inner_text("#mkMapa") and "OK" in p.inner_text("#mkGps"):
                    break
                time.sleep(0.4)
            self.assertIn("OK", p.inner_text("#mkMapa"), p.inner_text("#abChecagem"))
            self.assertIn("OK", p.inner_text("#mkGps"))
            self.assertIn("trechos", p.inner_text("#txMapa"))
            p.click("#abEntrar")
            self.assertEqual(p.evaluate(
                "() => getComputedStyle(document.getElementById('abertura')).display"),
                "none")
        finally:
            p.close()

    def test_14_menu_traz_diagnostico_e_servidor(self):
        self.pagina.click("#btMenu")
        self.assertEqual(self.pagina.evaluate(
            "() => getComputedStyle(document.getElementById('menu')).display"), "block")
        self.assertIn("http://", self.pagina.inner_text("#mnServidorVal"))
        self.assertIn("/dev/pts", self.pagina.inner_text("#mnFonte"))
        self.assertIn("boas", self.pagina.inner_text("#mnLinhas"))
        self.assertIn("trechos", self.pagina.inner_text("#mnMapa"))
        self.assertTrue(self.pagina.inner_text("#mnCall").strip())
        self.pagina.click("#fecharMenu")
        self.assertEqual(self.pagina.evaluate(
            "() => getComputedStyle(document.getElementById('menu')).display"), "none")

    def setUp(self):
        # menu aberto por um teste anterior cobriria os botoes do seguinte
        self.pagina.evaluate("() => { fechaMenu();"
                             " document.getElementById('painelFogo').style.display='none'; }")

    def test_15_menu_troca_camadas(self):
        self.pagina.click("#btMenu")
        antes = self.pagina.evaluate("() => window.mostrarLocais")
        self.pagina.click("#chPontos")
        self.assertNotEqual(self.pagina.evaluate("() => window.mostrarLocais"), antes)
        self.pagina.click("#chPontos")
        antes = self.pagina.evaluate("() => window.mostrarDesmonte")
        self.pagina.click("#chDesmonte")
        self.assertNotEqual(self.pagina.evaluate("() => window.mostrarDesmonte"), antes)
        self.pagina.click("#chDesmonte")
        self.pagina.click("#fecharMenu")

    def test_16_desmonte_aparece_e_avisa_quem_esta_dentro(self):
        """Cadastra um desmonte em cima do veiculo e confere os dois efeitos:
           o circulo no mapa e o aviso de que ele esta' na area."""
        import http.client as hc
        c = hc.HTTPConnection("127.0.0.1", self.porta_srv, timeout=10)
        c.request("POST", "/api/desmontes",
                  body=json.dumps({"nome": "Teste bancada", "lat": self.LAT,
                                   "lon": self.LON, "raio_m": 400}),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse(); criado = json.loads(r.read().decode()); c.close()
        self.assertTrue(criado.get("ok"), criado)
        try:
            self.pagina.evaluate("() => leDesmontes()")
            self.espera("() => (window.desmontes||[]).length > 0",
                        oque="o desmonte chegar no PTX")
            self.espera("() => document.getElementById('avisos').textContent"
                        ".indexOf('AREA DE DESMONTE') >= 0",
                        oque="o aviso de estar dentro da area")
            texto = self.pagina.inner_text("#avisos")
            self.assertIn("Teste bancada", texto)
            # zoom em que o raio de 400 m cabe na tela, senao a borda fica fora
            # dela e so' o preenchimento aparece
            self.pagina.evaluate("() => { centrar(); esc = 42000; desenha(); }")
            vermelho = self.pagina.evaluate("""() => {
                var c = document.getElementById('tela');
                var d = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                var n = 0;
                for (var i = 0; i < d.length; i += 4)
                    if (d[i] > 150 && d[i+1] < 90 && d[i+2] < 90) n++;
                return n;
            }""")
            self.assertGreater(vermelho, 200, "o circulo do desmonte nao apareceu")
        finally:
            c = hc.HTTPConnection("127.0.0.1", self.porta_srv, timeout=10)
            c.request("POST", "/api/desmontes/remover",
                      body=json.dumps({"id": criado["id"]}),
                      headers={"Content-Type": "application/json"})
            c.getresponse().read(); c.close()

    def test_17_pagina_de_desmonte_do_servidor(self):
        import http.client as hc
        c = hc.HTTPConnection("127.0.0.1", self.porta_srv, timeout=10)
        c.request("GET", "/desmonte")
        r = c.getresponse(); html = r.read().decode(); c.close()
        self.assertEqual(r.status, 200)
        self.assertIn('id="nome"', html)
        self.assertNotIn("http://unpkg", html)



    def test_18_painel_lista_so_as_erms(self):
        """So' ERM na lista: e' ela que precisa de equipe indo ate' la'.
           Caminhao e perfuratriz saem por conta propria."""
        frota = self.pagina.evaluate("""() => {
            var d = { nome: 'CS_0020_091', raio_m: 400, lat: pos.lat, lon: pos.lon,
                      n_pontos: 1, pontos: [{lat: pos.lat, lon: pos.lon}] };
            function perto(dlat, dlon) {
                return { lat: pos.lat + dlat, lon: pos.lon + dlon };
            }
            d.afetados = { dentro: [
                { nome: 'CA-1022', tipo: 'caminhao', movel: true,
                  distancia_m: 85, lat: perto(0.0005,0).lat, lon: pos.lon },
                { nome: 'ERM-07', tipo: 'repetidora', movel: false,
                  distancia_m: 89, lat: perto(0.0006,0).lat, lon: pos.lon },
                { nome: 'ERM-12', tipo: 'repetidora', movel: false,
                  distancia_m: 286, lat: perto(0.002,0).lat, lon: pos.lon }
            ], perto: [], locais_dentro: [] };
            window.desmontes = [d];
            indexaFogo();
            abrePainelFogo();
            return document.getElementById('fogoLista').innerText;
        }""")
        # o cabecalho das ERMs vem antes do de equipamento
        self.assertIn("ERMs que devem sair (2)", frota)
        self.assertIn("ERM-07", frota)
        self.assertIn("ERM-12", frota)
        # equipamento movel nao entra: sai por conta propria
        self.assertNotIn("CA-1022", frota)
        self.assertNotIn("Equipamento movel", frota)
        # o contador do botao conta ERM, nao tudo
        self.assertEqual(self.pagina.inner_text("#badgeFogo").strip(), "2")
        self.assertIn("2 ERM a retirar", self.pagina.inner_text("#fogoSub"))
        self.pagina.click("#fecharFogo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
