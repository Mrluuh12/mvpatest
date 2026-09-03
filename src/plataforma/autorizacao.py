"""Rotas de sessão, edição e administração.

Concentradas aqui para que o arquivo da API não vire um depósito, e porque
todas compartilham a mesma regra: **nenhuma escrita acontece sem uma conta
autenticada, uma permissão conferida naquela zona, e uma linha de auditoria
gravada na mesma transação.**

Duas decisões de zona que merecem estar escritas:

* **Metadado de ativo é atributo de negócio, não de rede.** Editar a função de
  negócio de um caminhão não toca em OT, então exige permissão na zona
  corporativa — mesmo que o caminhão tenha dispositivos em OT. Exigir OT ali
  seria burocracia sem ganho de segurança.
* **Mover um dispositivo de zona exige permissão nas duas.** Sem isso, quem
  administra só a zona corporativa poderia trazer um CLP para dentro dela e,
  em seguida, agir sobre ele. É a porta de escalonamento mais óbvia que este
  modelo tem, e ela fica fechada.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from inventario.modelo import Natureza, PapelUsuario, Permissao, Usuario, Zona
from plataforma.db import contas
from plataforma.db.esquema import dispositivo
from plataforma.db.repositorio_pg import (
    cadastrar_campo,
    sujeito_ativo,
    sujeito_dispositivo,
)
from plataforma.seguranca import SenhaFraca

COOKIE = "plataforma_sessao"

#: Campos que a área ADM pode editar. Lista fechada de propósito: campo livre
#: vira dialeto, e dialeto foi o problema que o dicionário canônico resolve.
ATIVO_EDITAVEL = {"funcao_negocio", "apelido", "criticidade"}
DISPOSITIVO_EDITAVEL = {"apelido", "criticidade"}


class Credencial(BaseModel):
    login: str
    senha: str


class NovoUsuario(BaseModel):
    login: str = Field(min_length=2, max_length=64)
    nome: str = Field(min_length=2)
    senha: str
    papel: PapelUsuario
    zonas: list[Zona] = Field(min_length=1)


class Edicao(BaseModel):
    campo: str
    valor: Any


class ContaVista(BaseModel):
    login: str
    nome: str
    ativo: bool
    concessoes: list[dict]

    @classmethod
    def de(cls, u: Usuario) -> ContaVista:
        return cls(
            login=u.login,
            nome=u.nome,
            ativo=u.ativo,
            concessoes=[
                {"papel": c.papel.value, "zonas": sorted(z.value for z in c.zonas)}
                for c in u.concessoes
            ],
        )


def criar_rotas(obter_engine) -> APIRouter:
    """``obter_engine`` devolve o engine em uso, ou ``None`` sem banco."""
    rotas = APIRouter()

    def engine_ou_erro() -> AsyncEngine:
        motor = obter_engine()
        if motor is None:
            raise HTTPException(status_code=503, detail="banco não configurado")
        return motor

    async def conta_atual(
        plataforma_sessao: str | None = Cookie(default=None, alias=COOKIE),
    ) -> contas.Autenticado | None:
        motor = obter_engine()
        if motor is None:
            return None
        async with motor.connect() as conexao:
            return await contas.resolver(conexao, plataforma_sessao)

    def exigir(conta, permissao: Permissao, zona: Zona) -> Usuario:
        try:
            return contas.exigir(conta, permissao, zona)
        except contas.NaoAutenticado as erro:
            raise HTTPException(status_code=401, detail="é preciso entrar") from erro
        except contas.NaoAutorizado as erro:
            raise HTTPException(status_code=403, detail=str(erro)) from erro

    # ------------------------------ sessão ------------------------------

    @rotas.post("/api/v1/sessao", tags=["conta"])
    async def entrar(
        resposta: Response, pedido: Request, credencial: Credencial
    ) -> ContaVista:
        motor = engine_ou_erro()
        # A transação fecha ANTES de qualquer exceção: levantar dentro dela
        # desfaria o registro da tentativa recusada, e tentativa recusada é
        # exatamente o que uma auditoria precisa guardar.
        async with motor.begin() as conexao:
            token = await contas.autenticar(
                conexao,
                credencial.login,
                credencial.senha,
                origem=pedido.client.host if pedido.client else None,
            )
            usuario = (
                await contas.carregar_usuario(conexao, credencial.login) if token else None
            )
        if token is None or usuario is None:
            # Mensagem única para login inexistente e senha errada:
            # distinguir os dois entrega a lista de quem existe.
            raise HTTPException(status_code=401, detail="login ou senha inválidos")
        resposta.set_cookie(
            COOKIE, token, httponly=True, samesite="strict", path="/", max_age=12 * 3600
        )
        return ContaVista.de(usuario)

    @rotas.delete("/api/v1/sessao", tags=["conta"])
    async def sair(
        resposta: Response,
        plataforma_sessao: str | None = Cookie(default=None, alias=COOKIE),
    ) -> dict:
        motor = obter_engine()
        if motor is not None and plataforma_sessao:
            async with motor.begin() as conexao:
                await contas.encerrar(conexao, plataforma_sessao)
        resposta.delete_cookie(COOKIE, path="/")
        return {"encerrada": True}

    @rotas.get("/api/v1/eu", tags=["conta"])
    async def eu(conta=Depends(conta_atual)) -> dict:
        if conta is None:
            return {"autenticado": False}
        return {
            "autenticado": True,
            "expira_em": conta.expira_em,
            **ContaVista.de(conta.usuario).model_dump(),
        }

    # ------------------------------ edição ------------------------------

    @rotas.put("/api/v1/ativos/{ativo_id}/campo", tags=["edicao"])
    async def editar_ativo(
        ativo_id: str, edicao: Edicao, conta=Depends(conta_atual)
    ) -> dict:
        """Metadado de ativo: zona corporativa, por ser atributo de negócio."""
        if edicao.campo not in ATIVO_EDITAVEL:
            raise HTTPException(
                status_code=422,
                detail=f"campo {edicao.campo!r} não é editável — "
                f"editáveis: {sorted(ATIVO_EDITAVEL)}",
            )
        usuario = exigir(conta, Permissao.EDITAR_ATIVO, Zona.CORPORATIVA)
        motor = engine_ou_erro()
        sujeito = sujeito_ativo(ativo_id)
        async with motor.begin() as conexao:
            await cadastrar_campo(
                conexao, sujeito, edicao.campo, edicao.valor, Natureza.INTENCAO
            )
            await contas.registrar(
                conexao,
                "ativo.editar",
                sujeito,
                login=usuario.login,
                zona=Zona.CORPORATIVA,
                detalhe={"campo": edicao.campo, "para": edicao.valor},
            )
        return {"sujeito": sujeito, "campo": edicao.campo, "valor": edicao.valor}

    @rotas.put("/api/v1/dispositivos/{chave:path}/campo", tags=["edicao"])
    async def editar_dispositivo(
        chave: str, edicao: Edicao, conta=Depends(conta_atual)
    ) -> dict:
        motor = engine_ou_erro()
        async with motor.connect() as conexao:
            linha = (
                await conexao.execute(
                    select(dispositivo.c.zona).where(dispositivo.c.chave == chave)
                )
            ).first()
        if linha is None:
            raise HTTPException(status_code=404, detail="dispositivo não existe")
        zona = Zona(linha.zona)

        if edicao.campo == "zona":
            return await _mover_de_zona(motor, chave, zona, edicao.valor, conta)

        if edicao.campo not in DISPOSITIVO_EDITAVEL:
            raise HTTPException(
                status_code=422,
                detail=f"campo {edicao.campo!r} não é editável — "
                f"editáveis: {sorted(DISPOSITIVO_EDITAVEL | {'zona'})}",
            )
        usuario = exigir(conta, Permissao.EDITAR_ATIVO, zona)
        sujeito = sujeito_dispositivo(chave)
        async with motor.begin() as conexao:
            await cadastrar_campo(
                conexao, sujeito, edicao.campo, edicao.valor, Natureza.INTENCAO
            )
            await contas.registrar(
                conexao,
                "dispositivo.editar",
                sujeito,
                login=usuario.login,
                zona=zona,
                detalhe={"campo": edicao.campo, "para": edicao.valor},
            )
        return {"sujeito": sujeito, "campo": edicao.campo, "valor": edicao.valor}

    async def _mover_de_zona(motor, chave: str, atual: Zona, destino: Any, conta) -> dict:
        """Mover de zona exige permissão **na origem e no destino**.

        Sem as duas, quem administra apenas a zona corporativa poderia trazer
        um controlador para dentro dela e, em seguida, agir sobre ele.
        """
        try:
            nova = Zona(destino)
        except ValueError as erro:
            raise HTTPException(status_code=422, detail=f"zona {destino!r} não existe") from erro
        usuario = exigir(conta, Permissao.CADASTRAR_ATIVO, atual)
        exigir(conta, Permissao.CADASTRAR_ATIVO, nova)
        async with motor.begin() as conexao:
            await conexao.execute(
                update(dispositivo).where(dispositivo.c.chave == chave).values(zona=nova.value)
            )
            await contas.registrar(
                conexao,
                "dispositivo.mover_zona",
                sujeito_dispositivo(chave),
                login=usuario.login,
                zona=nova,
                detalhe={"de": atual.value, "para": nova.value},
            )
        return {"chave": chave, "zona": nova.value}

    # --------------------------- administração ---------------------------

    @rotas.get("/api/v1/usuarios", tags=["administracao"])
    async def listar(conta=Depends(conta_atual)) -> list[ContaVista]:
        exigir(conta, Permissao.GERIR_USUARIOS, Zona.CORPORATIVA)
        motor = engine_ou_erro()
        async with motor.connect() as conexao:
            return [ContaVista.de(u) for u in await contas.listar_usuarios(conexao)]

    @rotas.post("/api/v1/usuarios", tags=["administracao"])
    async def criar(novo: NovoUsuario, conta=Depends(conta_atual)) -> ContaVista:
        quem = exigir(conta, Permissao.GERIR_USUARIOS, Zona.CORPORATIVA)
        # Não se concede o que não se tem: criar um administrador de OT sem
        # ser administrador de OT seria escalonamento por procuração.
        for zona in novo.zonas:
            exigir(conta, Permissao.GERIR_USUARIOS, zona)
        motor = engine_ou_erro()
        try:
            async with motor.begin() as conexao:
                await contas.criar_usuario(
                    conexao, novo.login, novo.nome, novo.senha,
                    [(novo.papel, novo.zonas)], por=quem.login,
                )
                criado = await contas.carregar_usuario(conexao, novo.login)
        except SenhaFraca as erro:
            raise HTTPException(status_code=422, detail=str(erro)) from erro
        return ContaVista.de(criado)

    @rotas.delete("/api/v1/usuarios/{login}", tags=["administracao"])
    async def desativar(login: str, conta=Depends(conta_atual)) -> dict:
        quem = exigir(conta, Permissao.GERIR_USUARIOS, Zona.CORPORATIVA)
        if quem.login == login:
            raise HTTPException(
                status_code=422,
                detail="não dá para desativar a própria conta — peça a outro administrador",
            )
        motor = engine_ou_erro()
        async with motor.begin() as conexao:
            achou = await contas.desativar_usuario(conexao, login, por=quem.login)
        if not achou:
            raise HTTPException(status_code=404, detail="usuário não existe")
        return {"desativado": login}

    @rotas.get("/api/v1/auditoria", tags=["administracao"])
    async def ver_auditoria(
        sujeito: str | None = None, limite: int = 50, conta=Depends(conta_atual)
    ) -> list[dict]:
        exigir(conta, Permissao.VER, Zona.CORPORATIVA)
        motor = engine_ou_erro()
        async with motor.connect() as conexao:
            return await contas.historico(conexao, sujeito, min(limite, 200))

    return rotas


__all__ = ["COOKIE", "ATIVO_EDITAVEL", "DISPOSITIVO_EDITAVEL", "criar_rotas", "Body"]
