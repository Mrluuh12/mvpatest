#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
 desmontes.py — area de exclusao de desmonte.

 O QUE E'
   Toda semana saem os pontos do desmonte. Tudo que esta' dentro do raio
   (400 m por padrao) precisa sair dali — equipamento, carretinha e as ERMs.
   Aqui o ponto e' cadastrado uma vez, no servidor, e todo PTX passa a ver o
   circulo e a lista do que esta' dentro.

 COMO A DISTANCIA E' MEDIDA
   Na grid da mina, que ja' e' metrica: converte lat/lon para (E,N) e mede em
   linha reta. Nada de aproximacao de graus — 400 m e' um limite operacional,
   nao um numero decorativo.

 O CADASTRO ACEITA OS DOIS JEITOS
   Coordenada de topografia (E,N na grid) ou lat/lon. Quem publica o desmonte
   costuma trabalhar em grid; quem le' no GPS, em graus.

 ISTO E' APOIO VISUAL. O plano de fogo oficial e' que manda; esta tela nao
 substitui a liberacao da area pela equipe de desmonte.
=============================================================================
"""
import json, math, os, re, threading, uuid
from datetime import datetime

RAIO_PADRAO_M = 400.0
# margem para avisar quem esta' perto de entrar na area
MARGEM_APROXIMACAO_M = 150.0


class ErroDeCadastro(ValueError):
    """Dado invalido vindo do formulario. A mensagem vai para a tela."""


def _num(v, nome, mini=None, maxi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ErroDeCadastro(f"{nome} invalido: {v!r}")
    if math.isnan(x) or math.isinf(x):
        raise ErroDeCadastro(f"{nome} invalido: {v!r}")
    if mini is not None and x < mini:
        raise ErroDeCadastro(f"{nome} tem que ser maior que {mini}")
    if maxi is not None and x > maxi:
        raise ErroDeCadastro(f"{nome} tem que ser menor que {maxi}")
    return x


# NOME (E,N) — e' assim que a topografia publica os furos do desmonte.
# Aceita tambem "NOME E N", ponto-e-virgula, tabulacao, e linha so' com os
# numeros. O nome pode ter digito dentro (CS_0020_091), entao a coordenada e'
# procurada primeiro dentro dos parenteses.
_SO_NUMERO = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")


def _tokens(trecho):
    """Quebra por espaco, tabulacao e ponto-e-virgula."""
    return [t for t in re.split(r"[\s;]+", trecho.strip()) if t]


def _dois_numeros(trecho):
    """Tira (E, N) de um pedaco de texto.

       A virgula e' ambigua: separador em '667401.967,7904915.048' e decimal
       em '667401,967'. Em vez de adivinhar com regex, decide pela estrutura:
       primeiro separa por espaco; se sobrar um token so', a virgula dentro
       dele so' pode ser separador."""
    toks = _tokens(trecho)
    if len(toks) == 1 and "," in toks[0]:
        partes = [p for p in toks[0].split(",") if p]
        if len(partes) == 2:
            toks = partes
    numeros = []
    for t in toks:
        t = t.strip("(),")
        if _SO_NUMERO.match(t):
            numeros.append(float(t.replace(",", ".")))
        elif "," in t and len(t.split(",")) == 2:
            a, b = t.split(",")
            if _SO_NUMERO.match(a) and _SO_NUMERO.match(b):
                numeros.extend([float(a), float(b)])
    if len(numeros) < 2:
        return None
    return numeros[-2], numeros[-1]


def interpretar_pontos(texto):
    """Le a lista colada e devolve [{nome, grid_e, grid_n}].

       Formato da topografia: NOME (E,N), uma linha por furo. Aceita tambem
       'NOME E N', ponto-e-virgula, tabulacao, e linha so' com os numeros.

       Nao inventa: linha sem duas coordenadas plausiveis entra na lista de
       recusadas, com o motivo — um furo faltando calado e' pior que um erro."""
    pontos, recusadas = [], []
    for bruto in (texto or "").splitlines():
        linha = bruto.strip()
        if not linha:
            continue
        m = re.search(r"\(([^)]*)\)", linha)
        if m:
            nome = linha[:m.start()].strip(" ,;\t")
            coords = _dois_numeros(m.group(1))
        else:
            toks = _tokens(linha)
            coords = _dois_numeros(linha)
            # o nome e' o que sobra na frente dos numeros
            nome = ""
            for t in toks:
                if _dois_numeros(t) or _SO_NUMERO.match(t.strip("(),")):
                    break
                nome = (nome + " " + t).strip()
        if coords is None:
            recusadas.append({"linha": bruto, "motivo": "nao achei E e N"})
            continue
        e, n = coords
        # coordenada de mina e' grande: assim digito de nome nao vira coordenada
        if abs(e) < 1000 or abs(n) < 1000:
            recusadas.append({"linha": bruto,
                              "motivo": "coordenada pequena demais para grid"})
            continue
        pontos.append({"nome": nome[:40], "grid_e": e, "grid_n": n})
    return pontos, recusadas


