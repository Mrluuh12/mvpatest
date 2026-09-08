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

Falando direto com o rádio (não precisa de snmpwalk instalado):

    python3 perfil_do_walk.py --radio 10.188.96.40 --comunidade publica

Ou a partir de um arquivo, se você já tem a saída do snmpwalk:

    snmpwalk -v2c -c publica -On 10.188.96.40 1.3.6.1.4.1.3942 > astra.walk
    python3 perfil_do_walk.py astra.walk

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
#:
#: Sem o `-On` ele resolve nomes e imprime `SNMPv2-SMI::iso.0.8802.1... = ...`.
#: O prefixo até o `::` é descartado em vez de a linha ser ignorada: esquecer o
#: `-On` é o erro mais comum, e recusar o arquivo inteiro por isso só faz a
#: pessoa achar que o equipamento não respondeu.
LINHA = re.compile(
    r"^\.?(?:[A-Za-z][\w-]*::)?(?:iso\.)?(?P<oid>[\d.]+)"
    r"\s*=\s*(?P<tipo>[A-Za-z0-9-]+):?\s*(?P<valor>.*)$"
)


@dataclass
class Coluna:
    numero: int
    tipos: set[str] = field(default_factory=set)
    amostras: list[str] = field(default_factory=list)
    indices: set[str] = field(default_factory=set)

    def resumo(self) -> str:
        amostra = ", ".join(self.amostras[:3])
        return f"{'/'.join(sorted(self.tipos)):14} {amostra[:60]}"


RAIZ_PADRAO = "1.3.6.1.4.1.3942"


async def _percorrer(ip: str, comunidade: str, raiz: str, porta: int) -> list[str]:
    """Percorre a árvore do equipamento e devolve linhas no formato do snmpwalk.

    Existe para tirar a dependência do ``snmpwalk``, que não vem no Windows —
    e a máquina de quem opera a mina é Windows. Uma ferramenta que exige
    instalar Net-SNMP antes de começar é uma ferramenta que não se usa.
    """
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        bulk_walk_cmd,
    )

    motor = SnmpEngine()
    destino = await UdpTransportTarget.create((ip, porta), timeout=3, retries=1)
    saida: list[str] = []
    async for erro, estado, indice, binds in bulk_walk_cmd(
        motor, CommunityData(comunidade, mpModel=1), destino, ContextData(),
        0, 25, ObjectType(ObjectIdentity(raiz)), lexicographicMode=False,
    ):
        if erro:
            raise RuntimeError(str(erro))
        if estado:
            raise RuntimeError(f"{estado.prettyPrint()} no índice {indice}")
        for oid, valor in binds:
            # `prettyPrint()` resolve nomes de MIB e devolve
            # `SNMPv2-SMI::iso.0.8802...`. O que serve aqui é o OID numérico.
            saida.append(f".{numerico(oid)} = {_como_snmpwalk(valor)}")
    return saida


def numerico(oid) -> str:
    """O OID em pontos, sem nome de MIB pelo meio."""
    for acessor in ("get_oid", "getOid"):
        if callable(metodo := getattr(oid, acessor, None)):
            return str(metodo())
    return str(oid)


def _como_snmpwalk(valor) -> str:
    """Formata como o ``snmpwalk -On`` faria, para o resto do código não saber
    de onde a linha veio."""
    octetos = getattr(valor, "asOctets", None)
    if callable(octetos):
        bytes_ = octetos()
        if all(32 <= b < 127 for b in bytes_):
            return f'STRING: "{bytes_.decode()}"'
        return "Hex-STRING: " + " ".join(f"{b:02X}" for b in bytes_)
    return f"{type(valor).__name__}: {valor.prettyPrint()}"


def _linhas(caminho: str):
    if caminho == "-":
        yield from sys.stdin
        return
    with open(caminho, encoding="utf-8", errors="replace") as f:
        yield from f


def ler(caminho: str) -> list[tuple[str, str, str]]:
    return interpretar(_linhas(caminho))


def interpretar(linhas) -> list[tuple[str, str, str]]:
    return [
        (casa["oid"], casa["tipo"], casa["valor"].strip())
        for linha in linhas
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


#: O snmpwalk escreve `INTEGER`, o pysnmp escreve `Integer`. Os dois caminhos
#: precisam classificar a coluna igual, ou o mesmo equipamento gera perfis
#: diferentes conforme quem leu.
TIPOS_NUMERICOS = frozenset({
    "INTEGER", "Integer", "Integer32", "Gauge32", "Gauge", "Unsigned32",
    "Counter32", "Counter64", "Counter", "TimeTicks", "Timeticks",
})


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
        elif col.tipos & TIPOS_NUMERICOS:
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
    p.add_argument("walk", nargs="?",
                   help="arquivo com a saída do snmpwalk -On, ou - para a entrada padrão")
    p.add_argument("--radio", metavar="IP",
                   help="fala direto com o equipamento, sem precisar de snmpwalk")
    p.add_argument("--comunidade", default="public",
                   help="comunidade de leitura (padrão: public)")
    p.add_argument("--raiz", default=RAIZ_PADRAO,
                   help=f"ramo a percorrer (padrão: {RAIZ_PADRAO}, a árvore da InfiNet)")
    p.add_argument("--porta", type=int, default=161)
    p.add_argument("--salvar", metavar="ARQUIVO",
                   help="grava o walk lido do rádio, para reusar depois")
    p.add_argument("--minimo", type=int, default=2,
                   help="quantas colunas e linhas para considerar tabela (padrão: 2)")
    p.add_argument("--esqueleto", metavar="OID",
                   help="imprime o rascunho de TabelaEnlace para esta entrada")
    args = p.parse_args(argv)

    if not args.radio and not args.walk:
        p.error("informe um arquivo de walk, ou --radio IP para ler do equipamento")

    if args.radio:
        try:
            import asyncio
        except ImportError:  # pragma: no cover
            return 1
        try:
            linhas = asyncio.run(
                _percorrer(args.radio, args.comunidade, args.raiz, args.porta)
            )
        except ImportError:
            print(
                "para ler direto do rádio é preciso o pysnmp:\n"
                "    pip install pysnmp\n"
                "Ou gere o arquivo com snmpwalk e passe o caminho dele.",
                file=sys.stderr,
            )
            return 1
        except Exception as erro:  # noqa: BLE001
            print(f"não deu para ler {args.radio}: {erro}", file=sys.stderr)
            return 1
        if args.salvar:
            with open(args.salvar, "w", encoding="utf-8") as f:
                f.write("\n".join(linhas) + "\n")
            print(f"walk salvo em {args.salvar}")
        entradas = interpretar(linhas)
    else:
        try:
            entradas = ler(args.walk)
        except FileNotFoundError:
            print(
                f"arquivo {args.walk!r} não existe nesta pasta.\n"
                "Ou gere ele antes com snmpwalk, ou leia direto do equipamento:\n"
                f"    python3 {sys.argv[0]} --radio IP_DO_RADIO --comunidade SUA_COMUNIDADE",
                file=sys.stderr,
            )
            return 1

    if not entradas:
        print(
            "nada reconhecido. Se veio de arquivo, o walk foi feito com -On? "
            "Se veio do rádio, este ramo pode estar vazio — tente --raiz 1.3.6.1",
            file=sys.stderr,
        )
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
