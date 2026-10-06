#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testes do app Android.

Sem Android SDK aqui, o Kotlin nao compila. O que da' para provar e' o que
mais importa: a interface.html e' a MESMA nas duas plataformas, e o app so'
precisa responder o contrato que ela espera. O simulador responde igual ao
Api.kt, e a pagina real roda em cima dele no Chromium.

O teste de consistencia compara os campos do simulador com os do Kotlin: se
um lado mudar sem o outro, acusa aqui em vez de acusar no tablet.
"""
import json, os, re, shutil, subprocess, sys, tempfile, threading, time, unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "testes"))
from teste_servidor import sr, banco
from teste_cliente_ptx import porta_livre
import simulador_android as sim

CHROMIUM = "/opt/pw-browsers/chromium"
try:
    from playwright.sync_api import sync_playwright
    TEM_PW = True
except ImportError:
    TEM_PW = False
KOTLIN = os.path.join(RAIZ, "android", "app", "src", "main", "java",
                      "br", "com", "mina", "navegacao")


class TesteContratoComOKotlin(unittest.TestCase):
    """O simulador so' vale se responder o mesmo que o Kotlin."""

    def campos_kotlin(self, arquivo, funcao):
        with open(os.path.join(KOTLIN, arquivo), encoding="utf-8") as f:
            texto = f.read()
        i = texto.index(funcao)
        trecho = texto[i:i + 1600]
        return set(re.findall(r'"(\w+)":', trecho))

    def test_pos_tem_os_mesmos_campos(self):
        gps = sim.GpsDoTablet()
        gps.registra(-18.89, -43.43)
        do_sim = set(gps.json().keys())
        do_kotlin = self.campos_kotlin("Posicao.kt", "fun json(")
        self.assertEqual(do_kotlin - do_sim, set(),
                         "Posicao.kt manda campo que o simulador nao tem")
        self.assertEqual(do_sim - do_kotlin, set(),
                         "o simulador manda campo que Posicao.kt nao tem")

    def test_estado_tem_os_mesmos_campos(self):
        app = sim.AppAndroid("http://127.0.0.1:1", sim.GpsDoTablet())
        do_sim = set(app.estado().keys())
        do_kotlin = self.campos_kotlin("Sincronizacao.kt", "fun estadoJson(")
        self.assertEqual(do_kotlin - do_sim, set(),
                         "Sincronizacao.kt manda campo que o simulador nao tem")
        self.assertEqual(do_sim - do_kotlin, set(),
                         "o simulador manda campo que Sincronizacao.kt nao tem")

    def test_a_interface_do_android_e_a_mesma_do_ptx(self):
        """Duas copias da tela divergiriam, e o campo pagaria a conta."""
        a = os.path.join(RAIZ, "ptx", "interface.html")
        b = os.path.join(RAIZ, "android", "app", "src", "main", "assets",
                         "interface.html")
        self.assertTrue(os.path.exists(b), "falta a interface nos assets do app")
        with open(a, "rb") as f1, open(b, "rb") as f2:
            self.assertEqual(f1.read(), f2.read(),
                             "a interface do app saiu de sincronia com a do PTX; "
                             "copie: cp ptx/interface.html android/app/src/main/"
                             "assets/interface.html")

    def test_o_build_nao_reescreve_os_assets(self):
        """Nenhuma tarefa do Gradle pode escrever dentro de src/main/assets.

           Havia um Copy trazendo ../ptx/interface.html a cada build. Em quem
           recebeu so' a pasta android/ e a extraiu ao lado de um ptx/ antigo,
           ele substituia a tela do APK pela velha, sem dizer nada: o tablet
           mostrava uma interface que ja' nao existia no repositorio. O asset
           versionado e' a fonte."""
        caminho = os.path.join(RAIZ, "android", "app", "build.gradle.kts")
        with open(caminho, encoding="utf-8") as f:
            linhas = [l for l in f if not l.lstrip().startswith("//")]
        script = "".join(linhas)
        self.assertNotIn("into(layout.projectDirectory.dir(\"src/main/assets", script,
                         "tarefa de build escrevendo nos assets: o arquivo "
                         "entregue no zip pode ser sobrescrito por uma copia velha")
        self.assertNotIn("Copy::class", script,
                         "sem copias no build do app; os assets vao versionados")

    def test_o_app_pede_permissao_de_localizacao(self):
        with open(os.path.join(RAIZ, "android", "app", "src", "main",
                               "AndroidManifest.xml"), encoding="utf-8") as f:
            m = f.read()
        self.assertIn("ACCESS_FINE_LOCATION", m)
        self.assertIn("android.hardware.location.gps", m)

    def test_config_de_rede_e_valida(self):
        """O servidor da mina fala HTTP puro, entao o cleartext tem que estar
           liberado. E o arquivo tem que obedecer o schema: o parser roda
           quando o processo sobe, e um erro aqui fecha o app antes da tela —
           foi o que aconteceu."""
        import xml.etree.ElementTree as ET
        caminho = os.path.join(RAIZ, "android", "app", "src", "main", "res",
                               "xml", "rede.xml")
        raiz = ET.parse(caminho).getroot()
        self.assertEqual(raiz.tag, "network-security-config")

        filhos = [e.tag for e in raiz]
        self.assertIn("base-config", filhos, "falta o base-config")

        # o schema exige base-config ANTES de qualquer domain-config
        if "domain-config" in filhos:
            self.assertLess(filhos.index("base-config"),
                            filhos.index("domain-config"),
                            "base-config tem que vir antes de domain-config")

        base = raiz.find("base-config")
        self.assertEqual(base.get("cleartextTrafficPermitted"), "true",
                         "sem cleartext o app nao alcanca o servidor da mina")

        # <domain> espera nome de host; IP ali nao faz o que parece
        for dc in raiz.findall("domain-config"):
            for d in dc.findall("domain"):
                texto = (d.text or "").strip()
                so_numeros_e_pontos = texto.replace(".", "").isdigit()
                self.assertFalse(so_numeros_e_pontos,
                                 f"<domain> com IP ({texto}): use base-config")



