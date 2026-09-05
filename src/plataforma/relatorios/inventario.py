"""Inventário e cobertura: o que existe, e de quanto disso a plataforma sabe.

Cobertura é o relatório mais desconfortável de uma plataforma de monitoramento,
e por isso mesmo o mais útil: ele diz **o tamanho do escuro**. Uma tela que só
mostra o que é coletado dá a impressão de que o coletado é tudo.

O inventário aqui é o que revela a dívida mais cara do cadastro — fabricante e
modelo. Sem modelo, todo perfil SNMP específico é chute e a descoberta
automática não tem em que se ancorar. O relatório não resolve isso; faz o
número aparecer toda vez que alguém abre a aba.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.db.esquema import (
    ativo,
    campo,
    dispositivo,
    estado,
    identificador,
    leitura,
    saude_modulo,
)

from .formatos import numero
from .modelo import Coluna, Relatorio, TipoColuna


async def cobertura(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """O que a plataforma sabe hoje, e de quem — por papel de equipamento."""
    total_papel = dict(
        (
            await conexao.execute(
                select(dispositivo.c.papel, func.count()).group_by(dispositivo.c.papel)
            )
        ).all()
    )
    com_estado = dict(
        (
            await conexao.execute(
                select(dispositivo.c.papel, func.count())
                .join(estado, estado.c.sujeito == dispositivo.c.chave)
                .group_by(dispositivo.c.papel)
            )
        ).all()
    )
    com_ip = dict(
        (
            await conexao.execute(
                select(dispositivo.c.papel, func.count(func.distinct(dispositivo.c.chave)))
                .join(identificador, identificador.c.dispositivo_chave == dispositivo.c.chave)
                .where(identificador.c.tipo == "ip")
                .group_by(dispositivo.c.papel)
            )
        ).all()
    )

    papel_de = dict(
        (await conexao.execute(select(dispositivo.c.chave, dispositivo.c.papel))).all()
    )
    medidos: dict[str, set[str]] = defaultdict(set)
    for ln in (
        await conexao.execute(select(leitura.c.sujeito).distinct())
    ).all():
        # O sujeito pode ser `chave/porta`; a cobertura conta o dispositivo.
        chave = ln.sujeito
        if papel := papel_de.get(chave) or papel_de.get(chave.split("/", 1)[0]):
            medidos[papel].add(chave.split("/", 1)[0])

    linhas = [
        {
            "papel": papel,
            "total": total,
            "com_ip": com_ip.get(papel, 0),
            "com_estado": com_estado.get(papel, 0),
            "com_metrica": len(medidos.get(papel, ())),
            "cobertura_pct": round(100 * com_estado.get(papel, 0) / total, 1),
        }
        for papel, total in sorted(total_papel.items(), key=lambda x: -x[1])
    ]
    r = Relatorio(
        nome="cobertura",
        titulo="Cobertura da coleta por papel de equipamento",
        desde=desde, ate=ate, linhas=linhas, parametros=p,
        colunas=(
            Coluna("papel", "Papel"),
            Coluna("total", "No cadastro", TipoColuna.NUMERO),
            Coluna("com_ip", "Com IP", TipoColuna.NUMERO),
            Coluna("com_estado", "Respondendo ou não", TipoColuna.NUMERO),
            Coluna("com_metrica", "Com número além disso", TipoColuna.NUMERO),
            Coluna("cobertura_pct", "Cobertura", TipoColuna.PERCENTUAL),
        ),
    )
    r.somar()
    total_geral = sum(total_papel.values())
    if total_geral:
        r.resumo = (
            f"{numero(sum(com_estado.values()), 0)} de {numero(total_geral, 0)} "
            "dispositivos são "
            f"observados de alguma forma."
        )
    mudos = [
        m.modulo
        for m in (
            await conexao.execute(
                select(saude_modulo.c.modulo, saude_modulo.c.ultima_coleta_ok)
            )
        ).all()
        if m.ultima_coleta_ok is None
    ]
    if mudos:
        r.notas.append(
            f"Módulos sem coleta bem-sucedida: {', '.join(sorted(mudos))}."
        )
    r.notas.append(
        "'Respondendo ou não' é estar de pé; 'com número além disso' é ter "
        "medição."
    )
    sem_ip = total_geral - sum(com_ip.values())
    if sem_ip > 0:
        r.notas.append(
            f"{sem_ip} dispositivos sem IP no cadastro — nenhum coletor os alcança."
        )
    return r


async def parque(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """O parque por papel, zona e fabricante — inclusive o que não se sabe."""
    campos = defaultdict(dict)
    for ln in (
        await conexao.execute(
            select(campo.c.sujeito, campo.c.nome, campo.c.valor).where(
                campo.c.nome.in_(["fabricante", "modelo", "firmware"])
            )
        )
    ).all():
        campos[ln.sujeito][ln.nome] = ln.valor

    linhas_bd = (
        await conexao.execute(
            select(
                dispositivo.c.chave, dispositivo.c.papel, dispositivo.c.zona,
                ativo.c.frota,
            ).select_from(
                dispositivo.outerjoin(ativo, ativo.c.ativo_id == dispositivo.c.ativo_id)
            )
        )
    ).all()
    zona = p.get("zona") or ""

    grupos: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"total": 0, "com_fabricante": 0, "com_modelo": 0, "com_firmware": 0}
    )
    for ln in linhas_bd:
        if zona and ln.zona != zona:
            continue
        c = campos.get(f"disp:{ln.chave}", {})
        g = grupos[(ln.papel, ln.zona, str(c.get("fabricante") or "não informado"))]
        g["total"] += 1
        g["com_fabricante"] += bool(c.get("fabricante"))
        g["com_modelo"] += bool(c.get("modelo"))
        g["com_firmware"] += bool(c.get("firmware"))

    linhas = [
        {"papel": k[0], "zona": k[1], "fabricante": k[2], **v}
        for k, v in sorted(grupos.items(), key=lambda x: -x[1]["total"])
    ]
    r = Relatorio(
        nome="parque",
        titulo="O parque por papel, zona e fabricante",
        desde=desde, ate=ate, linhas=linhas, parametros=p,
        colunas=(
            Coluna("papel", "Papel"),
            Coluna("zona", "Zona", TipoColuna.SELO),
            Coluna("fabricante", "Fabricante"),
            Coluna("total", "Quantos", TipoColuna.NUMERO),
            Coluna("com_modelo", "Com modelo", TipoColuna.NUMERO),
            Coluna("com_firmware", "Com firmware", TipoColuna.NUMERO),
        ),
    )
    r.somar()
    total = sum(x["total"] for x in linhas)
    sem_modelo = total - sum(x["com_modelo"] for x in linhas)
    if total:
        r.resumo = f"{total} dispositivos, {sem_modelo} deles sem modelo conhecido."
    if sem_modelo:
        r.notas.append(
            f"{sem_modelo} de {total} dispositivos sem modelo — perfil SNMP "
            "específico fica inviável."
        )
    return r
