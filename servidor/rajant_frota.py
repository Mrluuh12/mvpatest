#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
 rajant_frota.py — posicao dos equipamentos pelo GPS dos radios Rajant.

 POR QUE ISTO EXISTE
   Cada equipamento da mina carrega um BreadCrumb Rajant, e o radio sabe
   onde esta'. E' dali que sai a posicao da frota: escolhe-se o equipamento
   e o servidor de rotas traca o caminho ate ele.

 COMO FALA COM O RADIO
   Sessao BCAPI direto (TLS na porta 2300), autenticada com
   sha384(senha + desafio), mantida aberta e reconectada sozinha.
   Pede so' a subarvore 'gps' do state: payload de centenas de bytes em vez
   do state inteiro, por isso o ciclo curto nao pesa na malha Rajant.

 DEPENDENCIA
   Precisa do pacote 'rajant-api' (protobuf do BCAPI):
       pip install --no-deps rajant-api
       pip install protobuf
   O import e' preguicoso: sem o pacote, o servidor de rotas continua
   funcionando normalmente, so' sem a camada de frota.

 Este modulo nao depende do resto do servidor: da' para testar sozinho.
=============================================================================
"""
import hashlib, json, math, os, socket, ssl, struct, threading, time

PORTA_PADRAO = 2300
ROLE_PADRAO = "VIEW"
INTERVALO_PADRAO = 10

# prefixo do nome do radio -> tipo de equipamento
TIPOS_PADRAO = {
    "CA": "caminhao", "PA": "pa", "PF": "perfuratriz", "TT": "trator",
    "EH": "escavadeira", "ERM": "repetidora", "ERB": "torre",
}
# o que nao anda: fica no mapa, mas nao entra na lista de destinos moveis
TIPOS_FIXOS = ("repetidora", "torre")

# velocidade acima da qual o equipamento conta como em movimento
LIMIAR_MOVIMENTO_KMH = 2.0


class ErroDependencia(RuntimeError):
    """Falta o pacote rajant-api. Mensagem pronta para mostrar ao operador."""


def _protobuf():
    """Import preguicoso: so' custa quando a frota e' realmente ligada."""
    # Python 3.12+ tirou ssl.wrap_socket, mas o __init__ do rajant_api ainda
    # importa o nome. Cria-se um substituto so' para o pacote carregar; o
    # codigo daqui usa SSLContext direto e nunca chama isso.
    if not hasattr(ssl, "wrap_socket"):
        def _compat(sock, **kw):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx.wrap_socket(sock)
        ssl.wrap_socket = _compat
    try:
        from rajant_api import Message_pb2
        from google.protobuf.json_format import MessageToDict
        return Message_pb2, MessageToDict
    except ImportError as e:
        raise ErroDependencia(
            "o pacote rajant-api nao esta instalado; sem ele nao da para ler "
            "o GPS dos radios. Instale com: pip install --no-deps rajant-api "
            "&& pip install protobuf. Detalhe: " + str(e))


def empacotar(payload):
    """[tamanho >i][flags=0 >i][corpo]"""
    return struct.pack(">ii", len(payload), 0) + payload


def desempacotar(pacote):
    """Tira o cabecalho de 8 bytes e descomprime se preciso. O formato e'
       detectado pelo conteudo: gzip, zlib ou deflate cru — cobre as
       variacoes de firmware."""
    corpo = pacote[8:]
    if corpo[:2] == b"\x1f\x8b":
        import gzip
        return gzip.decompress(corpo)
    if corpo[:1] == b"\x78":
        import zlib
        try:
            return zlib.decompress(corpo)
        except zlib.error:
            pass
    return corpo


class SessaoBC:
    """Sessao autenticada com um BreadCrumb. Mantida aberta entre leituras."""

    def __init__(self, host, porta=PORTA_PADRAO, role=ROLE_PADRAO,
                 senha="", timeout=5):
        self.host, self.porta = host, porta
        self.role, self.senha = role, senha
        self.timeout = timeout
        self.conn = None
        self.seq = 0

    # -- TCP e' fluxo, nao mensagem: le exatamente N bytes --
    def _recebe_exato(self, n):
        buf = bytearray()
        while len(buf) < n:
            pedaco = self.conn.recv(n - len(buf))
            if not pedaco:
                raise ConnectionError("conexao fechada pelo radio")
            buf.extend(pedaco)
        return bytes(buf)

    def _recebe(self):
        Message_pb2, _ = _protobuf()
        cab = self._recebe_exato(8)
        tam = struct.unpack(">i", cab[:4])[0]
        if tam < 0 or tam > 50_000_000:
            raise ValueError(f"tamanho de mensagem invalido: {tam}")
        corpo = self._recebe_exato(tam)
        msg = Message_pb2.BCMessage()
        try:
            msg.ParseFromString(desempacotar(cab + corpo))
        except Exception as e:
            raise ConnectionError(
                f"nao entendi a resposta do radio (cab={cab.hex()} "
                f"corpo[:8]={corpo[:8].hex()} tam={tam}): {e}") from e
        self.seq += 1
        return msg

    def _envia(self, msg):
        self.conn.send(empacotar(msg.SerializeToString()))
        self.seq += 1

    def _novo(self):
        Message_pb2, _ = _protobuf()
        msg = Message_pb2.BCMessage()
        msg.sequenceNumber = self.seq
        return msg

    def conectar(self):
        Message_pb2, _ = _protobuf()
        self.fechar()
        self.seq = 0
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE          # certificado proprio do radio
        s = socket.socket(socket.AF_INET)
        s.settimeout(self.timeout)
        self.conn = ctx.wrap_socket(s)
        self.conn.connect((self.host, self.porta))

        desafio = self._recebe()
        tx = self._novo()
        tx.auth.action = Message_pb2.BCMessage.Auth.Action.Value("LOGIN")
        tx.auth.role = Message_pb2.Common__pb2.Role.Value(self.role)
        semente = bytes(desafio.auth.challengeOrResponse)
        tx.auth.challengeOrResponse = hashlib.sha384(
            self.senha.encode("utf-8") + semente).digest()
        # 0 = sem compressao: a resposta filtrada de gps e' pequena, e assim
        # nao dependemos do algoritmo do firmware
        tx.auth.compressionMask = 0
        self._envia(tx)

        r = self._recebe()
        estado = Message_pb2.BCMessage.Result.Status.Name(r.authResult.status)
        if estado != "SUCCESS":
            raise PermissionError(f"login recusado pelo radio: {estado}")

    def ler_gps(self):
        """So' a subarvore 'gps' do state."""
        _, MessageToDict = _protobuf()
        tx = self._novo()
        tx.state.Clear()
        tx.stateFilterPath.append("gps")
        self._envia(tx)
        resp = self._recebe()
        return MessageToDict(resp.state, preserving_proto_field_name=True)

    def fechar(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None


def nmea_para_graus(coord):
    """'2743.8950S' -> -27.7316 | '04612.3456W' -> -46.2058

       O hemisfero vem grudado no fim do campo. Sem ler o sufixo, toda
       longitude do Brasil sairia positiva — do outro lado do mundo."""
    if coord is None:
        return None
    coord = str(coord).strip()
    if not coord:
        return None
    hemi = coord[-1].upper() if coord[-1].isalpha() else ""
    num = coord[:-1] if hemi else coord
    if "." not in num:
        return None
    try:
        corte = num.index(".") - 2        # os minutos tem 2 digitos inteiros
        if corte <= 0:
            return None
        graus = float(num[:corte])
        minutos = float(num[corte:])
        valor = graus + minutos / 60.0
    except (ValueError, IndexError):
        return None
    return -valor if hemi in ("S", "W") else valor


def _busca_chave(d, termos):
    """Acha recursivamente a primeira chave cujo nome contenha um dos termos.
       Os nomes de velocidade e rumo mudam conforme o firmware."""
    if isinstance(d, dict):
        for k, v in d.items():
            if any(t in k.lower() for t in termos) and not isinstance(v, (dict, list)):
                return v
        for v in d.values():
            r = _busca_chave(v, termos)
            if r is not None:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _busca_chave(v, termos)
            if r is not None:
                return r
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_gps(estado):
    """Do dicionario de state do radio para
       {lat, lon, vel_kmh, rumo, altitude, qualidade, satelites}.
       Devolve None se o GPS estiver desligado, ainda sem fix, ou se o
       receptor disser que o fix nao presta.

       UNIDADE DA VELOCIDADE: o state expoe gpsSpeedKnots E gpsSpeedKph como
       campos separados. Procurar por "speed" e multiplicar por 1,852 sempre
       — como se costuma fazer — dobra a leitura no firmware que preenche o
       campo em km/h. Aqui a unidade e' lida pelo nome do campo, e a busca
       por aproximacao so' entra se nenhum dos dois vier preenchido.
       Isso importa: e' esta velocidade que trava a interface em movimento."""
    gps = (estado or {}).get("gps") or {}
    if not gps:
        return None
    chave = gps.get("gpsSwitch") or {}
    if chave and not chave.get("enabled", True):
        return None

    rmc = gps.get("gpsRMC") or {}
    if "gpsStatus" in rmc and not rmc.get("gpsStatus"):
        return None                      # o proprio receptor diz que nao vale

    pos = gps.get("gpsPos") or {}
    qualidade = _num(pos.get("gpsQuality"))
    if qualidade is not None and qualidade <= 0:
        return None                      # 0 = sem fix

    lat = nmea_para_graus(pos.get("gpsLat"))
    lon = nmea_para_graus(pos.get("gpsLong"))
    if lat is None or lon is None:
        return None

    vel = gps.get("gpsVel") or {}
    vel_kmh = _num(vel.get("gpsSpeedKph"))
    if vel_kmh is None:
        nos = _num(vel.get("gpsSpeedKnots"))
        if nos is not None:
            vel_kmh = nos * 1.852
        else:
            # firmware fora do padrao: procura qualquer campo de velocidade e
            # assume nos, que e' o que o NMEA manda
            achado = _num(_busca_chave(gps, ("speed",)))
            vel_kmh = achado * 1.852 if achado is not None else None
    if vel_kmh is not None:
        vel_kmh = round(vel_kmh, 1)

    rumo = _num(vel.get("gpsTrackDegreesTrue"))
    if rumo is None:
        rumo = _num(vel.get("gpsTrackDegreesMag"))
    if rumo is None:
        rumo = _num(_busca_chave(gps, ("course", "heading", "track")))
    if rumo is not None:
        rumo = round(rumo, 1)

    return {"lat": lat, "lon": lon, "vel_kmh": vel_kmh, "rumo": rumo,
            "altitude": _num(pos.get("gpsAlt")),
            "qualidade": int(qualidade) if qualidade is not None else None,
            "satelites": _num(pos.get("gpsSatsInView"))}


def tipo_do_nome(nome, mapa=None):
    """CA-1022 -> caminhao. Prefixo mais longo primeiro, senao 'ERB' cairia
       na regra de 'E' antes de casar com a propria."""
    mapa = mapa or TIPOS_PADRAO
    n = (nome or "").upper().strip()
    for pref in sorted(mapa, key=len, reverse=True):
        if n.startswith(pref.upper()):
            return mapa[pref]
    return "generico"


def carregar_cache_ips(caminho):
    """Le o rajant_ips_cache.json do exporter: ip -> {nome, ...}."""
    with open(caminho, encoding="utf-8") as f:
        dados = json.load(f)
    ips, nomes = [], {}
    for ip, info in dados.items():
        ips.append(ip)
        nomes[ip] = (info or {}).get("nome", ip) if isinstance(info, dict) else ip
    return ips, nomes


class Frota:
    """Uma thread por radio, sessao mantida aberta, reconexao propria.

       O estado publicado e' um dicionario por IP. Leitura e escrita sempre
       sob trava: o servidor HTTP le' isto de varias threads."""

    def __init__(self, ips, nomes=None, senha="", porta=PORTA_PADRAO,
                 role=ROLE_PADRAO, intervalo=INTERVALO_PADRAO,
                 tipos=None, fabrica_sessao=None, registrar=None):
        self.ips = list(ips)
        self.nomes = dict(nomes or {})
        self.senha = senha
        self.porta = porta
        self.role = role
        self.intervalo = max(1, int(intervalo))
        self.tipos = dict(tipos or TIPOS_PADRAO)
        # injetavel para teste: qualquer coisa com conectar/ler_gps/fechar
        self.fabrica_sessao = fabrica_sessao or (
            lambda ip: SessaoBC(ip, self.porta, self.role, self.senha))
        self.registrar = registrar or (lambda *a: None)

        self.trava = threading.Lock()
        self.posicoes = {}
        self.ultimo_ciclo = 0.0       # ultima leitura concluida, de qualquer radio
        self.ciclos = 0
        self.parar = threading.Event()
        self.threads = []
        self._registrar_frota()

    def _registrar_frota(self):
        """Uma entrada por radio ja' na largada, mesmo desligado ou sem fix:
           o operador ve' a lista inteira desde o inicio."""
        with self.trava:
            for ip in self.ips:
                nome = self.nomes.get(ip, ip)
                self.posicoes.setdefault(ip, {
                    "ip": ip, "nome": nome, "tipo": tipo_do_nome(nome, self.tipos),
                    "online": False, "ts": 0.0, "lat": None, "lon": None,
                    "vel_kmh": None, "rumo": None, "altitude": None,
                    "qualidade": None, "satelites": None,
                    "ts_fix": 0.0, "erro": None})

    def instantaneo(self):
        """Copia do estado atual, com idade e situacao ja' calculadas."""
        agora = time.time()
        saida = []
        with self.trava:
            for r in self.posicoes.values():
                idade = round(agora - r["ts"], 1) if r["ts"] else None
                tem_fix = r["lat"] is not None
                vel = r.get("vel_kmh")
                saida.append({
                    "ip": r["ip"], "nome": r["nome"], "tipo": r["tipo"],
                    "movel": r["tipo"] not in TIPOS_FIXOS,
                    "online": r["online"], "tem_fix": tem_fix,
                    "lat": r["lat"], "lon": r["lon"],
                    "vel_kmh": vel, "rumo": r["rumo"],
                    "altitude": r.get("altitude"),
                    "parado": (vel is not None and vel <= LIMIAR_MOVIMENTO_KMH),
                    "idade_s": idade,
                    "idade_fix_s": (round(agora - r["ts_fix"], 1)
                                    if r.get("ts_fix") else None),
                    "erro": r.get("erro"),
                })
        saida.sort(key=lambda r: (not r["tem_fix"], r["nome"]))
        return saida

    def por_nome(self, nome):
        alvo = (nome or "").strip().upper()
        for r in self.instantaneo():
            if (r["nome"] or "").strip().upper() == alvo:
                return r
        return None

    def resumo(self):
        radios = self.instantaneo()
        with self.trava:
            ultimo, ciclos = self.ultimo_ciclo, self.ciclos
        return {"ativa": True,
                "total": len(radios),
                "online": sum(1 for r in radios if r["online"]),
                "com_fix": sum(1 for r in radios if r["tem_fix"]),
                "leituras": ciclos,
                # se isto parar de crescer, a frota travou: e' o que dizer
                # "fez uma coleta e parou" significa, e da' para ver daqui
                "idade_ultima_leitura_s": (round(time.time() - ultimo, 1)
                                           if ultimo else None),
                "intervalo_s": self.intervalo}

    def iniciar(self):
        if self.threads:
            return self
        for ip in self.ips:
            t = threading.Thread(target=self._laco, args=(ip,), daemon=True,
                                 name="rajant-" + ip)
            t.start()
            self.threads.append(t)
        self.registrar("frota", f"lendo GPS de {len(self.ips)} radios "
                                f"a cada {self.intervalo}s")
        return self

    def encerrar(self):
        self.parar.set()
        for t in self.threads:
            t.join(timeout=2)
        self.threads = []

    def _laco(self, ip):
        sessao = self.fabrica_sessao(ip)
        conectado = False
        sem_gps = 0
        falhas = 0
        quedas = 0
        while not self.parar.is_set():
            try:
                if not conectado:
                    sessao.conectar()
                    conectado = True
                    sem_gps = 0
                    falhas = 0
                    # so' a primeira conexao merece linha; reconexao rotineira
                    # de radio que anda pela mina nao e' novidade
                    if quedas == 0:
                        self.registrar("frota", f"[{ip}] sessao aberta")
                gps = parse_gps(sessao.ler_gps())
                if gps:
                    sem_gps = 0
                else:
                    sem_gps += 1
                    # tinha fix e sumiu: sessao "surda" e' falha conhecida de
                    # firmware. Recicla em vez de ficar lendo nada.
                    if sem_gps >= 6 and self._ja_teve_fix(ip):
                        raise ConnectionError("sessao estagnada (6 leituras sem gps)")
                self._guardar(ip, gps)
            except Exception as e:
                if conectado:
                    quedas += 1
                    # 140 radios reconectando enchem o log e, no Windows,
                    # console cheio chega a travar o processo. Uma linha a
                    # cada 20 quedas basta para saber que este radio oscila.
                    if quedas == 1 or quedas % 20 == 0:
                        self.registrar("frota", f"[{ip}] sessao caiu ({quedas}x): {e}")
                else:
                    falhas += 1
                    if falhas == 1 or falhas % 60 == 0:
                        self.registrar("frota", f"[{ip}] nao conecta ({falhas}x): {e}")
                conectado = False
                try:
                    sessao.fechar()
                except Exception:
                    pass
                with self.trava:
                    if ip in self.posicoes:
                        self.posicoes[ip]["online"] = False
                        self.posicoes[ip]["erro"] = str(e)[:200]
                self.parar.wait(min(self.intervalo * 3, 30))
                continue
            self.parar.wait(self.intervalo)
        try:
            sessao.fechar()
        except Exception:
            pass

    def _ja_teve_fix(self, ip):
        with self.trava:
            return self.posicoes.get(ip, {}).get("lat") is not None

    def _guardar(self, ip, gps):
        with self.trava:
            r = self.posicoes.get(ip)
            if r is None:
                nome = self.nomes.get(ip, ip)
                r = {"ip": ip, "nome": nome, "tipo": tipo_do_nome(nome, self.tipos)}
                self.posicoes[ip] = r
            r["online"] = True
            r["ts"] = time.time()
            r["erro"] = None
            self.ultimo_ciclo = r["ts"]
            self.ciclos += 1
            if gps:
                r["lat"] = gps["lat"]
                r["lon"] = gps["lon"]
                r["vel_kmh"] = gps["vel_kmh"]
                r["rumo"] = gps["rumo"]
                r["altitude"] = gps.get("altitude")
                r["qualidade"] = gps.get("qualidade")
                r["satelites"] = gps.get("satelites")
                r["ts_fix"] = r["ts"]


def frota_de_configuracao(cfg, registrar=None):
    """Monta a Frota a partir dos argumentos/arquivo de configuracao.
       Devolve None quando a frota nao foi configurada."""
    ips = list(cfg.get("ips") or [])
    nomes = dict(cfg.get("nomes") or {})
    cache = cfg.get("cache")
    if cache:
        if not os.path.exists(cache):
            raise FileNotFoundError(f"cache de IPs nao encontrado: {cache}")
        cips, cnomes = carregar_cache_ips(cache)
        for ip in cips:
            if ip not in ips:
                ips.append(ip)
        nomes.update(cnomes)
    if not ips:
        return None
    tipos = dict(TIPOS_PADRAO)
    if cfg.get("tipos"):
        tipos.update(cfg["tipos"])
    return Frota(ips, nomes, senha=cfg.get("senha", ""),
                 porta=int(cfg.get("porta", PORTA_PADRAO)),
                 role=cfg.get("role", ROLE_PADRAO),
                 intervalo=int(cfg.get("intervalo", INTERVALO_PADRAO)),
                 tipos=tipos, registrar=registrar)
