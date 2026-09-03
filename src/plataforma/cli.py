"""CLI da plataforma: leva a planilha até o banco.

Fecha o ciclo do marco M0 — o inventário deixa de ser um JSON que alguém
precisa guardar e passa a viver no Postgres, sobrevivendo a reinício e sem
apagar o que foi corrigido no ADM.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from inventario.planilha import ler
from inventario.semeadura import semear

from .db.repositorio_pg import (
    RepositorioPostgres,
    aplicar_semeadura,
    criar_engine,
    divergencias,
)

VAR_BANCO = "PLATAFORMA_BANCO"


def url_do_banco(informada: str | None) -> str:
    url = informada or os.environ.get(VAR_BANCO)
    if not url:
        raise SystemExit(
            f"defina {VAR_BANCO} ou passe --banco "
            "(ex.: postgresql+asyncpg://usuario@host:5432/plataforma)"
        )
    return url


async def _semear(planilha: Path, url: str, aba: str | None) -> int:
    registros = ler(planilha, aba)
    semeada = semear(registros)
    motor = criar_engine(url)
    try:
        async with motor.begin() as conexao:
            gravado = await aplicar_semeadura(conexao, semeada)
        async with motor.connect() as conexao:
            divergentes = await divergencias(conexao)
        repo = await RepositorioPostgres.carregar(motor)
    finally:
        await motor.dispose()

    r = semeada.relatorio
    print("\n=== SEMEADURA ===", file=sys.stderr)
    for chave, valor in r.resumo().items():
        print(f"  {chave:<32} {valor}", file=sys.stderr)

    print("\n=== GRAVADO NO BANCO ===", file=sys.stderr)
    for chave, valor in gravado.items():
        print(f"  {chave:<32} {valor}", file=sys.stderr)
    print(f"  {'arestas abertas no total':<32} {repo.resumo()['arestas_abertas']}", file=sys.stderr)
    print(f"  {'divergências cadastro x real':<32} {len(divergentes)}", file=sys.stderr)

    # Um registro que entra tem de sair criado ou explicado. Se essa conta não
    # fecha, o inventário encolheu e ninguém percebeu — a pior falha possível.
    explicados = (
        r.dispositivos_criados + len(r.chaves_em_conflito) + len(r.linhas_duplicadas)
    )
    if explicados != r.total_registros:
        print(
            f"\n✗ CONSERVAÇÃO QUEBRADA: {r.total_registros} registros entraram, "
            f"{explicados} foram explicados",
            file=sys.stderr,
        )
        return 1

    print(
        f"\n✓ conservação: {r.dispositivos_criados} criados + "
        f"{len(r.chaves_em_conflito)} em conflito + {len(r.linhas_duplicadas)} duplicados "
        f"= {r.total_registros} registros",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Semeia o inventário direto no Postgres.")
    p.add_argument("planilha", type=Path)
    p.add_argument("--banco", default=None, help=f"URL; padrão vem de {VAR_BANCO}")
    p.add_argument("--aba", default=None)
    args = p.parse_args(argv)
    return asyncio.run(_semear(args.planilha, url_do_banco(args.banco), args.aba))


if __name__ == "__main__":
    raise SystemExit(main())
