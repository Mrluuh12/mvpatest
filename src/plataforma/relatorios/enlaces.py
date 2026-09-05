"""Enlaces: o relatório que nenhuma ferramenta de rede genérica faz.

Uma plataforma de rede vê **nós e portas**. Numa malha Rajant, o que quebra a
operação não é um nó nem uma porta: é o *enlace* entre dois rádios, que existe
por horas, muda de qualidade o tempo todo e some quando o caminhão entra na
cava. Por isso o enlace aqui é objeto de primeira classe — tem sujeito
próprio, medições próprias e validade com início e fim.

Duas coisas que só existem porque o enlace é dirigido (``a>b`` não é ``b>a``):

**A assimetria aparece.** O SNR que A vê de B não é o que B vê de A, e a
diferença entre os dois é sintoma — antena torta, potência desigual, obstáculo
de um lado só. Uma modelagem simétrica faria a média dos dois e apagaria
justamente o sinal.

**Enlace instável tem nome.** O que troca de par toda hora não aparece como
"queda", porque nó nenhum caiu: aparece como um enlace que abriu e fechou
quarenta vezes, e é isso que a malha faz quando está no limite.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.db.esquema import aresta, dispositivo, leitura
from plataforma.db.grafo import sujeito_do_enlace

from .modelo import Coluna, Relatorio, TipoColuna

#: Métricas de enlace em que **menor é melhor**. Ordenar sem saber disso
#: colocaria o melhor enlace no topo da lista dos piores.
MENOR_E_MELHOR = frozenset({"malha_custo_link", "rf_ruido_dbm"})


async def _nomes(conexao: AsyncConnection) -> dict[str, str]:
    return dict(
        (
            await conexao.execute(select(dispositivo.c.chave, dispositivo.c.nome_canonico))
        ).all()
    )


async def qualidade(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """Cada enlace aberto, com o que foi medido nele — e o par contrário junto.

    A coluna "assimetria" é a diferença entre o SNR nos dois sentidos. É a
    coluna que faz alguém subir na torre.
    """
    nomes = await _nomes(conexao)
    limite = max(1, int(p.get("limite") or 30))
    piso = p.get("snr_minimo_db")

    abertas = (
        await conexao.execute(
            select(
                aresta.c.origem_chave, aresta.c.destino_chave, aresta.c.tipo,
                aresta.c.validade,
            ).where(aresta.c.validade.op("@>")(ate))
        )
    ).all()

    sujeitos = [sujeito_do_enlace(a.origem_chave, a.destino_chave) for a in abertas]
    medidas: dict[str, dict[str, float]] = defaultdict(dict)
    if sujeitos:
        for m in (
            await conexao.execute(
                select(leitura.c.sujeito, leitura.c.metrica, leitura.c.valor).where(
                    leitura.c.sujeito.in_(sujeitos)
                )
            )
        ).all():
            medidas[m.sujeito][m.metrica] = m.valor

    linhas, sem_medida = [], 0
    for a in abertas:
        s = sujeito_do_enlace(a.origem_chave, a.destino_chave)
        med = medidas.get(s, {})
        if not med:
            sem_medida += 1
            continue
        contrario = medidas.get(sujeito_do_enlace(a.destino_chave, a.origem_chave), {})
        snr = med.get("rf_snr_db")
        snr_volta = contrario.get("rf_snr_db")
        linhas.append(
            {
                "origem": nomes.get(a.origem_chave, a.origem_chave),
                "destino": nomes.get(a.destino_chave, a.destino_chave),
                "tipo": a.tipo,
                "snr_db": _r(snr),
                "sinal_dbm": _r(med.get("rf_rssi_dbm")),
                "capacidade_mbps": _r(med.get("rf_capacidade_estimada_mbps")),
                "custo": _r(med.get("malha_custo_link")),
                "assimetria_db": (
                    _r(abs(snr - snr_volta)) if snr is not None and snr_volta is not None
                    else None
                ),
                "aberto_desde": a.validade.lower,
            }
        )

    if piso not in (None, ""):
        linhas = [x for x in linhas if x["snr_db"] is not None and x["snr_db"] < float(piso)]
    linhas.sort(key=lambda x: (x["snr_db"] if x["snr_db"] is not None else 999))

    r = Relatorio(
        nome="enlaces_qualidade",
        titulo="Qualidade dos enlaces abertos, do pior para o melhor",
        desde=desde, ate=ate, linhas=linhas[:limite], parametros=p,
        colunas=(
            Coluna("origem", "De"),
            Coluna("destino", "Para"),
            Coluna("tipo", "Tipo", TipoColuna.SELO),
            Coluna("snr_db", "SNR", TipoColuna.NUMERO, unidade="dB", soma=False),
            Coluna("sinal_dbm", "Sinal", TipoColuna.NUMERO, unidade="dBm", soma=False),
            Coluna("capacidade_mbps", "Capacidade", TipoColuna.NUMERO,
                   unidade="Mb/s", soma=False),
            Coluna("custo", "Custo", TipoColuna.NUMERO, soma=False),
            Coluna("assimetria_db", "Assimetria", TipoColuna.NUMERO,
                   unidade="dB", soma=False),
            Coluna("aberto_desde", "Aberto desde", TipoColuna.INSTANTE),
        ),
    )
    if linhas:
        pior = linhas[0]
        r.resumo = (
            f"o enlace mais fraco é {pior['origem']} → {pior['destino']}"
            + (f", com {pior['snr_db']} dB." if pior["snr_db"] is not None else ".")
        )
    tortos = [x for x in linhas if (x["assimetria_db"] or 0) >= 6]
    if tortos:
        r.notas.append(
            f"{len(tortos)} enlaces com 6 dB ou mais entre os dois sentidos."
        )
    if sem_medida:
        r.notas.append(
            f"{sem_medida} enlaces abertos sem medição — fora desta tabela."
        )
    r.notas.append(
        "O enlace é dirigido: A→B e B→A são medições diferentes. A coluna de "
        "assimetria compara as duas."
    )
    return r


async def instabilidade(
    conexao: AsyncConnection, desde: datetime, ate: datetime, p: dict
) -> Relatorio:
    """Os enlaces que mais abriram e fecharam no período.

    Nenhum equipamento caiu, e mesmo assim a malha esteve ruim. É o modo de
    falha que um relatório de disponibilidade por nó **não mostra** — e o
    motivo de guardar aresta com validade em vez de sobrescrever a vizinhança.
    """
    nomes = await _nomes(conexao)
    limite = max(1, int(p.get("limite") or 20))

    linhas_bd = (
        await conexao.execute(
            select(aresta.c.origem_chave, aresta.c.destino_chave, aresta.c.validade)
        )
    ).all()

    contagem: dict[tuple[str, str], list] = defaultdict(list)
    for ln in linhas_bd:
        inicio, fim = ln.validade.lower, ln.validade.upper
        if inicio and inicio > ate:
            continue
        if fim and fim < desde:
            continue
        contagem[(ln.origem_chave, ln.destino_chave)].append((inicio, fim))

    linhas = []
    for (o, d), periodos in contagem.items():
        fechamentos = sum(1 for _, f in periodos if f is not None and desde <= f <= ate)
        aberturas = sum(1 for i, _ in periodos if i is not None and desde <= i <= ate)
        if aberturas + fechamentos < 2:
            continue
        vivo = any(f is None for _, f in periodos)
        linhas.append(
            {
                "origem": nomes.get(o, o),
                "destino": nomes.get(d, d),
                "aberturas": aberturas,
                "fechamentos": fechamentos,
                "trocas": aberturas + fechamentos,
                "situacao": "aberto agora" if vivo else "fechado",
            }
        )
    linhas.sort(key=lambda x: -x["trocas"])

    r = Relatorio(
        nome="enlaces_instaveis",
        titulo="Enlaces que mais abriram e fecharam no período",
        desde=desde, ate=ate, linhas=linhas[:limite], parametros=p,
        colunas=(
            Coluna("origem", "De"),
            Coluna("destino", "Para"),
            Coluna("aberturas", "Abriu", TipoColuna.NUMERO),
            Coluna("fechamentos", "Fechou", TipoColuna.NUMERO),
            Coluna("trocas", "Trocas", TipoColuna.NUMERO),
            Coluna("situacao", "Agora", TipoColuna.SELO),
        ),
    )
    r.somar()
    if linhas:
        r.resumo = (
            f"{linhas[0]['origem']} → {linhas[0]['destino']} trocou de estado "
            f"{linhas[0]['trocas']} vezes."
        )
    r.notas.append(
        "Abrir e fechar é normal numa malha móvel. O que conta aqui é quem faz "
        "isso muito mais que os vizinhos."
    )
    r.notas.append(
        "Fechamento só conta quando o módulo declara ter visto a vizinhança "
        "inteira; numa leitura parcial, nada é fechado."
    )
    return r


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 2)
