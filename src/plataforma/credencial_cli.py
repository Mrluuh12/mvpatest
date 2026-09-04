"""Cadastra uma credencial no cofre, pela linha de comando.

A senha **não** entra como argumento: `ps` mostra a linha de comando de
qualquer processo para qualquer usuário da máquina, e o histórico do shell
guarda o resto. Ela é pedida no terminal, sem eco, ou lida de variável de
ambiente para uso em automação.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from inventario.modelo import Zona

from .db.credenciais import VAR_CHAVE, gerar_chave, guardar, listar, remover
from .db.repositorio_pg import criar_engine

VAR_BANCO = "PLATAFORMA_BANCO"
VAR_SEGREDO = "PLATAFORMA_SEGREDO"


def _url(informada: str | None) -> str:
    url = informada or os.environ.get(VAR_BANCO)
    if not url:
        raise SystemExit(f"defina {VAR_BANCO} ou passe --banco")
    return url


def _pedir(rotulo: str, obrigatorio: bool = True) -> str:
    if valor := os.environ.get(VAR_SEGREDO):
        # Um segredo por execução, para automação. Mais de um pede o terminal.
        del os.environ[VAR_SEGREDO]
        return valor
    valor = getpass.getpass(f"{rotulo}: ")
    if obrigatorio and not valor:
        raise SystemExit(f"{rotulo} não pode ser vazio")
    return valor


async def _gravar(args) -> int:
    motor = criar_engine(_url(args.banco))
    try:
        if args.tipo == "snmp_v2c":
            segredo = {"tipo": "snmp_v2c", "comunidade": _pedir("comunidade")}
            atributos = {"porta": args.porta}
        else:
            segredo = {
                "tipo": "snmp_v3",
                "usuario": args.usuario,
                "senha_auth": _pedir("senha de autenticação"),
                "senha_priv": _pedir("senha de privacidade", obrigatorio=False),
            }
            atributos = {"porta": args.porta, "usuario": args.usuario}
        async with motor.begin() as conexao:
            await guardar(
                conexao, args.nome, args.tipo, Zona(args.zona),
                segredo, atributos, por=args.por,
            )
    finally:
        await motor.dispose()
    print(f"credencial {args.nome!r} guardada para a zona {args.zona}")
    return 0


async def _listar(args) -> int:
    motor = criar_engine(_url(args.banco))
    try:
        async with motor.connect() as conexao:
            itens = await listar(conexao)
    finally:
        await motor.dispose()
    if not itens:
        print("nenhuma credencial cadastrada")
        return 0
    print(f"{'nome':22} {'tipo':10} {'zona':14} atributos")
    for i in itens:
        print(f"{i['nome']:22} {i['tipo']:10} {i['zona']:14} {i['atributos']}")
    return 0


async def _remover(args) -> int:
    motor = criar_engine(_url(args.banco))
    try:
        async with motor.begin() as conexao:
            existia = await remover(conexao, args.nome)
    finally:
        await motor.dispose()
    print("removida" if existia else "não existia")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Cofre de credenciais da plataforma.")
    sub = p.add_subparsers(dest="acao", required=True)

    g = sub.add_parser("guardar", help="cadastra ou substitui")
    g.add_argument("nome")
    g.add_argument("--tipo", default="snmp_v2c", choices=["snmp_v2c", "snmp_v3"])
    g.add_argument("--zona", default=Zona.CORPORATIVA.value,
                   choices=[z.value for z in Zona])
    g.add_argument("--usuario", default="", help="usuário do SNMPv3")
    g.add_argument("--porta", type=int, default=161)
    g.add_argument("--por", default=os.environ.get("USER", ""))
    g.add_argument("--banco", default=None)
    g.set_defaults(func=_gravar)

    li = sub.add_parser("listar", help="sem segredo — só nome, tipo e zona")
    li.add_argument("--banco", default=None)
    li.set_defaults(func=_listar)

    r = sub.add_parser("remover")
    r.add_argument("nome")
    r.add_argument("--banco", default=None)
    r.set_defaults(func=_remover)

    ch = sub.add_parser("chave", help="gera uma chave nova para PLATAFORMA_CHAVE")
    ch.set_defaults(func=None)

    args = p.parse_args(argv)
    if args.acao == "chave":
        print(gerar_chave())
        return 0
    if not os.environ.get(VAR_CHAVE):
        print(
            f"defina {VAR_CHAVE} antes (gere uma com: criar-credencial chave)",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
