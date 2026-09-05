"""Relatórios: uma pergunta sobre um período, com as ressalvas junto.

O catálogo é a parte de uma plataforma de monitoramento em que se perde ou se
ganha a comparação com o SolarWinds, que traz mais de cem modelos prontos. A
resposta aqui não é competir em número: é que **cada relatório diga o que
ficou de fora dele**. Um relatório que não declara suas ausências é um
relatório que vai ser citado errado numa reunião — e uma vez citado errado,
ninguém mais confia na ferramenta inteira.

Como o catálogo está organizado
-------------------------------

Por pergunta, não por tabela de origem. Quem procura relatório não pensa "isto
sai da tabela de transições", pensa "quanto a britagem ficou parada".

* **Disponibilidade** — quanto esteve de pé, quando não esteve, dia a dia
* **Capacidade** — quando o enlace satura, quais portas passam mais tráfego
* **Enlaces** — a qualidade da malha, que é o que uma ferramenta de nós não vê
* **Inventário** — o que existe e de quanto disso a plataforma sabe
* **Eventos** — o que os equipamentos contaram sozinhos
* **Governança** — quem mexeu, quem sondou, onde o cadastro se contradiz

Este pacote fica fora de ``db/`` de propósito: nem todo relatório é uma
consulta SQL. A previsão de capacidade pergunta ao Prometheus, e um pacote
dentro de ``db/`` que abre conexão HTTP confunde quem for mexer nele depois.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from . import capacidade, disponibilidade, enlaces, eventos, governanca, inventario
from .formatos import duracao, formatar_valor, para_csv, para_impressao
from .modelo import (
    P_FROTA,
    P_LIMITE,
    P_ZONA,
    ROTULO_CATEGORIA,
    Categoria,
    Coluna,
    Definicao,
    Param,
    Relatorio,
    TipoColuna,
    TipoParam,
)

P_MINIMO = Param(
    nome="minimo_s", rotulo="Ignorar quedas menores que", tipo=TipoParam.INTEIRO,
    padrao=0, ajuda="em segundos; útil para separar oscilação de queda de verdade",
)

RELATORIOS: dict[str, Definicao] = {
    # ------------------------------ disponibilidade
    "disponibilidade_frota": Definicao(
        "Por frota",
        "Média e pior por frota e função de negócio.",
        Categoria.DISPONIBILIDADE,
        disponibilidade.por_frota,
        (P_FROTA, P_ZONA),
    ),
    "disponibilidade_dia": Definicao(
        "Dia a dia",
        "Disponibilidade de cada dia do período.",
        Categoria.DISPONIBILIDADE,
        disponibilidade.por_dia,
        (P_FROTA, P_ZONA),
    ),
    "piores_disponibilidades": Definicao(
        "Piores equipamentos",
        "Equipamentos com mais tempo fora do ar.",
        Categoria.DISPONIBILIDADE,
        disponibilidade.piores,
        (P_LIMITE, P_FROTA, P_ZONA),
    ),
    "quedas": Definicao(
        "Quedas, uma a uma",
        "Cada queda com início, fim e duração.",
        Categoria.DISPONIBILIDADE,
        disponibilidade.quedas,
        (P_FROTA, P_ZONA, P_MINIMO),
    ),
    # ------------------------------ capacidade
    "previsao_interfaces": Definicao(
        "Saturação prevista",
        "Dias até o limiar de utilização, com a confiança da projeção.",
        Categoria.CAPACIDADE,
        capacidade.previsao_interfaces,
        (
            P_LIMITE,
            Param("aviso_pct", "Limiar de aviso", TipoParam.DECIMAL, 70,
                  ajuda="percentual de utilização em que se quer ser avisado"),
            Param("critico_pct", "Limiar crítico", TipoParam.DECIMAL, 90),
        ),
        exige_series=True,
        janela_padrao="24h",
    ),
    "top_interfaces": Definicao(
        "Tráfego por porta",
        "Entrada, saída, erros e descartes por porta.",
        Categoria.CAPACIDADE,
        capacidade.top_interfaces,
        (
            P_LIMITE,
            Param("ordenar", "Ordenar por", TipoParam.ESCOLHA, "entrada_mbps",
                  ("entrada_mbps", "saida_mbps", "erros_s", "descartes_s")),
        ),
        exige_series=True,
        janela_padrao="24h",
    ),
    # ------------------------------ enlaces
    "enlaces_qualidade": Definicao(
        "Enlaces: qualidade",
        "SNR, sinal, capacidade e assimetria de cada enlace aberto.",
        Categoria.DESEMPENHO,
        enlaces.qualidade,
        (
            P_LIMITE,
            Param("snr_minimo_db", "Só abaixo de (SNR)", TipoParam.DECIMAL, None,
                  ajuda="em dB; vazio traz todos"),
        ),
    ),
    "enlaces_instaveis": Definicao(
        "Enlaces instáveis",
        "Enlaces que mais abriram e fecharam no período.",
        Categoria.DESEMPENHO,
        enlaces.instabilidade,
        (P_LIMITE,),
    ),
    # ------------------------------ inventário
    "cobertura": Definicao(
        "Cobertura da coleta",
        "Quanto do parque é observado, por papel.",
        Categoria.INVENTARIO,
        inventario.cobertura,
    ),
    "parque": Definicao(
        "O parque",
        "Contagem por papel, zona e fabricante.",
        Categoria.INVENTARIO,
        inventario.parque,
        (P_ZONA,),
    ),
    # ------------------------------ eventos
    "eventos_severidade": Definicao(
        "Por gravidade",
        "Contagem de eventos por gravidade.",
        Categoria.EVENTOS,
        eventos.por_severidade,
    ),
    "eventos_faladores": Definicao(
        "Quem mais falou",
        "Origens que mais mandaram evento.",
        Categoria.EVENTOS,
        eventos.faladores,
        (
            P_LIMITE,
            Param("apenas_graves", "Só erro ou pior", TipoParam.ESCOLHA, "",
                  ("", "sim")),
        ),
    ),
    # ------------------------------ governança
    "alteracoes": Definicao(
        "Alterações",
        "Ações registradas, inclusive tentativas recusadas.",
        Categoria.GOVERNANCA,
        governanca.alteracoes,
        (P_LIMITE, Param("login", "Só de", TipoParam.TEXTO, "", ajuda="login exato")),
    ),
    "sondagens": Definicao(
        "Diagnósticos",
        "Sondas disparadas, por quem e com que resultado.",
        Categoria.GOVERNANCA,
        governanca.sondagens,
        (P_LIMITE,),
    ),
    "higiene": Definicao(
        "Contradições",
        "Contradições do cadastro, por categoria.",
        Categoria.GOVERNANCA,
        governanca.higiene,
    ),
}


def catalogo(com_series: bool = True) -> list[dict]:
    """O catálogo para a tela, agrupado por categoria.

    ``com_series`` falso marca os relatórios que dependem do Prometheus como
    indisponíveis **com o motivo**, em vez de deixá-los gerar uma tabela vazia
    que parece "não aconteceu nada".
    """
    return [
        {
            "nome": nome,
            "rotulo": d.rotulo,
            "descricao": d.descricao,
            "categoria": d.categoria.value,
            "categoria_rotulo": ROTULO_CATEGORIA[d.categoria],
            "janela_padrao": d.janela_padrao,
            "parametros": [p.para_json() for p in d.parametros],
            "disponivel": com_series or not d.exige_series,
            "motivo": (
                ""
                if com_series or not d.exige_series
                else "precisa do Prometheus: defina PLATAFORMA_PROMETHEUS e ligue "
                     "a exportação em /metrics"
            ),
        }
        for nome, d in RELATORIOS.items()
    ]


async def gerar(
    conexao: AsyncConnection,
    nome: str,
    desde: datetime,
    ate: datetime | None = None,
    parametros: dict[str, Any] | None = None,
) -> Relatorio:
    if nome not in RELATORIOS:
        raise KeyError(nome)
    definicao = RELATORIOS[nome]
    valores = definicao.ler_parametros(parametros or {})
    return await definicao.gerar(conexao, desde, ate or datetime.now(UTC), valores)


__all__ = [
    "RELATORIOS",
    "Categoria",
    "Coluna",
    "Definicao",
    "Param",
    "Relatorio",
    "TipoColuna",
    "TipoParam",
    "catalogo",
    "duracao",
    "formatar_valor",
    "gerar",
    "para_csv",
    "para_impressao",
]
