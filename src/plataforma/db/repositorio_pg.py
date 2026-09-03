"""Repositório em Postgres.

A parte que merece atenção é a escrita da semeadura: ela toca **apenas** as
linhas de origem ``derivado``. Não é uma regra que o código precisa lembrar de
respeitar — é o único caminho que existe, porque a chave primária de ``campo``
inclui a origem. A linha ``cadastrado`` de uma pessoa fica literalmente fora do
alcance de um ``INSERT … ON CONFLICT`` que só nomeia ``derivado``.

A precedência é declarada uma vez, em Python, e traduzida para SQL a partir da
mesma tabela. Duplicar essa regra em dois dialetos seria convidar a divergência
silenciosa que o projeto inteiro tenta evitar.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from inventario.modelo import PRECEDENCIA, Natureza, Origem
from inventario.semeadura import Semeadura

from ..repositorio import Achados, AtivoLido, DispositivoLido
from .esquema import (
    achado,
    aresta,
    ativo,
    campo,
    dispositivo,
    estado,
    identificador,
    metadata,
    saude_modulo,
)

INFINITO = None  # limite superior aberto de um tstzrange


def _sql_precedencia() -> str:
    """Gera o CASE de precedência a partir da tabela Python.

    Uma fonte de verdade só. Se alguém mudar a precedência no modelo, o SQL
    acompanha — não há um segundo lugar para esquecer.
    """
    ramos = []
    for natureza, pesos in PRECEDENCIA.items():
        interno = " ".join(
            f"WHEN '{origem.value}' THEN {peso}" for origem, peso in pesos.items()
        )
        ramos.append(f"WHEN '{natureza.value}' THEN CASE origem {interno} ELSE 0 END")
    return f"CASE natureza {' '.join(ramos)} ELSE 0 END"


PRECEDENCIA_SQL = _sql_precedencia()


def sujeito_ativo(ativo_id: str) -> str:
    return f"ativo:{ativo_id}"


def sujeito_dispositivo(chave: str) -> str:
    return f"disp:{chave}"


def criar_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


async def criar_esquema(engine: AsyncEngine) -> None:
    """Cria o esquema do zero. Para desenvolvimento e teste — em produção, Alembic."""
    async with engine.begin() as conexao:
        await conexao.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        await conexao.run_sync(metadata.create_all)


async def apagar_esquema(engine: AsyncEngine) -> None:
    async with engine.begin() as conexao:
        await conexao.run_sync(metadata.drop_all)


# --------------------------------------------------------------------------
# Escrita
# --------------------------------------------------------------------------


async def aplicar_semeadura(
    conexao: AsyncConnection, semeada: Semeadura, agora: datetime | None = None
) -> dict[str, int]:
    """Grava o resultado da semeadura, sem tocar em nada cadastrado por gente.

    Idempotente: rodar duas vezes com a mesma entrada deixa o banco igual.
    """
    momento = agora or datetime.now(UTC)

    if semeada.ativos:
        await conexao.execute(
            pg_insert(ativo)
            .values(
                [
                    {"ativo_id": a.ativo_id, "frota": a.frota, "numero": a.numero, "site": a.site}
                    for a in semeada.ativos.values()
                ]
            )
            .on_conflict_do_update(
                index_elements=["ativo_id"],
                set_={"frota": text("excluded.frota"), "numero": text("excluded.numero")},
            )
        )

    if semeada.dispositivos:
        await conexao.execute(
            pg_insert(dispositivo)
            .values(
                [
                    {
                        "chave": d.chave,
                        "nome_bruto": d.nome_bruto,
                        "nome_canonico": d.nome_canonico,
                        "papel": d.papel.value,
                        "zona": d.zona.value,
                        "ativo_id": d.ativo_id,
                    }
                    for d in semeada.dispositivos.values()
                ]
            )
            .on_conflict_do_update(
                index_elements=["chave"],
                set_={
                    "nome_bruto": text("excluded.nome_bruto"),
                    "nome_canonico": text("excluded.nome_canonico"),
                    "papel": text("excluded.papel"),
                    "zona": text("excluded.zona"),
                    "ativo_id": text("excluded.ativo_id"),
                },
            )
        )

        linhas_ident = [
            {"dispositivo_chave": d.chave, "tipo": i.tipo.value, "valor": i.valor}
            for d in semeada.dispositivos.values()
            for i in d.identificadores
        ]
        if linhas_ident:
            await conexao.execute(
                pg_insert(identificador).values(linhas_ident).on_conflict_do_nothing()
            )

    # Função de negócio: escrita SEMPRE como 'derivado'. É esta linha, e só
    # esta, que uma nova semeadura sobrescreve.
    linhas_campo = [
        {
            "sujeito": sujeito_ativo(a.ativo_id),
            "nome": "funcao_negocio",
            "origem": Origem.DERIVADO.value,
            "natureza": Natureza.INTENCAO.value,
            "valor": a.funcao_negocio.valor,
            "em": momento,
        }
        for a in semeada.ativos.values()
    ]
    if linhas_campo:
        await conexao.execute(
            pg_insert(campo)
            .values(linhas_campo)
            .on_conflict_do_update(
                index_elements=["sujeito", "nome", "origem"],
                set_={"valor": text("excluded.valor"), "em": text("excluded.em")},
            )
        )

    arestas_gravadas = await _gravar_arestas(conexao, semeada, momento)
    await _gravar_achados(conexao, semeada, momento)

    return {
        "ativos": len(semeada.ativos),
        "dispositivos": len(semeada.dispositivos),
        "arestas": arestas_gravadas,
    }


async def _gravar_arestas(
    conexao: AsyncConnection, semeada: Semeadura, momento: datetime
) -> int:
    """Abre as arestas que ainda não existem abertas.

    A restrição de exclusão do banco recusaria uma segunda aresta igual e
    sobreposta; aqui a checagem é feita antes para que rodar de novo seja
    silencioso em vez de virar erro.
    """
    if not semeada.arestas:
        return 0

    existentes = {
        (linha.origem_chave, linha.destino_chave, linha.tipo)
        for linha in (
            await conexao.execute(
                select(aresta.c.origem_chave, aresta.c.destino_chave, aresta.c.tipo).where(
                    func.upper_inf(aresta.c.validade)
                )
            )
        ).all()
    }

    novas = [
        {
            "origem_chave": e.origem_chave,
            "destino_chave": e.destino_chave,
            "tipo": e.tipo.value,
            "validade": Range(momento, INFINITO, bounds="[)"),
        }
        for e in semeada.arestas
        if (e.origem_chave, e.destino_chave, e.tipo.value) not in existentes
    ]
    if novas:
        await conexao.execute(insert(aresta).values(novas))
    return len(novas)


async def _gravar_achados(
    conexao: AsyncConnection, semeada: Semeadura, momento: datetime
) -> None:
    r = semeada.relatorio
    await conexao.execute(delete(achado))
    linhas = [
        {"categoria": categoria, "descricao": descricao, "em": momento}
        for categoria, itens in (
            ("conflito", r.chaves_em_conflito),
            ("homonimo", r.homonimos_desambiguados),
            ("divergencia", r.divergencias),
            ("papel_desconhecido", r.papel_desconhecido),
            ("fora_do_padrao", r.fora_do_padrao),
            ("linha_duplicada", r.linhas_duplicadas),
        )
        for descricao in itens
    ]
    if linhas:
        await conexao.execute(insert(achado).values(linhas))


async def cadastrar_campo(
    conexao: AsyncConnection,
    sujeito: str,
    nome: str,
    valor: Any,
    natureza: Natureza = Natureza.INTENCAO,
    agora: datetime | None = None,
) -> None:
    """O caminho do ADM: grava um valor com origem ``cadastrado``."""
    await conexao.execute(
        pg_insert(campo)
        .values(
            sujeito=sujeito,
            nome=nome,
            origem=Origem.CADASTRADO.value,
            natureza=natureza.value,
            valor=valor,
            em=agora or datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=["sujeito", "nome", "origem"],
            set_={"valor": text("excluded.valor"), "em": text("excluded.em")},
        )
    )


# --------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------


async def campos_vencedores(conexao: AsyncConnection, sujeito: str | None = None) -> dict:
    """Resolve a precedência e devolve ``{sujeito: {nome: (valor, origem)}}``."""
    filtro = "WHERE sujeito = :sujeito" if sujeito else ""
    consulta = text(
        f"""
        SELECT DISTINCT ON (sujeito, nome) sujeito, nome, valor, origem
        FROM campo {filtro}
        ORDER BY sujeito, nome, {PRECEDENCIA_SQL} DESC, em DESC
        """
    )
    linhas = (
        await conexao.execute(consulta, {"sujeito": sujeito} if sujeito else {})
    ).all()
    saida: dict[str, dict[str, tuple[Any, str]]] = {}
    for linha in linhas:
        saida.setdefault(linha.sujeito, {})[linha.nome] = (linha.valor, linha.origem)
    return saida


async def divergencias(conexao: AsyncConnection) -> list[dict]:
    """Onde cadastro e realidade discordam — achado, não erro a esconder."""
    linhas = (
        await conexao.execute(
            text(
                """
                SELECT sujeito, nome,
                       jsonb_object_agg(origem, valor) AS versoes
                FROM campo
                GROUP BY sujeito, nome
                HAVING COUNT(DISTINCT valor::text) > 1
                ORDER BY sujeito, nome
                """
            )
        )
    ).all()
    return [
        {"sujeito": ln.sujeito, "campo": ln.nome, "versoes": ln.versoes} for ln in linhas
    ]


class RepositorioPostgres:
    """Implementa o protocolo ``Repositorio`` lendo do banco.

    Carrega tudo na abertura e serve de memória: a esta escala (145 ativos,
    708 dispositivos) isso é mais simples e mais rápido do que ir ao banco a
    cada requisição, e o ponto de troca fica num lugar só quando deixar de ser.
    """

    def __init__(
        self,
        ativos: list[AtivoLido],
        dispositivos: list[DispositivoLido],
        resumo: dict,
        achados: Achados,
    ) -> None:
        self._ativos = {a.ativo_id: a for a in ativos}
        self._dispositivos = {d.chave: d for d in dispositivos}
        self._resumo = resumo
        self._achados = achados

    @classmethod
    async def carregar(cls, engine: AsyncEngine) -> RepositorioPostgres:
        async with engine.connect() as conexao:
            vencedores = await campos_vencedores(conexao)

            linhas_ativo = (await conexao.execute(select(ativo))).all()
            ativos = [
                AtivoLido(
                    ativo_id=ln.ativo_id,
                    frota=ln.frota,
                    numero=ln.numero,
                    funcao_negocio=vencedores.get(sujeito_ativo(ln.ativo_id), {})
                    .get("funcao_negocio", ("desconhecido", ""))[0],
                    dispositivos=[],
                )
                for ln in linhas_ativo
            ]

            ids: dict[str, dict[str, str]] = {}
            for ln in (await conexao.execute(select(identificador))).all():
                ids.setdefault(ln.dispositivo_chave, {})[ln.tipo] = ln.valor

            estados = {
                ln.sujeito: ln for ln in (await conexao.execute(select(estado))).all()
            }

            dispositivos = []
            for ln in (await conexao.execute(select(dispositivo))).all():
                meus = ids.get(ln.chave, {})
                forte = next((t for t in ("mac", "serie", "nome") if t in meus), "nenhum")
                visto = estados.get(ln.chave)
                dispositivos.append(
                    DispositivoLido(
                        chave=ln.chave,
                        nome=ln.nome_bruto,
                        papel=ln.papel,
                        zona=ln.zona,
                        ip=meus.get("ip", ""),
                        ativo_id=ln.ativo_id or "",
                        identidade=forte,
                        alcancavel=visto.alcancavel if visto else None,
                        latencia_ms=visto.latencia_ms if visto else None,
                        perda_pct=visto.perda_pct if visto else None,
                        qualidade=visto.qualidade if visto else None,
                        visto_em=visto.visto_em if visto else None,
                    )
                )

            por_ativo: dict[str, list[str]] = {}
            for d in dispositivos:
                if d.ativo_id:
                    por_ativo.setdefault(d.ativo_id, []).append(d.chave)
            ativos = [
                a.model_copy(update={"dispositivos": sorted(por_ativo.get(a.ativo_id, []))})
                for a in ativos
            ]

            abertas = await conexao.scalar(
                select(func.count()).select_from(aresta).where(func.upper_inf(aresta.c.validade))
            )
            achados_por_cat: dict[str, list[str]] = {}
            for ln in (await conexao.execute(select(achado))).all():
                achados_por_cat.setdefault(ln.categoria, []).append(ln.descricao)

            saude = {
                ln.modulo: {
                    "ultima_coleta_ok": ln.ultima_coleta_ok.isoformat()
                    if ln.ultima_coleta_ok
                    else None,
                    "alvos_total": ln.alvos_total,
                    "alvos_falha": ln.alvos_falha,
                    "duracao_s": round(ln.duracao_s, 3),
                    "rejeitadas": ln.rejeitadas,
                }
                for ln in (await conexao.execute(select(saude_modulo))).all()
            }
            sondados = [d for d in dispositivos if d.alcancavel is not None]
            resumo = {
                "ativos": len(ativos),
                "dispositivos": len(dispositivos),
                "arestas_abertas": abertas or 0,
                "divergencias": len(await divergencias(conexao)),
                "sondados": len(sondados),
                "alcancaveis": sum(1 for d in sondados if d.alcancavel),
                "modulos": saude,
            }
            achados_obj = Achados(
                conflitos=achados_por_cat.get("conflito", []),
                homonimos=achados_por_cat.get("homonimo", []),
                divergencias=achados_por_cat.get("divergencia", []),
                papel_desconhecido=achados_por_cat.get("papel_desconhecido", []),
                fora_do_padrao=achados_por_cat.get("fora_do_padrao", []),
            )
        return cls(ativos, dispositivos, resumo, achados_obj)

    # -- protocolo Repositorio -------------------------------------------

    def ativos(self) -> list[AtivoLido]:
        return sorted(self._ativos.values(), key=lambda a: (a.frota, a.numero))

    def ativo(self, ativo_id: str) -> AtivoLido | None:
        return self._ativos.get(ativo_id)

    def dispositivos(self, ativo_id: str | None = None) -> list[DispositivoLido]:
        todos = self._dispositivos.values()
        if ativo_id is None:
            return sorted(todos, key=lambda d: d.nome)
        return sorted((d for d in todos if d.ativo_id == ativo_id), key=lambda d: d.nome)

    def dispositivo(self, chave: str) -> DispositivoLido | None:
        return self._dispositivos.get(chave)

    def resumo(self) -> dict:
        return dict(self._resumo)

    def achados(self) -> Achados:
        return self._achados

    def distribuicao(self, campo_nome: str) -> dict[str, int]:
        if campo_nome == "frota":
            return dict(Counter(a.frota for a in self._ativos.values()).most_common())
        if campo_nome in {"papel", "zona"}:
            return dict(
                Counter(
                    getattr(d, campo_nome) for d in self._dispositivos.values()
                ).most_common()
            )
        raise ValueError(f"campo desconhecido: {campo_nome!r}")
