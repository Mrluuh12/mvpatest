"""Governança: quem mexeu, quem perguntou, e onde o cadastro está torto.

Estes três relatórios não falam de rede. Falam da própria plataforma e das
pessoas que a usam, e existem por uma razão prática: numa mina, "quem mudou
isto?" é pergunta de auditoria interna, e responder por memória não serve.

A auditoria é somente-escrita no esquema — não há rota que altere ou apague.
Um registro que se pode editar não é registro.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.db.esquema import achado, auditoria, diagnostico

from .modelo import Coluna, Relatorio, TipoColuna


async def alteracoes(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """Toda ação registrada no período, de quem e sobre o quê."""
    limite = max(1, int(p.get("limite") or 100))
    login = (p.get("login") or "").strip()

    consulta = (
        select(
            auditoria.c.em, auditoria.c.login, auditoria.c.acao,
            auditoria.c.sujeito, auditoria.c.zona, auditoria.c.detalhe,
        )
        .where(auditoria.c.em.between(desde, ate))
        .order_by(auditoria.c.em.desc())
    )
    if login:
        consulta = consulta.where(auditoria.c.login == login)

    linhas = [
        {
            "em": ln.em,
            "login": ln.login or "—",
            "acao": ln.acao,
            "sujeito": ln.sujeito,
            "zona": ln.zona or "—",
            "detalhe": _resumir(ln.detalhe),
        }
        for ln in (await conexao.execute(consulta)).all()[:limite]
    ]
    r = Relatorio(
        nome="alteracoes",
        titulo="Alterações e ações registradas",
        desde=desde, ate=ate, linhas=linhas, parametros=p,
        colunas=(
            Coluna("em", "Quando", TipoColuna.INSTANTE),
            Coluna("login", "Quem"),
            Coluna("acao", "Ação", TipoColuna.SELO),
            Coluna("sujeito", "Sobre o quê"),
            Coluna("zona", "Zona", TipoColuna.SELO),
            Coluna("detalhe", "Detalhe"),
        ),
    )
    por_acao: dict[str, int] = defaultdict(int)
    for x in linhas:
        por_acao[x["acao"]] += 1
    if linhas:
        top = max(por_acao.items(), key=lambda x: x[1])
        r.resumo = f"{len(linhas)} ações, a mais frequente sendo {top[0]} ({top[1]}×)."
    recusas = sum(1 for x in linhas if "recusad" in x["acao"] or "negad" in x["acao"])
    if recusas:
        r.notas.append(
            f"{recusas} linhas são tentativas recusadas."
        )
    r.notas.append(
        "A auditoria é somente-escrita: não há rota que altere ou apague."
    )
    return r


async def sondagens(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """Quais diagnósticos foram disparados, por quem, e o que deram.

    Uma sonda é leitura dirigida a um alvo **por uma pessoa**. O histórico
    serve para duas coisas: comparar com a próxima vez, e responder quem
    apontou o quê para onde.
    """
    limite = max(1, int(p.get("limite") or 100))
    linhas = [
        {
            "em": ln.em,
            "sonda": ln.sonda,
            "alvo": ln.alvo,
            "por": ln.por,
            "resultado": "respondeu" if ln.ok else "não respondeu",
            "duracao_s": round(ln.duracao_s, 2),
            "resumo": ln.resumo,
        }
        for ln in (
            await conexao.execute(
                select(diagnostico)
                .where(diagnostico.c.em.between(desde, ate))
                .order_by(diagnostico.c.em.desc())
            )
        ).all()[:limite]
    ]
    r = Relatorio(
        nome="sondagens",
        titulo="Diagnósticos executados",
        desde=desde, ate=ate, linhas=linhas, parametros=p,
        colunas=(
            Coluna("em", "Quando", TipoColuna.INSTANTE),
            Coluna("sonda", "Sonda", TipoColuna.SELO),
            Coluna("alvo", "Alvo"),
            Coluna("por", "Quem"),
            Coluna("resultado", "Resultado", TipoColuna.SELO),
            Coluna("duracao_s", "Levou", TipoColuna.NUMERO, unidade="s", soma=False),
            Coluna("resumo", "Resumo"),
        ),
    )
    if linhas:
        falhas = sum(1 for x in linhas if x["resultado"] != "respondeu")
        r.resumo = f"{len(linhas)} sondagens, {falhas} sem resposta do alvo."
    return r


async def higiene(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """Onde o cadastro se contradiz — a lista de trabalho humano.

    Não são erros da plataforma: são coisas que a planilha diz e que não podem
    ser todas verdade ao mesmo tempo. Dois ativos com o mesmo MAC, dois
    equipamentos com o mesmo nome, três rádios disputando um IP. Guardados
    porque somem do terminal e não podem sumir do sistema.
    """
    linhas_bd = (
        await conexao.execute(
            select(achado.c.categoria, func.count().label("quantos"),
                   func.max(achado.c.em).label("visto"))
            .group_by(achado.c.categoria)
            .order_by(func.count().desc())
        )
    ).all()
    exemplos: dict[str, str] = {}
    for ln in (
        await conexao.execute(
            select(achado.c.categoria, achado.c.descricao).order_by(achado.c.id)
        )
    ).all():
        exemplos.setdefault(ln.categoria, ln.descricao)

    linhas = [
        {
            "categoria": ln.categoria,
            "quantos": ln.quantos,
            "exemplo": exemplos.get(ln.categoria, ""),
            "visto_em": ln.visto,
        }
        for ln in linhas_bd
    ]
    r = Relatorio(
        nome="higiene",
        titulo="Contradições do cadastro, por categoria",
        desde=desde, ate=ate, linhas=linhas, parametros=p,
        colunas=(
            Coluna("categoria", "Categoria", TipoColuna.SELO),
            Coluna("quantos", "Quantos", TipoColuna.NUMERO),
            Coluna("exemplo", "Um exemplo"),
            Coluna("visto_em", "Da semeadura de", TipoColuna.INSTANTE),
        ),
    )
    r.somar()
    if linhas:
        r.resumo = (
            f"{sum(x['quantos'] for x in linhas)} contradições em "
            f"{len(linhas)} categorias."
        )
    r.notas.append(
        "Achados da última semeadura, não da janela escolhida."
    )
    r.notas.append(
        "Nada é corrigido automaticamente: as duas versões ficam guardadas."
    )
    return r


def _resumir(detalhe: dict | None) -> str:
    if not detalhe:
        return ""
    partes = [f"{k}={v}" for k, v in list(detalhe.items())[:3]]
    return ", ".join(partes)[:160]