class TesteReferenciasKotlin(unittest.TestCase):
    """Nao ha compilador de Kotlin nesta maquina, e sem ele um metodo que nao
       existe so' aparece quando o Android Studio abre o projeto — depois de o
       zip ja' ter sido entregue. Foi exatamente o que aconteceu com
       `posicao.semPermissao()`: a chamada entrou, o metodo nao. Esta conferencia
       resolve os nomes de um arquivo para outro e derruba o caso antes disso."""

    # nome.membro, sem casar numero (1.5) nem chamada encadeada em maiuscula
    USO = re.compile(r"\b([a-z]\w*)\.([a-zA-Z_]\w*)")
    DECL_FUN = re.compile(r"\bfun\s+(\w+)\s*\(")
    DECL_PROP = re.compile(r"\b(?:va[lr])\s+(\w+)\s*[:=]")
    # val posicao = Posicao(...)  /  lateinit var api: Api  /  val c: Config = ...
    ATRIB = re.compile(r"\b(?:va[lr])\s+(\w+)\s*=\s*([A-Z]\w*)\s*\(")
    TIPADO = re.compile(r"\b(?:va[lr])\s+(\w+)\s*:\s*([A-Z]\w*)")

    def fontes(self):
        for nome in sorted(os.listdir(KOTLIN)):
            if nome.endswith(".kt"):
                with open(os.path.join(KOTLIN, nome), encoding="utf-8") as f:
                    yield nome, f.read()

    def test_toda_chamada_entre_arquivos_existe(self):
        membros = {}
        for nome, texto in self.fontes():
            tipo = nome[:-3]
            membros[tipo] = (set(self.DECL_FUN.findall(texto)) |
                             set(self.DECL_PROP.findall(texto)))

        conferidas = 0
        for nome, texto in self.fontes():
            # de que tipo e' cada nome deste arquivo
            tipos = {}
            for var, tipo in self.ATRIB.findall(texto) + self.TIPADO.findall(texto):
                if tipo in membros:
                    tipos[var] = tipo
            for var, membro in self.USO.findall(texto):
                tipo = tipos.get(var)
                if tipo is None:
                    continue
                conferidas += 1
                self.assertIn(membro, membros[tipo],
                              f"{nome}: {var}.{membro}() nao existe em "
                              f"{tipo}.kt — o projeto nao compila")

        # se o resolvedor deixar de enxergar as chamadas, o teste passa vazio e
        # nao protege mais nada
        self.assertGreater(conferidas, 20,
                           "o resolvedor parou de enxergar as chamadas")

