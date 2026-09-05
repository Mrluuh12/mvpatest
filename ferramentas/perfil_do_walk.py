#!/usr/bin/env python3
"""Transforma um ``snmpwalk`` num rascunho de perfil declarativo.

Por que existe
--------------

O perfil SNMP promete que *"acrescentar um tipo de equipamento é configuração,
não código"*. A promessa só se cumpre se descobrir os OIDs de um equipamento
for barato — e num rádio de fabricante, a MIB nem sempre está à mão. O que
está sempre à mão é o próprio rádio.

Esta ferramenta lê a saída de um ``snmpwalk``, encontra as tabelas, mostra o
que cada coluna parece conter, e escreve o esqueleto do perfil. Quem conhece o
equipamento preenche o nome canônico de cada coluna; a ferramenta não adivinha
isso, porque adivinhar aqui é como um número vira métrica errada.

Como usar
---------

No rádio (somente leitura, comunidade de leitura):

    snmpwalk -v2c -c publica -On 10.188.96.40 1.3.6.1.4.1.3942 > astra.walk

Aqui:

    python3 ferramentas/perfil_do_walk.py astra.walk --minimo 2

Ele imprime as tabelas achadas, uma amostra por coluna, e um bloco de perfil
para colar em ``perfis_snmp.py``. As métricas saem comentadas de propósito: o
dicionário canônico recusa nome inventado, e é assim que se descobre que falta
decidir o nome antes de coletar.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field

#: `snmpwalk -On` imprime `.1.3.6.1.2.1.1.3.0 = Timeticks: (1234) 0:00:12.34`.
LINHA = re.compile(r"^\.?(?P<oid>[\d.]+)\s*=\s*(?P<tipo>[A-Za-z0-9-]+):?\s*(?P<valor>.*)$")


@dataclass
class Coluna:
    numero: int
    tipos: set[str] = field(default_factory=set)
    amostras: list[str] = field(default_factory=list)
    indices: set[str] = field(default_factory=set)

    def resumo(self) -> str:
        amostra = ", ".join(self.amostras[:3])
        return f"{'/'.join(sorted(self.tipos)):14} {amostra[:60]}"


def _linhas(caminho: str):
    if caminho == "-":
        yield from sys.stdin
        return
    with open(caminho, encoding="utf-8", errors="replace") as f:
        yield from f


def ler(caminho: str) -> list[tuple[str, str, str]]:
    return [
        (casa["oid"], casa["tipo"], casa["valor"].strip())
        for linha in _linhas(caminho)
        if (casa := LINHA.match(linha.strip()))
    ]


def agrupar(entradas: list[tuple[str, str, str]], minimo: int) -> dict[str, dict[int, Coluna]]:
    """Acha tabelas pela estrutura do SNMP, não por estatística.

    Num OID de tabela vale ``<entry>.<coluna>.<índice>``, e o índice pode ter
    mais de um componente: a ``lldpRemTable`` é indexada por três. A primeira
    versão disto parava no primeiro corte que desse um número e por isso só
    achava tabelas de índice simples — a de vizinhança, que é a que interessa
    num rádio, passava batido.

    O critério que funciona é a assinatura de uma tabela de verdade: **todas as
    colunas compartilham o mesmo conjunto de índices**. Um agrupamento errado
    quebra essa igualdade, porque cada "coluna" fica com índices próprios.
    """
    candidatas: dict[str, dict[int, Coluna]] = defaultdict(dict)
    for oid, tipo, valor in entradas:
        partes = oid.split(".")
        if len(partes) < 3 or partes[-1] == "0":
            continue
        # Todos os cortes plausíveis, sem parar no primeiro: índices de uma a
        # quatro partes cobrem o que se vê em MIB de equipamento.
        for corte in range(max(len(partes) - 4, 1), len(partes)):
            entrada = ".".join(partes[:corte])
            try:
                numero = int(partes[corte])
            except (ValueError, IndexError):
                continue
            indice = ".".join(partes[corte + 1 :])
            if not indice:
                continue
            col = candidatas[entrada].setdefault(numero, Coluna(numero=numero))
            col.tipos.add(tipo)
            col.indices.add(indice)
            if len(col.amostras) < 5:
                col.amostras.append(valor)

    achadas: dict[str, dict[int, Coluna]] = {}
    for entrada, cols in candidatas.items():
        if len(cols) < minimo:
            continue
        conjuntos = [c.indices for c in cols.values()]
        if len({frozenset(s) for s in conjuntos}) != 1:
            continue  # colunas com índices próprios: não é uma tabela
        if len(conjuntos[0]) < minimo:
            continue
        achadas[entrada] = cols

    # Sobrando agrupamentos aninhados, fica o de prefixo mais longo: é o mais
    # específico, e é o que corresponde ao `entry` da MIB.
    return {
        e: c for e, c in achadas.items()
        if not any(outra != e and outra.startswith(e + ".") for outra in achadas)
    }


def parece_identidade(col: Coluna) -> bool:
    """Coluna que parece identificar o vizinho: MAC ou nome de sistema."""
    if col.tipos & {"Hex-STRING"}:
        return True
    return any(re.fullmatch(r"[0-9A-Fa-f: ]{11,23}", a or "") for a in col.amostras)


def relatar(tabelas: dict[str, dict[int, Coluna]]) -> None:
    for entrada, cols in sorted(tabelas.items(), key=lambda x: -len(x[1])):
        linhas = max(len(c.indices) for c in cols.values())
        print(f"\n=== {entrada}   {len(cols)} colunas, {linhas} linhas")
        for numero in sorted(cols):
            marca = "  <- parece identidade" if parece_identidade(cols[numero]) else ""
            print(f"   .{numero:<3} {cols[numero].resumo()}{marca}")


def esqueleto(entrada: str, cols: dict[int, Coluna]) -> str:
    partes = ["TabelaEnlace(", f'    oid="{entrada}",', "    tipo=TipoAresta.PEER_PTP,",
              "    colunas=("]
    for numero in sorted(cols):
        col = cols[numero]
        if parece_identidade(col):
            partes.append(f'        ColunaEnlace(numero={numero}, papel="identidade"),')
        elif col.tipos & {"INTEGER", "Gauge32", "Counter32", "Counter64"}:
            partes.append(
                f"        # ColunaEnlace(numero={numero}, medida=\"?\"),"
                f"  # {col.resumo()}"
            )
        else:
            partes.append(
                f'        # ColunaEnlace(numero={numero}, papel="nome"),'
                f"  # {col.resumo()}"
            )
    partes += ["    ),", ")"]
    return "\n".join(partes)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("walk", help="arquivo com a saída do snmpwalk -On, ou - para a entrada padrão")
    p.add_argument("--minimo", type=int, default=2,
                   help="quantas colunas e linhas para considerar tabela (padrão: 2)")
    p.add_argument("--esqueleto", metavar="OID",
                   help="imprime o rascunho de TabelaEnlace para esta entrada")
    args = p.parse_args(argv)

    entradas = ler(args.walk)
    if not entradas:
        print("nada reconhecido: o walk foi feito com -On?", file=sys.stderr)
        return 1
    print(f"{len(entradas)} objetos lidos")
    tabelas = agrupar(entradas, args.minimo)
    if not tabelas:
        print("nenhuma tabela encontrada — só escalares neste ramo")
        return 0
    relatar(tabelas)
    if args.esqueleto:
        if args.esqueleto not in tabelas:
            print(f"\n{args.esqueleto!r} não está entre as tabelas achadas", file=sys.stderr)
            return 1
        print("\n--- para colar em perfis_snmp.py ---")
        print(esqueleto(args.esqueleto, tabelas[args.esqueleto]))
        print(
            "\n# As medidas saem comentadas de propósito: o nome canônico é\n"
            "# decisão, não dedução. O dicionário recusa nome inventado."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
