"""Canal de fatos: a vizinhança observada vira aresta com validade.

A tabela `aresta` sempre teve `TSTZRANGE` e restrição de exclusão, mas até
aqui só recebia o que veio da planilha — 672 arestas `embarcado_em`, nenhuma
observada. É este módulo que a preenche com o que a rede está fazendo, e é o
que separa a plataforma de um painel: *"quem era vizinho do nó X às 14h37"*
passa a ter resposta.

Três propriedades, e a segunda é a que evita um desastre
--------------------------------------------------------

**Conciliar é idempotente.** Rodar duas vezes com a mesma vizinhança não cria
linha nem fecha nada. O que existe e continua existindo fica em paz — é isso
que faz a tabela crescer com as *mudanças*, não com os ciclos.

**Só fecha aresta quando a leitura foi completa.** Se o Prometheus caiu ou
metade das consultas falhou, a ausência de um vizinho significa "não
perguntei", não "o enlace caiu". Fechar tudo nesse caso escreveria na história
que a malha inteira se desfez num instante — e essa mentira ficaria gravada,
porque aresta fechada é fato datado. É o mesmo raciocínio da suspeita de
isolamento, aplicado ao grafo.

**Identidade ambígua não vira aresta.** Um MAC ou IP disputado por dois
equipamentos do cadastro não resolve para nenhum: pendurar o enlace no
equipamento errado é pior do que não ter o enlace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import and_, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncConnection

from inventario.modelo import TipoAresta
from plataforma.modulos.contrato import Relacao

from .esquema import aresta, identificador

#: Identidade que o módulo relata prefixada — ``mac:02:D0:...``. Sem prefixo,
#: trata-se de endereço IP, que é como a maioria dos vizinhos aparece.
_PREFIXO = re.compile(r"^(mac|serie|nome):", re.I)


def sujeito_do_enlace(origem: str, destino: str) -> str:
    """A chave sob a qual as medidas de um enlace são guardadas.

    **Direcional, e de propósito.** O SNR que A mede do enlace com B não é o
    que B mede do mesmo enlace: antenas diferentes, alturas diferentes, ruído
    local diferente. Guardar os dois sob a mesma chave apagaria metade do
    diagnóstico — e é justamente a assimetria que diz de que lado está o
    problema.

    O ``>`` deixa a direção visível na própria chave, para quem lê uma linha
    de log não precisar consultar documentação.
    """
    return f"enlace:{origem}>{destino}"


def _normalizar_mac(valor: str) -> str:
    """MAC comparável: sem separador, maiúsculo.

    O cadastro guarda ``00:01:B9:66:A1:AE``; um equipamento pode publicar
    ``00-01-b9-66-a1-ae`` ou ``0001b966a1ae``. Comparar como texto cru perderia
    o enlace por causa de um hífen.
    """
    return re.sub(r"[^0-9A-Fa-f]", "", valor).upper()


def _chave_de_busca(tipo: str, valor: str) -> str:
    return f"{tipo}\t{_normalizar_mac(valor) if tipo == 'mac' else valor.strip().upper()}"


@dataclass
class Resolucao:
    """Identidades observadas traduzidas para chaves do inventário."""

    por_identidade: dict[str, str] = field(default_factory=dict)
    desconhecidas: set[str] = field(default_factory=set)
    ambiguas: set[str] = field(default_factory=set)


async def resolver_identidades(
    conexao: AsyncConnection, identidades: set[str]
) -> Resolucao:
    """Traduz ``mac:00:01:...`` ou ``10.188.99.5`` para a chave do dispositivo.

    Um identificador disputado por dois dispositivos não resolve — some do
    mapa e entra em ``ambiguas``. Ver o comentário do módulo: enlace no
    equipamento errado é pior que enlace ausente.
    """
    linhas = (
        await conexao.execute(
            select(identificador.c.tipo, identificador.c.valor,
                   identificador.c.dispositivo_chave)
        )
    ).all()

    mapa: dict[str, str] = {}
    ambiguos: set[str] = set()
    for ln in linhas:
        busca = _chave_de_busca(ln.tipo, ln.valor)
        if busca in mapa and mapa[busca] != ln.dispositivo_chave:
            ambiguos.add(busca)
        mapa[busca] = ln.dispositivo_chave
    for k in ambiguos:
        mapa.pop(k, None)

    r = Resolucao()
    for identidade in identidades:
        casa = _PREFIXO.match(identidade)
        tipo = casa.group(1).lower() if casa else "ip"
        valor = identidade[casa.end():] if casa else identidade
        busca = _chave_de_busca(tipo, valor)
        if chave := mapa.get(busca):
            r.por_identidade[identidade] = chave
        elif busca in ambiguos:
            r.ambiguas.add(identidade)
        else:
            r.desconhecidas.add(identidade)
    return r


@dataclass
class Conciliacao:
    """O que mudou no grafo — e o que deliberadamente não mudou."""

    abertas: int = 0
    fechadas: int = 0
    inalteradas: int = 0
    nao_resolvidas: int = 0
    fechamento_suspenso: bool = False


async def conciliar(
    conexao: AsyncConnection,
    tipo: TipoAresta,
    relacoes: tuple[Relacao, ...],
    momento: datetime | None = None,
    completo: bool = False,
    observadores: set[str] | None = None,
) -> Conciliacao:
    """Abre o que apareceu, fecha o que sumiu, deixa o que continua em paz.

    ``observadores`` limita o fechamento às arestas de quem realmente foi lido
    neste ciclo. Sem isso, uma coleta completa de 22 rádios fecharia as arestas
    dos outros 127 só por não tê-los mencionado.
    """
    agora = momento or datetime.now(UTC)
    resultado = Conciliacao(fechamento_suspenso=not completo)

    alvo = {(r.origem, r.destino) for r in relacoes if r.tipo is tipo}
    atributos = {(r.origem, r.destino): r.atributos for r in relacoes if r.tipo is tipo}

    abertas_hoje = {
        (ln.origem_chave, ln.destino_chave): ln.id
        for ln in (
            await conexao.execute(
                select(aresta.c.id, aresta.c.origem_chave, aresta.c.destino_chave).where(
                    and_(aresta.c.tipo == tipo.value, func.upper_inf(aresta.c.validade))
                )
            )
        ).all()
    }

    novas = alvo - set(abertas_hoje)
    if novas:
        await conexao.execute(
            insert(aresta),
            [
                {
                    "origem_chave": o,
                    "destino_chave": d,
                    "tipo": tipo.value,
                    # Objeto Range, não `func.tstzrange`: numa inserção em
                    # lote o valor vai como parâmetro, e o driver espera o
                    # tipo, não uma chamada SQL.
                    "validade": Range(agora, None, bounds="[)"),
                    "atributos": atributos.get((o, d), {}),
                }
                for o, d in sorted(novas)
            ],
        )
        resultado.abertas = len(novas)

    sumidas = set(abertas_hoje) - alvo
    if observadores is not None:
        # Fecha só o que partia de quem foi lido agora. Aresta de rádio que
        # este ciclo nem consultou continua valendo — ausência de leitura não
        # é evidência de queda.
        sumidas = {(o, d) for o, d in sumidas if o in observadores}
    if completo and sumidas:
        await conexao.execute(
            update(aresta)
            .where(aresta.c.id.in_([abertas_hoje[k] for k in sumidas]))
            .values(validade=func.tstzrange(func.lower(aresta.c.validade), agora, "[)"))
        )
        resultado.fechadas = len(sumidas)

    resultado.inalteradas = len(alvo & set(abertas_hoje))
    return resultado


async def vizinhos(
    conexao: AsyncConnection, chave: str, quando: datetime | None = None
) -> list[dict]:
    """Com quem este equipamento estava ligado — agora, ou no instante dado.

    É a pergunta que justifica guardar validade em vez de sobrescrever: o
    grafo de ontem às 14h37 continua lá.
    """
    momento = quando or datetime.now(UTC)
    linhas = (
        await conexao.execute(
            select(aresta)
            .where(aresta.c.origem_chave == chave)
            .where(aresta.c.validade.op("@>")(text(":quando").bindparams(quando=momento)))
            .order_by(aresta.c.tipo, aresta.c.destino_chave)
        )
    ).all()
    # As medidas moram na meia-aresta, não na aresta: buscar aqui evita que a
    # tela tenha de saber montar a chave do enlace.
    from .esquema import leitura

    chaves = [sujeito_do_enlace(chave, ln.destino_chave) for ln in linhas]
    medidas: dict[str, dict[str, float]] = {}
    if chaves:
        for m in (
            await conexao.execute(
                select(leitura.c.sujeito, leitura.c.metrica, leitura.c.valor).where(
                    leitura.c.sujeito.in_(chaves)
                )
            )
        ).all():
            medidas.setdefault(m.sujeito, {})[m.metrica] = m.valor

    return [
        {
            "destino": ln.destino_chave,
            "tipo": ln.tipo,
            "desde": ln.validade.lower,
            "ate": ln.validade.upper,
            "atributos": ln.atributos or {},
            "medidas": medidas.get(sujeito_do_enlace(chave, ln.destino_chave), {}),
        }
        for ln in linhas
    ]


__all__ = [
    "Conciliacao",
    "Resolucao",
    "conciliar",
    "resolver_identidades",
    "sujeito_do_enlace",
    "vizinhos",
]
