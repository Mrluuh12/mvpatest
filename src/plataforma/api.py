"""API da plataforma.

Duas regras que valem desde a primeira rota:

* **Tudo que a interface faz, a API faz.** A interface é um cliente da API, não
  um caminho privilegiado. Se algo só existe na tela, não existe.
* **Ausência de dado é resposta, não erro.** Uma família de sinais que ainda não
  tem coletor devolve ``disponivel: false`` com o motivo — nunca zero, nunca uma
  lista vazia que se confunde com "está tudo bem".

O repositório é recarregado periodicamente em segundo plano. Ler tudo de uma vez
e servir de memória é mais simples e mais rápido nesta escala; quando deixar de
ser, o ponto de troca está num lugar só.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Cookie, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from inventario.modelo import Permissao, Zona

from .autorizacao import COOKIE, criar_rotas
from .repositorio import AtivoLido, DispositivoLido, Repositorio, RepositorioMemoria

VERSAO = "0.2.0"
WEB = Path(__file__).parent / "web"

VAR_BANCO = "PLATAFORMA_BANCO"
VAR_INVENTARIO = "PLATAFORMA_INVENTARIO"
PADRAO_INVENTARIO = "dados/inventario.json"
INTERVALO_RECARGA_S = 20


class Sinal(BaseModel):
    """Uma família de sinais e o seu estado de disponibilidade.

    Existe para que a interface possa dizer *por que* um número está faltando,
    em vez de mostrar um traço mudo.
    """

    familia: str
    disponivel: bool
    motivo: str = ""


class FichaAtivo(BaseModel):
    ativo: AtivoLido
    dispositivos: list[DispositivoLido]
    sinais: list[Sinal]


#: Famílias do dicionário canônico e quem as alimenta. Enquanto o coletor não
#: existe, o motivo aparece na tela — a lacuna vira informação, não mistério.
SINAIS = [
    Sinal(familia="inventario", disponivel=True),
    Sinal(familia="topologia", disponivel=True, motivo="apenas arestas embarcado_em"),
    Sinal(familia="disponibilidade", disponivel=True, motivo="coletor ICMP, zona corporativa"),
    Sinal(familia="rf", disponivel=False, motivo="aguarda o módulo Rajant no canal de fatos"),
    Sinal(familia="malha", disponivel=False, motivo="aguarda vizinhança pela BC API"),
    Sinal(familia="interface", disponivel=False, motivo="aguarda o módulo SNMP declarativo"),
    Sinal(familia="dispositivo", disponivel=False, motivo="aguarda coletor por família"),
    Sinal(familia="geo", disponivel=False, motivo="aguarda integração com despacho"),
    Sinal(familia="ot", disponivel=False, motivo="fase 4 — leitura pelo historiador"),
    Sinal(familia="acao", disponivel=False, motivo="subsistema de ação não implantado"),
]


class Fonte:
    """Guarda o repositório em uso e sabe recarregá-lo."""

    def __init__(self, repositorio: Repositorio | None = None) -> None:
        self.repo: Repositorio = repositorio or RepositorioMemoria.vazio()
        self.fixo = repositorio is not None
        self.carregado_em: datetime | None = None
        self.erro: str | None = None
        self._engine = None
        self._tem_contas = False

    async def iniciar(self) -> None:
        if self.fixo:
            return
        if url := os.environ.get(VAR_BANCO):
            from .db.repositorio_pg import criar_engine

            self._engine = criar_engine(url)
            await self.recarregar()
            return
        caminho = Path(os.environ.get(VAR_INVENTARIO, PADRAO_INVENTARIO))
        if caminho.exists():
            self.repo = RepositorioMemoria.de_arquivo(caminho)
            self.carregado_em = datetime.now(UTC)

    async def recarregar(self) -> None:
        if self._engine is None:
            return
        from .db.repositorio_pg import RepositorioPostgres

        try:
            self.repo = await RepositorioPostgres.carregar(self._engine)
            self.carregado_em = datetime.now(UTC)
            self.erro = None
        except Exception as erro:  # noqa: BLE001 - falha de recarga não derruba a API
            # O repositório anterior continua servindo. Mas o erro fica à vista
            # em /saude: dado velho servido em silêncio é dado errado.
            self.erro = repr(erro)

    async def tem_contas(self) -> bool:
        """Existe ao menos uma conta? Guardado em memória depois do primeiro
        ``True``: a resposta só muda uma vez na vida da instalação."""
        if self._tem_contas:
            return True
        if self._engine is None:
            return False
        from sqlalchemy import func, select

        from .db.esquema import usuario

        async with self._engine.connect() as conexao:
            total = await conexao.scalar(select(func.count()).select_from(usuario))
        self._tem_contas = bool(total)
        return self._tem_contas

    async def encerrar(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()


def criar_app(repositorio: Repositorio | None = None) -> FastAPI:
    fonte = Fonte(repositorio)

    @contextlib.asynccontextmanager
    async def ciclo(_app: FastAPI) -> AsyncIterator[None]:
        await fonte.iniciar()
        tarefa: asyncio.Task | None = None
        if not fonte.fixo and fonte._engine is not None:

            async def recarregar_sempre() -> None:
                while True:
                    await asyncio.sleep(INTERVALO_RECARGA_S)
                    await fonte.recarregar()

            tarefa = asyncio.create_task(recarregar_sempre(), name="recarga")
        try:
            yield
        finally:
            if tarefa is not None:
                tarefa.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tarefa
            await fonte.encerrar()

    app = FastAPI(
        title="Plataforma TI + OT",
        version=VERSAO,
        description="Observabilidade e ação sobre os ativos da mina.",
        lifespan=ciclo,
    )

    @app.get("/api/v1/saude", tags=["plataforma"])
    async def saude() -> dict:
        """Saúde da própria plataforma — não dos equipamentos.

        Módulo morto faz as métricas pararem, e ausência de dado ruim é
        indistinguível de ausência de problema. Por isso a plataforma se
        observa antes de observar qualquer outra coisa.

        ``exige_login`` **consulta** o banco em vez de ler o atalho em memória.
        O atalho começa falso a cada processo novo, e é esta resposta que faz a
        interface decidir se mostra a porta de entrada: lendo o atalho, a
        primeira carga depois de um reinício entrava anônima numa instalação
        que já tem contas.
        """
        resumo = fonte.repo.resumo()
        return {
            "versao": VERSAO,
            "inventario_carregado": bool(resumo.get("dispositivos")),
            "exige_login": await fonte.tem_contas(),
            "carregado_em": fonte.carregado_em,
            "erro_de_recarga": fonte.erro,
            "resumo": resumo,
        }

    @app.get("/api/v1/sinais", response_model=list[Sinal], tags=["plataforma"])
    def sinais() -> list[Sinal]:
        return SINAIS

    @app.post("/api/v1/recarregar", tags=["plataforma"])
    async def recarregar() -> dict:
        await fonte.recarregar()
        return {"carregado_em": fonte.carregado_em, "erro": fonte.erro}

    # ---------------- imagens ----------------

    @app.post("/api/v1/imagens", tags=["imagens"])
    async def enviar_imagem(
        sujeito: str = Form(
            ...,
            description="disp:<chave> | papel:<papel> | ativo:<id> | frota:<sigla>",
        ),
        arquivo: UploadFile = File(...),
        sessao: str | None = Cookie(default=None, alias=COOKIE),
    ) -> dict:
        """Associa uma imagem a um sujeito.

        O sujeito é hierárquico: uma foto por **papel** cobre todos os
        dispositivos daquele papel, e uma por **frota** cobre todos os ativos
        dela. Foto específica, quando existir, tem precedência.
        """
        if fonte._engine is None:
            raise HTTPException(status_code=503, detail="banco não configurado")
        from .db import contas
        from .db.imagens import ImagemRecusada, guardar

        async with fonte._engine.connect() as conexao:
            conta = await contas.resolver(conexao, sessao)
        try:
            quem = contas.exigir(conta, Permissao.EDITAR_ATIVO, Zona.CORPORATIVA)
        except contas.NaoAutenticado as erro:
            raise HTTPException(status_code=401, detail="é preciso entrar") from erro
        except contas.NaoAutorizado as erro:
            raise HTTPException(status_code=403, detail=str(erro)) from erro

        conteudo = await arquivo.read()
        try:
            async with fonte._engine.begin() as conexao:
                gravada = await guardar(
                    conexao, sujeito, conteudo, arquivo.content_type or "",
                    enviado_por=quem.login,
                )
                await contas.registrar(
                    conexao, "imagem.enviar", sujeito, login=quem.login,
                    zona=Zona.CORPORATIVA,
                    detalhe={"arquivo": gravada.arquivo, "bytes": gravada.bytes},
                )
        except ImagemRecusada as erro:
            # 422 com o motivo: recusa que não explica custa uma tarde de alguém.
            raise HTTPException(status_code=422, detail=str(erro)) from erro
        await fonte.recarregar()
        return {"sujeito": gravada.sujeito, "arquivo": gravada.arquivo, "bytes": gravada.bytes}

    @app.delete("/api/v1/imagens/{sujeito:path}", tags=["imagens"])
    async def remover_imagem(
        sujeito: str, sessao: str | None = Cookie(default=None, alias=COOKIE)
    ) -> dict:
        if fonte._engine is None:
            raise HTTPException(status_code=503, detail="banco não configurado")
        from .db import contas
        from .db.imagens import remover

        async with fonte._engine.connect() as conexao:
            conta = await contas.resolver(conexao, sessao)
        try:
            quem = contas.exigir(conta, Permissao.EDITAR_ATIVO, Zona.CORPORATIVA)
        except contas.NaoAutenticado as erro:
            raise HTTPException(status_code=401, detail="é preciso entrar") from erro
        except contas.NaoAutorizado as erro:
            raise HTTPException(status_code=403, detail=str(erro)) from erro

        async with fonte._engine.begin() as conexao:
            existia = await remover(conexao, sujeito)
            await contas.registrar(
                conexao, "imagem.remover", sujeito, login=quem.login, zona=Zona.CORPORATIVA
            )
        await fonte.recarregar()
        return {"removida": existia}

    @app.get("/imagens/{arquivo}", include_in_schema=False)
    async def servir_imagem(arquivo: str) -> Response:
        """Serve o arquivo, com o tipo que **nós** registramos.

        Confiar no cabeçalho de quem enviou seria servir conteúdo com tipo
        enganoso; e como só se acha pelo nome registrado no banco, não há
        travessia de caminho a explorar.
        """
        if fonte._engine is None:
            raise HTTPException(status_code=404, detail="sem imagens")
        from .db.imagens import buscar

        async with fonte._engine.connect() as conexao:
            achado = await buscar(conexao, arquivo)
        if achado is None:
            raise HTTPException(status_code=404, detail="imagem não encontrada")
        caminho, tipo = achado
        return Response(
            content=caminho.read_bytes(),
            media_type=tipo,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/api/v1/transicoes", tags=["inventario"])
    async def transicoes(
        ativo_id: str | None = None, chave: str | None = None, limite: int = 20
    ) -> list[dict]:
        """Mudanças de estado observadas — o histórico que existe hoje.

        ``chave`` restringe a um dispositivo; ``ativo_id`` abre para todos os
        dispositivos do ativo. Na ficha de um aparelho, mostrar as mudanças dos
        outros onze do mesmo caminhão faria parecer que foi ele que oscilou.
        """
        if fonte._engine is None:
            return []
        from .db.coleta import ultimas_transicoes

        if chave:
            chaves = [chave]
        elif ativo_id:
            chaves = [d.chave for d in fonte.repo.dispositivos(ativo_id)]
            if not chaves:
                return []
        else:
            chaves = None
        async with fonte._engine.connect() as conexao:
            brutas = await ultimas_transicoes(conexao, chaves, limite)
        nomes = {d.chave: d.nome for d in fonte.repo.dispositivos()}
        return [{**t, "nome": nomes.get(t["sujeito"], t["sujeito"])} for t in brutas]

    @app.get("/api/v1/resumo", tags=["inventario"])
    def resumo() -> dict:
        return fonte.repo.resumo()

    @app.get("/api/v1/achados", tags=["inventario"])
    def achados() -> dict:
        return fonte.repo.achados().model_dump()

    @app.get("/api/v1/distribuicao/{campo}", tags=["inventario"])
    def distribuicao(campo: str) -> dict[str, int]:
        try:
            return fonte.repo.distribuicao(campo)
        except ValueError as erro:
            raise HTTPException(status_code=404, detail=str(erro)) from erro

    @app.get("/api/v1/ativos", response_model=list[AtivoLido], tags=["inventario"])
    def ativos() -> list[AtivoLido]:
        return fonte.repo.ativos()

    @app.get("/api/v1/ativos/{ativo_id}", response_model=FichaAtivo, tags=["inventario"])
    def ficha(ativo_id: str) -> FichaAtivo:
        alvo = fonte.repo.ativo(ativo_id)
        if alvo is None:
            raise HTTPException(status_code=404, detail=f"ativo {ativo_id!r} não existe")
        return FichaAtivo(
            ativo=alvo,
            dispositivos=fonte.repo.dispositivos(ativo_id),
            sinais=SINAIS,
        )

    @app.get("/api/v1/dispositivos", response_model=list[DispositivoLido], tags=["inventario"])
    def dispositivos(ativo_id: str | None = None) -> list[DispositivoLido]:
        return fonte.repo.dispositivos(ativo_id)

    @app.get(
        "/api/v1/dispositivos/{chave:path}",
        response_model=DispositivoLido,
        tags=["inventario"],
    )
    def dispositivo(chave: str) -> DispositivoLido:
        alvo = fonte.repo.dispositivo(chave)
        if alvo is None:
            raise HTTPException(status_code=404, detail=f"dispositivo {chave!r} não existe")
        return alvo

    #: Caminhos que precisam ficar abertos para a porta poder ser aberta.
    ABERTOS = ("/api/v1/sessao", "/api/v1/saude", "/estatico", "/imagens", "/docs",
               "/openapi.json", "/redoc")

    @app.middleware("http")
    async def exigir_login(pedido, seguir):
        """A plataforma fica aberta **até existir a primeira conta**.

        Antes disso não há como entrar, então exigir login trancaria a
        instalação para fora. Depois disso, tudo exige sessão — inclusive as
        leituras, porque elas expõem o inventário inteiro: cada endereço, cada
        zona, cada equipamento. É a regra que não dá para esquecer de ligar.
        """
        caminho = pedido.url.path
        if caminho == "/" or caminho.startswith(ABERTOS) or pedido.method == "OPTIONS":
            return await seguir(pedido)
        if fonte._engine is None or not await fonte.tem_contas():
            return await seguir(pedido)

        from .db import contas

        async with fonte._engine.connect() as conexao:
            conta = await contas.resolver(conexao, pedido.cookies.get(COOKIE))
        if conta is None:
            return JSONResponse({"detail": "é preciso entrar"}, status_code=401)
        return await seguir(pedido)

    app.include_router(criar_rotas(lambda: fonte._engine))

    if WEB.is_dir():
        app.mount("/estatico", StaticFiles(directory=WEB), name="estatico")

        @app.get("/", include_in_schema=False)
        def raiz() -> FileResponse:
            return FileResponse(WEB / "index.html")

    return app


app = criar_app()
