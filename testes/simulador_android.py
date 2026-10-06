#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simulador do app Android.

Nao ha Android SDK nesta maquina, entao o Kotlin nao compila aqui. O que da'
para provar mesmo assim e' o CONTRATO: o app responde /pos, /estado, /frota,
/desmontes e /rota, e a interface.html — que e' a mesma nas duas plataformas —
tem que funcionar em cima dessas respostas.

Este simulador responde exatamente o que Api.kt, Posicao.kt e Sincronizacao.kt
respondem, servindo a mesma interface.html. Se o Kotlin mudar de campo e este
arquivo nao, o teste de consistencia acusa.
"""
import json, os, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from urllib.request import urlopen
from urllib.error import URLError

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTERFACE = os.path.join(RAIZ, "ptx", "interface.html")

SEGUNDOS_SEM_FIX = 10.0


class GpsDoTablet:
    """Espelha Posicao.kt: o LocationManager entrega metros por segundo e
       rumo; a idade do fix e' o que decide se a posicao ainda vale."""

    def __init__(self, callsign="TABLET"):
        self.callsign = callsign
        self.lat = self.lon = 0.0
        self.cog = self.vel_kmh = 0.0
        self.altitude = self.precisao_m = 0.0
        self.satelites = 0
        self.quando = 0.0
        self.estado = "aguardando o GPS"
        self.limite_parado_kmh = 3.0

    def registra(self, lat, lon, vel_kmh=0.0, cog=0.0, precisao=4.0, sat=9):
        self.lat, self.lon = lat, lon
        self.vel_kmh, self.cog = vel_kmh, cog
        self.precisao_m, self.satelites = precisao, sat
        self.quando = time.time()
        self.estado = "posicao boa"

    def perde_fix(self):
        self.quando = 0.0
        self.estado = "GPS desligado no tablet"

    @property
    def idade_s(self):
        return -1.0 if not self.quando else round(time.time() - self.quando, 1)

    @property
    def tem_fix(self):
        return bool(self.quando) and 0 <= self.idade_s <= SEGUNDOS_SEM_FIX

    @property
    def parado(self):
        return self.vel_kmh <= self.limite_parado_kmh

    def json(self):
        return {"callsign": self.callsign, "lat": self.lat, "lon": self.lon,
                "cog": self.cog, "sog_kmh": self.vel_kmh,
                "altitude": self.altitude, "precisao_m": self.precisao_m,
                "satelites": self.satelites, "qualidade": 1 if self.tem_fix else 0,
                "idade_s": self.idade_s, "tem_fix": self.tem_fix,
                "parado": self.parado, "limite_parado_kmh": self.limite_parado_kmh}

    def descricao(self):
        if not self.tem_fix:
            return self.estado
        return f"GPS interno · {self.satelites} sat · {int(self.precisao_m)} m"


