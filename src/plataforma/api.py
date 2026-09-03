"""API da plataforma.

Duas regras que valem desde a primeira rota:

* **Tudo que a interface faz, a API faz.** A interface é um cliente da API, não
  um caminho privilegiado. Se algo só existe na tela, não existe.
* **Ausência de dado é resposta, não erro.** Uma família de sinais que ainda não
  tem coletor devolve ``disponivel: false`` com o motivo — nunca zero, nunca uma
  lista vazia que se confunde com "está tudo bem".
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .repositorio import AtivoLido, DispositivoLido, Repositorio, RepositorioMemoria

VERSAO = "0.1.0"
WEB = Path(__file__).parent / "web"

#: Onde está o inventário semeado. Configurável para que teste e produção não
#: disputem o mesmo arquivo.
VAR_INVENTARIO = "PLATAFORMA_INVENTARIO"
PADRAO_INVENTARIO = "dados/inventario.json"


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


def carregar_repositorio() -> Repositorio:
    caminho = Path(os.environ.get(VAR_INVENTARIO, PADRAO_INVENTARIO))
    if caminho.exists():
        return RepositorioMemoria.de_arquivo(caminho)
    return RepositorioMemoria.vazio()


#: Famílias do dicionário canônico e quem as alimenta. Enquanto o coletor não
#: existe, o motivo aparece na tela — a lacuna vira informação, não mistério.
SINAIS = [
    Sinal(familia="inventario", disponivel=True),
    Sinal(familia="topologia", disponivel=True, motivo="apenas arestas embarcado_em"),
    Sinal(familia="disponibilidade", disponivel=False, motivo="aguarda o coletor ICMP"),
    Sinal(familia="rf", disponivel=False, motivo="aguarda o módulo Rajant no canal de fatos"),
    Sinal(familia="malha", disponivel=False, motivo="aguarda vizinhança pela BC API"),
    Sinal(familia="interface", disponivel=False, motivo="aguarda o módulo SNMP declarativo"),
    Sinal(familia="dispositivo", disponivel=False, motivo="aguarda coletor por família"),
    Sinal(familia="geo", disponivel=False, motivo="aguarda integração com despacho"),
    Sinal(familia="ot", disponivel=False, motivo="fase 4 — leitura pelo historiador"),
    Sinal(familia="acao", disponivel=False, motivo="subsistema de ação não implantado"),
]


def criar_app(repositorio: Repositorio | None = None) -> FastAPI:
    repo = repositorio or carregar_repositorio()
    app = FastAPI(
        title="Plataforma TI + OT",
        version=VERSAO,
        description="Observabilidade e ação sobre os ativos da mina.",
    )

    @app.get("/api/v1/saude", tags=["plataforma"])
    def saude() -> dict:
        """Saúde da própria plataforma — não dos equipamentos.

        Módulo morto faz as métricas pararem, e ausência de dado ruim é
        indistinguível de ausência de problema. Por isso a plataforma se
        observa antes de observar qualquer outra coisa.
        """
        resumo = repo.resumo()
        return {
            "versao": VERSAO,
            "inventario_carregado": bool(resumo.get("dispositivos")),
            "modulos_registrados": 0,
            "coletas_ativas": 0,
            "resumo": resumo,
        }

    @app.get("/api/v1/sinais", response_model=list[Sinal], tags=["plataforma"])
    def sinais() -> list[Sinal]:
        return SINAIS

    @app.get("/api/v1/resumo", tags=["inventario"])
    def resumo() -> dict:
        return repo.resumo()

    @app.get("/api/v1/achados", tags=["inventario"])
    def achados() -> dict:
        return repo.achados().model_dump()

    @app.get("/api/v1/distribuicao/{campo}", tags=["inventario"])
    def distribuicao(campo: str) -> dict[str, int]:
        try:
            return repo.distribuicao(campo)
        except ValueError as erro:
            raise HTTPException(status_code=404, detail=str(erro)) from erro

    @app.get("/api/v1/ativos", response_model=list[AtivoLido], tags=["inventario"])
    def ativos() -> list[AtivoLido]:
        return repo.ativos()

    @app.get("/api/v1/ativos/{ativo_id}", response_model=FichaAtivo, tags=["inventario"])
    def ficha(ativo_id: str) -> FichaAtivo:
        alvo = repo.ativo(ativo_id)
        if alvo is None:
            raise HTTPException(status_code=404, detail=f"ativo {ativo_id!r} não existe")
        return FichaAtivo(
            ativo=alvo,
            dispositivos=repo.dispositivos(ativo_id),
            sinais=SINAIS,
        )

    @app.get("/api/v1/dispositivos", response_model=list[DispositivoLido], tags=["inventario"])
    def dispositivos(ativo_id: str | None = None) -> list[DispositivoLido]:
        return repo.dispositivos(ativo_id)

    @app.get(
        "/api/v1/dispositivos/{chave:path}",
        response_model=DispositivoLido,
        tags=["inventario"],
    )
    def dispositivo(chave: str) -> DispositivoLido:
        alvo = repo.dispositivo(chave)
        if alvo is None:
            raise HTTPException(status_code=404, detail=f"dispositivo {chave!r} não existe")
        return alvo

    if WEB.is_dir():
        app.mount("/estatico", StaticFiles(directory=WEB), name="estatico")

        @app.get("/", include_in_schema=False)
        def raiz() -> FileResponse:
            return FileResponse(WEB / "index.html")

    return app


app = criar_app()
