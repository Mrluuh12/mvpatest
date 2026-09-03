"""CLI da semeadura: planilha entra, inventário derivado e relatório saem."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .planilha import ler
from .semeadura import Semeadura, semear


def _serializar(s: Semeadura) -> dict:
    return {
        "ativos": [a.model_dump(mode="json") for a in s.ativos.values()],
        "dispositivos": [d.model_dump(mode="json") for d in s.dispositivos.values()],
        "arestas": [e.model_dump(mode="json") for e in s.arestas],
    }


def _imprimir_relatorio(s: Semeadura, detalhar: int) -> None:
    r = s.relatorio
    print("\n=== SEMEADURA ===", file=sys.stderr)
    for chave, valor in r.resumo().items():
        print(f"  {chave:<32} {valor}", file=sys.stderr)

    listas = (
        ("fora do padrão de nome", r.fora_do_padrao),
        ("papel não reconhecido", r.papel_desconhecido),
        ("divergência nome x endereço", r.divergencias),
        ("chaves em conflito", r.chaves_em_conflito),
        ("sem identificador forte (só nome)", r.sem_identificador_forte),
    )
    for titulo, itens in listas:
        if not itens or not detalhar:
            continue
        print(f"\n--- {titulo} ({len(itens)}) ---", file=sys.stderr)
        for item in itens[:detalhar]:
            print(f"  {item}", file=sys.stderr)
        if len(itens) > detalhar:
            print(f"  … mais {len(itens) - detalhar}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Semeia o inventário a partir da planilha.")
    p.add_argument("planilha", type=Path)
    p.add_argument("--aba", default=None)
    p.add_argument("--saida", type=Path, help="JSON de saída; omitido escreve em stdout")
    p.add_argument("--detalhar", type=int, default=10, help="itens por lista no relatório")
    args = p.parse_args(argv)

    resultado = semear(ler(args.planilha, args.aba))
    conteudo = json.dumps(_serializar(resultado), ensure_ascii=False, indent=1)

    if args.saida:
        args.saida.write_text(conteudo, encoding="utf-8")
    else:
        print(conteudo)

    _imprimir_relatorio(resultado, args.detalhar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
