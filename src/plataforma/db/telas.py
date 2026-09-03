"""Arranjos de tela no banco.

Guardados no banco para serem editáveis pela interface, e exportáveis em YAML
para serem versionáveis em Git. As duas coisas, não uma — e como a área ADM já
existe, cada alteração fica auditada com nome e hora.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.arranjos import PADROES, Arranjo, Contexto, cascata

from .esquema import arranjo


async def guardar(
    conexao: AsyncConnection, a: Arranjo, por: str | None = None
) -> None:
    a.validar_contexto()
    await conexao.execute(
        pg_insert(arranjo)
        .values(
            escopo=a.escopo,
            contexto=a.contexto.value,
            cartoes=[c.model_dump(mode="json") for c in a.cartoes],
            atualizado_em=datetime.now(UTC),
            atualizado_por=por,
        )
        .on_conflict_do_update(
            index_elements=["escopo"],
            set_={
                "contexto": a.contexto.value,
                "cartoes": [c.model_dump(mode="json") for c in a.cartoes],
                "atualizado_em": datetime.now(UTC),
                "atualizado_por": por,
            },
        )
    )


async def remover(conexao: AsyncConnection, escopo: str) -> bool:
    """Apagar um arranjo faz a cascata voltar a valer — é como se desfaz."""
    from sqlalchemy import delete

    r = await conexao.execute(delete(arranjo).where(arranjo.c.escopo == escopo))
    return bool(r.rowcount)


async def ler(conexao: AsyncConnection, escopo: str) -> Arranjo | None:
    linha = (
        await conexao.execute(select(arranjo).where(arranjo.c.escopo == escopo))
    ).first()
    if linha is None:
        return PADROES.get(escopo)
    return Arranjo(
        escopo=linha.escopo, contexto=Contexto(linha.contexto), cartoes=linha.cartoes
    )


async def resolver(
    conexao: AsyncConnection, contexto: Contexto, chave: str, grupo: str | None
) -> tuple[Arranjo, str]:
    """Percorre a cascata e devolve ``(arranjo, escopo_que_valeu)``.

    Devolver de onde veio importa: sem isso, quem edita não sabe se está
    mexendo no arranjo daquela máquina ou no de toda a frota.
    """
    for escopo in cascata(contexto, chave, grupo):
        if (a := await ler(conexao, escopo)) is not None:
            return a, escopo
    return PADROES[
        "padrao_ativo" if contexto is Contexto.ATIVO else "padrao_dispositivo"
    ], "embutido"


async def listar(conexao: AsyncConnection) -> list[dict]:
    return [
        {
            "escopo": ln.escopo,
            "contexto": ln.contexto,
            "cartoes": len(ln.cartoes),
            "atualizado_em": ln.atualizado_em,
            "atualizado_por": ln.atualizado_por,
        }
        for ln in (await conexao.execute(select(arranjo))).all()
    ]
