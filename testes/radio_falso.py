#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Radio Rajant falso: fala BCAPI de verdade (TLS + framing + protobuf) num
socket local. Serve para testar a SessaoBC sem depender de um BreadCrumb.

Reproduz o que importa do radio:
  - manda o desafio de autenticacao ao conectar;
  - confere sha384(senha + desafio) e recusa senha errada;
  - responde ao pedido com stateFilterPath 'gps' com a subarvore de GPS.
"""
import hashlib, os, socket, ssl, struct, subprocess, tempfile, threading

from rajant_api import Message_pb2


def gerar_certificado():
    """Certificado autoassinado, como o do proprio radio."""
    d = tempfile.mkdtemp(prefix="radiofalso-")
    cert, chave = os.path.join(d, "cert.pem"), os.path.join(d, "chave.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", chave, "-out", cert, "-days", "2", "-subj", "/CN=radiofalso"],
        check=True, capture_output=True)
    return cert, chave


def empacotar(payload):
    return struct.pack(">ii", len(payload), 0) + payload


class RadioFalso:
    def __init__(self, senha="segredo", gps=None, cert=None, chave=None,
                 porta=0, endereco="127.0.0.1"):
        self.senha = senha
        self.gps = gps
        self.logins = 0
        self.leituras = 0
        self.filtros = []
        if cert is None:
            cert, chave = gerar_certificado()
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, chave)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((endereco, porta))
        self.srv.listen(8)
        self.porta = self.srv.getsockname()[1]
        self.parar = False
        self.t = threading.Thread(target=self._aceita, daemon=True)
        self.t.start()

    # ------------------------------------------------------------ interno
    def _aceita(self):
        while not self.parar:
            try:
                cru, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._atende, args=(cru,), daemon=True).start()

    def _recebe_exato(self, c, n):
        buf = bytearray()
        while len(buf) < n:
            p = c.recv(n - len(buf))
            if not p:
                raise ConnectionError("fechou")
            buf.extend(p)
        return bytes(buf)

    def _le(self, c):
        cab = self._recebe_exato(c, 8)
        tam = struct.unpack(">i", cab[:4])[0]
        corpo = self._recebe_exato(c, tam)
        msg = Message_pb2.BCMessage()
        msg.ParseFromString(corpo)
        return msg

    def _manda(self, c, msg):
        c.send(empacotar(msg.SerializeToString()))

    def _atende(self, cru):
        try:
            c = self.ctx.wrap_socket(cru, server_side=True)
        except Exception:
            try: cru.close()
            except Exception: pass
            return
        try:
            desafio = os.urandom(32)
            m = Message_pb2.BCMessage()
            # proto2: sequenceNumber e auth.action sao obrigatorios; sem eles
            # a serializacao falha
            m.sequenceNumber = 0
            m.auth.action = Message_pb2.BCMessage.Auth.Action.Value("LOGIN")
            m.auth.challengeOrResponse = desafio
            self._manda(c, m)

            login = self._le(c)
            esperado = hashlib.sha384(self.senha.encode("utf-8") + desafio).digest()
            r = Message_pb2.BCMessage()
            r.sequenceNumber = 1
            if bytes(login.auth.challengeOrResponse) == esperado:
                r.authResult.status = Message_pb2.BCMessage.Result.Status.Value("SUCCESS")
                self.logins += 1
                ok = True
            else:
                r.authResult.status = Message_pb2.BCMessage.Result.Status.Value("FAILURE")
                ok = False
            self._manda(c, r)
            if not ok:
                c.close(); return

            while not self.parar:
                pedido = self._le(c)
                self.filtros.append(list(pedido.stateFilterPath))
                self.leituras += 1
                resp = Message_pb2.BCMessage()
                resp.sequenceNumber = self.leituras + 1
                self._preenche_gps(resp)
                self._manda(c, resp)
        except Exception:
            pass
        finally:
            try: c.close()
            except Exception: pass

    def _preenche_gps(self, msg):
        g = self.gps
        if g is None:
            return
        if g.get("desligado"):
            msg.state.gps.gpsSwitch.enabled = False
            return
        msg.state.gps.gpsSwitch.enabled = True
        if "status_rmc" in g:
            msg.state.gps.gpsRMC.gpsStatus = g["status_rmc"]
        if g.get("lat") is not None:
            msg.state.gps.gpsPos.gpsLat = g["lat"]
            msg.state.gps.gpsPos.gpsLong = g["lon"]
        msg.state.gps.gpsPos.gpsQuality = g.get("qualidade", 1)
        if g.get("alt") is not None:
            msg.state.gps.gpsPos.gpsAlt = g["alt"]
        if g.get("nos") is not None:
            msg.state.gps.gpsVel.gpsSpeedKnots = g["nos"]
        if g.get("kph") is not None:
            msg.state.gps.gpsVel.gpsSpeedKph = g["kph"]
        if g.get("rumo") is not None:
            msg.state.gps.gpsVel.gpsTrackDegreesTrue = g["rumo"]

    # ------------------------------------------------------------ publico
    def define_gps(self, gps):
        self.gps = gps

    def fecha(self):
        self.parar = True
        try: self.srv.close()
        except Exception: pass
