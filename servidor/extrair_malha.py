#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
 extrair_malha.py — tira do Modular.db os arquivos prontos para uso offline.

 Gera, num diretorio a escolha:
   malha_viaria.geojson   trechos (LineString) com nome, comprimento e fechado
   locais.geojson         locais nomeados (Point) com tipo e elevacao
   areas.geojson          poligonos de risco
   grafo.json             nos, arestas e a projecao — o grafo puro

 Com --semear DIR, grava tambem os arquivos NO FORMATO DO CACHE DO PTX
 (malha.json, locais.json, areas.json, hash.txt), para o terminal ja' abrir
 com o mapa antes de falar com o servidor pela primeira vez.

 Somente leitura sobre o banco.

   python3 extrair_malha.py --db Modular.db --saida dados/
=============================================================================
"""
import argparse, hashlib, json, os, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

from servidor_rotas import Malha       # noqa: E402


def grava(caminho, obj):
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, caminho)            # troca atomica
    return os.path.getsize(caminho)


def extrair(caminho_db, saida):
    os.makedirs(saida, exist_ok=True)
    m = Malha(caminho_db)

    escritos = []
    escritos.append(("malha_viaria.geojson", grava(
        os.path.join(saida, "malha_viaria.geojson"),
        {"type": "FeatureCollection", "features": m.estradas})))

    locais = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [o["lon"], o["lat"]]},
         "properties": {k: v for k, v in o.items() if k not in ("lat", "lon")}}
        for o in m.locais]}
    escritos.append(("locais.geojson", grava(
        os.path.join(saida, "locais.geojson"), locais)))

    escritos.append(("areas.geojson", grava(
        os.path.join(saida, "areas.geojson"),
        {"type": "FeatureCollection", "features": m.areas})))

    p = m.proj
    grafo = {
        "projecao": {"origem": "GeographicRegion.TransformationData",
                     "base_e": p.baseE, "base_n": p.baseN,
                     "rotacao_graus": round(__import__("math").degrees(p.rot), 8)},
        "nos": {nome: {"grid_e": d["grid_e"], "grid_n": d["grid_n"],
                       "lat": d["lat"], "lon": d["lon"], "grau": d["grau"]}
                for nome, d in m.nos.items()},
        "arestas": [{"de": a["de"], "para": a["para"], "id": a["id"],
                     "comprimento_m": a["comprimento_m"], "fechada": a["fechada"],
                     "extremo_de": list(a["grid"][0]),
                     "extremo_para": list(a["grid"][-1])} for a in m.arestas],
    }
    escritos.append(("grafo.json", grava(os.path.join(saida, "grafo.json"), grafo)))

    return m, escritos


def hash_do_banco(caminho):
    """Mesmo hash que o servidor publica no ETag: e' o que faz o PTX
       reconhecer o cache semeado como ja' atualizado e nao rebaixar nada."""
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:16]


def semear_cache(caminho_db, destino):
    """Grava o cache do PTX pronto: malha, locais, areas e o hash."""
    os.makedirs(destino, exist_ok=True)
    m = Malha(caminho_db)
    escritos = []
    escritos.append(("malha.json", grava(os.path.join(destino, "malha.json"),
        {"type": "FeatureCollection", "features": m.estradas})))
    escritos.append(("locais.json", grava(os.path.join(destino, "locais.json"),
        {"total": len(m.locais), "locais": m.locais})))
    escritos.append(("areas.json", grava(os.path.join(destino, "areas.json"),
        {"type": "FeatureCollection", "features": m.areas})))
    h = hash_do_banco(caminho_db)
    caminho_hash = os.path.join(destino, "hash.txt")
    with open(caminho_hash + ".tmp", "w", encoding="utf-8") as f:
        f.write(h)
    os.replace(caminho_hash + ".tmp", caminho_hash)
    escritos.append(("hash.txt", os.path.getsize(caminho_hash)))
    return h, escritos


def main():
    ap = argparse.ArgumentParser(description="Extrai a malha do Modular.db")
    ap.add_argument("--db", default="Modular.db")
    ap.add_argument("--saida", default="dados")
    ap.add_argument("--semear", metavar="DIR",
                    help="grava tambem o cache pronto do PTX neste diretorio")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        sys.exit(f"ERRO: banco nao encontrado: {a.db}")

    m, escritos = extrair(a.db, a.saida)
    print(f"extraido de {os.path.abspath(a.db)}")
    print(f"  {len(m.arestas)} trechos, {len(m.nos)} nos, "
          f"{len(m.locais)} locais, {len(m.areas)} areas")
    fechados = sum(1 for x in m.arestas if x["fechada"])
    if fechados:
        print(f"  {fechados} trechos fechados (fora do roteamento, mas desenhados)")
    print(f"em {os.path.abspath(a.saida)}:")
    for nome, tam in escritos:
        print(f"  {nome:24s} {tam/1024:8.1f} KB")

    if a.semear:
        h, semeados = semear_cache(a.db, a.semear)
        print(f"\ncache do PTX (hash {h}) em {os.path.abspath(a.semear)}:")
        for nome, tam in semeados:
            print(f"  {nome:24s} {tam/1024:8.1f} KB")
        print("\nLeve esse diretorio para o terminal e instale com:")
        print("  sh instalar_ptx.sh --semear <esse-diretorio>")


if __name__ == "__main__":
    main()
