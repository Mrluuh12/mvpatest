"""Arranjos de tela: quais cartões aparecem, com que nome e em que ordem.

O princípio que economiza o trabalho é o mesmo que já se repete na plataforma:
**configure o tipo, não a instância.** Arrumar a tela do caminhão uma vez vale
para os 299 — e para o que chegar amanhã. Por instância, arrumar seria um
trabalho que nunca termina.

A resolução é uma cascata, igual à das imagens:

    frota:CA           arranjo dos caminhões
    padrao_ativo       qualquer ativo sem arranjo próprio

    papel:radio_mesh   ficha de todo rádio Rajant
    padrao_dispositivo

Um arranjo por papel — 17 no total — cobre os 708 dispositivos.

**O catálogo de cartões é fechado.** É a mesma razão do dicionário canônico: se
cada tela puder inventar um tipo de cartão, em dois anos são vinte telas que não
se parecem e ninguém mantém. Você ganha liberdade de *compor*, não de
improvisar — o cartão que quiser, na ordem que quiser, com o nome que quiser.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Contexto(StrEnum):
    ATIVO = "ativo"
    DISPOSITIVO = "dispositivo"


class TipoCartao(StrEnum):
    RESUMO = "resumo"
    ALCANCE = "alcance"
    COMPONENTES = "componentes"
    TELEMETRIA = "telemetria"
    TRANSICOES = "transicoes"
    DISPOSITIVOS = "dispositivos"
    IDENTIDADE = "identidade"
    IMAGENS = "imagens"
    TEXTO = "texto"
    ACOES = "acoes"
    AUDITORIA = "auditoria"


class DefCartao(BaseModel):
    """O que um tipo de cartão é, onde serve e o que aceita configurar."""

    tipo: TipoCartao
    titulo_padrao: str
    contextos: tuple[Contexto, ...]
    descricao: str
    opcoes: tuple[str, ...] = ()
    disponivel: bool = True
    motivo: str = ""


A, D = Contexto.ATIVO, Contexto.DISPOSITIVO

CATALOGO: tuple[DefCartao, ...] = (
    DefCartao(
        tipo=TipoCartao.RESUMO, titulo_padrao="Resumo do Ativo", contextos=(A, D),
        descricao="Pares campo e valor.", opcoes=("campos",),
    ),
    DefCartao(
        tipo=TipoCartao.ALCANCE, titulo_padrao="Alcance", contextos=(A,),
        descricao="Medidor de respondendo sobre sondados, com a fórmula à vista.",
    ),
    DefCartao(
        tipo=TipoCartao.COMPONENTES, titulo_padrao="Componentes", contextos=(A,),
        descricao="Diagrama do que está embarcado, com imagem e estado.",
    ),
    DefCartao(
        tipo=TipoCartao.TELEMETRIA, titulo_padrao="Telemetria", contextos=(A, D),
        descricao="Lista de leituras; o que não tem coletor diz por quê.",
        opcoes=("linhas",),
    ),
    DefCartao(
        tipo=TipoCartao.TRANSICOES, titulo_padrao="Últimas mudanças", contextos=(A, D),
        descricao="Mudanças de estado observadas.", opcoes=("limite",),
    ),
    DefCartao(
        tipo=TipoCartao.DISPOSITIVOS, titulo_padrao="Dispositivos", contextos=(A,),
        descricao="Tabela dos dispositivos do ativo.", opcoes=("colunas",),
    ),
    DefCartao(
        tipo=TipoCartao.IDENTIDADE, titulo_padrao="Identidade", contextos=(D,),
        descricao="Identificadores conhecidos e qual deles resolve a identidade.",
    ),
    DefCartao(
        tipo=TipoCartao.IMAGENS, titulo_padrao="Imagens", contextos=(A, D),
        descricao="Imagem associada, com a abrangência de onde ela vem.",
    ),
    DefCartao(
        tipo=TipoCartao.TEXTO, titulo_padrao="Observações", contextos=(A, D),
        descricao="Texto livre: procedimento, contato do fornecedor, lembrete.",
        opcoes=("conteudo",),
    ),
    DefCartao(
        tipo=TipoCartao.AUDITORIA, titulo_padrao="Histórico de alterações",
        contextos=(A, D), descricao="Quem mudou o quê, e quando.", opcoes=("limite",),
    ),
    DefCartao(
        tipo=TipoCartao.ACOES, titulo_padrao="Ações Rápidas", contextos=(A, D),
        descricao="Botões que executam módulos de ação.",
        disponivel=False, motivo="subsistema de ação — marco M4",
    ),
)

POR_TIPO: dict[TipoCartao, DefCartao] = {d.tipo: d for d in CATALOGO}

ESCOPO_PADRAO = {Contexto.ATIVO: "padrao_ativo", Contexto.DISPOSITIVO: "padrao_dispositivo"}


class Cartao(BaseModel):
    tipo: TipoCartao
    #: ``None`` usa o título do catálogo. Preencher renomeia só nesta tela.
    titulo: str | None = None
    largura: int = Field(default=1, ge=1, le=4)
    visivel: bool = True
    opcoes: dict = Field(default_factory=dict)

    def titulo_efetivo(self) -> str:
        return self.titulo or POR_TIPO[self.tipo].titulo_padrao


class Arranjo(BaseModel):
    escopo: str
    contexto: Contexto
    cartoes: tuple[Cartao, ...]

    @field_validator("cartoes")
    @classmethod
    def _nao_vazio(cls, v: tuple[Cartao, ...]) -> tuple[Cartao, ...]:
        if not v:
            raise ValueError("um arranjo precisa de ao menos um cartão")
        return v

    def validar_contexto(self) -> None:
        """Recusa cartão que não faz sentido no contexto.

        Componentes numa ficha de dispositivo, por exemplo: um rádio não tem
        dispositivos embarcados. Deixar passar produziria um cartão vazio que
        alguém tentaria entender.
        """
        for c in self.cartoes:
            definicao = POR_TIPO[c.tipo]
            if self.contexto not in definicao.contextos:
                raise ValueError(
                    f"cartão {c.tipo.value!r} não serve no contexto "
                    f"{self.contexto.value!r} — serve em "
                    f"{[x.value for x in definicao.contextos]}"
                )


def cascata(contexto: Contexto, chave: str, grupo: str | None) -> list[str]:
    """Escopos a procurar, do mais específico para o mais geral."""
    if contexto is Contexto.ATIVO:
        alvos = [f"ativo:{chave}"] + ([f"frota:{grupo}"] if grupo else [])
    else:
        alvos = [f"disp:{chave}"] + ([f"papel:{grupo}"] if grupo else [])
    return [*alvos, ESCOPO_PADRAO[contexto]]


ARRANJO_ATIVO_PADRAO = Arranjo(
    escopo="padrao_ativo",
    contexto=Contexto.ATIVO,
    cartoes=(
        Cartao(tipo=TipoCartao.RESUMO, largura=1),
        Cartao(tipo=TipoCartao.ALCANCE, largura=1),
        Cartao(tipo=TipoCartao.TELEMETRIA, largura=1),
        Cartao(tipo=TipoCartao.COMPONENTES, largura=4),
        Cartao(tipo=TipoCartao.TRANSICOES, largura=2),
        Cartao(tipo=TipoCartao.ACOES, largura=2),
        Cartao(tipo=TipoCartao.DISPOSITIVOS, largura=4),
    ),
)

ARRANJO_DISPOSITIVO_PADRAO = Arranjo(
    escopo="padrao_dispositivo",
    contexto=Contexto.DISPOSITIVO,
    cartoes=(
        Cartao(tipo=TipoCartao.RESUMO, titulo="Resumo do dispositivo", largura=1),
        Cartao(tipo=TipoCartao.IDENTIDADE, largura=1),
        Cartao(tipo=TipoCartao.TELEMETRIA, largura=1),
        Cartao(tipo=TipoCartao.IMAGENS, largura=1),
        Cartao(tipo=TipoCartao.TRANSICOES, largura=2),
        Cartao(tipo=TipoCartao.AUDITORIA, largura=2),
    ),
)

PADROES = {
    "padrao_ativo": ARRANJO_ATIVO_PADRAO,
    "padrao_dispositivo": ARRANJO_DISPOSITIVO_PADRAO,
}
