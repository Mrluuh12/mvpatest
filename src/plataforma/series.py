"""Séries para os gráficos — de onde vêm e de onde **não** vêm.

A plataforma guarda a última leitura de cada métrica, não a série. Isso foi
decisão, não esquecimento: o Prometheus do exportador já guarda a série, e
muito melhor; duplicá-la aqui criaria duas verdades sobre o mesmo número. O
gráfico, portanto, **pergunta para quem tem**.

Três origens, e a diferença entre elas importa
----------------------------------------------

**Prometheus** — as métricas que o módulo Rajant lê. A consulta é montada a
partir da mesma tabela que a coleta usa, então gráfico e cartão não podem
divergir: se um mostra o pior SNR entre vizinhos, o outro mostra o mesmo.

**Transições** — a disponibilidade. E aqui não se amostra nada: a tabela
guarda os instantes em que o estado mudou, então a série *exata* é a lista de
faixas. Amostrar de dez em dez minutos perderia uma queda de dois minutos que
está registrada com precisão de segundo.

**A própria plataforma** — o que o nosso coletor publica no ``/metrics`` que o
Prometheus raspa. É o que faz tráfego de porta de switch, latência e perda
poderem virar linha: o dado sempre existiu, faltava chegar a quem guarda
série. A consulta é montada aqui, com a agregação declarada, para o cartão
poder dizer o que está mostrando.

**Nenhuma** — o resto. Um valor de SNMP tem só a última leitura, e o gráfico
diz isso em vez de desenhar uma linha de um ponto só. Linha reta feita de um
dado é pior que gráfico nenhum: parece informação.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.db.coleta import estado_no_inicio
from plataforma.db.esquema import estado, transicao
from plataforma.dicionario import POR_NOME
from plataforma.exportador import PREFIXO
from plataforma.modulos.rajant import POR_METRICA

_JANELA = re.compile(r"^(\d{1,4})([mhd])$")
_SEGUNDOS = {"m": 60, "h": 3600, "d": 86400}

#: Alvo de pontos por gráfico. Mais que isso não cabe em ~700 px de largura e
#: só custa banda; menos esconde variação real.
PONTOS_ALVO = 240


class JanelaInvalida(ValueError):
    pass


def segundos_da_janela(texto: str) -> int:
    casa = _JANELA.match((texto or "").strip())
    if not casa:
        raise JanelaInvalida(
            f"janela {texto!r} não entendida — use 30m, 6h, 7d (máximo 90d)"
        )
    total = int(casa.group(1)) * _SEGUNDOS[casa.group(2)]
    if not 60 <= total <= 90 * 86400:
        raise JanelaInvalida("janela precisa ficar entre 1 minuto e 90 dias")
    return total


def passo_para(segundos: int) -> int:
    """Passo que cabe em ``PONTOS_ALVO`` sem descer abaixo de 15 s.

    Pedir passo menor que o intervalo de coleta não traz detalhe nenhum: só
    devolve o mesmo valor repetido e faz o Prometheus trabalhar à toa.
    """
    return max(15, round(segundos / PONTOS_ALVO))


@dataclass
class Serie:
    metrica: str
    tipo: str  # "numerica" | "estados" | "ausente"
    unidade: str = ""
    #: numerica: [[timestamp, valor], ...]
    pontos: list[list[float]] = field(default_factory=list)
    #: estados: [[inicio, fim, alcancavel|None], ...]
    faixas: list[dict] = field(default_factory=list)
    #: A consulta que produziu isto. Gráfico que não pode ser conferido é
    #: gráfico em que ninguém confia depois da primeira surpresa.
    consulta: str = ""
    origem: str = ""
    agregacao: str = ""
    motivo: str = ""

    def para_json(self) -> dict[str, Any]:
        saida = {
            "metrica": self.metrica,
            "tipo": self.tipo,
            "unidade": self.unidade,
            "consulta": self.consulta,
            "origem": self.origem,
            "agregacao": self.agregacao,
            "motivo": self.motivo,
        }
        if self.tipo == "numerica":
            saida["pontos"] = self.pontos
        elif self.tipo == "estados":
            saida["faixas"] = self.faixas
        return saida


def _unidade(metrica: str) -> str:
    m = POR_NOME.get(metrica)
    return m.unidade if m else ""


async def _intervalo(
    url: str,
    promql: str,
    inicio: datetime,
    fim: datetime,
    passo: int,
    transporte: httpx.AsyncBaseTransport | None = None,
    fator: float = 1.0,
) -> list[list[float]]:
    async with httpx.AsyncClient(transport=transporte) as cliente:
        resposta = await cliente.get(
            f"{url.rstrip('/')}/api/v1/query_range",
            params={
                "query": promql,
                "start": inicio.timestamp(),
                "end": fim.timestamp(),
                "step": passo,
            },
            timeout=20.0,
        )
    resposta.raise_for_status()
    corpo = resposta.json()
    if corpo.get("status") != "success":
        raise RuntimeError(corpo.get("error", "consulta recusada pelo Prometheus"))
    pontos: list[list[float]] = []
    for serie in corpo["data"]["result"]:
        for instante, valor in serie.get("values", []):
            try:
                v = float(valor)
            except (TypeError, ValueError):
                continue
            if v != v:  # NaN é "sem dado", não zero
                continue
            pontos.append([float(instante), v * fator])
    pontos.sort(key=lambda p: p[0])
    return pontos


async def de_prometheus(
    url: str,
    metrica: str,
    ip: str,
    segundos: int,
    agora: datetime | None = None,
    transporte: httpx.AsyncBaseTransport | None = None,
) -> Serie:
    """Consulta de intervalo, montada da mesma tabela que a coleta usa."""
    consulta = POR_METRICA[metrica]
    fim = agora or datetime.now(UTC)
    inicio = fim - timedelta(seconds=segundos)
    promql = consulta.promql(f'ip="{ip}"')
    passo = passo_para(segundos)
    pontos = await _intervalo(url, promql, inicio, fim, passo, transporte, consulta.fator)

    return Serie(
        metrica=metrica,
        tipo="numerica",
        unidade=_unidade(metrica),
        pontos=pontos,
        consulta=f"{promql} [passo {passo}s]",
        origem="prometheus",
        agregacao=consulta.agregacao,
    )


async def de_transicoes(
    conexao: AsyncConnection, sujeito: str, segundos: int, agora: datetime | None = None
) -> Serie:
    """Disponibilidade como faixas, não como amostras.

    A tabela guarda o instante exato de cada mudança. Amostrar de dez em dez
    minutos perderia uma queda de dois minutos que está registrada com
    precisão de segundo — e o gráfico mentiria por omissão.
    """
    fim = agora or datetime.now(UTC)
    inicio = fim - timedelta(seconds=segundos)

    mudancas = (
        await conexao.execute(
            select(transicao.c.de, transicao.c.para, transicao.c.em)
            .where(transicao.c.sujeito == sujeito)
            .order_by(transicao.c.em)
        )
    ).all()
    corrente = (
        await conexao.execute(
            select(estado.c.alcancavel, estado.c.qualidade, estado.c.visto_em).where(
                estado.c.sujeito == sujeito
            )
        )
    ).first()
    if corrente is None:
        return Serie(
            metrica="ativo_alcancavel",
            tipo="ausente",
            motivo="este equipamento nunca foi sondado",
        )

    vivo = estado_no_inicio(mudancas, inicio, corrente.alcancavel)

    faixas: list[dict] = []
    marco = inicio
    for m in (x for x in mudancas if inicio < x.em <= fim):
        faixas.append(
            {"inicio": marco.timestamp(), "fim": m.em.timestamp(), "alcancavel": vivo}
        )
        vivo, marco = m.para, m.em
    faixas.append(
        {
            "inicio": marco.timestamp(),
            "fim": fim.timestamp(),
            "alcancavel": vivo,
            # A última faixa herda a qualidade corrente: sob suspeita de
            # isolamento, "sem resposta" ainda não é afirmação de queda, e o
            # desenho precisa poder mostrar essa diferença.
            "incerta": corrente.qualidade == "incerta",
        }
    )
    return Serie(
        metrica="ativo_alcancavel",
        tipo="estados",
        faixas=faixas,
        consulta=f"transições de {sujeito}",
        origem="transicoes",
    )


@dataclass(frozen=True)
class Local:
    """Como consultar uma métrica que a própria plataforma publica.

    ``dimensao`` é o rótulo em que a métrica se abre: uma porta de switch tem
    48 séries por equipamento. Sem escolher uma, a consulta agrega — e
    ``agregacao`` é a frase que o cartão mostra, porque número agregado que não
    diz como foi agregado vira medida direta na cabeça de quem lê.

    ``agregacao_impossivel`` marca o caso em que somar não significaria nada:
    o código de estado de uma porta. Aí a resposta é pedir a porta, não
    inventar um agregado.
    """

    agregador: str = ""
    agregacao: str = ""
    #: Contador precisa de ``rate()``: um contador de bytes desenhado cru é
    #: uma rampa que só sobe, e ninguém consegue ler tráfego nela.
    taxa: bool = False
    unidade: str = ""
    dimensao: str = ""
    agregacao_impossivel: str = ""


_SOMA_PORTAS = "soma de todas as portas"

#: O que a plataforma publica e sabe desenhar. Quem não está aqui continua
#: caindo em `sem_serie` com o motivo — e é assim que se descobre o que falta.
LOCAIS: dict[str, Local] = {
    "iface_bytes_rx": Local("sum", f"{_SOMA_PORTAS}, por segundo", True, "B/s", "porta"),
    "iface_bytes_tx": Local("sum", f"{_SOMA_PORTAS}, por segundo", True, "B/s", "porta"),
    "iface_pacotes_rx": Local("sum", f"{_SOMA_PORTAS}, por segundo", True, "p/s", "porta"),
    "iface_pacotes_tx": Local("sum", f"{_SOMA_PORTAS}, por segundo", True, "p/s", "porta"),
    "iface_erros_rx": Local("sum", f"{_SOMA_PORTAS}, por segundo", True, "erros/s", "porta"),
    "iface_erros_tx": Local("sum", f"{_SOMA_PORTAS}, por segundo", True, "erros/s", "porta"),
    "iface_descartes_rx": Local("sum", f"{_SOMA_PORTAS}, por segundo", True, "desc/s", "porta"),
    "iface_descartes_tx": Local("sum", f"{_SOMA_PORTAS}, por segundo", True, "desc/s", "porta"),
    "iface_velocidade_bps": Local("sum", _SOMA_PORTAS, dimensao="porta"),
    "iface_status_oper": Local(
        dimensao="porta",
        agregacao_impossivel=(
            "somar códigos de estado de 48 portas não significaria nada — "
            "escolha uma porta no cartão"
        ),
    ),
    "iface_status_admin": Local(
        dimensao="porta",
        agregacao_impossivel=(
            "somar códigos de estado de 48 portas não significaria nada — "
            "escolha uma porta no cartão"
        ),
    ),
    "ativo_latencia_ms": Local(),
    "ativo_perda_pacote_pct": Local(),
    "ativo_jitter_ms": Local(),
    "ativo_uptime_s": Local(),
    "disp_cpu_pct": Local(),
    "disp_memoria_pct": Local(),
    "disp_temperatura_c": Local(),
}


#: Piso da janela do ``rate()``. Quatro minutos porque a plataforma **não
#: sabe** de quanto em quanto tempo o Prometheus a raspa: com raspagem de 60 s,
#: uma janela de 60 s teria um ponto só e o ``rate`` devolveria vazio — gráfico
#: em branco sem nada explicar. Medido contra um Prometheus real raspando a
#: cada 15 s: janela de 60 s deu 15,1 MB/s onde o tráfego era 12,5, porque
#: extrapolou de poucas amostras; com 120 s, 12,95. A janela curta não é mais
#: fiel, é mais barulhenta.
PISO_JANELA_TAXA_S = 240


def _janela_de_taxa(passo: int) -> int:
    """Janela do ``rate()``: quatro passos, respeitado o piso.

    Suaviza mais do que o gráfico de 30 minutos gostaria, e é de propósito:
    errar para o lado do liso mostra a tendência certa; errar para o lado do
    curto mostra um número inventado, ou nada.
    """
    return max(PISO_JANELA_TAXA_S, passo * 4)


async def da_plataforma(
    url: str,
    metrica: str,
    chave: str,
    segundos: int,
    dimensao: str = "",
    agora: datetime | None = None,
    transporte: httpx.AsyncBaseTransport | None = None,
) -> Serie:
    """Série de uma métrica que a própria plataforma publicou no Prometheus.

    O filtro é por ``dispositivo`` — a chave do inventário —, não por IP: IP
    muda de dono, chave não.
    """
    local = LOCAIS[metrica]
    if local.dimensao and not dimensao and local.agregacao_impossivel:
        vazia = sem_serie(metrica)
        vazia.motivo = local.agregacao_impossivel
        return vazia

    fim = agora or datetime.now(UTC)
    inicio = fim - timedelta(seconds=segundos)
    passo = passo_para(segundos)

    filtro = f'dispositivo="{chave}"'
    if local.dimensao and dimensao:
        filtro += f', {local.dimensao}="{dimensao}"'
    alvo = f"{PREFIXO}{metrica}{{{filtro}}}"
    if local.taxa:
        alvo = f"rate({alvo}[{_janela_de_taxa(passo)}s])"
    promql = f"{local.agregador} by (dispositivo) ({alvo})" if local.agregador else alvo
    # Uma porta escolhida não é agregado: a frase que descreveria a agregação
    # seria falsa, e frase falsa embaixo do gráfico é pior que frase nenhuma.
    agregacao = "" if dimensao else local.agregacao

    pontos = await _intervalo(url, promql, inicio, fim, passo, transporte)
    return Serie(
        metrica=metrica,
        tipo="numerica",
        unidade=local.unidade or _unidade(metrica),
        pontos=pontos,
        consulta=f"{promql} [passo {passo}s]",
        origem="plataforma",
        agregacao=agregacao,
    )


def sem_serie(metrica: str) -> Serie:
    """A resposta honesta para métrica que só tem última leitura."""
    conhecida = metrica in POR_NOME
    return Serie(
        metrica=metrica,
        tipo="ausente",
        unidade=_unidade(metrica),
        motivo=(
            "esta métrica só tem a última leitura — a plataforma não guarda "
            "série dela, e desenhar uma linha de um ponto só pareceria "
            "informação"
            if conhecida
            else f"{metrica!r} não está no dicionário canônico"
        ),
    )


def tem_serie(metrica: str) -> bool:
    return metrica in POR_METRICA or metrica in LOCAIS or metrica == "ativo_alcancavel"


def dimensao_de(metrica: str) -> str:
    """Em que rótulo esta métrica se abre, se é que se abre."""
    local = LOCAIS.get(metrica)
    return local.dimensao if local else ""


__all__ = [
    "LOCAIS",
    "PISO_JANELA_TAXA_S",
    "PONTOS_ALVO",
    "Local",
    "JanelaInvalida",
    "Serie",
    "da_plataforma",
    "de_prometheus",
    "dimensao_de",
    "de_transicoes",
    "passo_para",
    "segundos_da_janela",
    "sem_serie",
    "tem_serie",
]
