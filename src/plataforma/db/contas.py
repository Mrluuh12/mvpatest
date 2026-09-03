"""Contas, sessões e auditoria no banco.

Duas regras estruturais moram aqui.

**Toda escrita passa por auditoria.** Não é convenção: as funções de escrita
deste módulo gravam a linha de auditoria na mesma transação da mudança. Ou as
duas acontecem, ou nenhuma — e não existe rota que altere ou apague auditoria.

**Autorização é negada por padrão.** ``exigir`` só devolve o usuário quando a
permissão vale *naquela zona*. Um administrador da zona corporativa não é
administrador de OT por consequência.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from inventario.modelo import Concessao, PapelUsuario, Permissao, Usuario, Zona
from plataforma.seguranca import (
    conferir,
    criar_credencial,
    nova_sessao,
    resumir,
)

from .esquema import auditoria, concessao, sessao, usuario


class NaoAutenticado(Exception):
    """Sem sessão válida."""


class NaoAutorizado(Exception):
    """Autenticado, mas sem a permissão naquela zona."""

    def __init__(self, permissao: Permissao, zona: Zona) -> None:
        super().__init__(
            f"falta a permissão {permissao.value!r} na zona {zona.value!r}"
        )
        self.permissao = permissao
        self.zona = zona


@dataclass(slots=True)
class Autenticado:
    usuario: Usuario
    expira_em: datetime


# --------------------------------------------------------------------------
# Auditoria
# --------------------------------------------------------------------------


async def registrar(
    conexao: AsyncConnection,
    acao: str,
    sujeito: str,
    login: str | None = None,
    zona: Zona | None = None,
    detalhe: dict[str, Any] | None = None,
    origem: str | None = None,
) -> None:
    """Grava uma linha de auditoria. Chamada na mesma transação da mudança."""
    await conexao.execute(
        insert(auditoria).values(
            em=datetime.now(UTC),
            login=login,
            acao=acao,
            sujeito=sujeito,
            zona=zona.value if zona else None,
            detalhe=detalhe or {},
            origem=origem,
        )
    )


async def historico(
    conexao: AsyncConnection, sujeito: str | None = None, limite: int = 50
) -> list[dict]:
    consulta = select(auditoria).order_by(auditoria.c.em.desc()).limit(limite)
    if sujeito:
        consulta = consulta.where(auditoria.c.sujeito == sujeito)
    return [
        {
            "em": ln.em,
            "login": ln.login,
            "acao": ln.acao,
            "sujeito": ln.sujeito,
            "zona": ln.zona,
            "detalhe": ln.detalhe,
        }
        for ln in (await conexao.execute(consulta)).all()
    ]


# --------------------------------------------------------------------------
# Contas
# --------------------------------------------------------------------------


async def criar_usuario(
    conexao: AsyncConnection,
    login: str,
    nome: str,
    senha: str,
    concessoes: list[tuple[PapelUsuario, list[Zona]]],
    por: str | None = None,
) -> None:
    senha_hash, sal = criar_credencial(senha)
    await conexao.execute(
        pg_insert(usuario)
        .values(
            login=login,
            nome=nome,
            senha_hash=senha_hash,
            sal=sal,
            ativo=True,
            criado_em=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=["login"],
            set_={"nome": nome, "senha_hash": senha_hash, "sal": sal, "ativo": True},
        )
    )
    await conexao.execute(delete(concessao).where(concessao.c.login == login))
    linhas = [
        {"login": login, "papel": papel.value, "zona": z.value}
        for papel, zonas in concessoes
        for z in zonas
    ]
    if linhas:
        await conexao.execute(insert(concessao).values(linhas))
    await registrar(
        conexao,
        "usuario.criar",
        f"usuario:{login}",
        login=por,
        detalhe={
            "nome": nome,
            "concessoes": [
                {"papel": p.value, "zonas": [z.value for z in zs]} for p, zs in concessoes
            ],
        },
    )


async def desativar_usuario(
    conexao: AsyncConnection, login: str, por: str | None = None
) -> bool:
    """Desativa em vez de apagar: histórico de auditoria precisa continuar
    apontando para alguém que existe."""
    resultado = await conexao.execute(
        update(usuario).where(usuario.c.login == login).values(ativo=False)
    )
    if resultado.rowcount:
        await conexao.execute(delete(sessao).where(sessao.c.login == login))
        await registrar(conexao, "usuario.desativar", f"usuario:{login}", login=por)
    return bool(resultado.rowcount)


async def carregar_usuario(conexao: AsyncConnection, login: str) -> Usuario | None:
    linha = (
        await conexao.execute(select(usuario).where(usuario.c.login == login))
    ).first()
    if linha is None:
        return None
    concessoes = (
        await conexao.execute(select(concessao).where(concessao.c.login == login))
    ).all()
    por_papel: dict[str, set[Zona]] = {}
    for c in concessoes:
        por_papel.setdefault(c.papel, set()).add(Zona(c.zona))
    return Usuario(
        login=linha.login,
        nome=linha.nome,
        ativo=linha.ativo,
        concessoes=tuple(
            Concessao(papel=PapelUsuario(p), zonas=frozenset(zs))
            for p, zs in por_papel.items()
        ),
    )


async def listar_usuarios(conexao: AsyncConnection) -> list[Usuario]:
    logins = [
        ln.login for ln in (await conexao.execute(select(usuario.c.login))).all()
    ]
    contas = [await carregar_usuario(conexao, login) for login in sorted(logins)]
    return [c for c in contas if c is not None]


# --------------------------------------------------------------------------
# Sessão
# --------------------------------------------------------------------------


async def autenticar(
    conexao: AsyncConnection, login: str, senha: str, origem: str | None = None
) -> str | None:
    """Devolve o token de sessão, ou ``None`` quando a credencial não confere.

    A mensagem de erro que o chamador mostra é a mesma para login inexistente
    e senha errada, de propósito: distinguir os dois entrega ao atacante a
    lista de quem existe.
    """
    linha = (
        await conexao.execute(select(usuario).where(usuario.c.login == login))
    ).first()
    if linha is None or not linha.ativo:
        # Deriva mesmo assim, para que o tempo de resposta não denuncie a
        # existência da conta.
        conferir(senha, "0" * 64, "00" * 16)
        return None
    if not conferir(senha, linha.senha_hash, linha.sal):
        await registrar(
            conexao, "sessao.recusada", f"usuario:{login}", login=login, origem=origem
        )
        return None

    token = nova_sessao()
    await conexao.execute(
        insert(sessao).values(
            resumo=token.resumo,
            login=login,
            criada_em=datetime.now(UTC),
            expira_em=token.expira_em,
            origem=origem,
        )
    )
    await conexao.execute(
        update(usuario).where(usuario.c.login == login).values(ultimo_acesso=datetime.now(UTC))
    )
    await registrar(conexao, "sessao.abrir", f"usuario:{login}", login=login, origem=origem)
    return token.token


async def encerrar(conexao: AsyncConnection, token: str) -> None:
    resumo = resumir(token)
    linha = (
        await conexao.execute(select(sessao.c.login).where(sessao.c.resumo == resumo))
    ).first()
    await conexao.execute(delete(sessao).where(sessao.c.resumo == resumo))
    if linha:
        await registrar(
            conexao, "sessao.encerrar", f"usuario:{linha.login}", login=linha.login
        )


async def resolver(conexao: AsyncConnection, token: str | None) -> Autenticado | None:
    """Traduz o token numa conta, ou ``None``. Sessão expirada é apagada."""
    if not token:
        return None
    linha = (
        await conexao.execute(select(sessao).where(sessao.c.resumo == resumir(token)))
    ).first()
    if linha is None:
        return None
    if linha.expira_em <= datetime.now(UTC):
        await conexao.execute(delete(sessao).where(sessao.c.resumo == linha.resumo))
        return None
    conta = await carregar_usuario(conexao, linha.login)
    if conta is None or not conta.ativo:
        return None
    return Autenticado(usuario=conta, expira_em=linha.expira_em)


def exigir(conta: Autenticado | None, permissao: Permissao, zona: Zona) -> Usuario:
    """Autorização negada por padrão.

    Um administrador da zona corporativa **não** é administrador de OT por
    consequência: a concessão vale onde foi concedida.
    """
    if conta is None:
        raise NaoAutenticado
    if not conta.usuario.pode(permissao, zona):
        raise NaoAutorizado(permissao, zona)
    return conta.usuario
