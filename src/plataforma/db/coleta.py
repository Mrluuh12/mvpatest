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

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.modulos.contrato import ResultadoColeta

from .esquema import estado, saude_modulo, transicao

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


async def disponibilidade(
    conexao: AsyncConnection, sujeito: str, desde: datetime
) -> float | None:
    """Percentual de tempo alcançável desde ``desde``, a partir das transições.

    Devolve ``None`` quando não há observação suficiente no período. Um número
    inventado aqui viraria um SLA que ninguém consegue defender.
    """
    linhas = (
        await conexao.execute(
            select(transicao.c.para, transicao.c.em)
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

    # Estado no início da janela: o da última transição anterior a `desde`;
    # sem nenhuma, assume-se o estado corrente retroagido.
    anteriores = [ln for ln in linhas if ln.em <= desde]
    vivo = anteriores[-1].para if anteriores else corrente.alcancavel

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
