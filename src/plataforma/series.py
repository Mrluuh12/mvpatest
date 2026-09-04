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
            pontos.append([float(instante), v * consulta.fator])
    pontos.sort(key=lambda p: p[0])

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
    return metrica in POR_METRICA or metrica == "ativo_alcancavel"


__all__ = [
    "PONTOS_ALVO",
    "JanelaInvalida",
    "Serie",
    "de_prometheus",
    "de_transicoes",
    "passo_para",
    "segundos_da_janela",
    "sem_serie",
    "tem_serie",
]
