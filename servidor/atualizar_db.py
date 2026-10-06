#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
 atualizar_db.py — mantem o Modular.db do servidor em dia, puxando dos PTX.

 POR QUE
   A lavra muda e o banco de topologia muda junto. Quem tem a versao nova
   sao os proprios PTX, que recebem do DISPATCH. Em vez de alguem lembrar de
   copiar na mao, o servidor busca sozinho de tempos em tempos.

 COMO
   1. Descobre quais PTX da faixa respondem em SSH (teste de porta, rapido).
   2. Pergunta a data do arquivo em cada um e escolhe O MAIS NOVO — nao o
      primeiro que responder: PTX parado ha semanas tem banco velho.
   3. Baixa para um arquivo temporario.
   4. So' troca o banco em producao se o baixado PASSAR NA CONFERENCIA:
      SQLite valido, com as tabelas de topologia e com estradas dentro.
      Banco truncado no meio do caminho nao pode derrubar a navegacao.
   5. Guarda o anterior como .anterior, para dar para voltar atras.

 A troca e' atomica (os.replace), entao uma requisicao no meio da atualizacao
 nunca ve' arquivo pela metade.
=============================================================================
"""
import os, shutil, socket, sqlite3, threading, time
from datetime import datetime

PORTA_SSH_PADRAO = 22
CAMINHO_REMOTO_PADRAO = "/home/mms/DE/Modular.db"
USUARIO_PADRAO = "mms"
INTERVALO_PADRAO_H = 6
# Quanto o arranque espera pela primeira busca antes de abrir a porta. Esperar
# sem hora marcada seria pior que nao esperar: um PTX que aceita a conexao e
# nao responde seguraria o servidor para sempre.
ESPERA_INICIO_PADRAO_S = 120

TABELAS_TOPOLOGIA = ("GeographicRegion", "TopologicalObject", "Road",
                     "FunctionalLocation", "LocationType")


class ErroDependencia(RuntimeError):
    """Falta o paramiko. Mensagem pronta para mostrar ao operador."""


def hosts_do_banco(caminho_db, prefixo=None):
    """Os terminais que o proprio DISPATCH conhece, tirados do Modular.db.

    HostAddress liga o nome do equipamento ao endereco: CA-1001 ->
    10.188.98.1. Ler dali e' melhor que varrer faixa na mao — a lista se
    mantem sozinha quando a mina poe ou tira terminal, e nao ha como errar o
    tamanho da faixa. Este projeto varria 10.188.98.1-50 e a mina tem 161
    terminais, ate' o .242: dois tercos ficavam de fora da busca.
    """
    try:
        con = sqlite3.connect(f"file:{caminho_db}?mode=ro", uri=True)
    except Exception:
        return []
    try:
        linhas = con.execute("SELECT Host, Address FROM HostAddress "
                             "WHERE Address IS NOT NULL").fetchall()
    except Exception:
        return []
    finally:
        con.close()
    saida = []
    for nome, endereco in linhas:
        endereco = (endereco or "").strip()
        if not endereco:
            continue
        if prefixo and not endereco.startswith(prefixo):
            continue
        # o servidor central nao serve: o banco dele nao vem de um PTX
        if nome and str(nome).lower().startswith("mtaf"):
            continue
        saida.append(endereco)
    vistos, unicos = set(), []
    for h in saida:
        if h not in vistos:
            vistos.add(h); unicos.append(h)
    return unicos


def expandir_hosts(spec):
    """'10.188.98.1-50' -> os 50 enderecos. Aceita virgula e IP solto:
       '10.188.98.1-50, 10.188.111.254'."""
    if not spec:
        return []
    if isinstance(spec, (list, tuple)):
        partes = []
        for p in spec:
            partes.extend(expandir_hosts(p))
        return partes
    saida = []
    for pedaco in str(spec).split(","):
        pedaco = pedaco.strip()
        if not pedaco:
            continue
        if "-" in pedaco:
            base, fim = pedaco.rsplit("-", 1)
            base = base.strip()
            try:
                ultimo = int(fim)
                cabeca, primeiro = base.rsplit(".", 1)
                for n in range(int(primeiro), ultimo + 1):
                    saida.append(f"{cabeca}.{n}")
                continue
            except ValueError:
                pass          # nao era faixa; entra como esta'
        saida.append(pedaco)
    # sem repetir, mantendo a ordem
    vistos, unicos = set(), []
    for h in saida:
        if h not in vistos:
            vistos.add(h); unicos.append(h)
    return unicos


def porta_aberta(host, porta=PORTA_SSH_PADRAO, timeout=1.5):
    """Teste de porta antes do SSH: varrer 50 PTX com timeout de SSH em cada
       um levaria minutos; assim os desligados caem em 1,5 s."""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, porta))
        return True
    except OSError:
        return False
    finally:
        try: s.close()
        except OSError: pass


class ClienteSftp:
    """SFTP com senha. Usa paramiko porque o scp do Windows nao aceita senha
       na linha de comando — e depender de interacao humana derrota o
       proposito de atualizar sozinho."""

    def __init__(self, host, usuario, senha, porta=PORTA_SSH_PADRAO, timeout=10):
        self.host, self.usuario, self.senha = host, usuario, senha
        self.porta, self.timeout = porta, timeout
        self.ssh = None
        self.sftp = None

    @staticmethod
    def _paramiko():
        try:
            import paramiko
            return paramiko
        except ImportError as e:
            raise ErroDependencia(
                "o pacote paramiko nao esta instalado; sem ele nao da para "
                "buscar o Modular.db dos PTX. Instale com: pip install paramiko. "
                "Detalhe: " + str(e))

    def conectar(self):
        paramiko = self._paramiko()
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(self.host, port=self.porta, username=self.usuario,
                         password=self.senha, timeout=self.timeout,
                         allow_agent=False, look_for_keys=False,
                         banner_timeout=self.timeout, auth_timeout=self.timeout)
        self.sftp = self.ssh.open_sftp()

    def info(self, caminho):
        """(mtime, tamanho) do arquivo remoto."""
        st = self.sftp.stat(caminho)
        return st.st_mtime, st.st_size

    def baixar(self, caminho, destino):
        self.sftp.get(caminho, destino)

    def fechar(self):
        for obj in (self.sftp, self.ssh):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self.sftp = self.ssh = None


def conferir_banco(caminho):
    """O baixado presta? Devolve (serve, motivo, estradas)."""
    try:
        if os.path.getsize(caminho) < 4096:
            return False, "arquivo pequeno demais", 0
        with open(caminho, "rb") as f:
            if not f.read(16).startswith(b"SQLite format 3"):
                return False, "nao e' um banco SQLite", 0
        c = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        try:
            tabelas = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            faltando = [t for t in TABELAS_TOPOLOGIA if t not in tabelas]
            if faltando:
                return False, "faltam as tabelas: " + ", ".join(faltando), 0
            estradas = c.execute("SELECT COUNT(*) FROM Road").fetchone()[0]
            if estradas <= 0:
                return False, "banco sem nenhuma estrada", 0
            return True, "", estradas
        finally:
            c.close()
    except Exception as e:
        return False, str(e), 0


def hash_curto(caminho):
    import hashlib
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:16]


class Atualizador:
    def __init__(self, hosts, destino, usuario=USUARIO_PADRAO, senha="",
                 caminho_remoto=CAMINHO_REMOTO_PADRAO, porta=PORTA_SSH_PADRAO,
                 intervalo_s=INTERVALO_PADRAO_H * 3600, fabrica_cliente=None,
                 registrar=None, ao_atualizar=None, max_paralelo=10):
        self.hosts = expandir_hosts(hosts)
        self.destino = destino
        self.usuario, self.senha = usuario, senha
        self.caminho_remoto = caminho_remoto
        self.porta = porta
        self.intervalo_s = max(60, int(intervalo_s))
        self.max_paralelo = max(1, int(max_paralelo))
        self.fabrica_cliente = fabrica_cliente or (
            lambda h: ClienteSftp(h, self.usuario, self.senha, self.porta))
        self.registrar = registrar or (lambda *a: None)
        self.ao_atualizar = ao_atualizar
        self.parar = threading.Event()
        self.thread = None
        self.trava = threading.Lock()
        # uma rodada por vez: a do arranque e a do laco periodico podem se
        # encontrar, e duas baixando para o mesmo arquivo temporario e' briga
        self.em_curso = threading.Lock()
        self.esperar_no_inicio = True
        self.espera_inicio_s = ESPERA_INICIO_PADRAO_S
        self.ultimo = {"quando": None, "resultado": "ainda nao rodou",
                       "host": None, "trocou": False, "tentativas": 0}

    def estado(self):
        with self.trava:
            e = dict(self.ultimo)
        e["hosts"] = len(self.hosts)
        e["intervalo_h"] = round(self.intervalo_s / 3600.0, 2)
        return e

    def _anota(self, **kw):
        with self.trava:
            self.ultimo.update(kw)
            self.ultimo["quando"] = datetime.now().isoformat(timespec="seconds")

    def candidatos(self):
        """PTX com SSH respondendo, em paralelo para nao demorar."""
        vivos, trava = [], threading.Lock()

        def prova(host):
            if porta_aberta(host, self.porta):
                with trava:
                    vivos.append(host)

        fila = list(self.hosts)
        while fila:
            lote = [fila.pop(0) for _ in range(min(self.max_paralelo, len(fila)))]
            ts = [threading.Thread(target=prova, args=(h,), daemon=True) for h in lote]
            for t in ts: t.start()
            for t in ts: t.join(timeout=6)
            if self.parar.is_set():
                break
        return sorted(vivos)

    def mais_novo(self, hosts):
        """Pergunta a data do arquivo e devolve o dono da versao mais recente.
           Primeiro que responde nao serve: PTX esquecido tem banco velho."""
        melhor = None
        for host in hosts:
            if self.parar.is_set():
                break
            cli = self.fabrica_cliente(host)
            try:
                cli.conectar()
                mtime, tamanho = cli.info(self.caminho_remoto)
                if melhor is None or mtime > melhor[1]:
                    melhor = (host, mtime, tamanho)
            except Exception as e:
                self.registrar("db", f"[{host}] nao deu para consultar: {e}")
            finally:
                try: cli.fechar()
                except Exception: pass
        return melhor

    def atualizar_agora(self):
        """Uma rodada completa. Devolve um dicionario com o que aconteceu."""
        if not self.em_curso.acquire(blocking=False):
            return {"ok": False, "motivo": "ja ha uma busca em andamento"}
        try:
            return self._rodada()
        finally:
            self.em_curso.release()

    def _rodada(self):
        hosts = self.candidatos()
        if not hosts:
            r = {"ok": False, "motivo": "nenhum PTX respondeu em SSH",
                 "tentativas": len(self.hosts)}
            self._anota(resultado=r["motivo"], trocou=False,
                        tentativas=len(self.hosts), host=None)
            self.registrar("db", r["motivo"] + f" ({len(self.hosts)} enderecos)")
            return r

        self.registrar("db", f"{len(hosts)} PTX respondendo; procurando o banco mais novo")
        melhor = self.mais_novo(hosts)
        if melhor is None:
            r = {"ok": False, "motivo": "nenhum PTX tem o arquivo acessivel",
                 "tentativas": len(hosts)}
            self._anota(resultado=r["motivo"], trocou=False,
                        tentativas=len(hosts), host=None)
            return r

        host, mtime, tamanho = melhor
        quando = datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
        self.registrar("db", f"o mais novo esta em {host} ({quando}, "
                             f"{tamanho/1024:.0f} KB); baixando")

        tmp = self.destino + ".baixando"
        cli = self.fabrica_cliente(host)
        try:
            cli.conectar()
            cli.baixar(self.caminho_remoto, tmp)
        except Exception as e:
            try: os.remove(tmp)
            except OSError: pass
            r = {"ok": False, "motivo": f"falhou baixar de {host}: {e}", "host": host}
            self._anota(resultado=r["motivo"], trocou=False, host=host,
                        tentativas=len(hosts))
            self.registrar("db", r["motivo"])
            return r
        finally:
            try: cli.fechar()
            except Exception: pass

        serve, motivo, estradas = conferir_banco(tmp)
        if not serve:
            try: os.remove(tmp)
            except OSError: pass
            r = {"ok": False, "motivo": f"o banco de {host} nao presta: {motivo}",
                 "host": host}
            self._anota(resultado=r["motivo"], trocou=False, host=host,
                        tentativas=len(hosts))
            self.registrar("db", r["motivo"] + " -- mantido o banco atual")
            return r

        novo_hash = hash_curto(tmp)
        igual = (os.path.exists(self.destino)
                 and hash_curto(self.destino) == novo_hash)
        if igual:
            os.remove(tmp)
            r = {"ok": True, "trocou": False, "host": host,
                 "motivo": "o banco ja' estava atualizado", "hash": novo_hash}
            self._anota(resultado=r["motivo"], trocou=False, host=host,
                        tentativas=len(hosts))
            self.registrar("db", f"sem novidade ({novo_hash})")
            return r

        # guarda o anterior antes de trocar: da' para voltar atras
        if os.path.exists(self.destino):
            try:
                shutil.copy2(self.destino, self.destino + ".anterior")
            except Exception as e:
                self.registrar("db", f"nao consegui guardar o anterior: {e}")
        os.replace(tmp, self.destino)      # troca atomica

        r = {"ok": True, "trocou": True, "host": host, "hash": novo_hash,
             "estradas": estradas, "data_remota": quando,
             "motivo": f"banco atualizado de {host}"}
        self._anota(resultado=r["motivo"], trocou=True, host=host,
                    tentativas=len(hosts))
        self.registrar("db", f"banco atualizado de {host}: {estradas} estradas, "
                             f"hash {novo_hash}")
        if self.ao_atualizar:
            try:
                self.ao_atualizar()
            except Exception as e:
                self.registrar("db", f"recarga apos atualizar falhou: {e}")
        return r

    def buscar_no_inicio(self, limite_s=None):
        """Uma rodada ANTES de o servidor abrir a porta.

        Quem liga o servidor de manha quer o mapa de hoje, nao o do ultimo
        desligamento. Por isso a primeira busca acontece na frente, com o
        resultado na tela. Passado o limite, ela continua sozinha e o servidor
        sobe com o banco que ja' tem: melhor servir a malha de ontem que nao
        servir nada.
        """
        limite = int(limite_s if limite_s is not None else self.espera_inicio_s)
        resultado = {}

        def roda():
            try:
                resultado.update(self.atualizar_agora() or {})
            except Exception as e:
                resultado.update({"ok": False, "motivo": f"erro: {e}"})

        t = threading.Thread(target=roda, daemon=True, name="busca-db-inicio")
        t.start()
        t.join(timeout=max(1, limite))
        if t.is_alive():
            return {"concluiu": False, "ok": False,
                    "motivo": f"passou de {limite}s; a busca continua "
                              f"em segundo plano"}
        r = dict(resultado)
        r["concluiu"] = True
        return r

    def iniciar(self, agora=True):
        if self.thread:
            return self
        def laco():
            if agora:
                try: self.atualizar_agora()
                except Exception as e: self.registrar("db", f"erro: {e}")
            while not self.parar.wait(self.intervalo_s):
                try: self.atualizar_agora()
                except Exception as e: self.registrar("db", f"erro: {e}")
        self.thread = threading.Thread(target=laco, daemon=True, name="atualiza-db")
        self.thread.start()
        self.registrar("db", f"atualizacao automatica ligada: {len(self.hosts)} "
                             f"enderecos, a cada {self.intervalo_s/3600:.1f} h")
        return self

    def encerrar(self):
        self.parar.set()
        if self.thread:
            self.thread.join(timeout=3)
            self.thread = None


def de_configuracao(cfg, destino, registrar=None, ao_atualizar=None):
    """Monta o atualizador a partir do bloco 'atualizar_db' do config.json."""
    if not cfg or not cfg.get("hosts"):
        return None
    hosts = cfg["hosts"]
    if str(hosts).strip().lower() in ("banco", "do-banco", "db"):
        # a lista sai do proprio Modular.db que esta' em uso
        achados = hosts_do_banco(destino, cfg.get("prefixo"))
        if achados:
            hosts = achados
            if registrar:
                registrar("db", f"{len(achados)} terminais lidos do "
                                f"HostAddress do proprio banco")
        else:
            hosts = cfg.get("hosts_reserva") or "10.188.98.1-254"
            if registrar:
                registrar("db", "nao consegui ler HostAddress; usando a faixa "
                                f"{hosts}")
    at = Atualizador(
        hosts=hosts,
        destino=destino,
        usuario=cfg.get("usuario", USUARIO_PADRAO),
        senha=cfg.get("senha", ""),
        caminho_remoto=cfg.get("caminho", CAMINHO_REMOTO_PADRAO),
        porta=int(cfg.get("porta", PORTA_SSH_PADRAO)),
        intervalo_s=int(float(cfg.get("intervalo_h", INTERVALO_PADRAO_H)) * 3600),
        registrar=registrar, ao_atualizar=ao_atualizar)
    at.esperar_no_inicio = bool(cfg.get("esperar_no_inicio", True))
    at.espera_inicio_s = int(float(cfg.get("espera_s", ESPERA_INICIO_PADRAO_S)))
    return at
