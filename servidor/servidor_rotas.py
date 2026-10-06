#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
 servidor_rotas.py — Servidor de rotas da mina.

 O QUE FAZ
   1. Le o Modular.db e extrai malha viaria, locais e areas.
   2. Reextrai sozinho de tempos em tempos (as rotas mudam com a lavra).
      So' reprocessa se o arquivo mudou de verdade (compara hash).
   3. Publica uma API HTTP que os PTX consomem.

 COMO RODAR
   python3 servidor_rotas.py --db "C:/Modular/Modular.db" --porta 5000

 API (o PTX so' bebe disso)
   GET /api/saude                          esta vivo?
   GET /api/versao                         hash+data dos dados (para cache)
   GET /api/locais[?tipo=Crusher&q=texto]  destinos possiveis
   GET /api/malha                          malha viaria (GeoJSON)
   GET /api/areas                          poligonos de risco
   GET /api/rota?de_lat=&de_lon=&para=NOME rota do veiculo ate o destino
   GET /api/rota?de=NO_A&para=NO_B         rota entre dois nos
   GET /api/equipamentos                   frota ao vivo (GPS dos radios Rajant)
   GET /api/rota?de_lat=&de_lon=&para_equip=CA-1022
                                           rota ate um equipamento em campo
   GET /                                   mapa de teste no navegador

 Somente leitura sobre o banco. Nao escreve nada nele.
=============================================================================
"""
import argparse, hashlib, heapq, json, math, os, posixpath, re, sqlite3, struct, sys, threading, time
from collections import defaultdict
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

VERSAO = "1.3"


class Registro:
    """Saida do servidor, serializada.

       Com 140 radios, varias threads escrevendo ao mesmo tempo embaralhavam
       as linhas — e, no Windows, um console em QuickEdit onde alguem clicou
       BLOQUEIA a escrita e congela o processo inteiro, servidor HTTP junto.
       Por isso: uma trava so', e a opcao de mandar tudo para arquivo em vez
       do console."""

    def __init__(self):
        self.trava = threading.Lock()
        self.arquivo = None
        self.max_bytes = 5 * 1024 * 1024
        self.console = True

    def configurar(self, arquivo=None, max_kb=5120, console=True):
        self.arquivo = arquivo
        self.max_bytes = max_kb * 1024
        self.console = console
        if arquivo:
            d = os.path.dirname(os.path.abspath(arquivo))
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)

    def __call__(self, tag, msg):
        linha = f"[{datetime.now():%F %T}] [{tag}] {msg}"
        with self.trava:
            if self.console:
                try:
                    print(linha, flush=True)
                except Exception:
                    self.console = False    # console morto nao derruba o servidor
            if self.arquivo:
                try:
                    if (os.path.exists(self.arquivo)
                            and os.path.getsize(self.arquivo) > self.max_bytes):
                        velho = self.arquivo + ".1"
                        if os.path.exists(velho):
                            os.remove(velho)
                        os.replace(self.arquivo, velho)
                    with open(self.arquivo, "a", encoding="utf-8") as f:
                        f.write(linha + "\n")
                except Exception:
                    pass


registrar = Registro()

# tabelas sem as quais o arquivo nao e' o banco de topologia do DISPATCH
TABELAS_NECESSARIAS = ("GeographicRegion", "TopologicalObject", "Road",
                       "FunctionalLocation", "LocationType")


def inspecionar_banco(caminho):
    """Diz se o arquivo serve como Modular.db — e, quando nao serve, POR QUE.

       Existe porque "no such table: GeographicRegion" nao ajuda ninguem em
       campo: o DISPATCH tem varios .db e so' um deles carrega a topologia."""
    info = {"caminho": os.path.abspath(caminho),
            "existe": os.path.exists(caminho), "serve": False}
    if not info["existe"]:
        info["motivo"] = "o arquivo nao existe"
        return info
    info["tamanho_mb"] = round(os.path.getsize(caminho) / 1048576.0, 2)
    try:
        with open(caminho, "rb") as f:
            if not f.read(16).startswith(b"SQLite format 3"):
                info["motivo"] = "nao e' um banco SQLite"
                return info
        c = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        try:
            tabelas = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            info["tabelas"] = len(tabelas)
            faltando = [t for t in TABELAS_NECESSARIAS if t not in tabelas]
            info["faltando"] = faltando
            if faltando:
                info["motivo"] = ("faltam as tabelas: " + ", ".join(faltando))
                info["algumas_tabelas"] = sorted(tabelas)[:12]
            else:
                info["serve"] = True
                info["contagem"] = {t: c.execute(
                    f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in TABELAS_NECESSARIAS}
        finally:
            c.close()
    except Exception as e:
        info["motivo"] = str(e)
    return info


def procurar_bancos(raiz, limite=4000):
    """Varre um diretorio atras do banco que tem a topologia."""
    achados, vistos = [], 0
    for pasta, subpastas, arquivos in os.walk(raiz):
        subpastas[:] = [d for d in subpastas
                        if d.lower() not in ("windows", "$recycle.bin",
                                             "system volume information")]
        for nome in arquivos:
            if not nome.lower().endswith((".db", ".sqlite", ".sqlite3", ".db3")):
                continue
            vistos += 1
            if vistos > limite:
                return achados, vistos
            achados.append(inspecionar_banco(os.path.join(pasta, nome)))
    return achados, vistos


def relatar_banco(info):
    if info["serve"]:
        print(f"  SERVE   {info['caminho']}  ({info['tamanho_mb']} MB)")
        c = info["contagem"]
        print(f"          {c['TopologicalObject']} objetos, {c['Road']} estradas, "
              f"{c['FunctionalLocation']} locais")
    else:
        print(f"  nao     {info['caminho']}")
        print(f"          {info.get('motivo', 'motivo desconhecido')}")
        if info.get("algumas_tabelas"):
            print(f"          tem: {', '.join(info['algumas_tabelas'])}...")

# A camada de frota e' opcional: se o modulo (ou o pacote rajant-api) nao
# estiver disponivel, o servidor de rotas continua servindo tudo o mais.
try:
    import rajant_frota
except ImportError:
    rajant_frota = None

# Atualizacao automatica do Modular.db a partir dos PTX. Tambem opcional:
# sem o modulo (ou sem o paramiko), o servidor roda com o banco que ja' tem.
try:
    import atualizar_db
except ImportError:
    atualizar_db = None

try:
    import desmontes as mod_desmontes
    from pagina_desmonte import HTML as PAGINA_DESMONTE
except ImportError:
    mod_desmontes = None
    PAGINA_DESMONTE = None

# nomes dos nos virtuais criados a cada requisicao de rota (nao existem na malha)
NO_ORIGEM  = "@VEICULO"
NO_DESTINO = "@DESTINO"

class TransversaMercator:
    """Transversa de Mercator com rotacao e origem deslocada.

    A grid da mina e' um caso desta formula; UTM e' outro (sem rotacao,
    origem em 500000 E). Manter uma implementacao so' evita que as duas
    divirjam — a foto aerea entra por aqui, vinda do seu proprio UTM.
    """
    def __init__(self, A, e2, phi0, lam0, k0, baseE, baseN, rot=0.0):
        self.A, self.e2 = A, e2
        self.phi0, self.lam0 = phi0, lam0
        self.k0 = k0
        self.baseE, self.baseN = baseE, baseN
        self.rot = rot

    def _M(self, phi):
        A, e2 = self.A, self.e2
        return A*((1-e2/4-3*e2**2/64-5*e2**3/256)*phi
                 -(3*e2/8+3*e2**2/32+45*e2**3/1024)*math.sin(2*phi)
                 +(15*e2**2/256+45*e2**3/1024)*math.sin(4*phi)
                 -(35*e2**3/3072)*math.sin(6*phi))

    def para_wgs84(self, E, N):
        A, e2, k0 = self.A, self.e2, self.k0
        dx, dy = E-self.baseE, N-self.baseN
        x =  math.cos(self.rot)*dx + math.sin(self.rot)*dy
        y = -math.sin(self.rot)*dx + math.cos(self.rot)*dy
        mu = (self._M(self.phi0)+y/k0)/(A*(1-e2/4-3*e2**2/64-5*e2**3/256))
        e1 = (1-math.sqrt(1-e2))/(1+math.sqrt(1-e2))
        p1 = (mu+(3*e1/2-27*e1**3/32)*math.sin(2*mu)
                +(21*e1**2/16-55*e1**4/32)*math.sin(4*mu)
                +(151*e1**3/96)*math.sin(6*mu)+(1097*e1**4/512)*math.sin(8*mu))
        ep2 = e2/(1-e2); C1 = ep2*math.cos(p1)**2; T1 = math.tan(p1)**2
        R1 = A*(1-e2)/(1-e2*math.sin(p1)**2)**1.5
        N1 = A/math.sqrt(1-e2*math.sin(p1)**2); D = x/(N1*k0)
        lat = p1-(N1*math.tan(p1)/R1)*(D**2/2-(5+3*T1+10*C1-4*C1**2-9*ep2)*D**4/24
              +(61+90*T1+298*C1+45*T1**2-252*ep2-3*C1**2)*D**6/720)
        lon = self.lam0+(D-(1+2*T1+C1)*D**3/6
              +(5-2*C1+28*T1-3*C1**2+8*ep2+24*T1**2)*D**5/120)/math.cos(p1)
        return math.degrees(lat), math.degrees(lon)

    def para_grid(self, lat, lon):
        A, e2, k0 = self.A, self.e2, self.k0
        phi, lam = math.radians(lat), math.radians(lon)
        N = A/math.sqrt(1-e2*math.sin(phi)**2)
        T = math.tan(phi)**2; C = e2/(1-e2)*math.cos(phi)**2
        a = (lam-self.lam0)*math.cos(phi); ep = e2/(1-e2)
        x = k0*N*(a+(1-T+C)*a**3/6+(5-18*T+T**2+72*C-58*ep)*a**5/120)
        y = k0*(self._M(phi)-self._M(self.phi0)+N*math.tan(phi)*
            (a**2/2+(5-T+9*C+4*C*C)*a**4/24+(61-58*T+T**2+600*C-330*ep)*a**6/720))
        return (math.cos(self.rot)*x-math.sin(self.rot)*y+self.baseE,
                math.sin(self.rot)*x+math.cos(self.rot)*y+self.baseN)

class Projecao(TransversaMercator):
    """Le a projecao do proprio banco. Converte nos dois sentidos."""
    def __init__(self, xml):
        g = lambda t: float(re.search(rf"<{t}>([-\d.]+)</{t}>", xml).group(1))
        TransversaMercator.__init__(
            self,
            A=float(re.search(r"<Type>utmWGS84 ([\d.]+)</Type>", xml).group(1)),
            e2=g("Eccentricity") ** 2,
            phi0=math.radians(g("Latitude")),
            lam0=math.radians(g("Longitude")),
            k0=g("Scale"),
            baseE=g("East"), baseN=g("North"),
            rot=math.radians(g("Rotation")))


def decodificar(blob):
    """byte0=versao | 4B=codigo | contadores | vertices XYZ float64"""
    if not blob or len(blob) < 5: return None, []
    cod = struct.unpack_from('<I', blob, 1)[0]
    if   cod == 1001: off, n = 5, 1
    elif cod == 1002: n = struct.unpack_from('<I', blob, 5)[0];  off = 9
    elif cod == 1003: n = struct.unpack_from('<I', blob, 9)[0];  off = 13
    else: return cod, []
    pts = []
    for i in range(n):
        o = off + i*24
        if o+24 > len(blob): break
        pts.append(struct.unpack_from('<ddd', blob, o))
    return cod, pts

def projeta_no_segmento(px, py, ax, ay, bx, by):
    """Pe da perpendicular de P sobre o segmento AB, preso aos extremos.
       Devolve (x, y, t) com t em [0,1] medindo a posicao dentro do segmento."""
    vx, vy = bx-ax, by-ay
    L2 = vx*vx + vy*vy
    if L2 <= 0.0:
        return ax, ay, 0.0
    t = ((px-ax)*vx + (py-ay)*vy) / L2
    if t < 0.0: t = 0.0
    elif t > 1.0: t = 1.0
    return ax + t*vx, ay + t*vy, t

class Malha:
    """Todo o estado servido. Trocado atomicamente quando reextrai.

       Depois de construida esta estrutura e' SOMENTE LEITURA: as rotas
       montam nos virtuais em dicionarios proprios, nunca alteram self.adj.
       E' o que permite atender varias requisicoes ao mesmo tempo."""
    def __init__(self, caminho_db):
        c = sqlite3.connect(f'file:{caminho_db}?mode=ro', uri=True)
        self.proj = Projecao(c.execute(
            "SELECT TransformationData FROM GeographicRegion").fetchone()[0])
        tipos = {i: n for i, n in c.execute("SELECT Id,Name FROM LocationType")}

        # --- estradas ---
        self.estradas, self.arestas = [], []
        pontos_no = defaultdict(list)
        for oid, nome, ativo, perigo, blob, vmax, fechada in c.execute("""
                SELECT t.Id,t.Name,t.IsActive,t.IsHazard,t.Geometry,
                       r.MaxSpeedLimit,r.IsClosed
                FROM Road r JOIN TopologicalObject t ON t.Id=r.Id"""):
            _, pts = decodificar(blob)
            if len(pts) < 2: continue
            comp = sum(math.dist(pts[i][:2], pts[i+1][:2]) for i in range(len(pts)-1))
            wgs = [list(self.proj.para_wgs84(p[0], p[1]))[::-1] for p in pts]
            self.estradas.append({"type":"Feature",
                "geometry":{"type":"LineString","coordinates":wgs},
                "properties":{"id":oid,"nome":nome,"comprimento_m":round(comp,1),
                              "fechada":bool(fechada),"ativa":bool(ativo)}})
            if nome and " - " in nome:
                a, b = [s.strip() for s in nome.split(" - ", 1)]
                self.arestas.append({"de":a,"para":b,"id":oid,"nome":nome,
                    "comprimento_m":round(comp,1),"fechada":bool(fechada),
                    "grid":[(p[0],p[1]) for p in pts],"wgs":wgs})
                pontos_no[a].append(pts[0]); pontos_no[b].append(pts[-1])

        # --- nos ---
        self.nos = {}
        for nome, ps in pontos_no.items():
            e = sum(p[0] for p in ps)/len(ps); n = sum(p[1] for p in ps)/len(ps)
            la, lo = self.proj.para_wgs84(e, n)
            self.nos[nome] = {"grid_e":round(e,2),"grid_n":round(n,2),
                              "lat":round(la,7),"lon":round(lo,7),"grau":0}

        # --- adjacencia (ignora trechos fechados) ---
        self.adj = defaultdict(list)
        for a in self.arestas:
            if a["de"] in self.nos: self.nos[a["de"]]["grau"] += 1
            if a["para"] in self.nos: self.nos[a["para"]]["grau"] += 1
            if a["fechada"]: continue
            self.adj[a["de"]].append((a["para"], a["comprimento_m"], a))
            self.adj[a["para"]].append((a["de"], a["comprimento_m"], a))

        # --- locais ---
        self.locais = []
        for oid, nome, ativo, blob, lt, elev in c.execute("""
                SELECT t.Id,t.Name,t.IsActive,t.Geometry,f.LocationTypeID,f.Elevation
                FROM FunctionalLocation f JOIN TopologicalObject t ON t.Id=f.Id"""):
            cod, pts = decodificar(blob)
            if not pts or cod != 1001: continue
            x, y, z = pts[0]; la, lo = self.proj.para_wgs84(x, y)
            self.locais.append({"id":oid,"nome":nome,"tipo":tipos.get(lt,str(lt)),
                "lat":round(la,7),"lon":round(lo,7),"elevacao":elev,
                "grid_e":round(x,2),"grid_n":round(y,2),"ativa":bool(ativo)})
        self.locais_por_nome = {}
        for o in self.locais:
            self.locais_por_nome.setdefault((o["nome"] or "").upper(), o)

        # --- areas ---
        self.areas = []
        for oid, nome, ativo, perigo, blob in c.execute(
                "SELECT Id,Name,IsActive,IsHazard,Geometry FROM TopologicalObject WHERE GeometryType=4"):
            _, pts = decodificar(blob)
            if len(pts) < 3: continue
            anel = [list(self.proj.para_wgs84(p[0], p[1]))[::-1] for p in pts]
            if anel[0] != anel[-1]: anel.append(anel[0])
            self.areas.append({"type":"Feature",
                "geometry":{"type":"Polygon","coordinates":[anel]},
                "properties":{"id":oid,"nome":nome,"ativa":bool(ativo),"perigo":bool(perigo)}})
        c.close()
        self.extraido_em = datetime.now().isoformat(timespec="seconds")

    def ancorar(self, E, N):
        """Compatibilidade: no mais proximo do ponto. Devolve (nome, dist_m)."""
        melhor, md = None, 1e18
        for nome, d in self.nos.items():
            dd = (d["grid_e"]-E)**2 + (d["grid_n"]-N)**2
            if dd < md: md, melhor = dd, nome
        return melhor, math.sqrt(md)

    def ancorar_trecho(self, E, N, so_abertos=True):
        """Projeta o ponto no TRECHO mais proximo (pe da perpendicular).

           E' o certo para um veiculo no meio de um trecho longo: ancorar no
           no' mais proximo faria a rota mandar voltar ate o no'.

           Devolve dict com o trecho, o indice do segmento, o ponto projetado
           em grid e em WGS84, e a distancia do veiculo ate a malha."""
        melhor, md = None, 1e18
        for ar in self.arestas:
            if so_abertos and ar["fechada"]: continue
            g = ar["grid"]
            for i in range(len(g)-1):
                x, y, t = projeta_no_segmento(E, N, g[i][0], g[i][1], g[i+1][0], g[i+1][1])
                d2 = (x-E)**2 + (y-N)**2
                if d2 < md:
                    md, melhor = d2, (ar, i, t, x, y)
        if melhor is None:
            # malha inteira fechada (nao deveria acontecer): aceita trecho fechado
            return None if so_abertos is False else self.ancorar_trecho(E, N, so_abertos=False)
        ar, i, t, x, y = melhor
        la, lo = self.proj.para_wgs84(x, y)
        return {"aresta": ar, "i": i, "t": t, "grid_e": x, "grid_n": y,
                "lat": la, "lon": lo, "dist_m": math.sqrt(md)}

    @staticmethod
    def _comp_ate_extremos(anc):
        """Distancia, andando pelo trecho, do ponto projetado ate cada extremo."""
        ar, i, x, y = anc["aresta"], anc["i"], anc["grid_e"], anc["grid_n"]
        g = ar["grid"]
        d_de = math.dist((x, y), g[i])
        for k in range(i, 0, -1):
            d_de += math.dist(g[k-1], g[k])
        d_para = math.dist((x, y), g[i+1])
        for k in range(i+1, len(g)-1):
            d_para += math.dist(g[k], g[k+1])
        return d_de, d_para

    @staticmethod
    def _s_ao_longo(anc):
        """Distancia acumulada do inicio do trecho ate o ponto projetado."""
        ar, i, x, y = anc["aresta"], anc["i"], anc["grid_e"], anc["grid_n"]
        g = ar["grid"]
        s = 0.0
        for k in range(i):
            s += math.dist(g[k], g[k+1])
        return s + math.dist(g[i], (x, y))

    @staticmethod
    def _wgs_ate_extremos(anc):
        """Geometria parcial do ponto projetado ate cada extremo do trecho.
           Sempre no sentido ponto -> extremo."""
        ar, i = anc["aresta"], anc["i"]
        w = ar["wgs"]; p = [anc["lon"], anc["lat"]]
        ate_de   = [p] + [w[k] for k in range(i, -1, -1)]
        ate_para = [p] + [w[k] for k in range(i+1, len(w))]
        return ate_de, ate_para

    def _trecho_virtual(self, de, para, wgs, comp, base):
        return {"de": de, "para": para, "wgs": wgs, "id": base["id"],
                "nome": base["nome"], "comprimento_m": round(comp, 1),
                "fechada": base["fechada"], "virtual": True}

    def _liga(self, extra, no, vizinho, peso, aresta):
        extra[no].append((vizinho, peso, aresta))

    def _adjacencias_do_ancoradouro(self, extra, anc, nome_virtual, como_origem):
        """Pendura o no' virtual nos dois extremos do trecho em que ancorou."""
        ar = anc["aresta"]
        d_de, d_para = self._comp_ate_extremos(anc)
        ate_de, ate_para = self._wgs_ate_extremos(anc)
        if como_origem:
            # sentido de marcha: virtual -> extremo
            a1 = self._trecho_virtual(nome_virtual, ar["de"],   ate_de,   d_de,   ar)
            a2 = self._trecho_virtual(nome_virtual, ar["para"], ate_para, d_para, ar)
        else:
            # sentido de marcha: extremo -> virtual
            a1 = self._trecho_virtual(ar["de"],   nome_virtual, ate_de[::-1],   d_de,   ar)
            a2 = self._trecho_virtual(ar["para"], nome_virtual, ate_para[::-1], d_para, ar)
        self._liga(extra, nome_virtual, ar["de"],   d_de,   a1)
        self._liga(extra, nome_virtual, ar["para"], d_para, a2)
        self._liga(extra, ar["de"],   nome_virtual, d_de,   a1)
        self._liga(extra, ar["para"], nome_virtual, d_para, a2)

    def _ligacao_no_mesmo_trecho(self, extra, anc_o, anc_d):
        """Origem e destino no mesmo trecho: liga um ao outro direto, senao a
           rota daria a volta pelo no' de uma das pontas."""
        ar = anc_o["aresta"]
        s_o, s_d = self._s_ao_longo(anc_o), self._s_ao_longo(anc_d)
        comp = abs(s_d - s_o)
        w = ar["wgs"]
        p_o = [anc_o["lon"], anc_o["lat"]]; p_d = [anc_d["lon"], anc_d["lat"]]
        if s_d >= s_o:
            meio = [w[k] for k in range(anc_o["i"]+1, anc_d["i"]+1)]
        else:
            meio = [w[k] for k in range(anc_o["i"], anc_d["i"], -1)]
        linha = [p_o] + meio + [p_d]
        a = self._trecho_virtual(NO_ORIGEM, NO_DESTINO, linha, comp, ar)
        self._liga(extra, NO_ORIGEM, NO_DESTINO, comp, a)
        self._liga(extra, NO_DESTINO, NO_ORIGEM, comp, a)

    def rota(self, origem, destino, extra=None):
        """Menor caminho por distancia. 'extra' traz a adjacencia dos nos
           virtuais desta requisicao — self.adj nunca e' alterado."""
        extra = extra or {}
        def vizinhos(u):
            return self.adj.get(u, []) + extra.get(u, [])
        conhecido = lambda n: n in self.adj or n in self.nos or n in extra
        if not conhecido(origem):
            return {"ok": False, "erro": f"no de origem desconhecido: {origem}"}
        if not conhecido(destino):
            return {"ok": False, "erro": f"no de destino desconhecido: {destino}"}
        if origem == destino:
            return {"ok": True, "origem": origem, "destino": destino,
                    "distancia_m": 0.0, "n_nos": 1, "nos": [origem],
                    "geometria": {"type": "LineString", "coordinates": []}}
        dist = {origem: 0.0}; ant = {}; pq = [(0.0, origem)]; visto = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in visto: continue
            visto.add(u)
            if u == destino: break
            for v, w, ar in vizinhos(u):
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd; ant[v] = (u, ar); heapq.heappush(pq, (nd, v))
        if destino not in dist:
            return {"ok": False, "erro": "sem caminho entre os pontos",
                    "detalhe": "os dois nos estao em partes desconexas da malha"}
        # reconstroi
        caminho, trechos, cur = [destino], [], destino
        while cur != origem:
            cur, ar = ant[cur]; caminho.append(cur); trechos.append(ar)
        caminho.reverse(); trechos.reverse()
        linha = []
        for i, ar in enumerate(trechos):
            pts = ar["wgs"]
            # orienta o trecho no sentido da marcha
            if ar["para"] == caminho[i]: pts = pts[::-1]
            linha.extend(pts if not linha else pts[1:])
        return {"ok": True, "origem": origem, "destino": destino,
                "distancia_m": round(dist[destino], 1),
                "n_nos": len(caminho), "nos": caminho,
                "geometria": {"type": "LineString", "coordinates": linha}}

    def local_por_nome(self, nome):
        return self.locais_por_nome.get((nome or "").upper())

    def rota_de_coordenada(self, lat, lon, destino, limite_ancoragem=500.0,
                           destino_ponto=None):
        """Rota a partir de uma coordenada GPS ate um no', um local nomeado ou
           um ponto solto (destino_ponto=(lat,lon), usado para equipamento em
           campo). Ancora as duas pontas por projecao no trecho mais proximo."""
        E, N = self.proj.para_grid(lat, lon)
        anc_o = self.ancorar_trecho(E, N)
        if anc_o is None:
            return {"ok": False, "erro": "malha viaria vazia",
                    "detalhe": "o servidor nao carregou nenhum trecho"}
        extra = defaultdict(list)
        self._adjacencias_do_ancoradouro(extra, anc_o, NO_ORIGEM, como_origem=True)

        #      ou local nomeado — os tres ancorados no trecho mais proximo
        anc_d = None; local = None
        if destino_ponto is not None:
            dE, dN = self.proj.para_grid(destino_ponto[0], destino_ponto[1])
            anc_d = self.ancorar_trecho(dE, dN)
            alvo = NO_DESTINO
            self._adjacencias_do_ancoradouro(extra, anc_d, NO_DESTINO, como_origem=False)
            if anc_d["aresta"]["id"] == anc_o["aresta"]["id"]:
                self._ligacao_no_mesmo_trecho(extra, anc_o, anc_d)
        elif destino in self.nos:
            alvo = destino
        else:
            local = self.local_por_nome(destino)
            if local is None:
                return {"ok": False, "erro": f"destino desconhecido: {destino}",
                        "detalhe": "nao e' nome de no' nem de local cadastrado"}
            anc_d = self.ancorar_trecho(local["grid_e"], local["grid_n"])
            alvo = NO_DESTINO
            self._adjacencias_do_ancoradouro(extra, anc_d, NO_DESTINO, como_origem=False)
            if anc_d["aresta"]["id"] == anc_o["aresta"]["id"]:
                self._ligacao_no_mesmo_trecho(extra, anc_o, anc_d)

        r = self.rota(NO_ORIGEM, alvo, extra)
        r["destino_pedido"] = destino
        r["ancorou_em"] = anc_o["aresta"]["nome"]
        r["dist_ate_malha_m"] = round(anc_o["dist_m"], 1)
        r["ancorou_lat"] = round(anc_o["lat"], 7)
        r["ancorou_lon"] = round(anc_o["lon"], 7)
        if r.get("ok"):
            # esconde os nos virtuais da lista devolvida ao operador
            r["nos"] = [n for n in r["nos"] if not n.startswith("@")]
            r["n_nos"] = len(r["nos"])
        if destino_ponto is not None:
            r["destino_lat"] = round(destino_ponto[0], 7)
            r["destino_lon"] = round(destino_ponto[1], 7)
            r["dist_destino_ate_malha_m"] = round(anc_d["dist_m"], 1)
        elif local:
            r["destino_lat"] = local["lat"]; r["destino_lon"] = local["lon"]
            r["destino_tipo"] = local["tipo"]
            r["dist_destino_ate_malha_m"] = round(anc_d["dist_m"], 1)
        elif destino in self.nos:
            r["destino_lat"] = self.nos[destino]["lat"]
            r["destino_lon"] = self.nos[destino]["lon"]
        if anc_o["dist_m"] > limite_ancoragem:
            r["aviso"] = (f"veiculo a {anc_o['dist_m']:.0f} m da via mais proxima; "
                          "confira a posicao do GPS")
        return r

class Repositorio:
    """Guarda a malha e reextrai de tempos em tempos."""
    def __init__(self, caminho_db, intervalo_s):
        self.caminho = caminho_db
        self.intervalo = intervalo_s
        self.malha = None
        self.hash = None
        self.erro = None
        self.ultima_checagem = None
        self.trava = threading.Lock()
        self.recarregar(forcar=True)
        t = threading.Thread(target=self._laco, daemon=True); t.start()

    def _hash_arquivo(self):
        h = hashlib.sha256()
        with open(self.caminho, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
        return h.hexdigest()[:16]

    def recarregar(self, forcar=False):
        try:
            self.ultima_checagem = datetime.now().isoformat(timespec="seconds")
            h = self._hash_arquivo()
            if not forcar and h == self.hash:
                return False           # nada mudou, nao reprocessa
            nova = Malha(self.caminho)  # extrai fora da trava
            with self.trava:
                self.malha, self.hash, self.erro = nova, h, None
            # o desmonte mede distancia na grid: precisa da projecao do banco
            if Handler.desmontes is not None:
                Handler.desmontes.projecao = nova.proj
            registrar("malha", f"recarregada: {len(nova.arestas)} trechos, "
                                f"{len(nova.nos)} nos, {len(nova.locais)} locais "
                                f"(hash {h})")
            return True
        except Exception as e:
            info = inspecionar_banco(self.caminho)
            detalhe = str(e)
            if not info["serve"]:
                detalhe = (f"{e} -- {info.get('motivo', '')}. Este arquivo nao e' o "
                           "banco de topologia do DISPATCH. Ache o certo com: "
                           "servidor_rotas --procurar-db <pasta>")
            with self.trava: self.erro = detalhe
            registrar("malha", f"ERRO ao extrair: {detalhe}")
            return False

    def _laco(self):
        while True:
            time.sleep(self.intervalo)
            self.recarregar()

PAGINA = """<!doctype html><meta charset=utf-8><title>Malha da mina</title>
<link rel=stylesheet href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#m{height:100%;margin:0}#p{position:absolute;z-index:999;background:#fff;
padding:8px;right:10px;top:10px;font:13px sans-serif;max-height:85%;overflow:auto}</style>
<div id=m></div><div id=p>
<b>Destino</b><br><select id=d style="max-width:220px"></select>
<button onclick=tracar()>Tracar</button><div id=i style="margin-top:6px"></div></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
var m=L.map('m'),cam=null;
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(m);
fetch('/api/malha').then(r=>r.json()).then(g=>{
  var l=L.geoJSON(g,{style:f=>({color:f.properties.fechada?'#c00':'#06c',weight:3})}).addTo(m);
  m.fitBounds(l.getBounds());});
fetch('/api/locais').then(r=>r.json()).then(j=>{
  var s=document.getElementById('d');
  j.locais.forEach(o=>{if(!o.ativa)return;
    var op=document.createElement('option');op.value=o.nome;
    op.textContent=o.tipo+': '+o.nome;s.appendChild(op);
    L.circleMarker([o.lat,o.lon],{radius:3,color:'#093'}).bindPopup(o.tipo+'<br>'+o.nome).addTo(m);});});
function tracar(){var d=document.getElementById('d').value;
  var c=m.getCenter();
  fetch('/api/rota?de_lat='+c.lat+'&de_lon='+c.lng+'&para='+encodeURIComponent(d))
  .then(r=>r.json()).then(j=>{
    if(!j.ok){document.getElementById('i').textContent='sem rota: '+j.erro;return;}
    if(cam)m.removeLayer(cam);
    cam=L.geoJSON(j.geometria,{style:{color:'#f60',weight:6}}).addTo(m);
    m.fitBounds(cam.getBounds());
    document.getElementById('i').innerHTML='<b>'+(j.distancia_m/1000).toFixed(2)+
    ' km</b><br>'+j.n_nos+' nos<br>ancorou no trecho: '+j.ancorou_em+
    '<br>a '+j.dist_ate_malha_m+' m da via';});}
</script>"""

class Handler(BaseHTTPRequestHandler):
    repo = None
    frota = None              # None quando a camada Rajant nao foi ligada
    atualizador = None        # busca do Modular.db nos PTX
    desmontes = None          # areas de exclusao de desmonte
    foto = None               # pasta dos ladrilhos da foto aerea
    frota_erro = None         # motivo, para explicar ao operador
    limite_ancoragem = 500.0
    idade_maxima_fix = 60.0   # segundos: acima disso a posicao vira aviso
    protocol_version = "HTTP/1.1"

    def log_message(self, *a): pass   # silencia o log padrao

    def _envia(self, obj, codigo=200, etag=None):
        corpo = (obj if isinstance(obj, bytes)
                 else json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        self.send_response(codigo)
        self.send_header("Content-Type",
            "text/html; charset=utf-8" if isinstance(obj, bytes)
            else "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if etag:
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(corpo)

    def _envia_foto(self, caminho_rel):
        """Serve um arquivo da pasta da foto aerea.

        Ladrilho nao muda depois de gerado: vale um cache longo no cliente. O
        tablet baixa cada um uma vez e nunca mais pergunta, o que importa numa
        rede de mina.
        """
        raiz = Handler.foto
        if not raiz:
            return self._envia({"ok": False, "erro": "sem foto configurada"}, 404)
        # nada de subir de pasta pela URL
        limpo = posixpath.normpath("/" + caminho_rel).lstrip("/")
        if not limpo or limpo.startswith("..") or os.path.isabs(limpo):
            return self._envia({"ok": False, "erro": "caminho invalido"}, 400)
        arq = os.path.join(raiz, *limpo.split("/"))
        if not os.path.isfile(arq):
            return self._envia({"ok": False, "erro": "sem esse ladrilho"}, 404)
        try:
            with open(arq, "rb") as f:
                corpo = f.read()
        except OSError as e:
            return self._envia({"ok": False, "erro": str(e)}, 500)
        ehjson = arq.endswith(".json")
        self.send_response(200)
        self.send_header("Content-Type",
                         "application/json; charset=utf-8" if ehjson
                         else "image/png" if arq.endswith(".png")
                         else "image/jpeg")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control",
                         "no-cache" if ehjson
                         else "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        rp = Handler.repo
        with rp.trava:
            malha, h, erro = rp.malha, rp.hash, rp.erro

        if u.path == "/":
            return self._envia(PAGINA.encode("utf-8"))

        if u.path in ("/desmonte", "/desmonte/"):
            if PAGINA_DESMONTE is None:
                return self._envia({"ok": False,
                    "erro": "pagina_desmonte.py nao encontrado ao lado do servidor"}, 503)
            return self._envia(PAGINA_DESMONTE.encode("utf-8"))

        if u.path == "/api/saude":
            saude = {"ok": malha is not None, "versao": VERSAO,
                "hash": h, "erro": erro,
                "extraido_em": malha.extraido_em if malha else None,
                "ultima_checagem": rp.ultima_checagem,
                "intervalo_s": rp.intervalo}
            saude["frota"] = (Handler.frota.resumo() if Handler.frota
                              else {"ativa": False, "motivo": Handler.frota_erro
                                    or "frota nao configurada"})
            saude["atualizacao_db"] = (Handler.atualizador.estado()
                                       if Handler.atualizador
                                       else {"ativa": False})
            return self._envia(saude)

        # antes da checagem da malha: a foto vale mesmo com o banco em falta
        if u.path.startswith("/foto/"):
            return self._envia_foto(u.path[len("/foto/"):])

        if malha is None:
            return self._envia({"ok": False, "erro": erro or "malha ainda nao carregada"}, 503)

        # cache: se o PTX ja tem esta versao, responde 304 e nao gasta rede
        if (self.headers.get("If-None-Match") == h and u.path.startswith("/api/")
                and u.path != "/api/rota"):
            self.send_response(304); self.send_header("ETag", h)
            self.send_header("Content-Length", "0"); self.end_headers(); return

        if u.path == "/api/versao":
            return self._envia({"hash": h, "extraido_em": malha.extraido_em,
                "trechos": len(malha.arestas), "nos": len(malha.nos),
                "locais": len(malha.locais)}, etag=h)

        if u.path == "/api/locais":
            tipo = q.get("tipo", [None])[0]; busca = (q.get("q", [""])[0] or "").upper()
            L = [o for o in malha.locais
                 if (not tipo or o["tipo"] == tipo) and (not busca or busca in o["nome"].upper())]
            return self._envia({"total": len(L), "locais": L}, etag=h)

        if u.path == "/api/malha":
            return self._envia({"type": "FeatureCollection",
                                "features": malha.estradas}, etag=h)

        if u.path == "/api/areas":
            return self._envia({"type": "FeatureCollection",
                                "features": malha.areas}, etag=h)

        if u.path == "/api/desmontes":
            if Handler.desmontes is None:
                return self._envia({"ok": False, "desmontes": [],
                    "erro": "modulo de desmonte nao disponivel"}, 503)
            equipamentos = (Handler.frota.instantaneo() if Handler.frota else [])
            todos = q.get("todos", ["0"])[0] not in ("0", "", "nao")
            lista = Handler.desmontes.com_afetados(
                equipamentos, malha.locais, apenas_ativos=not todos)
            return self._envia({"ok": True, "total": len(lista),
                                "desmontes": lista})

        if u.path == "/api/equipamentos":
            # Posicao ao vivo: nao entra no cache por ETag, muda a todo momento.
            if Handler.frota is None:
                return self._envia({"ok": False, "total": 0, "equipamentos": [],
                    "erro": Handler.frota_erro or
                            "camada de frota nao configurada neste servidor",
                    "detalhe": "suba o servidor com --rajant-cache/--rajant-ips "
                               "e --rajant-senha"}, 503)
            lista = Handler.frota.instantaneo()
            tipo = q.get("tipo", [None])[0]
            if tipo:
                lista = [r for r in lista if r["tipo"] == tipo]
            if q.get("moveis", ["0"])[0] not in ("0", "", "nao"):
                lista = [r for r in lista if r["movel"]]
            if q.get("com_fix", ["0"])[0] not in ("0", "", "nao"):
                lista = [r for r in lista if r["tem_fix"]]
            resumo = Handler.frota.resumo()
            resumo.update({"ok": True, "total": len(lista), "equipamentos": lista})
            return self._envia(resumo)

        if u.path == "/api/rota":
            destino = q.get("para", [None])[0]
            equipamento = q.get("para_equip", [None])[0]
            if equipamento:
                return self._rota_ate_equipamento(malha, q, equipamento)
            if not destino:
                return self._envia({"ok": False,
                    "erro": "falta o parametro 'para' (ou 'para_equip')"}, 400)
            if "de_lat" in q and "de_lon" in q:
                try:
                    la = float(q["de_lat"][0]); lo = float(q["de_lon"][0])
                except ValueError:
                    return self._envia({"ok": False, "erro": "de_lat/de_lon invalidos"}, 400)
                r = malha.rota_de_coordenada(la, lo, destino, Handler.limite_ancoragem)
            elif "de" in q:
                origem = q["de"][0]
                alvo = destino
                if destino not in malha.nos:
                    loc = malha.local_por_nome(destino)
                    if loc:
                        alvo, _ = malha.ancorar(loc["grid_e"], loc["grid_n"])
                r = malha.rota(origem, alvo)
                r["destino_pedido"] = destino
                r["ancorou_em"] = None
                r["dist_ate_malha_m"] = None
            else:
                return self._envia({"ok": False,
                    "erro": "informe 'de' (nome do no) ou 'de_lat'+'de_lon'"}, 400)
            return self._envia(r, 200 if r.get("ok") else 404)

        self._envia({"erro": "rota nao encontrada"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if Handler.desmontes is None:
            return self._envia({"ok": False,
                "erro": "modulo de desmonte nao disponivel"}, 503)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64 * 1024:
                return self._envia({"ok": False, "erro": "requisicao grande demais"}, 413)
            corpo = self.rfile.read(n).decode("utf-8") if n else "{}"
            dados = json.loads(corpo or "{}")
            if not isinstance(dados, dict):
                raise ValueError("esperava um objeto JSON")
        except Exception as e:
            return self._envia({"ok": False, "erro": f"pedido invalido: {e}"}, 400)

        if u.path == "/api/desmontes":
            try:
                item = Handler.desmontes.adicionar(dados)
            except mod_desmontes.ErroDeCadastro as e:
                return self._envia({"ok": False, "erro": str(e)}, 400)
            except Exception as e:
                return self._envia({"ok": False, "erro": f"nao consegui gravar: {e}"}, 500)
            registrar("desmonte", f"cadastrado: {item['nome']} "
                                  f"({item['lat']}, {item['lon']}) raio {item['raio_m']} m")
            # copia: sem isto o "ok" da resposta acabaria gravado dentro do
            # proprio registro no arquivo
            resposta = dict(item)
            resposta["ok"] = True
            return self._envia(resposta)

        if u.path == "/api/desmontes/remover":
            ident = (dados.get("id") or "").strip()
            r = Handler.desmontes.remover(ident)
            if r is None:
                return self._envia({"ok": False, "erro": "desmonte nao encontrado"}, 404)
            registrar("desmonte", f"encerrado: {r['nome']}")
            return self._envia({"ok": True, "id": ident})

        return self._envia({"ok": False, "erro": "rota nao encontrada"}, 404)

    def _rota_ate_equipamento(self, malha, q, nome):
        """O destino e' uma maquina que anda. A posicao vem do GPS do radio
           Rajant dela; a rota e' recalculada a cada pedido."""
        if Handler.frota is None:
            return self._envia({"ok": False,
                "erro": Handler.frota_erro or
                        "camada de frota nao configurada neste servidor"}, 503)
        if "de_lat" not in q or "de_lon" not in q:
            return self._envia({"ok": False,
                "erro": "informe 'de_lat' e 'de_lon' para rota ate equipamento"}, 400)
        try:
            la = float(q["de_lat"][0]); lo = float(q["de_lon"][0])
        except ValueError:
            return self._envia({"ok": False, "erro": "de_lat/de_lon invalidos"}, 400)

        eq = Handler.frota.por_nome(nome)
        if eq is None:
            return self._envia({"ok": False,
                "erro": f"equipamento desconhecido: {nome}",
                "detalhe": "nao ha radio com esse nome na lista da frota"}, 404)
        if not eq["tem_fix"]:
            return self._envia({"ok": False,
                "erro": f"{nome} esta sem posicao GPS",
                "detalhe": ("radio offline" if not eq["online"]
                            else "o radio responde mas ainda nao tem fix"),
                "equipamento": eq}, 404)

        r = malha.rota_de_coordenada(la, lo, nome, Handler.limite_ancoragem,
                                     destino_ponto=(eq["lat"], eq["lon"]))
        r["equipamento"] = eq
        r["destino_movel"] = True
        idade = eq.get("idade_fix_s")
        if idade is not None and idade > Handler.idade_maxima_fix:
            r["aviso_destino"] = (f"a posicao de {nome} tem {int(idade)} s; "
                                  "o equipamento pode ja' ter saido dali")
        return self._envia(r, 200 if r.get("ok") else 404)

def dir_do_programa():
    """Pasta onde o programa mora. Num .exe do PyInstaller e' a pasta do
       executavel, nao a temporaria onde ele se descompacta."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def achar_ao_lado(nome):
    """Procura um arquivo ao lado do programa e no diretorio atual."""
    for base in (dir_do_programa(), os.getcwd()):
        p = os.path.join(base, nome)
        if os.path.exists(p):
            return p
    return None


OPCOES_CONFIG = ("db", "porta", "endereco", "intervalo", "log", "log_max_kb",
                 "sem_console", "limite_ancoragem")


def ler_config(caminho=None):
    """config.json ao lado do programa. E' o que permite abrir com dois
       cliques, sem linha de comando. A linha de comando, quando usada,
       continua vencendo o arquivo."""
    arq = caminho or achar_ao_lado("config.json")
    if not arq:
        return {}
    try:
        with open(arq, encoding="utf-8") as f:
            bruto = json.load(f)
    except Exception as e:
        raise SystemExit(f"config.json invalido ({arq}): {e}")
    cfg = {}
    for k, v in bruto.items():
        if k.startswith("_"):        # JSON nao tem comentario: "_" e' o nosso
            continue
        cfg[k.replace("-", "_")] = v
    cfg["_arquivo"] = arq
    return cfg


def caminho_relativo_ao_programa(caminho):
    """Caminho relativo no config vale a partir da pasta do programa. Como
       tarefa agendada o diretorio atual e' o System32, e relativo nunca
       seria encontrado."""
    if not caminho or os.path.isabs(caminho):
        return caminho
    perto = achar_ao_lado(caminho)
    return perto or caminho


def segurar_janela(msg=None):
    """Aberto com dois cliques, a janela fecharia junto com o erro e o
       operador nao veria nada. Segura ate' ele ler."""
    if msg:
        print(msg)
    try:
        if sys.stdin and sys.stdin.isatty():
            input("\nPressione ENTER para fechar...")
    except Exception:
        pass


def iniciar_frota(a):
    """Liga a leitura do GPS dos radios, se ela tiver sido configurada.
       Nenhuma falha aqui pode impedir o servidor de rotas de subir.

       Sem nenhum argumento --rajant-*, procura sozinho o rajant.json e o
       rajant_ips_cache.json ao lado do programa: assim o servico sobe com a
       frota ligada sem precisar de linha de comando."""
    cfg = {}
    # bloco "rajant" dentro do config.json, para tudo caber num arquivo so'
    bloco = ler_config(a.config).get("rajant")
    if isinstance(bloco, dict):
        cfg.update({k: v for k, v in bloco.items() if not k.startswith("_")})
    if not a.rajant_config and not a.rajant_cache and not a.rajant_ips and not cfg:
        achado = achar_ao_lado("rajant.json")
        if achado:
            a.rajant_config = achado
            print(f"  frota    : usando {achado}")
    if a.rajant_config:
        try:
            with open(a.rajant_config, encoding="utf-8") as f:
                cfg = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
        except Exception as e:
            Handler.frota_erro = f"nao consegui ler {a.rajant_config}: {e}"
            print(f"  AVISO: {Handler.frota_erro}")
            return
    # caminho relativo dentro do rajant.json vale a partir da pasta do
    # programa, nao do diretorio atual: como servico agendado o processo roda
    # com o diretorio em System32, e um relativo nunca seria encontrado
    if cfg.get("cache") and not os.path.isabs(cfg["cache"]):
        perto = achar_ao_lado(cfg["cache"])
        if perto:
            cfg["cache"] = perto

    if a.rajant_cache: cfg["cache"] = a.rajant_cache
    if a.rajant_ips:   cfg["ips"] = [i.strip() for i in a.rajant_ips.split(",") if i.strip()]
    if a.rajant_senha: cfg["senha"] = a.rajant_senha
    if a.rajant_porta is not None: cfg["porta"] = a.rajant_porta
    if a.rajant_role  is not None: cfg["role"] = a.rajant_role
    if a.rajant_intervalo is not None: cfg["intervalo"] = a.rajant_intervalo

    # sem cache declarado, tenta o nome usado pelo exportador do Rajant
    if not cfg.get("cache") and not cfg.get("ips"):
        padrao = achar_ao_lado("rajant_ips_cache.json")
        if padrao:
            cfg["cache"] = padrao
            print(f"  frota    : usando {padrao}")

    if not cfg.get("cache") and not cfg.get("ips"):
        Handler.frota_erro = ("frota nao configurada: ponha um rajant.json "
                              "(ou rajant_ips_cache.json) ao lado do programa")
        return
    if not cfg.get("senha"):
        Handler.frota_erro = ("falta a senha dos radios: ponha \"senha\" no "
                              "rajant.json ou passe --rajant-senha")
        print(f"  AVISO: {Handler.frota_erro}")
        return
    if rajant_frota is None:
        Handler.frota_erro = ("modulo rajant_frota.py nao encontrado ao lado "
                              "do servidor")
        print(f"  AVISO: {Handler.frota_erro}")
        return
    try:
        frota = rajant_frota.frota_de_configuracao(cfg, registrar=registrar)
        if frota is None:
            Handler.frota_erro = "nenhum radio na lista"
            return
        Handler.frota = frota.iniciar()

        def resumo_periodico():
            """Uma linha de tempos em tempos, em vez de 140. E' por ela que se
               percebe a frota parar sem precisar abrir a API."""
            anterior = None
            while True:
                time.sleep(60)
                try:
                    r = Handler.frota.resumo()
                    agora = (r["online"], r["com_fix"])
                    idade = r.get("idade_ultima_leitura_s")
                    parada = idade is not None and idade > 5 * r["intervalo_s"]
                    if agora != anterior or parada:
                        registrar("frota",
                                  f"{r['com_fix']}/{r['total']} com posicao, "
                                  f"{r['online']} online, {r['leituras']} leituras"
                                  + (f" -- ATENCAO: nada novo ha {idade:.0f}s"
                                     if parada else ""))
                        anterior = agora
                except Exception as e:
                    registrar("frota", f"resumo falhou: {e}")

        threading.Thread(target=resumo_periodico, daemon=True).start()
    except Exception as e:
        Handler.frota_erro = str(e)
        print(f"  AVISO: frota desligada: {e}")


def iniciar_desmontes(a, cfg):
    """Areas de exclusao. O arquivo fica ao lado do programa, para o operador
       poder ler e corrigir na mao se precisar."""
    if mod_desmontes is None:
        return
    caminho = getattr(a, "desmontes", None) or cfg.get("desmontes")
    if not caminho:
        caminho = os.path.join(dir_do_programa(), "desmontes.json")
    else:
        caminho = caminho_relativo_ao_programa(caminho) or caminho
    try:
        proj = Handler.repo.malha.proj if Handler.repo.malha else None
        Handler.desmontes = mod_desmontes.Desmontes(caminho, projecao=proj)
        n = len(Handler.desmontes.listar())
        registrar("desmonte", f"{n} desmonte(s) ativo(s) em {caminho}")
    except Exception as e:
        registrar("desmonte", f"nao consegui abrir {caminho}: {e}")


def iniciar_foto(a, cfg):
    """Aponta a pasta dos ladrilhos da foto aerea, quando ha uma."""
    caminho = a.foto or cfg.get("foto") or "foto"
    caminho = caminho_relativo_ao_programa(caminho)
    manifesto = os.path.join(caminho, "manifesto.json")
    if not os.path.isfile(manifesto):
        return
    Handler.foto = caminho
    try:
        with open(manifesto, encoding="utf-8") as f:
            m = json.load(f)
        registrar("foto", f"{m.get('ladrilhos', '?')} ladrilhos, "
                          f"{len(m.get('niveis', []))} niveis, de "
                          f"{m.get('origem', '?')}")
    except Exception as e:
        registrar("foto", f"manifesto ilegivel: {e}")
        Handler.foto = None


def montar_atualizador(a, cfg):
    """Monta a busca do Modular.db nos PTX, sem ligar o laco ainda.
       Como tudo o mais aqui, falha nisso nao impede o servidor de servir."""
    if a.sem_atualizacao:
        return None
    bloco = cfg.get("atualizar_db")
    if not isinstance(bloco, dict) or not bloco.get("hosts"):
        return None
    if atualizar_db is None:
        registrar("db", "modulo atualizar_db.py nao encontrado ao lado do servidor")
        return None

    def recarrega():
        # no arranque o repositorio ainda nao existe; a malha ja' sai do
        # arquivo novo quando ele for montado, logo em seguida
        if Handler.repo is not None:
            Handler.repo.recarregar(forcar=True)

    try:
        return atualizar_db.de_configuracao(bloco, a.db, registrar=registrar,
                                            ao_atualizar=recarrega)
    except Exception as e:
        registrar("db", f"atualizacao automatica desligada: {e}")
        return None


def buscar_db_no_inicio(a, at):
    """Procura o banco mais novo nos PTX antes de abrir a porta.

       Devolve True quando a rodada terminou aqui — nesse caso o laco
       periodico comeca dormindo, em vez de repetir a busca no mesmo minuto.
    """
    if at is None:
        return False
    limite = a.espera_db if a.espera_db is not None else at.espera_inicio_s
    if a.sem_espera_db or not at.esperar_no_inicio or limite <= 0:
        return False
    print(f"  banco novo: procurando em {len(at.hosts)} PTX (ate {limite}s)...")
    r = at.buscar_no_inicio(limite)
    if not r.get("concluiu"):
        print(f"              {r.get('motivo')}")
    elif r.get("trocou"):
        print(f"              trocado pelo de {r.get('host')}"
              f" ({r.get('estradas', '?')} estradas, {r.get('data_remota', '')})")
    else:
        print(f"              {r.get('motivo', 'sem novidade')}")
    return bool(r.get("concluiu"))


def main():
    ap = argparse.ArgumentParser(description="Servidor de rotas da mina")
    # defaults None: o config.json so' vence quando a linha de comando calou
    ap.add_argument("--db", help="caminho do Modular.db (padrao: ao lado do programa)")
    ap.add_argument("--porta", type=int)
    ap.add_argument("--endereco")
    ap.add_argument("--intervalo", type=int,
                    help="segundos entre checagens do banco (padrao 900 = 15 min)")
    ap.add_argument("--config", help="arquivo de configuracao (padrao: config.json "
                                     "ao lado do programa)")
    ap.add_argument("--limite-ancoragem", type=float,
                    help="metros ate a via acima dos quais a rota vem com aviso")
    ap.add_argument("--atualizar-db-agora", action="store_true",
                    help="busca o Modular.db nos PTX uma vez e sai")
    ap.add_argument("--sem-atualizacao", action="store_true",
                    help="nao busca o Modular.db nos PTX")
    ap.add_argument("--sem-espera-db", action="store_true",
                    help="sobe sem esperar a primeira busca do banco nos PTX")
    ap.add_argument("--espera-db", type=int, default=None, metavar="S",
                    help="segundos que o arranque espera pela busca do banco")
    ap.add_argument("--foto", metavar="DIR",
                    help="pasta com os ladrilhos da foto aerea "
                         "(padrao: foto/ ao lado do programa)")
    ap.add_argument("--gerar-foto", metavar="TIF",
                    help="converte os ortofotos .tif em ladrilhos, e sai. "
                         "Aceita um arquivo ou a pasta com todos eles")
    # repassados ao mapa_foto: sem isto, quem so' tem o .exe nao alcanca
    ap.add_argument("--mais-fino", type=float, metavar="M",
                    help="metros por pixel do nivel mais detalhado da foto")
    ap.add_argument("--crs", help="sistema da foto: auto | grid | epsg:31983")
    ap.add_argument("--sem-datum", action="store_true",
                    help="nao corrige a diferenca de datum da foto")
    ap.add_argument("--fundo", action="append", metavar="TIF", default=[],
                    help="imagem de fundo para tapar onde nao ha ortofoto")
    ap.add_argument("--qualidade-foto", type=int, metavar="Q",
                    help="qualidade do JPEG dos ladrilhos, 1 a 100")
    ap.add_argument("--desmontes", metavar="ARQ",
                    help="arquivo das areas de desmonte "
                         "(padrao: desmontes.json ao lado do programa)")
    ap.add_argument("--conferir-db", metavar="ARQ",
                    help="diz se um arquivo serve como Modular.db, e sai")
    ap.add_argument("--procurar-db", metavar="DIR",
                    help="varre a pasta atras do banco com a topologia, e sai")
    ap.add_argument("--log", help="grava a saida neste arquivo. Recomendado no "
                    "Windows: console em QuickEdit congela o processo se alguem "
                    "clicar dentro dele")
    ap.add_argument("--log-max-kb", type=int)
    ap.add_argument("--sem-console", action="store_true",
                    help="nao escreve no console (use junto com --log)")
    g = ap.add_argument_group("frota Rajant (posicao dos equipamentos)")
    g.add_argument("--rajant-cache",
                   help="rajant_ips_cache.json: ip -> nome do radio")
    g.add_argument("--rajant-ips",
                   help="IPs separados por virgula, se nao usar o cache")
    g.add_argument("--rajant-senha", help="senha dos radios")
    # defaults None de proposito: com um valor aqui, a linha de comando
    # sobrescreveria silenciosamente o que veio do --rajant-config
    g.add_argument("--rajant-porta", type=int, default=None,
                   help="porta do BCAPI no radio (padrao 2300)")
    g.add_argument("--rajant-role", default=None, help="papel no radio (padrao VIEW)")
    g.add_argument("--rajant-intervalo", type=int, default=None,
                   help="segundos entre leituras de GPS de cada radio (padrao 10)")
    g.add_argument("--rajant-config",
                   help="JSON com senha/cache/ips (evita senha na linha de comando)")
    a = ap.parse_args()

    cfg = ler_config(a.config)
    for opcao in OPCOES_CONFIG:
        if getattr(a, opcao, None) in (None, False) and opcao in cfg:
            setattr(a, opcao, cfg[opcao])
    if a.porta is None: a.porta = 5000
    if a.endereco is None: a.endereco = "0.0.0.0"
    if a.intervalo is None: a.intervalo = 900
    if a.log_max_kb is None: a.log_max_kb = 5120
    if a.limite_ancoragem is None: a.limite_ancoragem = 500.0
    if a.db is None:
        a.db = achar_ao_lado("Modular.db") or "Modular.db"
    a.db = caminho_relativo_ao_programa(a.db)
    if a.log:
        a.log = caminho_relativo_ao_programa(a.log)

    registrar.configurar(a.log, a.log_max_kb, not a.sem_console)

    if a.conferir_db:
        print(f"=== conferindo {a.conferir_db} ===")
        info = inspecionar_banco(a.conferir_db)
        relatar_banco(info)
        sys.exit(0 if info["serve"] else 1)

    if a.gerar_foto:
        try:
            import mapa_foto
        except Exception as e:
            print(f"mapa_foto.py nao encontrado ao lado do servidor: {e}")
            segurar_janela()
            sys.exit(1)
        # a conversao precisa da grid da mina, que vem do banco. Sem essa
        # conferencia o sqlite estoura um traceback no rosto do operador
        info = inspecionar_banco(a.db)
        if not info["serve"]:
            print(f"ERRO: preciso do Modular.db para saber a grid da mina.")
            print(f"  procurei em: {os.path.abspath(a.db)}")
            print(f"  {info.get('motivo', '')}")
            print()
            print("Aponte o banco na linha de comando:")
            print('  servidor_rotas --gerar-foto <pasta> --db "C:\\Modular\\Modular.db"')
            print("Ou ache o certo:  servidor_rotas --procurar-db C:\\Modular")
            segurar_janela()
            sys.exit(1)
        saida = caminho_relativo_ao_programa(a.foto or "foto")
        try:
            argumentos = [a.gerar_foto, "--db", a.db, "--saida", saida]
            if a.mais_fino is not None:
                argumentos += ["--mais-fino", str(a.mais_fino)]
            if a.crs:
                argumentos += ["--crs", a.crs]
            if a.sem_datum:
                argumentos += ["--sem-datum"]
            if a.qualidade_foto is not None:
                argumentos += ["--qualidade", str(a.qualidade_foto)]
            for f in (a.fundo or []):
                argumentos += ["--fundo", f]
            codigo = mapa_foto.main(argumentos)
        except Exception as e:
            print(f"ERRO na conversao: {e}")
            codigo = 1
        segurar_janela()
        sys.exit(codigo)

    if a.atualizar_db_agora:
        bloco = cfg.get("atualizar_db")
        if not isinstance(bloco, dict) or not bloco.get("hosts"):
            print("Nada configurado. Ponha no config.json:")
            print('  "atualizar_db": {"hosts": "10.188.98.1-50", '
                  '"usuario": "mms", "senha": "...", '
                  '"caminho": "/home/mms/DE/Modular.db"}')
            segurar_janela()
            sys.exit(1)
        if atualizar_db is None:
            print("modulo atualizar_db.py nao encontrado ao lado do servidor")
            segurar_janela(); sys.exit(1)
        at = atualizar_db.de_configuracao(bloco, a.db, registrar=registrar)
        print(f"=== buscando o Modular.db em {len(at.hosts)} PTX ===")
        r = at.atualizar_agora()
        print()
        print(("  " + r.get("motivo", "")).rstrip())
        segurar_janela()
        sys.exit(0 if r.get("ok") else 1)

    if a.procurar_db:
        print(f"=== procurando o banco da topologia em {a.procurar_db} ===")
        achados, vistos = procurar_bancos(a.procurar_db)
        bons = [i for i in achados if i["serve"]]
        for info in sorted(achados, key=lambda i: not i["serve"]):
            relatar_banco(info)
        print(f"\n{vistos} arquivo(s) de banco examinado(s), {len(bons)} servem.")
        if bons:
            print("\nUse:")
            print(f'  servidor_rotas --db "{bons[0]["caminho"]}"')
        else:
            print("\nNenhum banco com a topologia nesta pasta. Procure a pasta do "
                  "servidor DISPATCH.")
        sys.exit(0 if bons else 1)

    print(f"=== Servidor de rotas v{VERSAO} ===")
    print(f"  banco     : {os.path.abspath(a.db)}")

    # antes de conferir e de extrair a malha: o banco que interessa e' o dos
    # PTX, e conferir o velho para logo em seguida trocar so' assusta quem esta'
    # lendo a tela
    Handler.atualizador = montar_atualizador(a, cfg)
    ja_buscou = buscar_db_no_inicio(a, Handler.atualizador)

    # a conferencia vem depois da busca: instalacao nova nao tem banco nenhum,
    # e desistir antes de perguntar aos PTX seria desistir do unico lugar onde
    # ele existe
    if not os.path.exists(a.db):
        print()
        print(f"ERRO: banco nao encontrado: {a.db}")
        print()
        if Handler.atualizador is not None:
            print("A busca nos PTX tambem nao trouxe nenhum -- veja as linhas acima.")
        print("Ponha o Modular.db ao lado do programa, ou aponte no config.json:")
        print('  { "db": "C:\\Modular\\Modular.db" }')
        print("Para achar o banco certo:  servidor_rotas --procurar-db C:\\Modular")
        segurar_janela()
        sys.exit(1)

    conferido = inspecionar_banco(a.db)
    if not conferido["serve"]:
        print()
        print("  *** ATENCAO: este arquivo nao serve como banco de topologia ***")
        print(f"  {conferido.get('motivo', '')}")
        print("  Sem ele nao ha mapa nem rota (a frota Rajant funciona do mesmo jeito).")
        print(f"  Ache o banco certo:  servidor_rotas --procurar-db C:\\Modular")
        print()
    print(f"  reextracao: a cada {a.intervalo}s (so' se o arquivo mudar)")
    Handler.repo = Repositorio(a.db, a.intervalo)
    Handler.limite_ancoragem = a.limite_ancoragem
    iniciar_frota(a)
    if Handler.atualizador is not None:
        try:
            Handler.atualizador.iniciar(agora=not ja_buscou)
        except Exception as e:
            registrar("db", f"atualizacao automatica desligada: {e}")
    iniciar_desmontes(a, cfg)
    iniciar_foto(a, cfg)
    srv = ThreadingHTTPServer((a.endereco, a.porta), Handler)
    if Handler.frota is not None:
        print(f"  frota     : {len(Handler.frota.ips)} radios Rajant, "
              f"GPS a cada {Handler.frota.intervalo}s")
    elif Handler.frota_erro:
        print(f"  frota     : desligada ({Handler.frota_erro})")
    print(f"  ouvindo   : http://{a.endereco}:{a.porta}/")
    print(f"  mapa teste: http://localhost:{a.porta}/")
    if Handler.desmontes is not None:
        print(f"  desmonte  : http://localhost:{a.porta}/desmonte")
    print()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrando.")

if __name__ == "__main__":
    main()
