"""Capacidade e previsão: quando o enlace satura.

É o relatório que o SolarWinds chama de *capacity forecasting*, e o que ele
faz é simples: ajusta uma reta ao histórico e diz em quantos dias a métrica
cruza o limiar. A documentação da própria SolarWinds avisa do defeito do
método — *"uses a linear approach... a single big change will impact heavily on
the trend"* — e o aviso é justo. Uma mudança de rota, uma obra, um caminhão
novo na frota: qualquer degrau vira "vai saturar em 4 dias".

Aqui a projeção existe, e vem com o que falta em quase toda ferramenta: **o
tamanho do histórico e a qualidade do ajuste, na mesma linha do número**. Uma
previsão feita sobre seis horas de série não é uma previsão, é uma
extrapolação de ruído — e quem lê precisa ver isso sem ter de perguntar.

A matéria-prima só existe desde que a plataforma passou a publicar no
Prometheus. Antes, tráfego de porta não tinha série nenhuma.
"""

from __future__ import annotations

import math
import os
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.exportador import PREFIXO

from .modelo import Coluna, Relatorio, TipoColuna

VAR_PROMETHEUS = "PLATAFORMA_PROMETHEUS"

#: Abaixo disto a reta descreve o ruído, não a tendência. Não é um número
#: mágico: é o ponto a partir do qual vale a pena mostrar a projeção em vez de
#: recusá-la, e está dito na ressalva de todo relatório que o usa.
HISTORICO_MINIMO_H = 24.0

#: Um ajuste ruim é um ajuste que não descreve os pontos. Abaixo deste R² a
#: linha ainda é desenhada, mas o relatório diz que ela não explica a série.
R2_CONFIAVEL = 0.5


class SemSeries(RuntimeError):
    """Não há de onde tirar histórico — e a recusa diz o que ligar."""


def _url() -> str:
    if url := os.environ.get(VAR_PROMETHEUS):
        return url
    raise SemSeries(
        "previsão precisa de histórico, e o histórico está no Prometheus — "
        f"defina {VAR_PROMETHEUS} e ligue a exportação (/metrics)"
    )


async def _consultar(promql: str, desde: datetime, ate: datetime, passo: int) -> list[dict]:
    async with httpx.AsyncClient() as cliente:
        resposta = await cliente.get(
            f"{_url().rstrip('/')}/api/v1/query_range",
            params={
                "query": promql, "start": desde.timestamp(),
                "end": ate.timestamp(), "step": passo,
            },
            timeout=30.0,
        )
    resposta.raise_for_status()
    corpo = resposta.json()
    if corpo.get("status") != "success":
        raise SemSeries(corpo.get("error", "consulta recusada pelo Prometheus"))
    return corpo["data"]["result"]


def _pontos(serie: dict) -> list[tuple[float, float]]:
    saida = []
    for instante, valor in serie.get("values", []):
        try:
            v = float(valor)
        except (TypeError, ValueError):
            continue
        if v == v:  # NaN é ausência, não zero
            saida.append((float(instante), v))
    return saida


