"""Eventos: o que os equipamentos contaram por conta própria.

Métrica e relação são perguntas: a plataforma vai lá e mede. Evento é o
contrário — o equipamento fala sem ser perguntado, e fala no instante em que
algo acontece, não no próximo ciclo de coleta.

Isso muda o que um relatório de eventos precisa dizer. Uma métrica ausente
significa "não medi". Um evento ausente **não significa nada**: pode ser que
nada aconteceu, que o equipamento não está configurado para mandar syslog, ou
que o datagrama se perdeu no caminho. UDP não avisa. Por isso todo relatório
daqui carrega a mesma ressalva, e ela não é formalidade.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.db.esquema import dispositivo, evento

from .modelo import Coluna, Relatorio, TipoColuna

#: Ordem de gravidade do syslog, para a tabela não sair em ordem alfabética —
#: "alerta" antes de "emergência" seria alfabeticamente certo e operacionalmente
#: absurdo.
GRAVIDADE = [
    "emergencia", "alerta", "critico", "erro",
    "aviso", "atencao", "informativo", "depuracao",
]

RESSALVA_UDP = (
    "Syslog anda em UDP: ausência de evento não prova que nada aconteceu."
)


async def por_severidade(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """Quantos eventos de cada gravidade, e de quantas origens distintas."""
    linhas_bd = (
        await conexao.execute(
            select(
                evento.c.severidade,
                func.count().label("quantos"),
                func.count(func.distinct(evento.c.origem_ip)).label("origens"),
                func.max(evento.c.recebido_em).label("ultimo"),
            )
            .where(evento.c.recebido_em.between(desde, ate))
            .group_by(evento.c.severidade)
        )
    ).all()
    por_nome = {ln.severidade: ln for ln in linhas_bd}
    linhas = [
        {
            "severidade": s,
            "quantos": por_nome[s].quantos,
            "origens": por_nome[s].origens,
            "ultimo": por_nome[s].ultimo,
        }
        for s in GRAVIDADE
        if s in por_nome
    ]
    linhas += [
        {"severidade": ln.severidade, "quantos": ln.quantos, "origens": ln.origens,
         "ultimo": ln.ultimo}
        for ln in linhas_bd
        if ln.severidade not in GRAVIDADE
    ]

    r = Relatorio(
        nome="eventos_severidade",
        titulo="Eventos por gravidade",
        desde=desde, ate=ate, linhas=linhas, parametros=p,
        colunas=(
            Coluna("severidade", "Gravidade", TipoColuna.SELO),
            Coluna("quantos", "Eventos", TipoColuna.NUMERO),
            Coluna("origens", "Origens distintas", TipoColuna.NUMERO, soma=False),
            Coluna("ultimo", "Mais recente", TipoColuna.INSTANTE),
        ),
    )
    r.somar()
    graves = sum(x["quantos"] for x in linhas if x["severidade"] in GRAVIDADE[:4])
    if graves:
        r.resumo = f"{graves} eventos de erro ou pior no período."
    elif linhas:
        r.resumo = "nenhum evento de erro ou pior no período."
    r.notas.append(RESSALVA_UDP)
    if not linhas:
        r.notas.append(
            "Nenhum evento recebido no período."
        )
    return r


async def faladores(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """Quem mais falou — e o que uma origem barulhenta costuma significar.

    Ordenado por volume porque volume é o sintoma: um equipamento que manda
    milhares de mensagens ou está com defeito repetitivo, ou está com
    ``logging debug`` ligado desde a última manutenção. Os dois merecem visita,
    por motivos diferentes.
    """
    limite = max(1, int(p.get("limite") or 20))
    graves_apenas = str(p.get("apenas_graves") or "") == "sim"

    consulta = (
        select(
            evento.c.origem_ip,
            func.count().label("quantos"),
            func.count(func.distinct(evento.c.severidade)).label("gravidades"),
            func.max(evento.c.recebido_em).label("ultimo"),
        )
        .where(evento.c.recebido_em.between(desde, ate))
        .group_by(evento.c.origem_ip)
        .order_by(func.count().desc())
    )
    if graves_apenas:
        consulta = consulta.where(evento.c.severidade.in_(GRAVIDADE[:4]))
    linhas_bd = (await conexao.execute(consulta)).all()

    # Traduz IP para nome quando o cadastro conhece — origem_ip é o que o
    # datagrama trouxe, e é por ele que o receptor guarda.
    from plataforma.db.esquema import identificador

    nomes = dict(
        (
            await conexao.execute(
                select(identificador.c.valor, dispositivo.c.nome_canonico)
                .join(dispositivo, dispositivo.c.chave == identificador.c.dispositivo_chave)
                .where(identificador.c.tipo == "ip")
            )
        ).all()
    )

    linhas = [
        {
            "origem_ip": ln.origem_ip,
            "equipamento": nomes.get(ln.origem_ip, "não está no cadastro"),
            "quantos": ln.quantos,
            "gravidades": ln.gravidades,
            "ultimo": ln.ultimo,
        }
        for ln in linhas_bd[:limite]
    ]
    r = Relatorio(
        nome="eventos_faladores",
        titulo=f"As {limite} origens que mais mandaram evento",
        desde=desde, ate=ate, linhas=linhas, parametros=p,
        colunas=(
            Coluna("origem_ip", "Origem"),
            Coluna("equipamento", "Equipamento"),
            Coluna("quantos", "Eventos", TipoColuna.NUMERO),
            Coluna("gravidades", "Gravidades distintas", TipoColuna.NUMERO, soma=False),
            Coluna("ultimo", "Mais recente", TipoColuna.INSTANTE),
        ),
    )
    r.somar()
    if linhas:
        r.resumo = f"{linhas[0]['equipamento']} mandou {linhas[0]['quantos']} eventos."
    orfas = [x for x in linhas if x["equipamento"] == "não está no cadastro"]
    if orfas:
        r.notas.append(
            f"{len(orfas)} origens fora do cadastro."
        )
    r.notas.append(RESSALVA_UDP)
    return r
