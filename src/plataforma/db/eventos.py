"""Gravação de eventos, com as duas defesas que o canal push exige.

Coleta a plataforma controla: ela decide quando perguntar e para quantos. Aqui
não — o equipamento manda quando quer, quanto quer. Isso obriga a duas coisas
que não existiam nos outros canais.

**Limite por origem.** Uma porta oscilando manda milhares de mensagens por
minuto. Sem limite, um equipamento defeituoso enche a tabela e leva junto o
que importa. O receptor conta, descarta o excedente e **grava um evento
dizendo quantos descartou** — silêncio aqui seria a plataforma escondendo que
está cega.

**Retenção.** Evento não é estado: não se agrega, não se resume, e acumula
para sempre se ninguém apagar. A limpeza é explícita e diz quantos removeu.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, desc, func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.eventos import Evento

from .esquema import evento, identificador

#: Mensagens por origem por minuto. Acima disso, conta e descarta. Um switch
#: saudável manda unidades por hora; centenas por minuto é defeito, e o
#: defeito não pode derrubar a plataforma junto.
LIMITE_POR_MINUTO = 120


@dataclass
class Vazao:
    """Contador de mensagens por origem, com janela de um minuto."""

    limite: int = LIMITE_POR_MINUTO
    _janela: datetime | None = None
    _contagem: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _descartes: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def aceita(self, origem_ip: str, agora: datetime) -> bool:
        minuto = agora.replace(second=0, microsecond=0)
        if self._janela != minuto:
            self._janela = minuto
            self._contagem.clear()
        self._contagem[origem_ip] += 1
        if self._contagem[origem_ip] > self.limite:
            self._descartes[origem_ip] += 1
            return False
        return True

    def descartes(self) -> dict[str, int]:
        """Quantos foram descartados por origem, e zera o contador.

        Devolver e zerar de uma vez é de propósito: quem chama grava o resumo,
        e um resumo gravado duas vezes seria pior que nenhum.
        """
        saida = dict(self._descartes)
        self._descartes.clear()
        return saida


async def _mapa_de_ips(conexao: AsyncConnection) -> dict[str, str]:
    """IP → chave do dispositivo, recusando IP disputado.

    Mesmo princípio da junção do Rajant: um endereço que dois equipamentos
    reivindicam não identifica nenhum, e pendurar o evento no errado é pior do
    que deixá-lo sem dono.
    """
    mapa: dict[str, str] = {}
    ambiguos: set[str] = set()
    for ln in (
        await conexao.execute(
            select(identificador.c.valor, identificador.c.dispositivo_chave).where(
                identificador.c.tipo == "ip"
            )
        )
    ).all():
        if ln.valor in mapa and mapa[ln.valor] != ln.dispositivo_chave:
            ambiguos.add(ln.valor)
        mapa[ln.valor] = ln.dispositivo_chave
    for ip in ambiguos:
        mapa.pop(ip, None)
    return mapa


async def gravar(conexao: AsyncConnection, eventos: list[Evento]) -> dict[str, int]:
    """Resolve a origem e grava. Devolve o que aconteceu, para a saúde."""
    if not eventos:
        return {"gravados": 0, "sem_dono": 0}
    mapa = await _mapa_de_ips(conexao)
    linhas = []
    sem_dono = 0
    for e in eventos:
        chave = mapa.get(e.origem_ip)
        if chave is None:
            sem_dono += 1
        linhas.append(
            {
                "sujeito": chave,
                "origem_ip": e.origem_ip,
                "tipo": e.tipo,
                "severidade": e.severidade,
                "facilidade": e.facilidade,
                "mensagem": e.mensagem[:4000],
                "remetente": e.remetente[:200],
                "confianca": e.confianca,
                "atributos": e.atributos,
                "em": e.em,
                "recebido_em": e.recebido_em,
            }
        )
    await conexao.execute(insert(evento), linhas)
    return {"gravados": len(linhas), "sem_dono": sem_dono}


async def ultimos(
    conexao: AsyncConnection,
    sujeito: str | None = None,
    severidade_minima: str | None = None,
    limite: int = 50,
) -> list[dict]:
    from plataforma.eventos import SEVERIDADES

    consulta = select(evento).order_by(desc(evento.c.recebido_em)).limit(min(limite, 500))
    if sujeito:
        consulta = consulta.where(evento.c.sujeito == sujeito)
    if severidade_minima:
        corte = SEVERIDADES.index(severidade_minima)
        consulta = consulta.where(
            evento.c.severidade.in_(list(SEVERIDADES[: corte + 1]))
        )
    return [
        {
            "id": ln.id,
            "sujeito": ln.sujeito,
            "origem_ip": ln.origem_ip,
            "tipo": ln.tipo,
            "severidade": ln.severidade,
            "facilidade": ln.facilidade,
            "mensagem": ln.mensagem,
            "remetente": ln.remetente,
            "confianca": ln.confianca,
            "em": ln.em,
            "recebido_em": ln.recebido_em,
            "atributos": ln.atributos or {},
        }
        for ln in (await conexao.execute(consulta)).all()
    ]


async def resumo(conexao: AsyncConnection, desde: datetime) -> list[dict]:
    linhas = (
        await conexao.execute(
            select(evento.c.severidade, func.count())
            .where(evento.c.recebido_em >= desde)
            .group_by(evento.c.severidade)
        )
    ).all()
    return [{"severidade": s, "total": n} for s, n in linhas]


async def limpar(conexao: AsyncConnection, dias: int = 30) -> int:
    """Apaga o que passou da retenção. Evento não se agrega — só se apaga."""
    corte = datetime.now(UTC) - timedelta(days=dias)
    r = await conexao.execute(delete(evento).where(evento.c.recebido_em < corte))
    return r.rowcount or 0


__all__ = ["LIMITE_POR_MINUTO", "Vazao", "gravar", "limpar", "resumo", "ultimos"]
