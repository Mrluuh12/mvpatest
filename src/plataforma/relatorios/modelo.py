"""Tipos do motor de relatórios.

Um relatório aqui é uma **pergunta sobre um período, com as ressalvas junto**.
A ressalva não é rodapé decorativo: *"disponibilidade de 94%"* sem *"de 22 dos
46 sondados"* é número que alguém defende numa reunião sem saber o que está
defendendo.

Três coisas que este modelo carrega e que a versão anterior não tinha:

**Coluna com tipo.** Sem isso a tela alinhava percentual à esquerda, o CSV
mandava duração em segundos crus e ninguém sabia se ``0`` era zero medido ou
zero por falta de medição. O tipo viaja com a coluna e os três formatos —
tela, CSV, impressão — usam o mesmo.

**Parâmetro declarado.** Relatório que só aceita "de quando até quando" força
quem pergunta a filtrar na planilha depois. Aqui o filtro é do relatório, com
o tipo do controle declarado, do mesmo jeito que as opções de cartão.

**Totais separados das linhas.** Somar uma coluna de percentuais dá um número
sem significado; somar uma de contagens dá o total certo. Quem sabe qual é
qual é a definição da coluna, não quem desenha a tabela.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Categoria(StrEnum):
    """Agrupamento na tela. Espelha como as pessoas procuram relatório:
    pela pergunta que querem responder, não pela tabela de onde sai."""

    DISPONIBILIDADE = "disponibilidade"
    DESEMPENHO = "desempenho"
    CAPACIDADE = "capacidade"
    INVENTARIO = "inventario"
    EVENTOS = "eventos"
    GOVERNANCA = "governanca"


ROTULO_CATEGORIA = {
    Categoria.DISPONIBILIDADE: "Disponibilidade",
    Categoria.DESEMPENHO: "Desempenho",
    Categoria.CAPACIDADE: "Capacidade e previsão",
    Categoria.INVENTARIO: "Inventário e cobertura",
    Categoria.EVENTOS: "Eventos",
    Categoria.GOVERNANCA: "Governança",
}


class TipoColuna(StrEnum):
    TEXTO = "texto"
    NUMERO = "numero"
    PERCENTUAL = "percentual"
    DURACAO = "duracao"  # em segundos, formatada na apresentação
    INSTANTE = "instante"
    SELO = "selo"  # texto curto que a tela pinta como pastilha


class TipoParam(StrEnum):
    """A forma do controle que a tela desenha. Sem o tipo, o parâmetro é só um
    nome numa lista e a interface teria de conhecer cada relatório."""

    JANELA = "janela"
    INTEIRO = "inteiro"
    TEXTO = "texto"
    ESCOLHA = "escolha"
    DECIMAL = "decimal"


#: Colunas que **nunca** somam, por tipo. Percentual somado dá 4.700%.
NAO_SOMAM = frozenset({TipoColuna.PERCENTUAL, TipoColuna.TEXTO, TipoColuna.SELO,
                       TipoColuna.INSTANTE})


@dataclass(frozen=True)
class Coluna:
    nome: str
    rotulo: str
    tipo: TipoColuna = TipoColuna.TEXTO
    unidade: str = ""
    #: Somar esta coluna no rodapé faz sentido? O padrão vem do tipo, mas
    #: contagem de dispositivos soma e média de latência não — e só quem
    #: escreveu o relatório sabe a diferença.
    soma: bool | None = None

    @property
    def somavel(self) -> bool:
        return self.tipo not in NAO_SOMAM if self.soma is None else self.soma

    def para_json(self) -> dict:
        return {
            "nome": self.nome, "rotulo": self.rotulo, "tipo": self.tipo.value,
            "unidade": self.unidade, "somavel": self.somavel,
        }


@dataclass(frozen=True)
class Param:
    nome: str
    rotulo: str
    tipo: TipoParam
    padrao: Any = None
    escolhas: tuple[str, ...] = ()
    ajuda: str = ""

    def para_json(self) -> dict:
        return {
            "nome": self.nome, "rotulo": self.rotulo, "tipo": self.tipo.value,
            "padrao": self.padrao, "escolhas": list(self.escolhas), "ajuda": self.ajuda,
        }

    def converter(self, bruto: Any) -> Any:
        """Do texto da query para o valor. Erro vira ``ValueError`` com o nome
        do parâmetro, porque "422" sozinho não diz qual campo estava errado."""
        if bruto is None or bruto == "":
            return self.padrao
        try:
            if self.tipo is TipoParam.INTEIRO:
                return int(bruto)
            if self.tipo is TipoParam.DECIMAL:
                return float(bruto)
        except (TypeError, ValueError) as erro:
            raise ValueError(f"{self.rotulo!r}: {bruto!r} não é número") from erro
        if self.tipo is TipoParam.ESCOLHA and self.escolhas and bruto not in self.escolhas:
            raise ValueError(
                f"{self.rotulo!r}: {bruto!r} não é uma opção — use "
                f"{', '.join(self.escolhas)}"
            )
        return bruto


#: Parâmetros que quase todo relatório de recorte aceita. Declarados uma vez
#: para que o rótulo e a ajuda não divirjam entre relatórios.
P_FROTA = Param(
    nome="frota", rotulo="Frota", tipo=TipoParam.TEXTO,
    ajuda="prefixo do ativo (CA, ERB…); vazio traz todas",
)
P_ZONA = Param(
    nome="zona", rotulo="Zona", tipo=TipoParam.ESCOLHA, padrao="",
    escolhas=("", "corporativa", "industrial_dmz", "ot_nivel3"),
    ajuda="as zonas de nível 0 a 2 não aparecem: a plataforma não as alcança",
)
P_LIMITE = Param(
    nome="limite", rotulo="Quantas linhas", tipo=TipoParam.INTEIRO, padrao=20,
    ajuda="o 'Top N' — quantos piores mostrar",
)


@dataclass
class Relatorio:
    nome: str
    titulo: str
    desde: datetime
    ate: datetime
    colunas: tuple[Coluna, ...]
    linhas: list[dict] = field(default_factory=list)
    #: O que ficou de fora, e por quê. Nunca vazio sem motivo.
    notas: list[str] = field(default_factory=list)
    #: Rodapé de totais, quando somar significa alguma coisa.
    totais: dict[str, Any] = field(default_factory=dict)
    #: Uma frase que resume o achado, para quem não vai ler a tabela.
    resumo: str = ""
    parametros: dict[str, Any] = field(default_factory=dict)

    def somar(self) -> None:
        """Preenche ``totais`` com as colunas que aceitam soma.

        Chamado pelo relatório, não pelo motor: há relatório em que a soma é
        exatamente o número errado a mostrar, e o silêncio é a resposta certa.
        """
        for c in self.colunas:
            if not c.somavel:
                continue
            valores = [linha.get(c.nome) for linha in self.linhas]
            numeros = [v for v in valores if isinstance(v, int | float)]
            if numeros:
                self.totais[c.nome] = round(sum(numeros), 2)

    def para_json(self) -> dict:
        return {
            "nome": self.nome,
            "titulo": self.titulo,
            "desde": self.desde,
            "ate": self.ate,
            "colunas": [c.para_json() for c in self.colunas],
            "linhas": self.linhas,
            "notas": self.notas,
            "totais": self.totais,
            "resumo": self.resumo,
            "parametros": self.parametros,
        }


@dataclass(frozen=True)
class Definicao:
    """Nome curto para o botão, frase inteira para quem quer entender.

    Usar a primeira linha do docstring no botão dava rótulos de oito palavras
    dentro de um retângulo de três.
    """

    rotulo: str
    descricao: str
    categoria: Categoria
    gerar: Any
    parametros: tuple[Param, ...] = ()
    #: Este relatório precisa do Prometheus? A tela avisa antes de gerar, em
    #: vez de devolver uma tabela vazia que parece "nada aconteceu".
    exige_series: bool = False

    #: A janela que faz sentido abrir este relatório. Sete dias é bom para
    #: disponibilidade e péssimo para uma série que começou ontem: a tabela
    #: vem vazia e parece defeito. Quem escolhe é o relatório, não a tela.
    janela_padrao: str = "7d"

    def ler_parametros(self, bruto: dict[str, Any]) -> dict[str, Any]:
        return {p.nome: p.converter(bruto.get(p.nome)) for p in self.parametros}


__all__ = [
    "NAO_SOMAM",
    "P_FROTA",
    "P_LIMITE",
    "P_ZONA",
    "ROTULO_CATEGORIA",
    "Categoria",
    "Coluna",
    "Definicao",
    "Param",
    "Relatorio",
    "TipoColuna",
    "TipoParam",
]
