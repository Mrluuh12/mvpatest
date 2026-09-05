"""Contrato de módulo.

Um módulo declara o que faz num manifesto e a plataforma **recusa carregá-lo**
se a declaração for inválida. Duas recusas são de princípio, não de
configuração:

* **Zona proibida.** Um manifesto que declare operar nos níveis 0 a 2 do
  Purdue não é rejeitado em tempo de execução, quando já seria tarde — é
  rejeitado no carregamento. Lá vivem os 32 CLPs que controlam processo
  físico.
* **Métrica fora do dicionário.** E a recusa vem com sugestão do nome certo,
  porque recusa que não ajuda a corrigir custa uma tarde de alguém.

O contrato é de **processo**, não de biblioteca: um módulo pode ser Python, Go
ou um script de dez linhas. O que a plataforma exige é a forma da declaração e
a forma do que sai.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

from inventario.modelo import ZONAS_PROIBIDAS, TipoAresta, Zona
from plataforma.dicionario import DERIVADAS, MetricaDesconhecida, validar


class Alvo(StrEnum):
    DISPOSITIVO = "dispositivo"  # fala com um equipamento por vez
    SISTEMA = "sistema"  # fala com UM sistema e cobre N ativos


class Descoberta(StrEnum):
    PROPRIA = "propria"  # o módulo descobre — e por isso é fonte de inventário
    DELEGADA = "delegada"  # a plataforma entrega a lista de alvos


class Qualidade(StrEnum):
    """Toda observação carrega qualidade. Herança do mundo OT, onde valor sem
    código de qualidade não é dado — e boa prática em todo o resto."""

    BOA = "boa"
    INCERTA = "incerta"
    RUIM = "ruim"


class Observacao(BaseModel):
    """A unidade de dado do canal de métricas."""

    sujeito: str
    metrica: str
    valor: float
    em: datetime = Field(default_factory=lambda: datetime.now(UTC))
    qualidade: Qualidade = Qualidade.BOA
    rotulos: dict[str, str] = Field(default_factory=dict)

    @field_validator("metrica")
    @classmethod
    def _no_dicionario(cls, v: str) -> str:
        validar(v)  # levanta MetricaDesconhecida com sugestão
        return v


class Relacao(BaseModel):
    """A unidade de dado do **canal de fatos**.

    Métrica responde "quanto"; relação responde "quem estava ligado a quem".
    São canais separados porque mudam em ritmos diferentes: a métrica muda a
    cada ciclo e é substituída, a relação dura horas e o que interessa nela é
    justamente **quando começou e quando deixou de valer**.

    ``destino`` é uma identidade observada, não uma chave do inventário: o
    vizinho pode ser um rádio que a planilha não tem. Quem resolve é a
    plataforma, que conhece os identificadores; o módulo relata o que viu.
    """

    origem: str
    destino: str
    tipo: TipoAresta
    atributos: dict[str, Any] = Field(default_factory=dict)

    #: O que foi medido **no enlace**, por nome canônico. Um rádio de malha
    #: não tem "o SNR": tem um por vizinho, e é aqui que cada um vem inteiro,
    #: sem a agregação que a ficha do aparelho mostra.
    #:
    #: A plataforma resolve o destino e grava sob a meia-aresta, porque o
    #: módulo não conhece o inventário — mesma divisão de trabalho das
    #: relações.
    medidas: dict[str, float] = Field(default_factory=dict)

    @field_validator("medidas")
    @classmethod
    def _medidas_canonicas(cls, v: dict[str, float]) -> dict[str, float]:
        for nome in v:
            validar(nome)
        return v


class Manifesto(BaseModel):
    """A declaração de um módulo. É por ela que a plataforma decide se carrega."""

    nome: str
    contrato: int = 1
    versao: str = "0.1.0"
    fabricante: str = "generico"

    alvo: Alvo = Alvo.DISPOSITIVO
    descoberta: Descoberta = Descoberta.DELEGADA

    intervalo_metricas_s: int = 60
    intervalo_fatos_s: int | None = None

    produz_metricas: tuple[str, ...] = ()
    produz_entidades: tuple[str, ...] = ()
    produz_relacoes: tuple[str, ...] = ()

    somente_leitura: bool = True
    zona_permitida: tuple[Zona, ...] = (Zona.CORPORATIVA,)

    #: Papéis que este módulo cobre. Vazio significa "todos" — é o caso do
    #: ICMP, que responde por qualquer coisa com endereço. Um módulo de rádio
    #: que recebesse os 388 alvos da zona reportaria 380 falhas de cobertura a
    #: cada ciclo: o número certo tem de ser sobre o que ele deveria alcançar.
    papeis_alvo: tuple[str, ...] = ()

    @field_validator("zona_permitida")
    @classmethod
    def _zona_valida(cls, v: tuple[Zona, ...]) -> tuple[Zona, ...]:
        if proibidas := sorted(z.value for z in v if z in ZONAS_PROIBIDAS):
            raise ValueError(
                f"zona {proibidas} é inválida em qualquer manifesto: lá vivem os "
                "controladores de processo. Não é configuração, é impossibilidade."
            )
        if not v:
            raise ValueError("um módulo precisa declarar ao menos uma zona")
        return v

    @field_validator("produz_metricas")
    @classmethod
    def _metricas_conhecidas(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for nome in v:
            validar(nome)
            if nome in DERIVADAS:
                raise ValueError(
                    f"{nome!r} é calculada pela plataforma a partir do grafo. "
                    "Um módulo sem estado não teria como computá-la."
                )
        return v

    @model_validator(mode="after")
    def _coerencia(self) -> Manifesto:
        if self.intervalo_metricas_s < 1:
            raise ValueError("intervalo de métricas precisa ser de ao menos 1 segundo")
        if self.descoberta is Descoberta.PROPRIA and not self.produz_entidades:
            raise ValueError(
                "um módulo que descobre sozinho é fonte de inventário: "
                "precisa declarar as entidades que produz"
            )
        return self

    def pode_operar_em(self, zona: Zona) -> bool:
        return zona in self.zona_permitida


class ResultadoColeta(BaseModel):
    """O que uma rodada de coleta devolve — inclusive quando corre mal.

    ``alvos_falha`` não é detalhe de log: é o que separa "perguntei e está
    ruim" de "não consegui perguntar". Sem essa distinção, uma queda do
    coletor vira centenas de incidentes falsos.
    """

    observacoes: tuple[Observacao, ...] = ()
    relacoes: tuple[Relacao, ...] = ()
    alvos_total: int = 0
    alvos_falha: int = 0
    duracao_s: float = 0.0
    rejeitadas: tuple[str, ...] = ()

    #: ``relacoes`` é a vizinhança **inteira** que este módulo observa?
    #:
    #: Só com ``True`` a plataforma pode fechar uma aresta que sumiu da lista,
    #: porque só aí a ausência significa "deixou de existir". Numa leitura
    #: parcial — o Prometheus fora do ar, metade das consultas falhando — a
    #: ausência significa "não perguntei", e fechar tudo escreveria que a
    #: malha inteira se desfez.
    #:
    #: O padrão é ``False`` de propósito: um módulo que esqueça de declarar
    #: nunca provoca fechamento em massa. É a mesma regra da autorização —
    #: nega por omissão.
    relacoes_completas: bool = False

    @property
    def completa(self) -> bool:
        return self.alvos_falha == 0


class Modulo(Protocol):
    """O que a plataforma espera de um módulo de coleta."""

    manifesto: Manifesto

    async def coletar(self, alvos: list[dict[str, Any]]) -> ResultadoColeta: ...


def filtrar_observacoes(
    brutas: list[Observacao | dict[str, Any]],
) -> tuple[tuple[Observacao, ...], tuple[str, ...]]:
    """Separa o que o dicionário aceita do que ele recusa.

    A recusa é devolvida, nunca engolida: quem escreveu o módulo precisa ver o
    nome recusado para corrigi-lo.
    """
    aceitas: list[Observacao] = []
    recusadas: list[str] = []
    for bruta in brutas:
        # O dicionário é conferido antes de construir, e não pelo validador do
        # modelo, porque o Pydantic embrulha a exceção — e o embrulho perde a
        # sugestão do nome certo, que é justamente o que torna a recusa útil.
        nome = bruta.metrica if isinstance(bruta, Observacao) else bruta.get("metrica")
        try:
            validar(str(nome))
        except MetricaDesconhecida as erro:
            recusadas.append(str(erro))
            continue

        try:
            aceitas.append(bruta if isinstance(bruta, Observacao) else Observacao(**bruta))
        except Exception as erro:  # noqa: BLE001 - observação malformada também é recusa
            recusadas.append(f"observação inválida: {erro}")
    return tuple(aceitas), tuple(recusadas)