def tendencia(pontos: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Mínimos quadrados: devolve ``(inclinacao_por_s, intercepto, r2)``.

    ``r2`` é o que separa "vai saturar em 12 dias" de "os pontos não descrevem
    reta nenhuma". Sem ele, a projeção parece igualmente confiável nos dois
    casos — e é aí que a previsão vira armadilha.
    """
    n = len(pontos)
    if n < 2:
        return 0.0, pontos[0][1] if pontos else 0.0, 0.0
    mx = sum(p[0] for p in pontos) / n
    my = sum(p[1] for p in pontos) / n
    sxx = sum((p[0] - mx) ** 2 for p in pontos)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pontos)
    if sxx == 0:
        return 0.0, my, 0.0
    a = sxy / sxx
    b = my - a * mx
    syy = sum((p[1] - my) ** 2 for p in pontos)
    r2 = 0.0 if syy == 0 else max(0.0, 1 - sum((p[1] - (a * p[0] + b)) ** 2 for p in pontos) / syy)
    return a, b, r2


def dias_ate(atual: float, por_dia: float, limiar: float) -> float | None:
    """Dias até cruzar o limiar, ou ``None`` quando a pergunta não se aplica.

    Três casos devolvem ``None``, e nenhum deles é "zero dias": já passou do
    limiar, não está crescendo, ou cresce tão devagar que a data cairia fora de
    qualquer horizonte de planejamento. Devolver um número gigante seria
    tecnicamente certo e praticamente uma mentira.
    """
    if atual >= limiar or por_dia <= 0:
        return None
    dias = (limiar - atual) / por_dia
    return None if dias > 3650 else round(dias, 1)


async def previsao_interfaces(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """Quando cada porta cruza o limiar de utilização, se o ritmo continuar."""
    aviso = float(p.get("aviso_pct") or 70)
    critico = float(p.get("critico_pct") or 90)
    limite = max(1, int(p.get("limite") or 25))
    horas = max(1.0, (ate - desde).total_seconds() / 3600)
    passo = max(60, int((ate - desde).total_seconds() / 500))

    # Utilização = taxa de bytes × 8 sobre a velocidade nominal da porta. A
    # velocidade vem da mesma coleta, e porta sem velocidade declarada fica de
    # fora: dividir por um valor inventado daria percentual inventado.
    janela = max(300, passo * 4)
    promql = (
        f"100 * 8 * rate({PREFIXO}iface_bytes_rx[{janela}s]) "
        f"/ on (dispositivo, porta) group_left() {PREFIXO}iface_velocidade_bps > 0"
    )
    series = await _consultar(promql, desde, ate, passo)

    linhas, sem_ajuste, curtos = [], 0, 0
    for s in series:
        pontos = _pontos(s)
        if len(pontos) < 2:
            curtos += 1
            continue
        m = s["metric"]
        atual = pontos[-1][1]
        pico = max(v for _, v in pontos)
        por_s, _, r2 = tendencia(pontos)
        por_dia = por_s * 86400
        if r2 < R2_CONFIAVEL:
            sem_ajuste += 1
        linhas.append(
            {
                "equipamento": m.get("nome", m.get("dispositivo", "?")),
                "porta": m.get("porta", "—"),
                "ativo": m.get("ativo", ""),
                "atual_pct": round(atual, 2),
                "pico_pct": round(pico, 2),
                "variacao_dia_pp": round(por_dia, 3),
                "dias_ate_aviso": dias_ate(atual, por_dia, aviso),
                "dias_ate_critico": dias_ate(atual, por_dia, critico),
                "ajuste_r2": round(r2, 2),
                "confianca": _confianca(horas, r2),
            }
        )

    # Ordena por urgência: quem chega antes ao crítico vem primeiro; quem não
    # chega nunca vai para o fim, ordenado pela utilização de hoje.
    linhas.sort(
        key=lambda x: (
            x["dias_ate_critico"] if x["dias_ate_critico"] is not None else math.inf,
            -x["atual_pct"],
        )
    )

    r = Relatorio(
        nome="previsao_interfaces",
        titulo=f"Previsão de saturação por porta (aviso {aviso:.0f}%, crítico {critico:.0f}%)",
        desde=desde, ate=ate, linhas=linhas[:limite], parametros=p,
        colunas=(
            Coluna("equipamento", "Equipamento"),
            Coluna("porta", "Porta"),
            Coluna("ativo", "Ativo"),
            Coluna("atual_pct", "Utilização agora", TipoColuna.PERCENTUAL),
            Coluna("pico_pct", "Pico no período", TipoColuna.PERCENTUAL),
            Coluna("variacao_dia_pp", "Variação por dia", TipoColuna.NUMERO,
                   unidade="pp/dia", soma=False),
            Coluna("dias_ate_aviso", "Dias até o aviso", TipoColuna.NUMERO, soma=False),
            Coluna("dias_ate_critico", "Dias até o crítico", TipoColuna.NUMERO, soma=False),
            Coluna("ajuste_r2", "Ajuste (R²)", TipoColuna.NUMERO, soma=False),
            Coluna("confianca", "Confiança", TipoColuna.SELO),
        ),
    )

    # O resumo é a linha que as pessoas leem — muitas vezes a única. Anunciar
    # "satura em 0,7 dias" a partir de um ajuste que o próprio relatório diz não
    # valer é a armadilha clássica da previsão de capacidade: o número vira meta
    # de reunião e a ressalva fica na tabela que ninguém abriu. Por isso o
    # destaque só sai quando a projeção se sustenta.
    urgentes = [x for x in r.linhas if x["dias_ate_critico"] is not None]
    confiaveis = [x for x in urgentes if x["confianca"] == "razoável"]
    if confiaveis:
        pri = confiaveis[0]
        r.resumo = (
            f"{pri['equipamento']} {pri['porta']} é a primeira a chegar ao crítico: "
            f"{pri['dias_ate_critico']} dias no ritmo atual."
        )
    elif urgentes:
        r.resumo = (
            f"{len(urgentes)} portas têm projeção de saturação, e nenhuma delas se "
            "sustenta: ou a série é curta demais, ou os pontos não descrevem uma "
            "reta. Os números da tabela ainda não servem para decidir."
        )
    elif r.linhas:
        r.resumo = "nenhuma porta caminha para saturar no horizonte de planejamento."

    r.notas.append(
        "Projeção por reta ajustada ao período: um degrau isolado domina a "
        "inclinação. Confira a coluna de ajuste antes de usar."
    )
    if horas < HISTORICO_MINIMO_H:
        r.notas.append(
            f"{horas:.0f} h de história, abaixo das {HISTORICO_MINIMO_H:.0f} h mínimas "
            "para uma tendência. Projeções marcadas como não confiáveis."
        )
    if sem_ajuste:
        r.notas.append(
            f"{sem_ajuste} portas com R² abaixo de {R2_CONFIAVEL} — projeção não usável."
        )
    if curtos:
        r.notas.append(
            f"{curtos} portas fora: menos de dois pontos no período."
        )
    if len(linhas) > limite:
        r.notas.append(f"{len(linhas) - limite} portas além das {limite} mostradas.")
    if not linhas:
        # Duas causas muito diferentes davam a mesma frase, e a frase acusava a
        # errada: pedir 7 dias de uma série que tem 30 minutos deixa a tabela
        # vazia, e dizer "sem velocidade nominal" manda quem lê procurar defeito
        # no lugar onde não há nenhum.
        r.notas.append(
            f"a janela pedida tem {horas:.0f} h e a série não a cobre — todas as "
            "portas ficaram com menos de dois pontos. Escolha uma janela que caiba "
            "no histórico que já existe."
            if curtos
            else "nenhuma porta com velocidade nominal conhecida e tráfego medido. "
            "Sem `iface_velocidade_bps` não há como transformar bytes em "
            "percentual de utilização — e inventar a velocidade inventaria o "
            "percentual."
        )
    return r


def _confianca(horas: float, r2: float) -> str:
    if horas < HISTORICO_MINIMO_H:
        return "história curta"
    if r2 < R2_CONFIAVEL:
        return "ajuste fraco"
    return "razoável"


async def top_interfaces(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """As portas que mais passam tráfego, mais erram e mais descartam.

    O "Top N" do SolarWinds, com uma diferença: erro e descarte vêm na mesma
    linha do tráfego. Porta cheia é assunto de capacidade; porta que erra é
    assunto de cabo, e as duas conversas começam pela mesma tabela.
    """
    limite = max(1, int(p.get("limite") or 20))
    periodo = max(60, int((ate - desde).total_seconds()))

    async def media(metrica: str) -> dict[tuple[str, str], float]:
        """Taxa média no período inteiro.

        ``rate(x[periodo])`` **é** a média: o incremento do contador dividido
        pela duração. A primeira versão disto empilhava ``avg_over_time`` sobre
        uma subconsulta de ``rate``, o que parecia mais cuidadoso e era pior —
        média de médias com janelas que se sobrepõem, sensível a buracos na
        série. Contra dados reais devolveu 256 Mb/s onde o tráfego era 728.
        """
        series = await _consultar(
            f"rate({PREFIXO}{metrica}[{periodo}s])", ate, ate, periodo
        )
        saida = {}
        for s in series:
            pontos = _pontos(s)
            if pontos:
                m = s["metric"]
                saida[(m.get("nome", m.get("dispositivo", "?")), m.get("porta", "—"))] = (
                    pontos[-1][1]
                )
        return saida

    rx = await media("iface_bytes_rx")
    tx = await media("iface_bytes_tx")
    erros = await media("iface_erros_rx")
    descartes = await media("iface_descartes_rx")

    linhas = []
    for alvo in sorted(set(rx) | set(tx) | set(erros) | set(descartes)):
        linhas.append(
            {
                "equipamento": alvo[0],
                "porta": alvo[1],
                "entrada_mbps": round(rx.get(alvo, 0.0) * 8 / 1e6, 3),
                "saida_mbps": round(tx.get(alvo, 0.0) * 8 / 1e6, 3),
                "erros_s": round(erros.get(alvo, 0.0), 4),
                "descartes_s": round(descartes.get(alvo, 0.0), 4),
            }
        )
    ordem = p.get("ordenar") or "entrada_mbps"
    linhas.sort(key=lambda x: -x.get(ordem, 0))

    r = Relatorio(
        nome="top_interfaces",
        titulo=f"As {limite} portas com maior {ordem.replace('_', ' ')}",
        desde=desde, ate=ate, linhas=linhas[:limite], parametros=p,
        colunas=(
            Coluna("equipamento", "Equipamento"),
            Coluna("porta", "Porta"),
            Coluna("entrada_mbps", "Entrada", TipoColuna.NUMERO, unidade="Mb/s"),
            Coluna("saida_mbps", "Saída", TipoColuna.NUMERO, unidade="Mb/s"),
            Coluna("erros_s", "Erros", TipoColuna.NUMERO, unidade="/s"),
            Coluna("descartes_s", "Descartes", TipoColuna.NUMERO, unidade="/s"),
        ),
    )
    r.somar()
    r.notas.append(
        "Médias do período, não picos. O pico está no gráfico da porta, na ficha "
        "do equipamento."
    )
    if com_erro := [x for x in linhas if x["erros_s"] > 0]:
        r.notas.append(
            f"{len(com_erro)} portas com erro maior que zero."
        )
    return r
