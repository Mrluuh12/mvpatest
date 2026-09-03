"""Criação da primeira conta administradora.

Existe porque não há senha padrão embutida em lugar nenhum — e não deve haver.
Sistema que nasce com ``admin/admin`` nasce comprometido; a primeira conta é
criada por quem instala, com uma senha que só essa pessoa conhece.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from inventario.modelo import PapelUsuario, Zona

from .db import contas
from .db.repositorio_pg import criar_engine
from .seguranca import SenhaFraca

VAR_BANCO = "PLATAFORMA_BANCO"


async def _criar(url: str, login: str, nome: str, senha: str, zonas: list[Zona]) -> int:
    motor = criar_engine(url)
    try:
        async with motor.begin() as conexao:
            await contas.criar_usuario(
                conexao, login, nome, senha,
                [(PapelUsuario.ADMINISTRADOR, zonas)],
                por="instalacao",
            )
    except SenhaFraca as erro:
        print(f"✗ {erro}", file=sys.stderr)
        return 1
    finally:
        await motor.dispose()
    print(f"✓ administrador {login!r} criado para as zonas "
          f"{', '.join(z.value for z in zonas)}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Cria uma conta administradora.")
    p.add_argument("login")
    p.add_argument("--nome", default=None)
    p.add_argument("--banco", default=None)
    p.add_argument(
        "--zona", action="append", choices=[z.value for z in Zona],
        help="pode repetir; padrão é apenas a zona corporativa",
    )
    args = p.parse_args(argv)

    url = args.banco or os.environ.get(VAR_BANCO)
    if not url:
        raise SystemExit(f"defina {VAR_BANCO} ou passe --banco")

    senha = os.environ.get("PLATAFORMA_SENHA") or getpass.getpass("senha: ")
    zonas = [Zona(z) for z in (args.zona or [Zona.CORPORATIVA.value])]
    return asyncio.run(_criar(url, args.login, args.nome or args.login, senha, zonas))


if __name__ == "__main__":
    raise SystemExit(main())
