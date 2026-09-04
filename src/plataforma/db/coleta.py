"""Gravação do que os módulos coletam.

Duas regras governam este arquivo.

**Só as mudanças viram histórico.** O estado corrente é sobrescrito a cada
ciclo; a tabela de transições recebe uma linha apenas quando o estado de fato
muda. Sondando 708 dispositivos por minuto, a diferença é entre ~1 milhão de
linhas por dia e algumas dezenas — e nenhuma informação se perde, porque
disponibilidade em qualquer janela é reconstruível a partir das transições.

**Ausência não vira zero.** Quando o módulo não mede latência, a coluna fica
nula. Nulo e zero dizem coisas diferentes: um é "não sei", o outro é "medi e
deu zero". Confundi-los corrompe médias meses depois, num relatório que
ninguém consegue explicar.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.modulos.contrato import ResultadoColeta

from .esquema import estado, leitura, saude_modulo, transicao

#: A partir de quantos alvos uma falha total deixa de ser coincidência.
#: Com poucos alvos, todos caírem juntos é plausível; com dezenas, é quase
#: sempre o coletor que perdeu a rede.
MINIMO_PARA_SUSPEITAR = 5


def suspeita_de_isolamento(resultado: ResultadoColeta) -> bool:
    """True quando é mais provável que o coletor esteja isolado.

    Se **todos** os alvos falham de uma vez, a explicação mais provável não é
    que o parque inteiro caiu — é que quem pergunta ficou sem rede. Tratar as
    duas hipóteses como a mesma coisa produz centenas de incidentes falsos, e
    é a versão de coletor da regra que impede silêncio fechar aresta no grafo.
    """
    return (
        resultado.alvos_total >= MINIMO_PARA_SUSPEITAR
        and resultado.alvos_falha == resultado.alvos_total
    )


def _por_sujeito(resultado: ResultadoColeta) -> dict[str, dict[str, float]]:
    """Agrupa as observações de dispositivo por sujeito.

    As séries da família ``modulo`` ficam de fora: elas descrevem o coletor,
    não um equipamento, e vão para outra tabela.
    """
    agrupado: dict[str, dict[str, float]] = {}
    for obs in resultado.observacoes:
        if obs.sujeito.startswith("modulo:"):
            continue
        agrupado.setdefault(obs.sujeito, {})[obs.metrica] = obs.valor
    return agrupado


#: O que já vira estado e transição — não se repete em `leitura`.
DE_DISPONIBILIDADE = frozenset(
    {"ativo_alcancavel", "ativo_latencia_ms", "ativo_perda_pacote_pct", "ativo_jitter_ms"}
)


async def _gravar_leituras(
    conexao: AsyncConnection,
    nome_modulo: str,
    resultado: ResultadoColeta,
    momento: datetime,
) -> int:
    """Substitui a última leitura de cada (sujeito, métrica).

    Substitui, não acumula: a tabela responde "quanto está agora". Quem
    precisa de série tem o Prometheus, que a guarda muito melhor — e
    duplicá-la aqui só criaria duas verdades sobre o mesmo número.
    """
    # Um mesmo par (sujeito, métrica) duas vezes na mesma remessa faz o
    # Postgres recusar o lote inteiro — "cannot affect row a second time".
    # Acontece de verdade: um Rajant com três rádios publica três séries com
    # IPs diferentes que resolvem para o mesmo equipamento. Aqui vence a
    # última; quem deveria ter agregado é o módulo, e é lá que está o comentário.
    unicas: dict[tuple[str, str], dict] = {}
    for o in resultado.observacoes:
        if o.metrica in DE_DISPONIBILIDADE:
            continue
        unicas[(o.sujeito, o.metrica)] = {
            "sujeito": o.sujeito,
            "metrica": o.metrica,
            "valor": o.valor,
            "qualidade": o.qualidade.value,
            "rotulos": o.rotulos,
            "modulo": nome_modulo,
            "em": o.em or momento,
        }
    linhas = list(unicas.values())
    if not linhas:
        return 0
    await conexao.execute(
        pg_insert(leitura)
        .values(linhas)
        .on_conflict_do_update(
            index_elements=["sujeito", "metrica"],
            set_={
                c: text(f"excluded.{c}")
                for c in ("valor", "qualidade", "rotulos", "modulo", "em")
            },
        )
    )
    return len(linhas)


async def leituras_de(conexao: AsyncConnection, sujeito: str) -> list[dict]:
    """As leituras de um equipamento, para a ficha dele."""
    linhas = (
        await conexao.execute(
            select(leitura).where(leitura.c.sujeito == sujeito).order_by(leitura.c.metrica)
        )
    ).all()
    return [
        {
            "metrica": ln.metrica,
            "valor": ln.valor,
            "qualidade": ln.qualidade,
            "rotulos": ln.rotulos or {},
            "modulo": ln.modulo,
            "em": ln.em,
        }
        for ln in linhas
    ]


async def _conciliar_grafo(
    conexao: AsyncConnection, resultado: ResultadoColeta, momento: datetime
) -> dict[str, int]:
    """Traduz as relações observadas e reconcilia o grafo temporal.

    O módulo relata identidades (``mac:...``, um IP); traduzir para chave do
    inventário é trabalho da plataforma, que é quem conhece os
    identificadores. Vizinho que não resolve não vira aresta — mas é contado,
    porque rádio que a malha vê e a planilha não tem é achado de inventário.
    """
    from .grafo import conciliar, resolver_identidades

    if not resultado.relacoes and not resultado.relacoes_completas:
        return {}

    resolucao = await resolver_identidades(
        conexao, {r.destino for r in resultado.relacoes}
    )
    traduzidas = tuple(
        r.model_copy(update={"destino": resolucao.por_identidade[r.destino]})
        for r in resultado.relacoes
        if r.destino in resolucao.por_identidade
    )
    # Quem foi lido de fato neste ciclo. Fechar aresta de quem não foi lido
    # seria afirmar queda sem ter perguntado.
    lidos = {o.sujeito for o in resultado.observacoes}

    total = {"nao_resolvidos": len(resolucao.desconhecidas) + len(resolucao.ambiguas)}
    for tipo in {r.tipo for r in resultado.relacoes}:
        c = await conciliar(
            conexao, tipo, traduzidas, momento,
            completo=resultado.relacoes_completas, observadores=lidos,
        )
        total["arestas_abertas"] = total.get("arestas_abertas", 0) + c.abertas
        total["arestas_fechadas"] = total.get("arestas_fechadas", 0) + c.fechadas
    return total


async def gravar_coleta(
    conexao: AsyncConnection,
    nome_modulo: str,
    resultado: ResultadoColeta,
    agora: datetime | None = None,
) -> dict[str, int]:
    """Grava estado, transições e saúde do módulo. Devolve o que mudou."""
    momento = agora or datetime.now(UTC)
    medidas = _por_sujeito(resultado)
    isolado = suspeita_de_isolamento(resultado)

    anterior = {
        linha.sujeito: linha.alcancavel
        for linha in (await conexao.execute(select(estado.c.sujeito, estado.c.alcancavel))).all()
    }

    await _gravar_leituras(conexao, nome_modulo, resultado, momento)
    grafo = await _conciliar_grafo(conexao, resultado, momento)

    linhas_estado = []
    mudancas = []
    for sujeito, m in medidas.items():
        if "ativo_alcancavel" not in m:
            continue
        vivo = bool(m["ativo_alcancavel"])
        linhas_estado.append(
            {
                "sujeito": sujeito,
                "alcancavel": vivo,
                # `.get` devolvendo None é intencional: o módulo não publica
                # latência de quem não respondeu, e nulo preserva essa verdade.
                "latencia_ms": m.get("ativo_latencia_ms"),
                "perda_pct": m.get("ativo_perda_pacote_pct"),
                "jitter_ms": m.get("ativo_jitter_ms"),
                "qualidade": "incerta" if isolado else "boa",
                "visto_em": momento,
            }
        )
        antes = anterior.get(sujeito)
        # Sob suspeita de isolamento a transição não é registrada: não se sabe
        # se o equipamento caiu ou se o coletor ficou surdo, e escrever a
        # queda seria afirmar a primeira hipótese sem evidência.
        if antes != vivo and not isolado:
            mudancas.append(
                {"sujeito": sujeito, "de": antes, "para": vivo, "em": momento}
            )

    if linhas_estado:
        await conexao.execute(
            pg_insert(estado)
            .values(linhas_estado)
            .on_conflict_do_update(
                index_elements=["sujeito"],
                set_={
                    c: getattr(pg_insert(estado).excluded, c)
                    for c in (
                        "alcancavel",
                        "latencia_ms",
                        "perda_pct",
                        "jitter_ms",
                        "qualidade",
                        "visto_em",
                    )
                },
            )
        )
    if mudancas:
        await conexao.execute(insert(transicao).values(mudancas))

    await _gravar_saude(conexao, nome_modulo, resultado, momento)

    return {
        **grafo,
        "estados": len(linhas_estado),
        "transicoes": len(mudancas),
        "alcancaveis": sum(1 for ln in linhas_estado if ln["alcancavel"]),
        "isolamento_suspeito": int(isolado),
    }


async def _gravar_saude(
    conexao: AsyncConnection,
    nome_modulo: str,
    resultado: ResultadoColeta,
    momento: datetime,
) -> None:
    """Persiste as cinco séries de auto-observação do módulo.

    O carimbo de última coleta bem-sucedida só avança quando houve sucesso —
    é ele que denuncia módulo parado, que de outra forma é indistinguível de
    parque saudável.
    """
    houve_sucesso = any(
        o.metrica == "modulo_ultima_coleta_ok_timestamp" for o in resultado.observacoes
    )
    valores = {
        "modulo": nome_modulo,
        "alvos_total": resultado.alvos_total,
        "alvos_falha": resultado.alvos_falha,
        "duracao_s": resultado.duracao_s,
        "rejeitadas": len(resultado.rejeitadas),
        "atualizado_em": momento,
    }
    if houve_sucesso:
        valores["ultima_coleta_ok"] = momento

    excluida = pg_insert(saude_modulo).excluded
    atualizar = {c: getattr(excluida, c) for c in valores if c != "modulo"}
    await conexao.execute(
        pg_insert(saude_modulo)
        .values(valores)
        .on_conflict_do_update(index_elements=["modulo"], set_=atualizar)
    )


def estado_no_inicio(mudancas, desde, alcancavel_agora: bool) -> bool:
    """Em que estado o equipamento estava quando a janela começou.

    A ordem importa e já custou caro:

    1. Houve transição **antes** da janela? O estado é o `para` da última.
    2. Não houve, mas há transição **dentro**? O estado é o `de` da primeira —
       ela própria diz de onde veio.
    3. Nenhuma transição? Só então o estado corrente vale para trás.

    Pular o passo 2 e cair direto no estado corrente é o defeito que apareceu
    no relatório: um equipamento que passou 12 h de pé e caiu no meio da
    janela era contado como caído desde o início — 0% em vez de 50%. Ele
    errava exatamente nos equipamentos sobre os quais o relatório é feito, e
    acertava nos que nunca mudaram.

    ``mudancas`` precisa vir ordenada por instante.
    """
    anteriores = [m for m in mudancas if m.em <= desde]
    if anteriores:
        return anteriores[-1].para
    dentro = [m for m in mudancas if m.em > desde]
    if dentro:
        return dentro[0].de if dentro[0].de is not None else not dentro[0].para
    return alcancavel_agora


async def disponibilidade(
    conexao: AsyncConnection, sujeito: str, desde: datetime
) -> float | None:
    """Percentual de tempo alcançável desde ``desde``, a partir das transições.

    Devolve ``None`` quando não há observação suficiente no período. Um número
    inventado aqui viraria um SLA que ninguém consegue defender.
    """
    linhas = (
        await conexao.execute(
            select(transicao.c.de, transicao.c.para, transicao.c.em)
            .where(transicao.c.sujeito == sujeito)
            .order_by(transicao.c.em)
        )
    ).all()
    atual = await conexao.execute(
        select(estado.c.alcancavel, estado.c.visto_em).where(estado.c.sujeito == sujeito)
    )
    corrente = atual.first()
    if corrente is None:
        return None

    fim = corrente.visto_em
    if fim <= desde:
        return None

    vivo = estado_no_inicio(linhas, desde, corrente.alcancavel)

    marco = desde
    acumulado = 0.0
    for ln in (x for x in linhas if desde < x.em <= fim):
        if vivo:
            acumulado += (ln.em - marco).total_seconds()
        vivo, marco = ln.para, ln.em
    if vivo:
        acumulado += (fim - marco).total_seconds()

    total = (fim - desde).total_seconds()
    return 100.0 * acumulado / total if total > 0 else None


async def ultimas_transicoes(
    conexao: AsyncConnection, sujeitos: list[str] | None = None, limite: int = 20
) -> list[dict]:
    """As últimas mudanças de estado, mais recentes primeiro.

    É o que a plataforma tem de mais próximo de um histórico de eventos
    enquanto o motor de alarmes não existe — e, diferente de um alarme, cada
    linha aqui é um fato observado, não uma regra que alguém escreveu.
    """
    consulta = select(
        transicao.c.sujeito, transicao.c.de, transicao.c.para, transicao.c.em
    ).order_by(transicao.c.em.desc()).limit(limite)
    if sujeitos:
        consulta = consulta.where(transicao.c.sujeito.in_(sujeitos))
    linhas = (await conexao.execute(consulta)).all()
    return [
        {"sujeito": ln.sujeito, "de": ln.de, "para": ln.para, "em": ln.em}
        for ln in linhas
    ]