class TesteRecursosDoApp(unittest.TestCase):
    """Referencia quebrada no manifesto so' aparece no aapt, ja' no Android
       Studio, depois de baixar o SDK inteiro. Aqui aparece em um segundo."""

    APP = os.path.join(RAIZ, "android", "app", "src", "main")

    def valores_declarados(self):
        """Tudo que os res/values*/*.xml declaram, por tipo."""
        import xml.etree.ElementTree as ET
        declarados = {}
        raiz_res = os.path.join(self.APP, "res")
        for pasta in os.listdir(raiz_res):
            if not pasta.startswith("values"):
                continue
            caminho = os.path.join(raiz_res, pasta)
            for arq in os.listdir(caminho):
                if not arq.endswith(".xml"):
                    continue
                for el in ET.parse(os.path.join(caminho, arq)).getroot():
                    tipo = el.get("type") or el.tag       # <item type=...> ou <string>
                    nome = el.get("name")
                    if nome:
                        declarados.setdefault(tipo, set()).add(nome)
        return declarados

    def arquivos_de_recurso(self):
        """Tudo que existe como arquivo em res/<tipo>[-qualificador]/."""
        arquivos = {}
        raiz_res = os.path.join(self.APP, "res")
        for pasta in os.listdir(raiz_res):
            tipo = pasta.split("-")[0]
            caminho = os.path.join(raiz_res, pasta)
            if not os.path.isdir(caminho):
                continue
            for arq in os.listdir(caminho):
                nome = os.path.splitext(arq)[0]
                arquivos.setdefault(tipo, set()).add(nome)
        return arquivos

    def test_manifesto_nao_aponta_para_recurso_inexistente(self):
        caminho = os.path.join(self.APP, "AndroidManifest.xml")
        with open(caminho, encoding="utf-8") as f:
            manifesto = f.read()

        referencias = set(re.findall(r'"@([a-z]+)/([A-Za-z0-9_]+)"', manifesto))
        self.assertTrue(referencias, "nenhuma referencia encontrada no manifesto")

        valores = self.valores_declarados()
        arquivos = self.arquivos_de_recurso()
        faltando = []
        for tipo, nome in sorted(referencias):
            # cada tipo resolve no proprio tipo: para o aapt, @mipmap/x nao
            # e' atendido por um res/drawable/x. Foi assim que o build quebrou.
            existe = (nome in valores.get(tipo, set()) or
                      nome in arquivos.get(tipo, set()))
            if not existe:
                faltando.append(f"@{tipo}/{nome}")
        self.assertEqual(faltando, [],
                         "o manifesto aponta para recurso que nao existe: " +
                         ", ".join(faltando))

    def test_tema_do_manifesto_nao_exige_biblioteca_ausente(self):
        """AppCompatActivity com tema que nao seja Theme.AppCompat estoura
           DENTRO do super.onCreate — antes de qualquer try/catch nosso, e o
           app fecha sem dizer nada. Foi assim que ele fechou no tablet."""
        with open(os.path.join(self.APP, "AndroidManifest.xml"), encoding="utf-8") as f:
            manifesto = f.read()
        with open(os.path.join(RAIZ, "android", "app", "build.gradle.kts"),
                  encoding="utf-8") as f:
            gradle = f.read()
        fontes = ""
        for arq in os.listdir(KOTLIN):
            with open(os.path.join(KOTLIN, arq), encoding="utf-8") as f:
                fontes += f.read()

        usa_appcompat = "AppCompatActivity" in fontes
        tem_dependencia = "androidx.appcompat" in gradle
        tema_appcompat = "Theme.AppCompat" in manifesto or "Theme.AppCompat" in fontes

        if usa_appcompat:
            self.assertTrue(tem_dependencia,
                            "AppCompatActivity sem a dependencia androidx.appcompat")
            self.assertTrue(tema_appcompat,
                            "AppCompatActivity exige um tema Theme.AppCompat")
        if tema_appcompat:
            self.assertTrue(tem_dependencia,
                            "tema Theme.AppCompat sem a dependencia que o define")

    def test_apk_leva_o_mapa_dentro(self):
        """Sem o cache semeado, o tablet abre sem mapa nenhum ate' alcancar o
           servidor — e ai' a tela nao parece nada com a do PTX. Aconteceu."""
        semente = os.path.join(self.APP, "assets", "semente")
        for nome in ("malha.json", "locais.json", "hash.txt"):
            caminho = os.path.join(semente, nome)
            self.assertTrue(os.path.exists(caminho), f"falta {nome} na semente")
            self.assertGreater(os.path.getsize(caminho), 0, f"{nome} vazio")
        with open(os.path.join(semente, "malha.json"), encoding="utf-8") as f:
            malha = json.load(f)
        self.assertGreater(len(malha["features"]), 100,
                           "a malha semeada nao tem trechos")

    def test_posicao_nao_depende_so_do_gps(self):
        """So' o GPS_PROVIDER nao pega fix dentro de predio, e o app fica
           dizendo "sem posicao" numa mesa, sem jeito de conferir nada."""
        with open(os.path.join(KOTLIN, "Posicao.kt"), encoding="utf-8") as f:
            fonte = f.read()
        self.assertIn("NETWORK_PROVIDER", fonte)
        # e o GPS tem que continuar mandando quando esta' fresco
        self.assertIn("ehGps", fonte)

    def test_gradle_kts_sem_referencia_ao_pacote_java(self):
        """Em script Gradle Kotlin, "java" resolve para a extensao do plugin
           Java, nao para o pacote: java.time.LocalDateTime vira "Unresolved
           reference: time" e o sync inteiro falha. Ja' aconteceu."""
        raiz = os.path.join(RAIZ, "android")
        problemas = []
        for pasta, _, arquivos in os.walk(raiz):
            for nome in arquivos:
                if not nome.endswith(".kts"):
                    continue
                caminho = os.path.join(pasta, nome)
                with open(caminho, encoding="utf-8") as f:
                    for n, linha in enumerate(f, 1):
                        codigo = linha.split("//")[0]
                        if re.search(r"(^|[^\w.\"])java\.(time|text|util|io|net)\.",
                                     codigo):
                            problemas.append(f"{nome}:{n}: {linha.strip()}")
        self.assertEqual(problemas, [],
                         "referencia ao pacote java dentro de .kts:\n" +
                         "\n".join(problemas))

    def test_tela_cheia_so_depois_do_setContentView(self):
        """Antes do setContentView nao existe DecorView, e pedir o controlador
           de insets a uma janela sem decor da' NullPointerException — foi o
           que fechou o app no tablet."""
        with open(os.path.join(KOTLIN, "MainActivity.kt"), encoding="utf-8") as f:
            fonte = f.read()
        corpo = fonte[fonte.index("private fun monta("):]
        corpo = corpo[:corpo.index("\n    private fun ", 10)]

        self.assertIn("setContentView(", corpo)
        chamada = corpo.find("telaCheia()")
        conteudo = corpo.find("setContentView(")
        if chamada >= 0:
            self.assertGreater(chamada, conteudo,
                               "telaCheia() antes do setContentView: a janela "
                               "ainda nao tem DecorView")

    def test_ajustes_de_aparencia_nao_derrubam_o_app(self):
        """Barra de status escondida e' acabamento. Falhar nisso nao pode
           tirar a navegacao do operador."""
        with open(os.path.join(KOTLIN, "MainActivity.kt"), encoding="utf-8") as f:
            fonte = f.read()
        corpo = fonte[fonte.index("private fun telaCheia("):]
        corpo = corpo[:corpo.index("\n    override fun ")]
        self.assertIn("try {", corpo)
        self.assertIn("catch", corpo)

    def test_classe_de_application_do_manifesto_existe(self):
        with open(os.path.join(self.APP, "AndroidManifest.xml"), encoding="utf-8") as f:
            manifesto = f.read()
        m = re.search(r'<application[^>]*android:name="\.(\w+)"', manifesto,
                      re.DOTALL)
        if m:
            self.assertTrue(os.path.exists(os.path.join(KOTLIN, m.group(1) + ".kt")),
                            f"o manifesto declara .{m.group(1)} e o arquivo nao existe")

    def test_tem_icone_de_aplicativo(self):
        with open(os.path.join(self.APP, "AndroidManifest.xml"), encoding="utf-8") as f:
            self.assertIn("android:icon=", f.read())