class AppAndroid:
    """Espelha Sincronizacao.kt + Api.kt."""

    def __init__(self, servidor, gps):
        self.ultima_falha = ""
        self.versao_app = "versao 1.1 · build simulador"
        self.servidor = servidor.rstrip("/")
        self.gps = gps
        self.cache = {}
        self.orientacao = "paisagem"
        self.falado = []           # o que o TextToSpeech teria dito
        self.cache_foto = {}
        self.frota_guardada = ""
        self.estado_servidor = "nunca contatado"
        self.sinc = "nunca"
        self.hash_local = ""

    def _pega(self, caminho, tempo=8):
        with urlopen(self.servidor + caminho, timeout=tempo) as r:
            return r.status, r.read().decode("utf-8")

    def sincroniza(self):
        try:
            st, corpo = self._pega("/api/versao")
            h = json.loads(corpo).get("hash", "")
            if h and h != self.hash_local:
                _, malha = self._pega("/api/malha")
                _, locais = self._pega("/api/locais")
                try:
                    _, areas = self._pega("/api/areas")
                except Exception:
                    areas = ""
                if len(malha) > 100 and len(locais) > 100:
                    self.cache["malha.json"] = malha
                    self.cache["locais.json"] = locais
                    if len(areas) > 20:
                        self.cache["areas.json"] = areas
                    self.hash_local = h
            self.estado_servidor = "online"
            self.sinc = time.strftime("%Y-%m-%d %H:%M:%S")
            return True
        except Exception as e:
            self.estado_servidor = f"offline: {e}"
            return False

    def frota(self):
        try:
            _, corpo = self._pega("/api/equipamentos")
            self.frota_guardada = corpo
            return corpo
        except Exception:
            if self.frota_guardada:
                return self.frota_guardada.replace("{", '{"do_cache":true,', 1)
            return json.dumps({"ok": False, "total": 0, "equipamentos": [],
                               "erro": "servidor indisponivel e sem frota em cache"})

    def foto(self, rel):
        """Espelha Sincronizacao.foto: ladrilho vem do cache, manifesto vem
           do servidor (foto nova troca o manifesto, nunca o ladrilho)."""
        if not rel or ".." in rel:
            return None
        if rel.endswith(".json"):
            try:
                _, corpo = self._pega_bytes("/foto/" + rel)
                self.cache_foto[rel] = corpo
                return corpo
            except Exception:
                return self.cache_foto.get(rel)
        if rel in self.cache_foto:
            return self.cache_foto[rel]
        try:
            _, corpo = self._pega_bytes("/foto/" + rel)
        except Exception:
            return None
        self.cache_foto[rel] = corpo
        return corpo

    def _pega_bytes(self, caminho, tempo=8):
        with urlopen(self.servidor + caminho, timeout=tempo) as r:
            return r.status, r.read()

    def desmontes(self):
        try:
            _, corpo = self._pega("/api/desmontes")
            self.cache["desmontes.json"] = corpo
            return corpo
        except Exception:
            g = self.cache.get("desmontes.json", "")
            if g:
                return g.replace("{", '{"do_cache":true,', 1)
            return json.dumps({"ok": False, "total": 0, "desmontes": [],
                               "erro": "servidor indisponivel e sem desmonte em cache"})

    def rota(self, q):
        if not self.gps.tem_fix:
            return json.dumps({"ok": False, "erro": "sem posicao GPS valida",
                               "detalhe": "o tablet ainda nao tem fix; a rota "
                                          "precisa saber onde voce esta"})
        equip = (q.get("equip") or [""])[0]
        para = (q.get("para") or [""])[0]
        if not equip and not para:
            return json.dumps({"ok": False, "erro": "escolha um destino"})
        origem = f"de_lat={self.gps.lat}&de_lon={self.gps.lon}"
        alvo = (f"para_equip={quote(equip)}" if equip else f"para={quote(para)}")
        try:
            _, corpo = self._pega(f"/api/rota?{origem}&{alvo}")
            return corpo
        except Exception as e:
            return json.dumps({"ok": False, "offline": True,
                               "erro": "servidor de rotas indisponivel",
                               "detalhe": str(e)})

    def estado(self):
        return {"plataforma": "android",
                "servidor": self.estado_servidor, "servidor_url": self.servidor,
                "fonte": self.gps.descricao(),
                "ultima_falha": self.ultima_falha,
                "versao_app": self.versao_app,
                "hash": self.hash_local, "sinc": self.sinc,
                "orientacao": self.orientacao,
                "voz": "pronta",
                "tem_cache": "malha.json" in self.cache}

    def muda_orientacao(self, q):
        """Espelha MainActivity: guarda o modo e responde. Girar de verdade
           e' com o Android; aqui so' importa o contrato com a tela."""
        modo = (q.get("modo") or [""])[0]
        if modo not in ("paisagem", "retrato", "auto"):
            return json.dumps({"ok": False,
                               "erro": "modo deve ser paisagem, retrato ou auto"})
        self.orientacao = modo
        return json.dumps({"ok": True, "orientacao": modo})

    def config(self, q):
        url = (q.get("servidor") or [""])[0].strip().rstrip("/")
        if not url:
            return json.dumps({"ok": False, "erro": "endereco vazio"})
        if not url.startswith("http"):
            url = "http://" + url
        self.servidor = url
        return json.dumps({"ok": True, "servidor": url})


def cria_servidor(app, porta=0):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _envia(self, corpo, tipo="application/json; charset=utf-8"):
            b = corpo.encode("utf-8") if isinstance(corpo, str) else corpo
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def _responde(self, u, q):
            c = u.path
            if c in ("/", "/interface.html"):
                with open(INTERFACE, encoding="utf-8") as f:
                    return self._envia(f.read(), "text/html; charset=utf-8")
            if c == "/pos":       return self._envia(json.dumps(app.gps.json()))
            if c == "/estado":    return self._envia(json.dumps(app.estado()))
            if c == "/malha":     return self._envia(app.cache.get("malha.json", "") or
                                                     json.dumps({"erro": "sem dados em cache"}))
            if c == "/locais":    return self._envia(app.cache.get("locais.json", "") or
                                                     json.dumps({"erro": "sem dados em cache"}))
            if c == "/areas":     return self._envia(app.cache.get("areas.json", "") or
                                                     json.dumps({"erro": "sem dados em cache"}))
            if c.startswith("/foto/"):
                rel = c[len("/foto/"):]
                dados = app.foto(rel)
                if dados is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                tipo = ("application/json; charset=utf-8" if rel.endswith(".json")
                        else "image/png" if rel.endswith(".png") else "image/jpeg")
                return self._envia(dados, tipo)
            if c == "/frota":     return self._envia(app.frota())
            if c == "/desmontes": return self._envia(app.desmontes())
            if c == "/rota":      return self._envia(app.rota(q))
            if c == "/orientacao": return self._envia(app.muda_orientacao(q))
            if c == "/falar":
                app.falado.append(((q.get("t") or [""])[0],
                                   (q.get("urgente") or [""])[0] == "1"))
                return self._envia('{"ok":true}')
            if c == "/config":    return self._envia(app.config(q))
            self.send_response(404); self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            u = urlparse(self.path)
            self._responde(u, parse_qs(u.query))

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)      # o WebView do Android tambem ignora
            u = urlparse(self.path)
            self._responde(u, parse_qs(u.query))

    srv = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