class Desmontes:
    """Guarda os desmontes num JSON ao lado do servidor.

       Sao poucos por semana: um arquivo simples, gravado de forma atomica,
       resolve — e da' para ler e corrigir na mao se precisar."""

    def __init__(self, caminho, projecao=None):
        self.caminho = caminho
        self.projecao = projecao          # objeto com para_grid(lat, lon)
        self.trava = threading.Lock()
        self.itens = []
        self.carregar()

    def carregar(self):
        try:
            with open(self.caminho, encoding="utf-8") as f:
                dados = json.load(f)
            itens = dados.get("desmontes", []) if isinstance(dados, dict) else dados
            with self.trava:
                self.itens = [d for d in itens if isinstance(d, dict)]
        except FileNotFoundError:
            with self.trava:
                self.itens = []
        except Exception:
            # arquivo corrompido nao pode impedir o servidor de subir
            with self.trava:
                self.itens = []
        return self

    def _gravar(self):
        """Chamar com a trava tomada."""
        tmp = self.caminho + ".tmp"
        d = os.path.dirname(os.path.abspath(self.caminho))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"desmontes": self.itens}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.caminho)

    def adicionar(self, dados):
        """Valida e grava. Aceita lat/lon ou grid_e/grid_n."""
        nome = (dados.get("nome") or "").strip()
        if not nome:
            raise ErroDeCadastro("de um nome ao desmonte")
        if len(nome) > 80:
            nome = nome[:80]

        raio = _num(dados.get("raio_m", RAIO_PADRAO_M), "raio", 1, 20000)

        brutos = dados.get("pontos")
        recusadas = []
        if isinstance(brutos, str):
            brutos, recusadas = interpretar_pontos(brutos)
        if not brutos:
            tem_grid = dados.get("grid_e") not in (None, "") and \
                       dados.get("grid_n") not in (None, "")
            tem_geo = dados.get("lat") not in (None, "") and \
                      dados.get("lon") not in (None, "")
            if not tem_grid and not tem_geo:
                raise ErroDeCadastro("informe a coordenada: E/N da grid, "
                                     "lat/lon, ou cole a lista de pontos")
            if tem_grid:
                brutos = [{"nome": "", "grid_e": dados["grid_e"],
                           "grid_n": dados["grid_n"]}]
            else:
                brutos = [{"nome": "", "lat": dados["lat"], "lon": dados["lon"]}]

        pontos = []
        for i, b in enumerate(brutos, 1):
            if b.get("grid_e") not in (None, "") and b.get("grid_n") not in (None, ""):
                pe = _num(b["grid_e"], "E")
                pn = _num(b["grid_n"], "N")
                if self.projecao is None:
                    raise ErroDeCadastro("o servidor ainda nao carregou a projecao "
                                         "do banco; use lat/lon ou tente de novo")
                pla, plo = self.projecao.para_wgs84(pe, pn)
            else:
                pla = _num(b.get("lat"), "latitude", -90, 90)
                plo = _num(b.get("lon"), "longitude", -180, 180)
                pe = pn = None
                if self.projecao is not None:
                    pe, pn = self.projecao.para_grid(pla, plo)
            pontos.append({"nome": (b.get("nome") or f"P{i}")[:40],
                           "lat": round(pla, 7), "lon": round(plo, 7),
                           "grid_e": round(pe, 2) if pe is not None else None,
                           "grid_n": round(pn, 2) if pn is not None else None})

        # o centro serve para rotular e enquadrar; a distancia usa os pontos
        lat = round(sum(p["lat"] for p in pontos) / len(pontos), 7)
        lon = round(sum(p["lon"] for p in pontos) / len(pontos), 7)
        e = n = None
        if all(p["grid_e"] is not None for p in pontos):
            e = round(sum(p["grid_e"] for p in pontos) / len(pontos), 2)
            n = round(sum(p["grid_n"] for p in pontos) / len(pontos), 2)

        item = {
            "id": uuid.uuid4().hex[:12],
            "nome": nome,
            "lat": round(lat, 7), "lon": round(lon, 7),
            "grid_e": round(e, 2) if e is not None else None,
            "grid_n": round(n, 2) if n is not None else None,
            "raio_m": round(raio, 1),
            "pontos": pontos,
            "n_pontos": len(pontos),
            "recusadas": recusadas,
            "quando": (dados.get("quando") or "").strip()[:40],
            "observacao": (dados.get("observacao") or "").strip()[:300],
            "ativo": True,
            "criado_em": datetime.now().isoformat(timespec="seconds"),
        }
        with self.trava:
            self.itens.append(item)
            self._gravar()
        return item

    def remover(self, ident):
        """Desativa em vez de apagar: o historico da semana serve de registro."""
        with self.trava:
            for d in self.itens:
                if d.get("id") == ident:
                    d["ativo"] = False
                    d["removido_em"] = datetime.now().isoformat(timespec="seconds")
                    self._gravar()
                    return d
        return None

    def apagar(self, ident):
        with self.trava:
            antes = len(self.itens)
            self.itens = [d for d in self.itens if d.get("id") != ident]
            if len(self.itens) != antes:
                self._gravar()
                return True
        return False

    def listar(self, apenas_ativos=True):
        with self.trava:
            itens = [dict(d) for d in self.itens]
        if apenas_ativos:
            itens = [d for d in itens if d.get("ativo", True)]
        itens.sort(key=lambda d: d.get("criado_em", ""), reverse=True)
        return itens

    def _para_grid(self, lat, lon):
        if self.projecao is None:
            return None
        return self.projecao.para_grid(lat, lon)

    def distancia_m(self, desmonte, lat, lon):
        """Menor distancia ate QUALQUER furo do desmonte, medida na grid.

           O raio de 400 m vale de cada furo, nao do centro do conjunto: um
           desmonte espalhado tem area de exclusao maior que um circulo so'."""
        pontos = desmonte.get("pontos") or [desmonte]
        alvo = self._para_grid(lat, lon)
        melhor = float("inf")
        for p in pontos:
            if alvo is not None and p.get("grid_e") is not None:
                d = math.hypot(alvo[0] - p["grid_e"], alvo[1] - p["grid_n"])
            else:
                k = math.cos(math.radians(lat))
                dx = (lon - p["lon"]) * 111320.0 * k
                dy = (lat - p["lat"]) * 110540.0
                d = math.hypot(dx, dy)
            if d < melhor:
                melhor = d
        return melhor

    def afetados(self, desmonte, equipamentos=(), locais=()):
        """Quem esta' dentro do raio. E' esta a lista que o operador quer:
           o que precisa sair antes do fogo."""
        raio = desmonte.get("raio_m", RAIO_PADRAO_M)
        dentro, perto = [], []
        for e in equipamentos:
            if not e.get("tem_fix"):
                continue
            d = self.distancia_m(desmonte, e["lat"], e["lon"])
            reg = {"nome": e.get("nome"), "tipo": e.get("tipo"),
                   "movel": e.get("movel", True), "distancia_m": round(d, 1),
                   "lat": e.get("lat"), "lon": e.get("lon"),
                   "idade_s": e.get("idade_s")}
            if d <= raio:
                dentro.append(reg)
            elif d <= raio + MARGEM_APROXIMACAO_M:
                perto.append(reg)
        dentro.sort(key=lambda r: r["distancia_m"])
        perto.sort(key=lambda r: r["distancia_m"])

        locais_dentro = []
        for o in locais:
            d = self.distancia_m(desmonte, o["lat"], o["lon"])
            if d <= raio:
                locais_dentro.append({"nome": o.get("nome"), "tipo": o.get("tipo"),
                                      "distancia_m": round(d, 1)})
        locais_dentro.sort(key=lambda r: r["distancia_m"])

        return {
            "dentro": dentro,
            "perto": perto,
            "locais_dentro": locais_dentro,
            "n_dentro": len(dentro),
            "n_erms_dentro": sum(1 for r in dentro if not r["movel"]),
            "n_moveis_dentro": sum(1 for r in dentro if r["movel"]),
        }

    def com_afetados(self, equipamentos=(), locais=(), apenas_ativos=True):
        saida = []
        for d in self.listar(apenas_ativos):
            item = dict(d)
            item["afetados"] = self.afetados(d, equipamentos, locais)
            saida.append(item)
        return saida