@unittest.skipIf(not TEM_PW or not os.path.exists(CHROMIUM),
                 "playwright/chromium ausentes")
class TesteFotoNoTablet(unittest.TestCase):
    """A foto aerea, do .tif ate' o pixel na tela do tablet.

       Conferir que o mosaico foi gerado nao prova nada: o que importa e' a
       foto aparecer embaixo da malha, no lugar certo, e sumir quando o
       operador desliga a camada."""

    LAT, LON = -18.915, -43.42

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(RAIZ, "servidor"))
        import mapa_foto as mf
        import gerar_geotiff as gg
        cls.dir = tempfile.mkdtemp(prefix="foto-tablet-")
        malha = sr.Malha(banco())
        cls.LAT, cls.LON = malha.proj.para_wgs84(
            *[(a + b) / 2 for a, b in zip(
                mf.limites_da_malha(malha, folga=0)[:2],
                mf.limites_da_malha(malha, folga=0)[2:])])

        # foto azul solida cobrindo a mina: da' para achar no canvas sem duvida
        utm = mf.projecao_utm(23, "S")
        lim = mf.limites_da_malha(malha, folga=300)
        cantos = [utm.para_grid(*malha.proj.para_wgs84(e, n))
                  for e in (lim[0], lim[2]) for n in (lim[1], lim[3])]
        X0 = min(c[0] for c in cantos); Y1 = max(c[1] for c in cantos)
        res = 8.0
        W = int((max(c[0] for c in cantos) - X0) / res)
        H = int((Y1 - min(c[1] for c in cantos)) / res)
        px = bytes([0, 0, 255]) * (W * H)
        tif = os.path.join(cls.dir, "orto.tif")
        gg.escrever(tif, W, H, px, (res, res), (X0, Y1), epsg=31983,
                    compressao=8, por_faixa=64)
        cls.foto = os.path.join(cls.dir, "foto")
        mf.gerar_mosaico(tif, malha.proj, cls.foto, proj_foto=utm,
                         limites=mf.limites_da_malha(malha), mais_fino=32.0,
                         registrar=lambda *a: None)

        cls.porta_srv = porta_livre()
        cls.srv_proc = subprocess.Popen(
            [sys.executable, os.path.join(RAIZ, "servidor", "servidor_rotas.py"),
             "--db", banco(), "--porta", str(cls.porta_srv),
             "--endereco", "127.0.0.1", "--intervalo", "3600",
             "--foto", cls.foto,
             "--desmontes", os.path.join(cls.dir, "desmontes.json")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        fim = time.time() + 60
        import http.client
        while time.time() < fim:
            try:
                c = http.client.HTTPConnection("127.0.0.1", cls.porta_srv, timeout=5)
                c.request("GET", "/api/saude")
                if json.loads(c.getresponse().read().decode())["ok"]:
                    c.close(); break
                c.close()
            except Exception:
                time.sleep(0.5)

        cls.gps = sim.GpsDoTablet("TABLET-FOTO")
        cls.gps.registra(cls.LAT, cls.LON, vel_kmh=0.0, cog=0.0)
        cls.app = sim.AppAndroid(f"http://127.0.0.1:{cls.porta_srv}", cls.gps)
        assert cls.app.sincroniza(), "o app nao sincronizou"
        cls.srv_app = sim.cria_servidor(cls.app)
        cls.porta_app = cls.srv_app.server_address[1]

        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROMIUM)
        cls.pagina = cls.browser.new_page(viewport={"width": 1280, "height": 800})
        cls.pagina.goto(f"http://127.0.0.1:{cls.porta_app}/", timeout=60000)
        TesteAppNoNavegador.espera(
            cls.pagina, "() => !!(window.malha && window.foto)",
            "a interface achar a foto")
        cls.pagina.click("#abEntrar")
        cls.pagina.wait_for_timeout(1500)

    @classmethod
    def tearDownClass(cls):
        for f in (lambda: cls.browser.close(), lambda: cls.pw.stop(),
                  lambda: cls.srv_app.shutdown(), lambda: cls.srv_app.server_close()):
            try: f()
            except Exception: pass
        try:
            cls.srv_proc.terminate(); cls.srv_proc.wait(timeout=10)
        except Exception:
            pass
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _azul_na_tela(self):
        return self.pagina.evaluate("""() => {
            var c = document.getElementById('tela');
            var d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            var n = 0;
            for (var i = 0; i < d.length; i += 4) {
                if (d[i+2] > 150 && d[i] < 90 && d[i+1] < 90) n++;
            }
            return n;
        }""")

    def test_1_a_camada_aparece_no_menu(self):
        """Sem foto no servidor a linha fica escondida; com foto, aparece."""
        self.pagina.click("#btMenu")
        self.pagina.wait_for_timeout(300)
        visivel = self.pagina.is_visible("#linhaFoto")
        self.pagina.click("#fecharMenu")
        self.assertTrue(visivel, "a linha da foto nao apareceu no menu")

    def test_2_a_foto_e_desenhada_no_mapa(self):
        self.assertGreater(self._azul_na_tela(), 20000,
                           "a foto nao chegou ao canvas")

    def test_3_desligar_a_camada_tira_a_foto(self):
        self.pagina.click("#btMenu")
        self.pagina.click("#chFoto")
        self.pagina.wait_for_timeout(500)
        desligada = self._azul_na_tela()
        self.pagina.click("#chFoto")
        self.pagina.wait_for_timeout(800)
        ligada = self._azul_na_tela()
        self.pagina.click("#fecharMenu")
        self.assertLess(desligada, 2000, "a foto continuou na tela depois de desligada")
        self.assertGreater(ligada, 20000, "a foto nao voltou ao religar a camada")

    def test_3b_a_foto_insiste_quando_a_primeira_tentativa_falha(self):
        """No arranque o Wi-Fi do tablet as vezes ainda nao subiu. Com uma
           tentativa so', a foto ficava fora pelo resto da sessao — foi o que
           aconteceu em campo. Aqui a tela perde o manifesto e tem que
           recupera-lo sozinha."""
        self.pagina.evaluate("() => { window.foto = null; }")
        self.assertIsNone(self.pagina.evaluate("() => window.foto"))
        # o laco tenta a cada 20 s
        self.pagina.wait_for_function("() => !!window.foto", timeout=40000)
        self.assertGreater(self._azul_na_tela(), 20000,
                           "a foto nao voltou a ser desenhada")

    def test_4_a_malha_continua_por_cima(self):
        """A foto e' fundo. Se ela cobrisse a estrada, o mapa perderia a
           serventia. A via no tema noite e' #7f93aa."""
        n = self.pagina.evaluate("""() => {
            var c = document.getElementById('tela');
            var d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            var n = 0;
            for (var i = 0; i < d.length; i += 4) {
                if (Math.abs(d[i] - 127) < 30 && Math.abs(d[i+1] - 147) < 30 &&
                    Math.abs(d[i+2] - 170) < 30) n++;
            }
            return n;
        }""")
        self.assertGreater(n, 500, "a malha sumiu embaixo da foto")


@unittest.skipIf(not TEM_PW or not os.path.exists(CHROMIUM),
                 "playwright/chromium ausentes")
class TesteAppNoNavegador(unittest.TestCase):
    """A interface real rodando em cima das respostas do app."""

    LAT = -18.893508
    LON = -43.432549

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="android-")
        cls.malha = sr.Malha(banco())

        # servidor de rotas de verdade
        cls.porta_srv = porta_livre()
        cls.srv_proc = subprocess.Popen(
            [sys.executable, os.path.join(RAIZ, "servidor", "servidor_rotas.py"),
             "--db", banco(), "--porta", str(cls.porta_srv),
             "--endereco", "127.0.0.1", "--intervalo", "3600",
             "--desmontes", os.path.join(cls.dir, "desmontes.json")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        fim = time.time() + 60
        import http.client
        while time.time() < fim:
            try:
                c = http.client.HTTPConnection("127.0.0.1", cls.porta_srv, timeout=5)
                c.request("GET", "/api/saude")
                if json.loads(c.getresponse().read().decode())["ok"]:
                    c.close(); break
                c.close()
            except Exception:
                time.sleep(0.5)

        # o "app": GPS do tablet + sincronizacao
        cls.gps = sim.GpsDoTablet("TABLET-01")
        cls.gps.registra(cls.LAT, cls.LON, vel_kmh=0.0, cog=45.0)
        cls.app = sim.AppAndroid(f"http://127.0.0.1:{cls.porta_srv}", cls.gps)
        assert cls.app.sincroniza(), "o app nao sincronizou com o servidor"
        cls.srv_app = sim.cria_servidor(cls.app)
        cls.porta_app = cls.srv_app.server_address[1]

        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROMIUM)
        cls.pagina = cls.browser.new_page(viewport={"width": 1280, "height": 800})
        cls.pagina.goto(f"http://127.0.0.1:{cls.porta_app}/", timeout=60000)
        cls.espera(cls.pagina, "() => !!(window.malha && window.pos && pos.tem_fix)",
                   "a interface ficar pronta")
        cls.pagina.click("#abEntrar")

    @classmethod
    def tearDownClass(cls):
        for f in (lambda: cls.browser.close(), lambda: cls.pw.stop(),
                  lambda: cls.srv_app.shutdown(), lambda: cls.srv_app.server_close()):
            try: f()
            except Exception: pass
        try:
            cls.srv_proc.terminate(); cls.srv_proc.wait(timeout=10)
            cls.srv_proc.stdout.close()
        except Exception:
            pass
        import shutil
        shutil.rmtree(cls.dir, ignore_errors=True)

    @classmethod
    def espera(cls, pagina, expr, oque, limite=60):
        fim = time.time() + limite
        while time.time() < fim:
            if pagina.evaluate(expr):
                return True
            time.sleep(0.3)
        raise AssertionError("tempo esgotado esperando " + oque)

    def test_01_mapa_desenha_com_a_posicao_do_tablet(self):
        pintados = self.pagina.evaluate("""() => {
            var c = document.getElementById('tela');
            var d = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
            var n = 0;
            for (var i = 0; i < d.length; i += 4)
                if (d[i] > 30 || d[i+1] > 30 || d[i+2] > 30) n++;
            return n;
        }""")
        self.assertGreater(pintados, 5000, "o mapa nao desenhou")
        p = self.pagina.evaluate("() => ({lat: pos.lat, lon: pos.lon, "
                                 "fix: pos.tem_fix, call: pos.callsign})")
        self.assertAlmostEqual(p["lat"], self.LAT, places=5)
        self.assertTrue(p["fix"])
        self.assertEqual(p["call"], "TABLET-01")

    def test_02_menu_mostra_a_fonte_como_gps_interno(self):
        self.pagina.click("#btMenu")
        self.assertIn("GPS interno", self.pagina.inner_text("#mnFonte"))
        self.assertIn("http://127.0.0.1", self.pagina.inner_text("#mnServidorVal"))
        self.assertIn("trechos", self.pagina.inner_text("#mnMapa"))
        self.pagina.click("#fecharMenu")

    def test_03_trava_de_seguranca_pela_velocidade_do_tablet(self):
        self.gps.registra(self.LAT, self.LON, vel_kmh=30.0, cog=45.0)
        self.espera(self.pagina, "() => window.pos && !pos.parado",
                    "o tablet ver o veiculo andando")
        self.espera(self.pagina,
                    "() => getComputedStyle(document.getElementById('movimento'))"
                    ".display === 'block' || document.getElementById('destino').disabled",
                    "a trava de movimento")
        self.assertTrue(self.pagina.evaluate(
            "() => document.getElementById('destino').disabled"))
        self.gps.registra(self.LAT, self.LON, vel_kmh=0.0, cog=45.0)
        self.espera(self.pagina, "() => window.pos && pos.parado", "o veiculo parar")

    def test_04_fix_vence_por_idade(self):
        self.gps.perde_fix()
        self.espera(self.pagina, "() => window.pos && !pos.tem_fix",
                    "o fix ser invalidado")
        self.espera(self.pagina,
                    "() => document.getElementById('avisos').textContent"
                    ".indexOf('GPS') >= 0", "o aviso de GPS")
        self.gps.registra(self.LAT, self.LON)
        self.espera(self.pagina, "() => window.pos && pos.tem_fix", "o fix voltar")

    def test_05_desmonte_chega_no_tablet(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.porta_srv, timeout=10)
        c.request("POST", "/api/desmontes",
                  body=json.dumps({"nome": "Bancada 880", "lat": self.LAT,
                                   "lon": self.LON, "raio_m": 400}),
                  headers={"Content-Type": "application/json"})
        criado = json.loads(c.getresponse().read().decode()); c.close()
        self.assertTrue(criado.get("ok"), criado)
        try:
            self.pagina.evaluate("() => leDesmontes()")
            self.espera(self.pagina, "() => (window.desmontes||[]).length > 0",
                        "o desmonte chegar")
            self.espera(self.pagina,
                        "() => document.getElementById('avisos').textContent"
                        ".indexOf('AREA DE DESMONTE') >= 0",
                        "o aviso de estar na area")
        finally:
            c = http.client.HTTPConnection("127.0.0.1", self.porta_srv, timeout=10)
            c.request("POST", "/api/desmontes/remover",
                      body=json.dumps({"id": criado["id"]}),
                      headers={"Content-Type": "application/json"})
            c.getresponse().read(); c.close()

    def test_06_troca_de_servidor_pelo_menu(self):
        """No Android o corpo do POST nao chega; o endereco vai na URL."""
        antes = self.app.servidor
        try:
            self.pagina.click("#btMenu")
            self.pagina.fill("#mnServidor", "http://10.188.111.249:5000")
            self.pagina.click("#mnSalvar")
            self.espera(self.pagina,
                        "() => document.getElementById('mnResultado')"
                        ".textContent.indexOf('Salvo') >= 0", "a confirmacao")
            self.assertEqual(self.app.servidor, "http://10.188.111.249:5000")
        finally:
            self.app.servidor = antes
            self.pagina.click("#fecharMenu")

    # ------------------------------------------------ avisos e orientacao
    # Pedidos do operador: aviso de informacao aparece por um tempo e vai
    # para o menu; o de seguranca nao sai da tela. Tudo isso so' no tablet —
    # a tela e' o mesmo arquivo no PTX, e la' nada pode mudar.

    def _na_tela(self, texto):
        return self.pagina.evaluate(
            "(t) => document.getElementById('avisos').textContent.indexOf(t) >= 0",
            texto)

    def _no_menu(self, texto):
        return self.pagina.evaluate(
            "(t) => document.getElementById('mnAvisos').textContent.indexOf(t) >= 0",
            texto)

    def _encurta_o_tempo(self):
        # oito segundos de espera por teste seria caro; a regra e' a mesma
        self.pagina.evaluate("() => { AVISO_NA_TELA_MS = 1200; }")

    def tearDown(self):
        try:
            self.pagina.evaluate("""() => {
                AVISO_NA_TELA_MS = 8000; ehAndroid = true;
                aviso('teste', ''); aviso('gps', ''); aviso('rota', '');
            }""")
        except Exception:
            pass

    def test_07_tela_se_identifica_como_tablet(self):
        self.espera(self.pagina, "() => ehAndroid === true",
                    "a tela reconhecer que esta' no tablet")

    def test_08_aviso_de_informacao_sai_da_tela_e_vai_para_o_menu(self):
        self._encurta_o_tempo()
        self.pagina.evaluate("() => aviso('teste', 'ROTA RECALCULADA AQUI')")
        self.assertTrue(self._na_tela("ROTA RECALCULADA AQUI"))
        time.sleep(2.2)
        self.assertFalse(self._na_tela("ROTA RECALCULADA AQUI"),
                         "o aviso de informacao continuou cobrindo o mapa")
        self.assertTrue(self._no_menu("ROTA RECALCULADA AQUI"),
                        "o aviso sumiu da tela e nao foi para o menu")
        selo = self.pagina.inner_text("#badgeMenu").strip()
        self.assertTrue(selo and int(selo) >= 1,
                        "o botao do menu nao avisa que ha aviso guardado")

    def test_09_aviso_de_seguranca_nao_sai_da_tela(self):
        """Sem GPS nao se navega: isso nao pode ir para dentro de um menu.

           Com o GPS perdido de verdade, e nao com aviso injetado: o laco de
           posicao roda a cada segundo e apagaria um aviso falso, ja' que o
           tablet do teste tem fix."""
        self._encurta_o_tempo()
        self.gps.perde_fix()
        try:
            self.espera(self.pagina, "() => window.pos && !pos.tem_fix",
                        "o fix ser invalidado")
            self.espera(self.pagina, "() => document.getElementById('avisos')"
                        ".textContent.indexOf('GPS') >= 0", "o aviso de GPS")
            time.sleep(2.5)                     # bem alem do tempo de tela
            self.assertTrue(self._na_tela("GPS"),
                            "o aviso de GPS saiu da tela")
        finally:
            self.gps.registra(self.LAT, self.LON)
            self.espera(self.pagina, "() => window.pos && pos.tem_fix",
                        "o fix voltar")

    def test_10_repetir_o_mesmo_texto_nao_segura_o_aviso(self):
        """Varios avisos sao repetidos a cada leitura. Se cada repeticao
           reiniciasse o tempo, nenhum sairia da tela nunca."""
        self._encurta_o_tempo()
        for _ in range(8):
            self.pagina.evaluate("() => aviso('teste', 'MESMO TEXTO')")
            time.sleep(0.3)
        self.assertFalse(self._na_tela("MESMO TEXTO"),
                         "repetir o mesmo aviso o prendeu na tela")

    def test_11_texto_novo_volta_para_a_tela(self):
        self._encurta_o_tempo()
        self.pagina.evaluate("() => aviso('teste', 'PRIMEIRO')")
        time.sleep(2.2)
        self.assertFalse(self._na_tela("PRIMEIRO"))
        self.pagina.evaluate("() => aviso('teste', 'SEGUNDO')")
        self.assertTrue(self._na_tela("SEGUNDO"),
                        "aviso com texto novo nao reapareceu")

    def test_12_limpar_tira_so_os_de_informacao(self):
        self.pagina.evaluate("""() => {
            aviso('teste', 'INFORMACAO X'); aviso('gps', 'SEGURANCA Y'); }""")
        self.pagina.evaluate("() => limparAvisos()")
        self.assertFalse(self._no_menu("INFORMACAO X"))
        self.assertTrue(self._na_tela("SEGURANCA Y"),
                        "limpar apagou um aviso de seguranca")

    def test_13_no_ptx_os_avisos_ficam_como_sempre(self):
        """A mesma tela, fora do tablet: nada muda."""
        self.pagina.evaluate("() => { ehAndroid = false; AVISO_NA_TELA_MS = 1200; }")
        self.pagina.evaluate("() => aviso('teste', 'COMPORTAMENTO DO PTX')")
        time.sleep(2.2)
        self.pagina.evaluate("() => pintaAvisos()")
        self.assertTrue(self._na_tela("COMPORTAMENTO DO PTX"),
                        "fora do tablet o aviso nao podia ter saido da tela")

    def test_14_orientacao_troca_pelo_menu(self):
        self.espera(self.pagina,
                    "() => getComputedStyle(document.getElementById('linhaTela'))"
                    ".display !== 'none'", "a opcao de tela aparecer")
        antes = self.app.orientacao
        try:
            self.pagina.click("#btMenu")
            vistos = []
            for _ in range(3):
                self.pagina.click("#chTela")
                time.sleep(0.4)
                vistos.append((self.app.orientacao,
                               self.pagina.inner_text("#chTela").strip()))
            self.assertEqual([v[0] for v in vistos], ["retrato", "auto", "paisagem"],
                             "a troca nao percorreu os tres modos no app")
            self.assertEqual([v[1] for v in vistos],
                             ["RETRATO", "AUTOMATICA", "PAISAGEM"])
        finally:
            self.app.orientacao = antes
            self.pagina.click("#fecharMenu")


if __name__ == "__main__":
    unittest.main(verbosity=2)
