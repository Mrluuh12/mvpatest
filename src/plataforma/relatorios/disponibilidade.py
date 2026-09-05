"""Disponibilidade: quanto tempo esteve de pé, e quando não esteve.

Uma unidade atravessa estes relatórios e merece o nome certo: **equipamento‑hora**.
Somar o tempo fora do ar de 654 equipamentos num dia de 24 h dá 654 "dias", e
lido como duração isso é absurdo na cara. Não é duração — é volume de
indisponibilidade, do mesmo jeito que homem‑hora não é hora. Onde a coluna soma
equipamentos diferentes, a unidade é ``equip·h`` e está escrita no cabeçalho.

Tudo aqui sai das **transições**, não de amostras. A tabela guarda o instante
de cada mudança, então o cálculo é exato dentro da janela — não uma média de
pontos espaçados. É a diferença entre "94%" e "94%, e a queda das 3h12 durou
17 minutos".

Uma regra atravessa os três relatórios: **nunca sondado não é o mesmo que
caído**. Quem não tem observação no período sai da média e entra na ressalva.
Contá-lo como zero rebaixaria a frota inteira por falta de coleta, que é
exatamente o erro que faz gente parar de confiar no relatório.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.db.coleta import estado_no_inicio
from plataforma.db.esquema import ativo, campo, dispositivo, estado, transicao

from .formatos import numero
from .modelo import Coluna, Relatorio, TipoColuna


async def _mudancas(conexao: AsyncConnection) -> dict[str, list]:
    saida: dict[str, list] = defaultdict(list)
    for ln in (
        await conexao.execute(
            select(transicao.c.sujeito, transicao.c.de, transicao.c.para, transicao.c.em)
            .order_by(transicao.c.em)
        )
    ).all():
        saida[ln.sujeito].append(ln)
    return saida


async def _parque(conexao: AsyncConnection) -> dict[str, dict]:
    """Identidade de cada dispositivo, com a função de negócio do ativo."""
    linhas = (
        await conexao.execute(
            select(
                dispositivo.c.chave,
                dispositivo.c.nome_canonico,
                dispositivo.c.papel,
                dispositivo.c.zona,
                dispositivo.c.ativo_id,
                ativo.c.frota,
            ).select_from(
                dispositivo.outerjoin(ativo, ativo.c.ativo_id == dispositivo.c.ativo_id)
            )
        )
    ).all()
    funcoes = {
        ln.sujeito.removeprefix("ativo:"): ln.valor
        for ln in (
            await conexao.execute(
                select(campo.c.sujeito, campo.c.valor).where(
                    campo.c.nome == "funcao_negocio"
                )
            )
        ).all()
        if ln.sujeito.startswith("ativo:")
    }
    return {
        ln.chave: {
            "chave": ln.chave, "nome": ln.nome_canonico, "papel": ln.papel,
            "zona": ln.zona, "ativo": ln.ativo_id or "", "frota": ln.frota or "?",
            "funcao": funcoes.get(ln.ativo_id, "não definida"),
        }
        for ln in linhas
    }


def _cabe(info: dict, frota: str, zona: str) -> bool:
    if frota and not str(info["frota"]).upper().startswith(frota.upper()):
        return False
    return not (zona and info["zona"] != zona)


async def _por_sujeito(
    conexao: AsyncConnection, desde: datetime, ate: datetime
) -> dict[str, tuple[float, float] | None]:
    """``{sujeito: (fracao_no_ar, segundos_fora)}``, ou ``None`` sem observação.

    Calculado de uma vez para todos: 708 chamadas a uma função por sujeito
    seriam 708 idas ao banco por relatório, e o relatório é a tela que mais
    gente abre ao mesmo tempo.
    """
    mudancas = await _mudancas(conexao)
    saida: dict[str, tuple[float, float] | None] = {}
    if (ate - desde).total_seconds() <= 0:
        return saida

    for ln in (
        await conexao.execute(
            select(estado.c.sujeito, estado.c.alcancavel, estado.c.visto_em)
        )
    ).all():
        fim = min(ln.visto_em, ate)
        if fim <= desde:
            saida[ln.sujeito] = None
            continue
        historico = mudancas.get(ln.sujeito, [])
        vivo = estado_no_inicio(historico, desde, ln.alcancavel)

        marco, no_ar = desde, 0.0
        for m in (x for x in historico if desde < x.em <= fim):
            if vivo:
                no_ar += (m.em - marco).total_seconds()
            vivo, marco = m.para, m.em
        if vivo:
            no_ar += (fim - marco).total_seconds()
        total = (fim - desde).total_seconds()
        saida[ln.sujeito] = (no_ar / total, total - no_ar)
    return saida


async def por_frota(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """A pergunta que troca "3 nós down" por "a britagem está a 87%"."""
    disp = await _por_sujeito(conexao, desde, ate)
    parque = await _parque(conexao)
    frota, zona = p.get("frota") or "", p.get("zona") or ""

    grupos: dict[tuple[str, str], list[float]] = defaultdict(list)
    ativos: dict[tuple[str, str], set[str]] = defaultdict(set)
    fora: dict[tuple[str, str], float] = defaultdict(float)
    sem_medida = 0
    for chave, info in parque.items():
        if not _cabe(info, frota, zona):
            continue
        g = (info["frota"], info["funcao"])
        ativos[g].add(info["ativo"])
        medida = disp.get(chave)
        if medida is None:
            sem_medida += 1
            continue
        grupos[g].append(medida[0])
        fora[g] += medida[1]

    linhas = [
        {
            "frota": g[0],
            "funcao": g[1],
            "ativos": len(ativos[g]),
            "medidos": len(v),
            "disponibilidade_pct": round(100 * sum(v) / len(v), 2),
            "pior_pct": round(100 * min(v), 2),
            "fora_do_ar_eqh": round(fora[g] / 3600, 1),
        }
        for g, v in sorted(grupos.items(), key=lambda x: sum(x[1]) / len(x[1]))
    ]

    r = Relatorio(
        nome="disponibilidade_frota",
        titulo="Disponibilidade por frota e função de negócio",
        desde=desde, ate=ate, linhas=linhas, parametros=p,
        colunas=(
            Coluna("frota", "Frota"),
            Coluna("funcao", "Função de negócio"),
            Coluna("ativos", "Ativos", TipoColuna.NUMERO),
            Coluna("medidos", "Dispositivos medidos", TipoColuna.NUMERO),
            Coluna("disponibilidade_pct", "Disponibilidade", TipoColuna.PERCENTUAL),
            Coluna("pior_pct", "Pior do grupo", TipoColuna.PERCENTUAL),
            Coluna("fora_do_ar_eqh", "Fora do ar", TipoColuna.NUMERO,
                   unidade="equip·h"),
        ),
    )
    r.somar()
    if linhas:
        pior = linhas[0]
        r.resumo = (
            f"{pior['funcao']} na frota {pior['frota']} é o grupo mais baixo do "
            f"período, com {numero(pior['disponibilidade_pct'], 2)}%."
        )
    if sem_medida:
        r.notas.append(
            f"{sem_medida} dispositivos sem observação no período — fora da média."
        )
    if sem_funcao := sum(1 for _, fn in grupos if fn == "não definida"):
        r.notas.append(
            f"{sem_funcao} grupos com função de negócio não definida no cadastro."
        )
    return r


async def piores(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """Os equipamentos que menos estiveram de pé — o "Top N" invertido.

    Ordenado por tempo fora do ar, não por percentual: 90% num rádio sondado
    há uma hora e 90% num sondado há um mês são o mesmo número e problemas de
    tamanhos muito diferentes.
    """
    disp = await _por_sujeito(conexao, desde, ate)
    parque = await _parque(conexao)
    frota, zona = p.get("frota") or "", p.get("zona") or ""
    limite = max(1, int(p.get("limite") or 20))

    candidatos = []
    for chave, info in parque.items():
        medida = disp.get(chave)
        if medida is None or not _cabe(info, frota, zona):
            continue
        if medida[1] <= 0:
            continue
        candidatos.append(
            {
                "nome": info["nome"], "ativo": info["ativo"], "frota": info["frota"],
                "papel": info["papel"], "zona": info["zona"],
                "disponibilidade_pct": round(100 * medida[0], 2),
                "fora_do_ar_s": round(medida[1], 0),
            }
        )
    candidatos.sort(key=lambda x: -x["fora_do_ar_s"])

    r = Relatorio(
        nome="piores_disponibilidades",
        titulo=f"Os {limite} equipamentos com mais tempo fora do ar",
        desde=desde, ate=ate, linhas=candidatos[:limite], parametros=p,
        colunas=(
            Coluna("nome", "Equipamento"),
            Coluna("ativo", "Ativo"),
            Coluna("frota", "Frota"),
            Coluna("papel", "Papel"),
            Coluna("zona", "Zona", TipoColuna.SELO),
            Coluna("disponibilidade_pct", "Disponibilidade", TipoColuna.PERCENTUAL),
            Coluna("fora_do_ar_s", "Fora do ar", TipoColuna.DURACAO, soma=False),
        ),
    )
    r.somar()
    r.notas.append(
        "Coluna não somada: soma de durações de equipamentos diferentes é "
        "equipamento·hora, não duração."
    )
    if len(candidatos) > limite:
        r.notas.append(
            f"{len(candidatos) - limite} equipamentos com queda além dos mostrados."
        )
    if not candidatos:
        r.notas.append(
            "Nenhum equipamento medido ficou fora do ar no período."
        )
    return r


async def quedas(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """Cada queda do período, com início, fim e duração.

    É o relatório que operação abre depois de um turno ruim. Vale por causa de
    uma escolha feita lá atrás: guardar **transições** e não amostras. Com
    amostra de minuto em minuto, uma queda de dois minutos apareceria como
    "dois pontos ruins"; aqui aparece como um intervalo com hora de começo e
    de fim.

    A queda que ainda não terminou aparece com fim vazio e é dita como tal.
    Fechar a duração no instante do relatório inventaria um fim que não houve.
    """
    parque = await _parque(conexao)
    frota, zona = p.get("frota") or "", p.get("zona") or ""
    minimo = float(p.get("minimo_s") or 0)
    mudancas = await _mudancas(conexao)

    correntes = {
        ln.sujeito: ln
        for ln in (
            await conexao.execute(
                select(estado.c.sujeito, estado.c.alcancavel, estado.c.qualidade)
            )
        ).all()
    }

    linhas, abertas, incertas = [], 0, 0
    for sujeito, historico in mudancas.items():
        info = parque.get(sujeito)
        if info is None or not _cabe(info, frota, zona):
            continue
        dentro = [m for m in historico if desde < m.em <= ate]
        vivo = estado_no_inicio(
            historico, desde, correntes[sujeito].alcancavel if sujeito in correntes else True
        )
        inicio_queda = None if vivo else desde
        for m in dentro:
            if not m.para and inicio_queda is None:
                inicio_queda = m.em
            elif m.para and inicio_queda is not None:
                linhas.append(_queda(info, inicio_queda, m.em, False, False))
                inicio_queda = None
        if inicio_queda is not None:
            incerta = sujeito in correntes and correntes[sujeito].qualidade == "incerta"
            linhas.append(_queda(info, inicio_queda, ate, True, incerta))
            abertas += 1
            incertas += int(incerta)

    linhas = [x for x in linhas if x["duracao_s"] >= minimo]
    linhas.sort(key=lambda x: -x["duracao_s"])

    r = Relatorio(
        nome="quedas",
        titulo="Quedas do período, uma linha por queda",
        desde=desde, ate=ate, linhas=linhas, parametros=p,
        colunas=(
            Coluna("nome", "Equipamento"),
            Coluna("ativo", "Ativo"),
            Coluna("frota", "Frota"),
            Coluna("papel", "Papel"),
            Coluna("inicio", "Caiu às", TipoColuna.INSTANTE),
            Coluna("fim", "Voltou às", TipoColuna.INSTANTE),
            Coluna("duracao_s", "Durou", TipoColuna.DURACAO, soma=False),
            Coluna("situacao", "Situação", TipoColuna.SELO),
        ),
    )
    r.somar()
    if linhas:
        r.resumo = (
            f"{len(linhas)} quedas somando "
            f"{numero(sum(x['duracao_s'] for x in linhas) / 3600)} equipamento·hora "
            "fora do ar."
        )
    if abertas:
        r.notas.append(
            f"{abertas} quedas em curso — duração contada até o fim do período."
        )
    if incertas:
        r.notas.append(
            f"{incertas} marcadas como incertas: suspeita de isolamento do coletor."
        )
    if minimo:
        r.notas.append(f"Quedas de menos de {int(minimo)} s omitidas por filtro.")
    return r


def _queda(info: dict, inicio: datetime, fim: datetime, aberta: bool, incerta: bool) -> dict:
    return {
        "nome": info["nome"], "ativo": info["ativo"], "frota": info["frota"],
        "papel": info["papel"],
        "inicio": inicio, "fim": None if aberta else fim,
        "duracao_s": round((fim - inicio).total_seconds(), 0),
        "situacao": "incerta" if incerta else ("em curso" if aberta else "encerrada"),
    }


def _dia(d: datetime) -> datetime:
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


async def por_dia(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """Disponibilidade dia a dia — a linha do tempo que a tabela por frota esconde.

    Uma média de 97% no mês pode ser trinta dias de 97% ou vinte e nove de 100%
    com um dia de 10%. São situações diferentes e a mesma célula.
    """
    parque = await _parque(conexao)
    frota, zona = p.get("frota") or "", p.get("zona") or ""
    alvos = {c for c, i in parque.items() if _cabe(i, frota, zona)}

    linhas = []
    dia = _dia(desde)
    while dia < ate:
        fim_dia = min(dia + timedelta(days=1), ate)
        if fim_dia > desde:
            disp = await _por_sujeito(conexao, max(dia, desde), fim_dia)
            medidos = [v for c, v in disp.items() if c in alvos and v is not None]
            if medidos:
                linhas.append(
                    {
                        "dia": max(dia, desde),
                        "medidos": len(medidos),
                        "disponibilidade_pct": round(
                            100 * sum(m[0] for m in medidos) / len(medidos), 2
                        ),
                        "fora_do_ar_eqh": round(sum(m[1] for m in medidos) / 3600, 1),
                        "com_queda": sum(1 for m in medidos if m[1] > 0),
                    }
                )
        dia += timedelta(days=1)

    r = Relatorio(
        nome="disponibilidade_dia",
        titulo="Disponibilidade dia a dia",
        desde=desde, ate=ate, linhas=linhas, parametros=p,
        colunas=(
            Coluna("dia", "Dia", TipoColuna.INSTANTE),
            Coluna("medidos", "Dispositivos medidos", TipoColuna.NUMERO, soma=False),
            Coluna("disponibilidade_pct", "Disponibilidade", TipoColuna.PERCENTUAL),
            Coluna("com_queda", "Com alguma queda", TipoColuna.NUMERO, soma=False),
            Coluna("fora_do_ar_eqh", "Fora do ar", TipoColuna.NUMERO,
                   unidade="equip·h"),
        ),
    )
    r.somar()
    if linhas:
        pior = min(linhas, key=lambda x: x["disponibilidade_pct"])
        r.resumo = (
            f"o pior dia foi {pior['dia']:%d/%m}, com "
            f"{numero(pior['disponibilidade_pct'], 2)}%."
        )
    r.notas.append(
        "'Medidos' e 'com alguma queda' não somam: o mesmo equipamento aparece "
        "em vários dias."
    )
    r.notas.append(
        "'Fora do ar' em equipamento·hora. Num dia de 24 h com 600 equipamentos, "
        "o máximo é 14.400."
    )
    return r
