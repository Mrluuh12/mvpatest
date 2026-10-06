#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monta o dados/grafo.json que os testes usam como fixture.

ESTA MALHA E' INVENTADA. Ela nao e' a topologia da mina: e' uma rede com a
mesma FORMA (uma via tronco com ramais para bancadas, pilhas e oficina),
dentro da mesma area e com a mesma projecao, e com as contagens que a suite
cobra. Serve para rodar os testes sem o Modular.db, que nao vai para o
repositorio.

Com o banco real em maos, o arquivo de verdade sai de:

    python3 servidor/extrair_malha.py --db Modular.db --saida dados

e substitui este sem que nada mais mude.

O contrato que a suite exige:
  325 trechos, 318 nos        (teste_servidor, teste_cliente_ptx, teste_frota)
  ao menos um trecho fechado  (fica fora do roteamento, mas continua no mapa)
  duas componentes desconexas (rota entre elas tem que falhar explicando)
  um trecho longo e aberto    (os testes de ancoragem projetam sobre ele)

    python3 testes/gerar_grafo_sintetico.py [saida.json]
"""
import json, math, os, random, sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# a area da mina de verdade, para a foto aerea e o datum continuarem fazendo
# sentido nos testes que cruzam as duas coisas
E0, E1 = 664950.0, 667557.0
N0, N1 = 7904748.0, 7910979.0
ALT_BAIXA, ALT_ALTA = 729.0, 836.0

# Trechos longos de proposito: a via tronco de uma mina corre centenas de
# metros entre entroncamentos, e os testes de ancoragem projetam um ponto no
# meio de um trecho e cobram que ele caia longe das pontas. Com o tronco
# picado em pedacos de 150 m nao ha onde essa margem caber.
NOS_TRONCO = 15          # a via principal, de sul a norte
NOS_ILHA = 8             # patio sem ligacao viaria: as componentes desconexas
TOTAL_NOS = 318
TOTAL_ARESTAS = 325
FECHADAS = 5
ATALHO_MAX_M = 400      # atalho e' ligacao entre pontas vizinhas


def _alt(n):
    """Mais alto ao norte, como uma cava que sobe para a pilha."""
    t = (n - N0) / (N1 - N0)
    return round(ALT_BAIXA + (ALT_ALTA - ALT_BAIXA) * t, 2)


def gerar(saida):
    r = random.Random(20260901)          # fixture tem que ser reprodutivel
    nos, arestas = {}, []

    def poe(nome, e, n):
        nos[nome] = {"grid_e": round(e, 2), "grid_n": round(n, 2), "alt": _alt(n)}

    def liga(de, para, fechada=False):
        arestas.append({
            "id": len(arestas) + 1, "de": de, "para": para,
            "extremo_de": [nos[de]["grid_e"], nos[de]["grid_n"]],
            "extremo_para": [nos[para]["grid_e"], nos[para]["grid_n"]],
            "fechada": fechada,
        })

    # --- via tronco: sobe serpenteando, como estrada de cava ------------
    meio = (E0 + E1) / 2
    tronco = []
    for i in range(NOS_TRONCO):
        t = i / (NOS_TRONCO - 1)
        n = N0 + (N1 - N0) * t
        e = meio + 420 * math.sin(t * 5.2) + r.uniform(-45, 45)
        nome = f"TR-{i+1:03d}"
        poe(nome, e, n)
        tronco.append(nome)
    for a, b in zip(tronco, tronco[1:]):
        liga(a, b)

    # --- ramais: cada um sai do tronco e termina num destino -----------
    destinos = ["BANCADA", "PILHA", "BASCULA", "PATIO", "ACESSO"]
    ramais = 0
    while len(nos) < TOTAL_NOS - NOS_ILHA:
        base = tronco[r.randrange(1, len(tronco) - 1)]
        comp = min(r.randint(2, 6), TOTAL_NOS - NOS_ILHA - len(nos))
        if comp <= 0:
            break
        ramais += 1
        lado = 1 if ramais % 2 else -1
        ant = base
        be, bn = nos[base]["grid_e"], nos[base]["grid_n"]
        rumo = r.uniform(-0.6, 0.6)
        for k in range(comp):
            d = 180 * (k + 1)
            e = be + lado * d * math.cos(rumo)
            n = bn + d * math.sin(rumo) * 0.6
            nome = f"{destinos[ramais % len(destinos)]}-{ramais:02d}-{k+1}"
            poe(nome, max(E0, min(E1, e)), max(N0, min(N1, n)))
            liga(ant, nome)
            ant = nome

    # --- a ilha: um patio que a topologia nao liga por via --------------
    for k in range(NOS_ILHA):
        poe(f"ILHA-{k+1}", E0 + 120 + k * 60, N1 - 180 - (k % 3) * 70)
    for k in range(NOS_ILHA - 1):
        liga(f"ILHA-{k+1}", f"ILHA-{k+2}")

    # --- atalhos entre pontas de ramais: e' aqui que entram as fechadas -
    # fecham-se SO' atalhos, nunca a arvore: trecho fechado sai do
    # roteamento, e fechar a unica ligacao de um ramal deixaria no sem rota
    # os pares saem por PROXIMIDADE, nao por ordem de nome: atalho e' ligacao
    # entre duas pontas vizinhas. Escolhidos pelo nome, saiam trechos retos de
    # quatro quilometros cruzando a cava inteira, que nao existem em mina
    # nenhuma e ainda por cima viravam o trecho mais longo da malha.
    pontas = sorted(x for x in nos if x.startswith(tuple(destinos)))
    pares = []
    for i, a in enumerate(pontas):
        for b in pontas[i + 1:]:
            if a.split("-")[1] == b.split("-")[1]:
                continue        # mesmo ramal: ligar nao acrescenta caminho
            d = math.dist((nos[a]["grid_e"], nos[a]["grid_n"]),
                          (nos[b]["grid_e"], nos[b]["grid_n"]))
            if d <= ATALHO_MAX_M:
                pares.append((d, a, b))
    pares.sort()
    faltam = TOTAL_ARESTAS - len(arestas)
    usados = set()
    for d, a, b in pares:
        if faltam <= 0:
            break
        if (a, b) in usados or (b, a) in usados:
            continue
        liga(a, b, fechada=(faltam <= FECHADAS))
        usados.add((a, b))
        faltam -= 1
    if faltam:
        raise SystemExit(f"faltaram {faltam} arestas: aumente ATALHO_MAX_M")

    grafo = {
        "_leia": "MALHA SINTETICA, nao e' a topologia da mina. Ver "
                 "testes/gerar_grafo_sintetico.py. O arquivo de verdade sai "
                 "de servidor/extrair_malha.py com o Modular.db.",
        "nos": nos, "arestas": arestas,
    }
    os.makedirs(os.path.dirname(saida), exist_ok=True)
    with open(saida, "w", encoding="utf-8") as f:
        json.dump(grafo, f, ensure_ascii=False, indent=1)
    return grafo


if __name__ == "__main__":
    destino = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RAIZ, "dados", "grafo.json")
    g = gerar(destino)
    print(f"{len(g['nos'])} nos, {len(g['arestas'])} arestas "
          f"({sum(1 for a in g['arestas'] if a['fechada'])} fechadas) -> {destino}")
