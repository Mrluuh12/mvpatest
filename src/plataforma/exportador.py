"""Exportador Prometheus: a plataforma publica o que coletou.

Por que isto existe
-------------------

A plataforma guarda a **última** leitura de cada métrica, não a série — e isso
foi decisão, não esquecimento: o Prometheus já guarda série, e muito melhor.
Só que a consequência apareceu no gráfico: das 61 métricas canônicas, apenas
19 podiam virar linha, porque só as do Rajant passavam por um Prometheus.
Tráfego de porta de switch, que é o gráfico mais aberto de qualquer
plataforma de rede, não existia.

A saída não é guardar série aqui. É **entregar o número a quem guarda série**.
O coletor lê o parque, esta rota publica no formato que o Prometheus raspa, e
a série continua tendo um dono só.

O que **não** é publicado, e por quê
------------------------------------

**O que já veio de um Prometheus.** O módulo Rajant lê do Prometheus do seu
exportador. Republicar aquilo aqui fecharia um laço: o mesmo número entraria
de novo com outro nome, e passariam a existir duas verdades sobre a mesma
grandeza — exatamente o que a decisão original evitou. Quem sabe disso é o
manifesto do módulo, no campo ``serie_externa``.

**A disponibilidade também sai daqui, e não é redundância inútil.**
Alcançabilidade, latência, perda e jitter não moram em ``leitura``: moram em
``estado``, porque viram transição. Se o exportador só olhasse ``leitura``, o
gráfico de latência — que é dos mais pedidos — continuaria impossível. Então
``estado`` é lido também.

Sobre ``ativo_alcancavel`` há uma ressalva honesta: o registro **exato** de
quando o equipamento caiu está na tabela de transições, com precisão de
segundo, e é dela que sai o gráfico de disponibilidade e o relatório. A série
publicada aqui é *amostrada* na cadência da raspagem, e serve para alarme e
correlação no Prometheus — não para contar percentual. Está escrito no HELP,
para quem consultar de fora não descobrir a diferença por acidente.

**Leitura velha.** Uma amostra que o Prometheus raspa vale como se fosse de
agora, e é assim que um coletor parado vira uma linha reta que parece
saudável. Passada a validade, a leitura simplesmente **não sai** — o
Prometheus marca a série como obsoleta e o gráfico mostra buraco, que é a
verdade. Quantas ficaram de fora é publicado como métrica: omissão silenciosa
seria o mesmo defeito com outra roupa.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.db.esquema import ativo, dispositivo, estado, identificador, leitura
from plataforma.db.repositorio_pg import campos_vencedores
from plataforma.dicionario import POR_NOME, Tipo

#: Prefixo de espaço de nomes. O nome canônico continua sendo o contrato —
#: ``rf_snr_db`` é ``rf_snr_db`` —, mas no Prometheus ele convive com o
#: ``node_exporter`` de alguém, e métrica sem dono é métrica que colide.
PREFIXO = "plataforma_"

#: Depois de quanto tempo uma leitura deixa de ser publicada.
#:
#: O módulo mais lento hoje roda a cada 2 minutos. Cinco dá folga para um
#: ciclo perdido sem que o Prometheus registre dado velho como se fosse de
#: agora.
VALIDADE_PADRAO_S = 300

_TIPO_PROM = {Tipo.MEDIDA: "gauge", Tipo.CONTADOR: "counter", Tipo.ESTADO: "gauge"}
_NOME_INVALIDO = re.compile(r"[^a-zA-Z0-9_]")

#: Rótulos que descrevem a leitura e já viram coluna própria; não repetir.
_ROTULOS_INTERNOS = frozenset({"dispositivo", "fonte"})

#: Colunas de ``estado`` que viram métrica canônica. ``alcancavel`` é booleano
#: e vira 1/0; as demais podem ser nulas, e nulo **não** vira zero: some.
_DE_ESTADO: tuple[tuple[str, str], ...] = (
    ("alcancavel", "ativo_alcancavel"),
    ("latencia_ms", "ativo_latencia_ms"),
    ("perda_pct", "ativo_perda_pacote_pct"),
    ("jitter_ms", "ativo_jitter_ms"),
)

#: Ressalva que viaja no HELP da série amostrada de disponibilidade.
_RESSALVA_ALCANCE = (
    " Amostrada na cadência da raspagem; o registro exato de cada mudança está"
    " na tabela de transições, e é de lá que saem o relatório e o gráfico de"
    " disponibilidade."
)


def manifestos() -> dict[str, Any]:
    """Manifestos dos módulos conhecidos, sem instanciar nenhum.

    Importado aqui dentro de propósito: o módulo SNMP arrasta o pysnmp, e a
    rota de métricas não deveria custar isso a cada processo que só serve
    tela.
    """
    from plataforma.modulos import icmp, rajant, snmp

    return {m.MANIFESTO.nome: m.MANIFESTO for m in (icmp, rajant, snmp)}


def _escapar(valor: str) -> str:
    return valor.replace("\\", r"\\").replace('"', r"\"").replace("\n", r"\n")


def _nome_de_rotulo(bruto: str) -> str:
    limpo = _NOME_INVALIDO.sub("_", bruto)
    return limpo if limpo[:1].isalpha() or limpo[:1] == "_" else f"_{limpo}"


@dataclass
class Amostra:
    metrica: str
    valor: float
    rotulos: dict[str, str] = field(default_factory=dict)

    def linha(self) -> str:
        pares = ",".join(
            f'{k}="{_escapar(str(v))}"' for k, v in sorted(self.rotulos.items()) if v != ""
        )
        return f"{PREFIXO}{self.metrica}{{{pares}}} {self.valor!r}"


@dataclass
class Exportacao:
    amostras: list[Amostra]
    omitidas_velhas: int
    omitidas_externas: int
    duracao_s: float
    validade_s: int


async def _rotulos_do_parque(conexao: AsyncConnection) -> dict[str, dict[str, str]]:
    """Identidade de negócio de cada dispositivo, por chave.

    É o que transforma ``rf_rssi_dbm`` numa pergunta de operação:
    ``avg by (funcao_negocio) (...)`` só responde porque a função de negócio
    viaja junto com a amostra.
    """
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

    ips = dict(
        (
            await conexao.execute(
                select(identificador.c.dispositivo_chave, identificador.c.valor).where(
                    identificador.c.tipo == "ip"
                )
            )
        ).all()
    )

    # A função de negócio mora em `campo`, com precedência entre origens, e a
    # precedência é o ponto: uma correção humana vence a derivação automática,
    # e é justamente a correção que precisa chegar ao gráfico. Por isso passa
    # pelo mesmo resolvedor que a tela usa, e não por um SELECT à parte que
    # daria a resposta antiga.
    vencedores = await campos_vencedores(conexao)

    saida: dict[str, dict[str, str]] = {}
    for ln in linhas:
        fn = (
            vencedores.get(f"ativo:{ln.ativo_id}", {}).get("funcao_negocio", ("", ""))[0]
            if ln.ativo_id
            else ""
        )
        saida[ln.chave] = {
            "dispositivo": ln.chave,
            "nome": ln.nome_canonico,
            "papel": ln.papel,
            "zona": ln.zona,
            "ativo": ln.ativo_id or "",
            "frota": ln.frota or "",
            "funcao_negocio": str(fn) if fn else "",
            "ip": ips.get(ln.chave, ""),
        }
    return saida


def _rotulos_do_sujeito(sujeito: str, parque: dict[str, dict[str, str]]) -> dict[str, str]:
    """Sujeito vira rótulos — e sujeito que não se reconhece não some.

    Quatro formas convivem hoje: dispositivo, porta (``chave/porta``), enlace
    (``enlace:a>b``) e o próprio módulo (``modulo:icmp``). O que não casar com
    nenhuma sai com o sujeito cru: exportar sem rótulo bonito é ruim, exportar
    de menos é perder dado sem avisar.
    """
    if achado := parque.get(sujeito):
        return dict(achado)
    if sujeito.startswith("modulo:"):
        return {"modulo": sujeito.removeprefix("modulo:")}
    if sujeito.startswith("enlace:"):
        origem, _, destino = sujeito.removeprefix("enlace:").partition(">")
        base = dict(parque.get(origem, {}))
        base.update({"origem": origem, "destino": destino})
        return base
    return {"sujeito": sujeito}


async def _amostras_de_estado(
    conexao: AsyncConnection,
    parque: dict[str, dict[str, str]],
    corte: datetime,
) -> tuple[list[Amostra], int]:
    """Disponibilidade e seus três acompanhantes, vindos de ``estado``."""
    linhas = (
        await conexao.execute(
            select(
                estado.c.sujeito,
                estado.c.alcancavel,
                estado.c.latencia_ms,
                estado.c.perda_pct,
                estado.c.jitter_ms,
                estado.c.qualidade,
                estado.c.visto_em,
            )
        )
    ).all()

    amostras: list[Amostra] = []
    velhas = 0
    for ln in linhas:
        if ln.visto_em < corte:
            velhas += 1
            continue
        base = _rotulos_do_sujeito(ln.sujeito, parque)
        base["modulo"] = "icmp"
        base["qualidade"] = ln.qualidade
        for coluna, metrica in _DE_ESTADO:
            valor = getattr(ln, coluna)
            # Nulo é ausência de medição, não zero. Latência zero afirmaria
            # resposta instantânea — número plausível e errado, que meses
            # depois aparece num relatório que ninguém consegue explicar.
            if valor is None:
                continue
            amostras.append(
                Amostra(metrica=metrica, valor=float(valor), rotulos=dict(base))
            )
    return amostras, velhas


async def exportar(
    conexao: AsyncConnection,
    agora: datetime | None = None,
    validade_s: int = VALIDADE_PADRAO_S,
) -> Exportacao:
    inicio = time.perf_counter()
    agora = agora or datetime.now(UTC)
    corte = agora - timedelta(seconds=validade_s)

    externos = {n for n, m in manifestos().items() if m.serie_externa}
    parque = await _rotulos_do_parque(conexao)

    linhas = (
        await conexao.execute(
            select(
                leitura.c.sujeito,
                leitura.c.metrica,
                leitura.c.valor,
                leitura.c.qualidade,
                leitura.c.rotulos,
                leitura.c.modulo,
                leitura.c.em,
            )
        )
    ).all()

    amostras: list[Amostra] = []
    velhas = extern = 0
    for ln in linhas:
        if ln.modulo in externos:
            extern += 1
            continue
        if ln.em < corte:
            velhas += 1
            continue
        if ln.valor is None or math.isnan(ln.valor) or math.isinf(ln.valor):
            continue

        rotulos = _rotulos_do_sujeito(ln.sujeito, parque)
        # O dispositivo de uma leitura de porta vem declarado pelo módulo, não
        # deduzido do texto do sujeito: nome de porta tem barra ("Gi0/1") e
        # qualquer tentativa de partir a string erra em algum fabricante.
        if (dono := (ln.rotulos or {}).get("dispositivo")) and dono in parque:
            rotulos = dict(parque[dono])
        for chave, valor in (ln.rotulos or {}).items():
            if chave not in _ROTULOS_INTERNOS:
                rotulos[_nome_de_rotulo(chave)] = str(valor)
        rotulos["modulo"] = ln.modulo
        # Qualidade viaja com o número, herança do mundo OT: valor sem código
        # de qualidade não é dado. Quem plota pode filtrar `qualidade="boa"`.
        rotulos["qualidade"] = ln.qualidade
        amostras.append(Amostra(metrica=ln.metrica, valor=float(ln.valor), rotulos=rotulos))

    de_estado, velhas_estado = await _amostras_de_estado(conexao, parque, corte)
    amostras += de_estado
    velhas += velhas_estado

    return Exportacao(
        amostras=amostras,
        omitidas_velhas=velhas,
        omitidas_externas=extern,
        duracao_s=time.perf_counter() - inicio,
        validade_s=validade_s,
    )


def formatar(exp: Exportacao) -> str:
    """Texto no formato de exposição, agrupado por métrica com HELP e TYPE."""
    por_metrica: dict[str, list[Amostra]] = {}
    for a in exp.amostras:
        por_metrica.setdefault(a.metrica, []).append(a)

    partes: list[str] = []
    for metrica in sorted(por_metrica):
        definicao = POR_NOME.get(metrica)
        ajuda = (definicao.descricao if definicao else "") or metrica
        if metrica == "ativo_alcancavel":
            ajuda += _RESSALVA_ALCANCE
        unidade = f" ({definicao.unidade})" if definicao and definicao.unidade else ""
        tipo = _TIPO_PROM.get(definicao.tipo, "gauge") if definicao else "gauge"
        partes.append(f"# HELP {PREFIXO}{metrica} {_escapar(ajuda)}{unidade}")
        partes.append(f"# TYPE {PREFIXO}{metrica} {tipo}")
        partes.extend(a.linha() for a in por_metrica[metrica])

    # Auto-observação do exportador. Vale a mesma regra dos módulos: quem não
    # publica a própria saúde falha em silêncio, e falha em silêncio é
    # indistinguível de tudo bem.
    partes += [
        f"# HELP {PREFIXO}exportador_amostras Amostras publicadas nesta raspagem.",
        f"# TYPE {PREFIXO}exportador_amostras gauge",
        f"{PREFIXO}exportador_amostras {len(exp.amostras)!r}",
        f"# HELP {PREFIXO}exportador_omitidas_velhas Leituras fora da validade"
        f" ({exp.validade_s}s) — série obsoleta é melhor que número velho.",
        f"# TYPE {PREFIXO}exportador_omitidas_velhas gauge",
        f"{PREFIXO}exportador_omitidas_velhas {exp.omitidas_velhas!r}",
        f"# HELP {PREFIXO}exportador_omitidas_externas Leituras cuja série já"
        " vive noutro Prometheus e não são republicadas.",
        f"# TYPE {PREFIXO}exportador_omitidas_externas gauge",
        f"{PREFIXO}exportador_omitidas_externas {exp.omitidas_externas!r}",
        f"# HELP {PREFIXO}exportador_duracao_s Tempo para montar esta resposta.",
        f"# TYPE {PREFIXO}exportador_duracao_s gauge",
        f"{PREFIXO}exportador_duracao_s {exp.duracao_s!r}",
    ]
    return "\n".join(partes) + "\n"


__all__ = [
    "PREFIXO",
    "VALIDADE_PADRAO_S",
    "Amostra",
    "Exportacao",
    "exportar",
    "formatar",
    "manifestos",
]
