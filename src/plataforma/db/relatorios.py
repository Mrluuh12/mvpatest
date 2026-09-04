"""Relatórios: uma pergunta sobre um período, com as ressalvas junto.

Um relatório que não declara o que ficou de fora é um relatório que vai ser
citado errado numa reunião. Por isso todo relatório aqui devolve ``notas`` — e
elas não são rodapé decorativo: dizem quantos equipamentos ficaram fora da
conta e por quê. *"Disponibilidade de 94%"* sem *"de 22 dos 46 sondados"* é
número que alguém defende sem saber o que está defendendo.

A disponibilidade sai das **transições**, não de amostras. A tabela guarda o
instante de cada mudança, então o cálculo é exato dentro da janela — não uma
média de pontos espaçados.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from .coleta import estado_no_inicio
from .esquema import (
    ativo,
    campo,
    dispositivo,
    estado,
    leitura,
    saude_modulo,
    transicao,
)


@dataclass
class Relatorio:
    nome: str
    titulo: str
    desde: datetime
    ate: datetime
    colunas: tuple[str, ...]
    linhas: list[dict] = field(default_factory=list)
    #: O que ficou de fora, e por quê. Nunca vazio sem motivo.
    notas: list[str] = field(default_factory=list)

    def para_json(self) -> dict:
        return {
            "nome": self.nome,
            "titulo": self.titulo,
            "desde": self.desde,
            "ate": self.ate,
            "colunas": list(self.colunas),
            "linhas": self.linhas,
            "notas": self.notas,
        }


async def _disponibilidade_por_sujeito(
    conexao: AsyncConnection, desde: datetime, ate: datetime
) -> dict[str, float | None]:
    """Fração do tempo alcançável, por dispositivo, dentro da janela.

    Calculada de uma vez para todos: 708 chamadas a uma função por sujeito
    seriam 708 idas ao banco por relatório, e o relatório é a tela que mais
    gente abre ao mesmo tempo.

    ``None`` para quem não tem observação no período. Zero seria afirmar que
    ficou fora o tempo todo — e nunca sondado não é o mesmo que caído.
    """
    mudancas: dict[str, list] = defaultdict(list)
    for ln in (
        await conexao.execute(
            select(
                transicao.c.sujeito, transicao.c.de, transicao.c.para, transicao.c.em
            ).order_by(
                transicao.c.em
            )
        )
    ).all():
        mudancas[ln.sujeito].append(ln)

    saida: dict[str, float | None] = {}
    janela = (ate - desde).total_seconds()
    if janela <= 0:
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

        marco, acumulado = desde, 0.0
        for m in (x for x in historico if desde < x.em <= fim):
            if vivo:
                acumulado += (m.em - marco).total_seconds()
            vivo, marco = m.para, m.em
        if vivo:
            acumulado += (fim - marco).total_seconds()
        saida[ln.sujeito] = acumulado / (fim - desde).total_seconds()
    return saida


async def disponibilidade_por_frota(
    conexao: AsyncConnection, desde: datetime, ate: datetime
) -> Relatorio:
    """A pergunta que troca "3 nós down" por "a britagem está a 87%"."""
    disp = await _disponibilidade_por_sujeito(conexao, desde, ate)

    linhas_inv = (
        await conexao.execute(
            select(
                ativo.c.ativo_id,
                ativo.c.frota,
                dispositivo.c.chave,
            ).join(dispositivo, dispositivo.c.ativo_id == ativo.c.ativo_id)
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

    por_grupo: dict[tuple[str, str], list[float]] = defaultdict(list)
    ativos_do_grupo: dict[tuple[str, str], set[str]] = defaultdict(set)
    sem_medida = 0
    for ln in linhas_inv:
        grupo = (ln.frota or "?", funcoes.get(ln.ativo_id, "não definida"))
        ativos_do_grupo[grupo].add(ln.ativo_id)
        d = disp.get(ln.chave)
        if d is None:
            sem_medida += 1
            continue
        por_grupo[grupo].append(d)

    linhas = []
    for grupo, valores in sorted(por_grupo.items(), key=lambda x: -len(x[1])):
        frota, funcao = grupo
        linhas.append(
            {
                "frota": frota,
                "funcao": funcao,
                "ativos": len(ativos_do_grupo[grupo]),
                "dispositivos_medidos": len(valores),
                "disponibilidade_pct": round(100 * sum(valores) / len(valores), 2),
                "pior_pct": round(100 * min(valores), 2),
            }
        )

    r = Relatorio(
        nome="disponibilidade_frota",
        titulo="Disponibilidade por frota e função de negócio",
        desde=desde,
        ate=ate,
        colunas=("frota", "funcao", "ativos", "dispositivos_medidos",
                 "disponibilidade_pct", "pior_pct"),
        linhas=linhas,
    )
    if sem_medida:
        r.notas.append(
            f"{sem_medida} dispositivos ficaram fora da média por não terem "
            f"observação no período — nunca sondado não é o mesmo que caído, e "
            f"contá-los como zero rebaixaria a frota inteira por falta de coleta."
        )
    grupos_sem_funcao = sum(1 for f, fn in por_grupo if fn == "não definida")
    if grupos_sem_funcao:
        r.notas.append(
            f"{grupos_sem_funcao} grupos aparecem como função 'não definida': o "
            f"cadastro ainda não diz o que aqueles ativos fazem."
        )
    return r


async def cobertura_da_coleta(
    conexao: AsyncConnection, desde: datetime, ate: datetime
) -> Relatorio:
    """O que a plataforma sabe hoje, e de quem — por papel de equipamento."""
    inventario = (
        await conexao.execute(
            select(dispositivo.c.papel, func.count()).group_by(dispositivo.c.papel)
        )
    ).all()
    com_estado = dict(
        (
            await conexao.execute(
                select(dispositivo.c.papel, func.count())
                .join(estado, estado.c.sujeito == dispositivo.c.chave)
                .group_by(dispositivo.c.papel)
            )
        ).all()
    )
    # O sujeito da leitura pode ser `chave/porta`; o prefixo até a barra é o
    # dispositivo, e é por ele que se conta cobertura.
    medidos: dict[str, set[str]] = defaultdict(set)
    papel_de = dict(
        (await conexao.execute(select(dispositivo.c.chave, dispositivo.c.papel))).all()
    )
    for ln in (
        await conexao.execute(select(leitura.c.sujeito, leitura.c.modulo).distinct())
    ).all():
        chave = ln.sujeito.split("/", 1)[0]
        if papel := papel_de.get(chave):
            medidos[papel].add(chave)

    linhas = [
        {
            "papel": papel,
            "total": total,
            "com_estado": com_estado.get(papel, 0),
            "com_metrica": len(medidos.get(papel, ())),
            "cobertura_pct": round(100 * com_estado.get(papel, 0) / total, 1),
        }
        for papel, total in sorted(inventario, key=lambda x: -x[1])
    ]
    r = Relatorio(
        nome="cobertura",
        titulo="Cobertura da coleta por papel de equipamento",
        desde=desde,
        ate=ate,
        colunas=("papel", "total", "com_estado", "com_metrica", "cobertura_pct"),
        linhas=linhas,
    )
    modulos = (
        await conexao.execute(
            select(saude_modulo.c.modulo, saude_modulo.c.ultima_coleta_ok)
        )
    ).all()
    mudos = [m.modulo for m in modulos if m.ultima_coleta_ok is None]
    if mudos:
        r.notas.append(
            f"módulos sem coleta bem-sucedida registrada: {', '.join(sorted(mudos))} "
            f"— a cobertura acima não conta com eles."
        )
    r.notas.append(
        "`com_estado` é responder ou não responder; `com_metrica` é ter número "
        "além disso. Um papel com estado e sem métrica está sendo vigiado, não medido."
    )
    return r


@dataclass(frozen=True)
class Definicao:
    """Nome curto para o botão, frase inteira para quem quer entender.

    Usar a primeira linha do docstring no botão dava rótulos de oito palavras
    dentro de um retângulo de três.
    """

    rotulo: str
    descricao: str
    gerar: object


RELATORIOS: dict[str, Definicao] = {
    "disponibilidade_frota": Definicao(
        "Disponibilidade",
        'Troca "3 nós down" por "a britagem primária está a 87%".',
        disponibilidade_por_frota,
    ),
    "cobertura": Definicao(
        "Cobertura",
        "O que a plataforma sabe hoje, e de quem — por papel de equipamento.",
        cobertura_da_coleta,
    ),
}


async def gerar(
    conexao: AsyncConnection, nome: str, desde: datetime, ate: datetime | None = None
) -> Relatorio:
    if nome not in RELATORIOS:
        raise KeyError(nome)
    return await RELATORIOS[nome].gerar(conexao, desde, ate or datetime.now(UTC))


def para_csv(r: Relatorio) -> str:
    """CSV com as ressalvas em comentário no topo.

    Sai da ferramenta junto com o número: quem abrir a planilha três semanas
    depois precisa das mesmas ressalvas que quem viu a tela.
    """
    import csv
    import io

    saida = io.StringIO()
    saida.write(f"# {r.titulo}\n")
    saida.write(f"# período: {r.desde:%d/%m/%Y %H:%M} a {r.ate:%d/%m/%Y %H:%M}\n")
    for nota in r.notas:
        saida.write(f"# ressalva: {nota}\n")
    escritor = csv.DictWriter(saida, fieldnames=list(r.colunas), extrasaction="ignore")
    escritor.writeheader()
    escritor.writerows(r.linhas)
    return saida.getvalue()


__all__ = ["RELATORIOS", "Definicao", "Relatorio", "gerar", "para_csv"]
