"""Histórico das sondas rodadas.

Guardar não é zelo excessivo: comparar o traceroute de agora com o da semana
passada é metade do diagnóstico numa rede que muda — e numa mina ela muda o
tempo todo, porque metade dos nós anda de caminhão.

E é registro de quem sondou o quê. Sonda é leitura, mas leitura dirigida a um
alvo específico por uma pessoa específica, e isso se audita.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.diagnostico import Resultado

from .esquema import diagnostico


async def registrar(
    conexao: AsyncConnection,
    sonda: str,
    alvo: str,
    resultado: Resultado,
    por: str,
    sujeito: str | None = None,
) -> None:
    await conexao.execute(
        insert(diagnostico).values(
            sonda=sonda,
            alvo=alvo,
            sujeito=sujeito,
            por=por,
            em=datetime.now(UTC),
            duracao_s=resultado.duracao_s,
            ok=resultado.ok,
            resumo=resultado.resumo,
            resultado={"linhas": list(resultado.linhas), "dados": resultado.dados},
        )
    )


async def historico(
    conexao: AsyncConnection,
    sujeito: str | None = None,
    sonda: str | None = None,
    limite: int = 20,
) -> list[dict]:
    consulta = (
        select(diagnostico).order_by(desc(diagnostico.c.em)).limit(min(limite, 200))
    )
    if sujeito:
        consulta = consulta.where(diagnostico.c.sujeito == sujeito)
    if sonda:
        consulta = consulta.where(diagnostico.c.sonda == sonda)
    return [
        {
            "id": ln.id,
            "sonda": ln.sonda,
            "alvo": ln.alvo,
            "sujeito": ln.sujeito,
            "por": ln.por,
            "em": ln.em,
            "duracao_s": ln.duracao_s,
            "ok": ln.ok,
            "resumo": ln.resumo,
            "linhas": (ln.resultado or {}).get("linhas", []),
            "dados": (ln.resultado or {}).get("dados", {}),
        }
        for ln in (await conexao.execute(consulta)).all()
    ]


__all__ = ["historico", "registrar"]
