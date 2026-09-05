"""Formatos de saída — e a mesma regra em todos: a ressalva vai junto.

Um relatório sai da tela e vira anexo de e-mail, print em reunião, aba de
planilha. Se as ressalvas ficam só na tela, o número viaja sozinho e é citado
sozinho: *"disponibilidade de 94%"* sem *"de 22 dos 46 sondados"*.

Por isso o CSV leva as ressalvas em comentário no topo e a impressão leva no
rodapé. Não é capricho de formatação — é a diferença entre o número ser
defensável e não ser.

Sobre PDF: não há gerador aqui de propósito. Toda biblioteca de PDF em Python
traz peso e uma superfície de manutenção que este projeto não precisa carregar
para resolver um problema que o navegador já resolve. A saída de impressão é
HTML com folha de estilo de página; ``Ctrl+P → salvar como PDF`` produz o
arquivo, com as fontes e a paginação do sistema de quem imprime.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from html import escape
from typing import Any

from .modelo import Relatorio, TipoColuna


def formatar_valor(valor: Any, tipo: TipoColuna) -> str:
    """Um valor, do jeito que se lê. Vazio é vazio — nunca zero.

    ``None`` vira travessão em todos os formatos porque a distinção importa:
    zero é uma medição, ausência é a falta de uma.
    """
    if valor is None or valor == "":
        return "—"
    if tipo is TipoColuna.PERCENTUAL:
        return f"{valor:.2f}%".replace(".", ",")
    if tipo is TipoColuna.DURACAO:
        return duracao(float(valor))
    if tipo is TipoColuna.INSTANTE:
        return valor.strftime("%d/%m/%Y %H:%M") if isinstance(valor, datetime) else str(valor)
    if tipo is TipoColuna.NUMERO and isinstance(valor, int | float):
        texto = f"{valor:,.2f}" if isinstance(valor, float) else f"{valor:,}"
        return texto.replace(",", "§").replace(".", ",").replace("§", ".")
    return str(valor)


def numero(valor: float, casas: int = 1) -> str:
    """Número no formato daqui: milhar com ponto, decimal com vírgula.

    Vale principalmente para a frase de resumo, que é a linha que mais gente
    lê — e onde "13804.6" obriga quem lê a contar as casas.
    """
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def duracao(segundos: float) -> str:
    """Segundos viram algo que se lê de relance.

    "3.842 s" obriga quem lê a fazer uma divisão de cabeça no meio de uma
    reunião, e a conta que se faz de cabeça é a que sai errada.
    """
    s = int(round(segundos))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    if s < 86400:
        return f"{s // 3600} h {(s % 3600) // 60:02d} min"
    return f"{s // 86400} d {(s % 86400) // 3600:02d} h"


def para_csv(r: Relatorio) -> str:
    """CSV com título, período e ressalvas em comentário no topo.

    Os valores saem **crus**, não formatados: a planilha é para calcular em
    cima, e "94,32%" como texto quebra qualquer fórmula. A formatação é
    problema da apresentação; o CSV é dado.
    """
    saida = io.StringIO()
    saida.write(f"# {r.titulo}\n")
    saida.write(f"# período: {r.desde:%d/%m/%Y %H:%M} a {r.ate:%d/%m/%Y %H:%M}\n")
    if r.resumo:
        saida.write(f"# resumo: {r.resumo}\n")
    for nome, valor in (r.parametros or {}).items():
        if valor not in (None, "", 0):
            saida.write(f"# parâmetro: {nome}={valor}\n")
    for nota in r.notas:
        saida.write(f"# ressalva: {nota}\n")

    nomes = [c.nome for c in r.colunas]
    escritor = csv.writer(saida)
    escritor.writerow([c.rotulo + (f" ({c.unidade})" if c.unidade else "") for c in r.colunas])
    for linha in r.linhas:
        escritor.writerow([_cru(linha.get(n)) for n in nomes])
    if r.totais:
        escritor.writerow(
            ["TOTAL"] + [_cru(r.totais.get(n)) for n in nomes[1:]]
        )
    return saida.getvalue()


def _cru(valor: Any) -> Any:
    if isinstance(valor, datetime):
        return valor.isoformat()
    return "" if valor is None else valor


ESTILO = """
@page { size: A4 landscape; margin: 14mm; }
* { box-sizing: border-box; }
body { font: 12px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
       color: #14181f; margin: 0; }
h1 { font-size: 19px; margin: 0 0 2px; }
.periodo { color: #5a6472; font-size: 12px; margin: 0 0 14px; }
.resumo { background: #f2f5f9; border-left: 3px solid #1a7a46; padding: 9px 12px;
          margin: 0 0 14px; font-size: 13px; }
table { border-collapse: collapse; width: 100%; font-size: 11.5px; }
th, td { padding: 5px 8px; border-bottom: 1px solid #dfe4ea; text-align: left;
         vertical-align: top; }
th { background: #f2f5f9; font-weight: 600; border-bottom: 1.5px solid #b9c2ce; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.totais td { border-top: 1.5px solid #b9c2ce; font-weight: 600; background: #fafbfc; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
.ressalvas { margin-top: 16px; font-size: 11px; color: #3d4653; }
.ressalvas h2 { font-size: 12px; margin: 0 0 5px; text-transform: uppercase;
                letter-spacing: .04em; color: #5a6472; }
.ressalvas li { margin-bottom: 4px; }
.rodape { margin-top: 14px; font-size: 10px; color: #7b8593;
          border-top: 1px solid #dfe4ea; padding-top: 7px; }
@media print { .dica-impressao { display: none; } }
.dica-impressao { margin: 0 0 14px; padding: 8px 12px; background: #fff8e6;
                  border: 1px solid #e8d9a8; font-size: 12px; border-radius: 4px; }
"""


def para_impressao(r: Relatorio, gerado_por: str = "") -> str:
    """HTML pronto para papel — e para virar PDF pelo navegador.

    A tabela repete o cabeçalho a cada página (``display: table-header-group``)
    e evita quebrar linha no meio. As ressalvas vão no rodapé, na mesma folha:
    quem recebe o papel recebe o que ficou de fora.
    """
    cabecalho = "".join(
        f'<th class="{_classe(c)}">{escape(c.rotulo)}'
        f'{f" <small>({escape(c.unidade)})</small>" if c.unidade else ""}</th>'
        for c in r.colunas
    )
    corpo = "".join(
        "<tr>"
        + "".join(
            f'<td class="{_classe(c)}">'
            f"{escape(formatar_valor(linha.get(c.nome), c.tipo))}</td>"
            for c in r.colunas
        )
        + "</tr>"
        for linha in r.linhas
    ) or (
        f'<tr><td colspan="{len(r.colunas)}">'
        "Sem linhas no período — o que não é o mesmo que sem problema. "
        "Veja as ressalvas.</td></tr>"
    )
    totais = ""
    if r.totais:
        celulas = []
        for i, c in enumerate(r.colunas):
            if i == 0:
                celulas.append('<td class="">Total</td>')
            elif c.nome in r.totais:
                celulas.append(
                    f'<td class="{_classe(c)}">'
                    f"{escape(formatar_valor(r.totais[c.nome], c.tipo))}</td>"
                )
            else:
                celulas.append(f'<td class="{_classe(c)}"></td>')
        totais = f'<tr class="totais">{"".join(celulas)}</tr>'

    ressalvas = ""
    if r.notas:
        itens = "".join(f"<li>{escape(n)}</li>" for n in r.notas)
        ressalvas = (
            f'<div class="ressalvas"><h2>O que ficou de fora</h2><ul>{itens}</ul></div>'
        )

    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    assinatura = f" por {escape(gerado_por)}" if gerado_por else ""
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<title>{escape(r.titulo)}</title><style>{ESTILO}</style></head><body>
<p class="dica-impressao">Para gerar o PDF: <b>Ctrl+P</b> (ou ⌘P) e escolha
&ldquo;Salvar como PDF&rdquo;. Esta faixa não sai no papel.</p>
<h1>{escape(r.titulo)}</h1>
<p class="periodo">Período de {r.desde:%d/%m/%Y %H:%M} a {r.ate:%d/%m/%Y %H:%M}</p>
{f'<p class="resumo">{escape(r.resumo)}</p>' if r.resumo else ""}
<table><thead><tr>{cabecalho}</tr></thead><tbody>{corpo}{totais}</tbody></table>
{ressalvas}
<p class="rodape">Gerado em {agora}{assinatura} pela Plataforma TI + OT.
Os números acima valem para o período declarado e para os equipamentos
efetivamente observados nele.</p>
</body></html>"""


def _classe(coluna) -> str:
    return "num" if coluna.tipo in {
        TipoColuna.NUMERO, TipoColuna.PERCENTUAL, TipoColuna.DURACAO
    } else ""


__all__ = ["duracao", "formatar_valor", "numero", "para_csv", "para_impressao"]
