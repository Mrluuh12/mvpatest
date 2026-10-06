#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monta um Modular.db sintetico a partir do dados/grafo.json ja' extraido.

Serve so' para teste: reproduz o esquema, o formato do BLOB de geometria e a
projecao do banco real, sem precisar do Modular.db (que nao vai para o repo).

    python3 testes/gerar_banco_sintetico.py [saida.db]
"""
import json, os, sqlite3, struct, sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAFO = os.path.join(RAIZ, "dados", "grafo.json")

# mesma projecao do banco analisado (secao 3.4 da especificacao)
XML_PROJECAO = """<?xml version="1.0" encoding="utf-16"?>
<conversion type="LEGACY">
  <Type>utmWGS84 6378137</Type>
  <Latitude>-18.93882038</Latitude>
  <Longitude>-43.41417951</Longitude>
  <Scale>0.99994276</Scale>
  <Eccentricity>0.081819190842622</Eccentricity>
  <East>667029.824</East>
  <North>7905236.66</North>
  <Rotation>-0.51479842</Rotation>
</conversion>"""

TIPOS = [(1, "Crusher"), (2, "Dump"), (3, "Bench"), (4, "Pit"),
         (5, "Maintenance Shop"), (6, "Fuel Bay"), (7, "Call Point"),
         (8, "Stockpile"), (9, "Park Up")]


def blob_ponto(x, y, z=0.0):
    return b"\x01" + struct.pack("<I", 1001) + struct.pack("<ddd", x, y, z)


def blob_polilinha(pts):
    b = b"\x01" + struct.pack("<I", 1002) + struct.pack("<I", len(pts))
    for p in pts:
        b += struct.pack("<ddd", p[0], p[1], p[2] if len(p) > 2 else 0.0)
    return b


def blob_poligono(pts):
    b = (b"\x01" + struct.pack("<I", 1003) + struct.pack("<I", 1)
         + struct.pack("<I", len(pts)))
    for p in pts:
        b += struct.pack("<ddd", p[0], p[1], p[2] if len(p) > 2 else 0.0)
    return b


def gerar(saida):
    grafo = json.load(open(GRAFO, encoding="utf-8"))
    nos, arestas = grafo["nos"], grafo["arestas"]

    if os.path.exists(saida):
        os.remove(saida)
    c = sqlite3.connect(saida)
    c.executescript("""
        CREATE TABLE GeographicRegion (Id INTEGER PRIMARY KEY,
            TransformationData TEXT, BackgroundImage BLOB);
        CREATE TABLE LocationType (Id INTEGER PRIMARY KEY, Name TEXT);
        CREATE TABLE TopologicalObject (Id INTEGER PRIMARY KEY, Name TEXT,
            Geometry BLOB, GeometryType INTEGER, IsActive INTEGER, IsHazard INTEGER);
        CREATE TABLE Road (Id INTEGER PRIMARY KEY, MaxSpeedLimit REAL,
            MinSpeedLimit REAL, IsClosed INTEGER, LocationTypeID INTEGER);
        CREATE TABLE FunctionalLocation (Id INTEGER PRIMARY KEY,
            LocationTypeID INTEGER, Elevation REAL);
    """)
    c.execute("INSERT INTO GeographicRegion VALUES (1,?,NULL)", (XML_PROJECAO,))
    c.executemany("INSERT INTO LocationType VALUES (?,?)", TIPOS)

    # --- estradas -------------------------------------------------------
    for a in arestas:
        de, para = a["de"], a["para"]
        p1 = a["extremo_de"] + [nos.get(de, {}).get("alt", 0.0)]
        p2 = a["extremo_para"] + [nos.get(para, {}).get("alt", 0.0)]
        # vertice intermediario: o banco real tem polilinhas de varios pontos,
        # e o teste de ancoragem precisa de trecho com mais de um segmento
        meio = [(p1[0]+p2[0])/2, (p1[1]+p2[1])/2, (p1[2]+p2[2])/2]
        c.execute("INSERT INTO TopologicalObject VALUES (?,?,?,?,?,?)",
                  (a["id"], f"{de} - {para}", blob_polilinha([p1, meio, p2]),
                   5, 1, 0))
        c.execute("INSERT INTO Road VALUES (?,?,?,?,?)",
                  (a["id"], 0.0, 0.0, 1 if a["fechada"] else 0, None))

    # --- locais: um por no', mais alguns destinos com nome de operacao ----
    oid = 900000000
    nomes = sorted(nos)
    for i, nome in enumerate(nomes):
        d = nos[nome]
        oid += 1
        c.execute("INSERT INTO TopologicalObject VALUES (?,?,?,?,?,?)",
                  (oid, nome, blob_ponto(d["grid_e"], d["grid_n"], d.get("alt", 0.0)),
                   1, 1, 0))
        c.execute("INSERT INTO FunctionalLocation VALUES (?,?,?)",
                  (oid, TIPOS[i % len(TIPOS)][0], d.get("alt", 0.0)))

    # --- uma area de risco, para /api/areas nao vir vazio ----------------
    d = nos[nomes[0]]
    e, n = d["grid_e"], d["grid_n"]
    anel = [(e-50, n-50, 0), (e+50, n-50, 0), (e+50, n+50, 0), (e-50, n+50, 0)]
    c.execute("INSERT INTO TopologicalObject VALUES (?,?,?,?,?,?)",
              (999000001, "AREA-TESTE", blob_poligono(anel), 4, 1, 1))

    c.commit(); c.close()
    return saida


if __name__ == "__main__":
    destino = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RAIZ, "Modular_sintetico.db")
    print("banco sintetico gerado em", gerar(destino))
