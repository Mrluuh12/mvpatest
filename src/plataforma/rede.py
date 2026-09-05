"""Visão de rede: rádios, enlaces e o desenho da malha.

O resto da plataforma olha o parque por **ativo** — o caminhão, a escavadeira,
o que aquilo faz na operação. Esta seção olha por **enlace**, que é a unidade
que quebra a produção numa malha sem fio: nenhum rádio precisa cair para a
frota perder rede.

Três coisas moram aqui e em nenhum outro lugar:

* **O enlace de ida e o de volta na mesma linha.** O grafo guarda meia-aresta
  dirigida, porque o SNR que A vê de B não é o que B vê de A. Para operar, o
  útil é ver os dois lados juntos e a diferença entre eles.
* **A posição, com a idade dela.** O exportador publica GPS, e com isso o mapa
  deixa de ser diagrama e passa a ser mapa. Só que posição de caminhão envelhece
  rápido: uma leitura de três horas atrás desenha o equipamento onde ele não
  está mais, e um mapa que mente sobre onde as coisas estão é pior que nenhum.
  Por isso a idade da leitura viaja junto e a posição vencida é marcada.
* **A classe do enlace.** Fixo com fixo é espinha dorsal; fixo com veículo é
  cobertura de frente de lavra. São problemas diferentes com a mesma métrica.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from plataforma.db.coleta import estado_no_inicio
from plataforma.db.esquema import (
    aresta,
    ativo,
    dispositivo,
    estado,
    identificador,
    leitura,
    transicao,
)
from plataforma.db.grafo import sujeito_do_enlace

#: Frotas cujos rádios não andam: estação base e a guarita de acesso. A
#: classificação vem do cadastro, não de adivinhação — e é o que separa
#: espinha dorsal de cobertura de frente de lavra.
FROTAS_FIXAS = frozenset({"ERB", "GST"})

#: Estação móvel é torre sobre carreta: muda de lugar entre campanhas e fica
#: parada durante a lavra. Não é veículo nem é fixa, e tratá-la como uma das
#: duas apaga a diferença que o operador conhece.
FROTAS_SEMIFIXAS = frozenset({"ERM"})

#: Faixas de RSSI usadas no mapa e nas tabelas. São as mesmas que a operação
#: usa para decidir se sobe na torre.
FAIXAS_RSSI: tuple[tuple[float, str, str], ...] = (
    (-65.0, "excelente", "ok"),
    (-75.0, "bom", "ok"),
    (-85.0, "regular", "atencao"),
    (-95.0, "ruim", "mau"),
    (-999.0, "muito ruim", "mau"),
)

#: A partir daqui a diferença entre os dois sentidos deixa de ser variação
#: normal de medida. Costuma ser antena desalinhada, potência desigual ou
#: obstáculo de um lado só.
ASSIMETRIA_SUSPEITA_DB = 6.0

METRICAS_ENLACE = ("rf_snr_db", "rf_rssi_dbm", "rf_capacidade_estimada_mbps",
                   "malha_custo_link")
#: A partir daqui a posição não descreve mais onde o equipamento está. Um
#: caminhão a 30 km/h anda 5 km em dez minutos.
POSICAO_VENCE_S = 600

METRICAS_RADIO = ("rf_ruido_dbm", "rf_potencia_tx_dbm", "rf_clientes_associados",
                  "malha_peers_ativos", "disp_temperatura_c", "disp_cpu_pct",
                  "disp_bateria_pct", "geo_latitude", "geo_longitude",
                  "geo_altitude_m", "geo_velocidade_kmh", "ativo_uptime_s",
                  "servico_tempo_resposta_ms")


def faixa_rssi(rssi: float | None) -> tuple[str, str]:
    """Rótulo e classe de cor. Sem leitura devolve "sem medida", não "ruim":
    não medir e medir mal são coisas diferentes."""
    if rssi is None:
        return "sem medida", "nd"
    for limite, rotulo, classe in FAIXAS_RSSI:
        if rssi >= limite:
            return rotulo, classe
    return "muito ruim", "mau"


def classe_da_frota(frota: str | None) -> str:
    if frota in FROTAS_FIXAS:
        return "fixo"
    return "semifixo" if frota in FROTAS_SEMIFIXAS else "movel"


def classe_do_enlace(a: str, b: str) -> str:
    """Espinha dorsal, distribuição ou frente de lavra."""
    par = {a, b}
    if par <= {"fixo"}:
        return "espinha"
    if par <= {"fixo", "semifixo"}:
        return "distribuicao"
    return "lavra"


@dataclass
class Radio:
    chave: str
    nome: str
    ativo_id: str
    frota: str
    classe: str
    alcancavel: bool | None
    qualidade: str
    medidas: dict[str, float]
    posicao_em: datetime | None = None


async def _radios(conexao: AsyncConnection) -> dict[str, Radio]:
    linhas = (
        await conexao.execute(
            select(
                dispositivo.c.chave,
                dispositivo.c.nome_canonico,
                dispositivo.c.ativo_id,
                ativo.c.frota,
                estado.c.alcancavel,
                estado.c.qualidade,
            )
            .select_from(
                dispositivo.outerjoin(ativo, ativo.c.ativo_id == dispositivo.c.ativo_id)
                .outerjoin(estado, estado.c.sujeito == dispositivo.c.chave)
            )
            .where(dispositivo.c.papel == "radio_mesh")
        )
    ).all()

    chaves = [ln.chave for ln in linhas]
    medidas: dict[str, dict[str, float]] = defaultdict(dict)
    quando: dict[str, datetime] = {}
    if chaves:
        for m in (
            await conexao.execute(
                select(leitura.c.sujeito, leitura.c.metrica, leitura.c.valor,
                       leitura.c.em)
                .where(leitura.c.sujeito.in_(chaves))
                .where(leitura.c.metrica.in_(METRICAS_RADIO))
            )
        ).all():
            medidas[m.sujeito][m.metrica] = m.valor
            if m.metrica == "geo_latitude":
                quando[m.sujeito] = m.em

    return {
        ln.chave: Radio(
            chave=ln.chave,
            nome=ln.nome_canonico,
            ativo_id=ln.ativo_id or "",
            frota=ln.frota or "?",
            classe=classe_da_frota(ln.frota),
            alcancavel=ln.alcancavel,
            qualidade=ln.qualidade or "",
            medidas=medidas.get(ln.chave, {}),
            posicao_em=quando.get(ln.chave),
        )
        for ln in linhas
    }


async def _meias_arestas(
    conexao: AsyncConnection, momento: datetime
) -> tuple[list, dict[str, dict[str, float]]]:
    abertas = (
        await conexao.execute(
            select(
                aresta.c.origem_chave, aresta.c.destino_chave,
                aresta.c.tipo, aresta.c.validade,
            ).where(aresta.c.validade.op("@>")(momento))
        )
    ).all()
    sujeitos = [sujeito_do_enlace(a.origem_chave, a.destino_chave) for a in abertas]
    medidas: dict[str, dict[str, float]] = defaultdict(dict)
    if sujeitos:
        for m in (
            await conexao.execute(
                select(leitura.c.sujeito, leitura.c.metrica, leitura.c.valor)
                .where(leitura.c.sujeito.in_(sujeitos))
                .where(leitura.c.metrica.in_(METRICAS_ENLACE))
            )
        ).all():
            medidas[m.sujeito][m.metrica] = m.valor
    return abertas, medidas


def _par(a: str, b: str) -> tuple[str, str]:
    """Chave estável do enlace bidirecional, para juntar ida e volta."""
    return (a, b) if a <= b else (b, a)


async def enlaces(
    conexao: AsyncConnection, momento: datetime | None = None
) -> list[dict[str, Any]]:
    """Um registro por par de rádios, com os dois sentidos lado a lado."""
    agora = momento or datetime.now(UTC)
    radios = await _radios(conexao)
    abertas, medidas = await _meias_arestas(conexao, agora)

    juntos: dict[tuple[str, str], dict[str, Any]] = {}
    for a in abertas:
        chave = _par(a.origem_chave, a.destino_chave)
        reg = juntos.setdefault(
            chave,
            {
                "a": chave[0], "b": chave[1], "tipo": a.tipo,
                "ida": {}, "volta": {}, "desde": None, "meias": 0,
            },
        )
        lado = "ida" if a.origem_chave == chave[0] else "volta"
        reg[lado] = medidas.get(sujeito_do_enlace(a.origem_chave, a.destino_chave), {})
        reg["meias"] += 1
        inicio = a.validade.lower
        if inicio and (reg["desde"] is None or inicio < reg["desde"]):
            reg["desde"] = inicio

    saida = []
    for (ca, cb), reg in juntos.items():
        ra, rb = radios.get(ca), radios.get(cb)
        ida, volta = reg["ida"], reg["volta"]
        snr_i, snr_v = ida.get("rf_snr_db"), volta.get("rf_snr_db")
        rssi = _pior(ida.get("rf_rssi_dbm"), volta.get("rf_rssi_dbm"))
        rotulo, cor = faixa_rssi(rssi)
        saida.append(
            {
                "a": ca, "b": cb,
                "nome_a": ra.nome if ra else ca,
                "nome_b": rb.nome if rb else cb,
                "ativo_a": ra.ativo_id if ra else "",
                "ativo_b": rb.ativo_id if rb else "",
                "classe": classe_do_enlace(
                    ra.classe if ra else "movel", rb.classe if rb else "movel"
                ),
                "tipo": reg["tipo"],
                "snr_ida_db": _r(snr_i), "snr_volta_db": _r(snr_v),
                "rssi_ida_dbm": _r(ida.get("rf_rssi_dbm")),
                "rssi_volta_dbm": _r(volta.get("rf_rssi_dbm")),
                "capacidade_mbps": _r(
                    _pior(ida.get("rf_capacidade_estimada_mbps"),
                          volta.get("rf_capacidade_estimada_mbps"))
                ),
                "custo": _r(_melhor(ida.get("malha_custo_link"),
                                    volta.get("malha_custo_link"))),
                "assimetria_db": (
                    _r(abs(snr_i - snr_v)) if snr_i is not None and snr_v is not None
                    else None
                ),
                "rssi_pior_dbm": _r(rssi),
                "qualidade": rotulo,
                "cor": cor,
                # Um sentido só quer dizer que o outro rádio ainda não relatou
                # este vizinho. Acontece o tempo todo numa malha e não é falha.
                "bidirecional": reg["meias"] == 2,
                "desde": reg["desde"],
                "distancia_m": _distancia(ra, rb),
            }
        )
    saida.sort(key=lambda x: (x["rssi_pior_dbm"] is None, x["rssi_pior_dbm"] or 0))
    return saida


async def radios(conexao: AsyncConnection) -> list[dict[str, Any]]:
    """Um registro por rádio, com o que ele mede de si e da vizinhança."""
    agora = datetime.now(UTC)
    todos = await _radios(conexao)
    ligacoes = await enlaces(conexao, agora)

    vizinhos: dict[str, list[dict]] = defaultdict(list)
    for e in ligacoes:
        vizinhos[e["a"]].append(e)
        vizinhos[e["b"]].append(e)

    ips = dict(
        (
            await conexao.execute(
                select(identificador.c.dispositivo_chave, identificador.c.valor).where(
                    identificador.c.tipo == "ip"
                )
            )
        ).all()
    )

    saida = []
    for chave, r in todos.items():
        meus = vizinhos.get(chave, [])
        rssis = [e["rssi_pior_dbm"] for e in meus if e["rssi_pior_dbm"] is not None]
        m = r.medidas
        saida.append(
            {
                "chave": chave, "nome": r.nome, "ativo": r.ativo_id, "frota": r.frota,
                "classe": r.classe, "ip": ips.get(chave, ""),
                "alcancavel": r.alcancavel, "incerto": r.qualidade == "incerta",
                "vizinhos": len(meus),
                "vizinhos_declarados": _i(m.get("malha_peers_ativos")),
                "melhor_rssi_dbm": _r(max(rssis)) if rssis else None,
                "pior_rssi_dbm": _r(min(rssis)) if rssis else None,
                "ruido_dbm": _r(m.get("rf_ruido_dbm")),
                "potencia_tx_dbm": _r(m.get("rf_potencia_tx_dbm")),
                "clientes": _i(m.get("rf_clientes_associados")),
                "temperatura_c": _r(m.get("disp_temperatura_c")),
                "cpu_pct": _r(m.get("disp_cpu_pct")),
                "bateria_pct": _r(m.get("disp_bateria_pct")),
                "velocidade_kmh": _r(m.get("geo_velocidade_kmh")),
                "altitude_m": _r(m.get("geo_altitude_m")),
                "uptime_s": _i(m.get("ativo_uptime_s")),
                "resposta_ms": _r(m.get("servico_tempo_resposta_ms")),
                "lat": m.get("geo_latitude"),
                "lon": m.get("geo_longitude"),
                "posicao_em": r.posicao_em,
                "posicao_idade_s": (
                    round((agora - r.posicao_em).total_seconds())
                    if r.posicao_em else None
                ),
            }
        )
    saida.sort(key=lambda x: (x["pior_rssi_dbm"] is None, x["pior_rssi_dbm"] or 0))
    return saida


async def mapa(conexao: AsyncConnection) -> dict[str, Any]:
    """Rádios em coordenada de terreno, com os enlaces entre eles.

    Sem imagem de fundo: numa rede de mina não há como buscar telha de mapa, e
    fingir um fundo genérico seria pior que não ter. O que o desenho precisa é
    a posição relativa correta e uma escala — e as duas saem do GPS que o
    exportador já publica.
    """
    todos = await radios(conexao)
    com_gps = [r for r in todos if r["lat"] is not None and r["lon"] is not None]
    ligacoes = await enlaces(conexao)

    if not com_gps:
        return {
            "nos": [], "enlaces": [], "escala_m": 0, "sem_gps": len(todos),
            "limites": None,
        }

    lats = [r["lat"] for r in com_gps]
    lons = [r["lon"] for r in com_gps]
    lat0 = sum(lats) / len(lats)
    # Metro por grau: a latitude é quase constante; a longitude encolhe com o
    # cosseno da latitude. Sem essa correção o mapa fica esticado no eixo X e
    # a distância entre rádios sai errada — a 19° S, em 21%.
    m_por_lat = 111_320.0
    m_por_lon = 111_320.0 * math.cos(math.radians(lat0))

    nos = []
    for r in com_gps:
        idade = r.get("posicao_idade_s")
        nos.append(
            {
                **r,
                "x_m": (r["lon"] - min(lons)) * m_por_lon,
                "y_m": (max(lats) - r["lat"]) * m_por_lat,
                "posicao_vencida": idade is not None and idade > POSICAO_VENCE_S,
            }
        )
    posicao = {n["chave"]: (n["x_m"], n["y_m"]) for n in nos}

    desenhaveis = [
        {**e, "ax": posicao[e["a"]][0], "ay": posicao[e["a"]][1],
         "bx": posicao[e["b"]][0], "by": posicao[e["b"]][1]}
        for e in ligacoes
        if e["a"] in posicao and e["b"] in posicao
    ]
    largura = (max(lons) - min(lons)) * m_por_lon
    altura = (max(lats) - min(lats)) * m_por_lat
    return {
        "nos": nos,
        "enlaces": desenhaveis,
        "largura_m": round(largura, 1),
        "altura_m": round(altura, 1),
        "sem_gps": len(todos) - len(com_gps),
        "posicoes_vencidas": sum(1 for n in nos if n["posicao_vencida"]),
        "vence_em_s": POSICAO_VENCE_S,
        "enlaces_sem_posicao": len(ligacoes) - len(desenhaveis),
        "centro": {"lat": lat0, "lon": sum(lons) / len(lons)},
    }


async def resumo(conexao: AsyncConnection) -> dict[str, Any]:
    """Os números do topo da seção."""
    todos = await radios(conexao)
    ligacoes = await enlaces(conexao)

    online = sum(1 for r in todos if r["alcancavel"])
    incertos = sum(1 for r in todos if r["incerto"])
    sem_estado = sum(1 for r in todos if r["alcancavel"] is None)

    medidos = [e for e in ligacoes if e["rssi_pior_dbm"] is not None]
    rssis = sorted(e["rssi_pior_dbm"] for e in medidos)
    snrs = sorted(
        e["snr_ida_db"] for e in ligacoes if e["snr_ida_db"] is not None
    )
    capac = [e["capacidade_mbps"] for e in medidos if e["capacidade_mbps"] is not None]
    tortos = [e for e in ligacoes
              if (e["assimetria_db"] or 0) >= ASSIMETRIA_SUSPEITA_DB]
    ruins = [e for e in medidos if e["cor"] == "mau"]
    vizinhanca = [r["vizinhos"] for r in todos if r["vizinhos"]]

    por_classe: dict[str, int] = defaultdict(int)
    for e in ligacoes:
        por_classe[e["classe"]] += 1

    # O rádio relata o vizinho pela identidade que ele vê; quem resolve para um
    # equipamento do cadastro é a plataforma. Quando não resolve, o enlace
    # existe e o outro lado é um MAC solto. É achado de cadastro, não defeito
    # de coleta — e some da conta se ninguém contar.
    conhecidos = set(todos_por_chave := {r["chave"] for r in todos})
    del todos_por_chave
    fora = sum(
        1 for e in ligacoes if e["a"] not in conhecidos or e["b"] not in conhecidos
    )

    return {
        "radios_total": len(todos),
        "radios_online": online,
        "radios_incertos": incertos,
        "radios_sem_estado": sem_estado,
        "enlaces_abertos": len(ligacoes),
        "enlaces_medidos": len(medidos),
        "enlaces_bidirecionais": sum(1 for e in ligacoes if e["bidirecional"]),
        "enlaces_ruins": len(ruins),
        "enlaces_vizinho_fora_do_cadastro": fora,
        "enlaces_assimetricos": len(tortos),
        "por_classe": dict(por_classe),
        "rssi_mediano_dbm": _mediana(rssis),
        "rssi_p10_dbm": _percentil(rssis, 0.10),
        "snr_mediano_db": _mediana(snrs),
        "capacidade_mediana_mbps": _mediana(sorted(capac)),
        "vizinhos_media": _r(sum(vizinhanca) / len(vizinhanca)) if vizinhanca else None,
        "vizinhos_min": min(vizinhanca) if vizinhanca else None,
        "vizinhos_max": max(vizinhanca) if vizinhanca else None,
        "isolados": sum(1 for r in todos if r["vizinhos"] == 0),
    }


# --------------------------------------------------------------- panorama


def _histograma(
    valores: list[float], cortes: tuple[float, ...], rotulos: tuple[str, ...]
) -> list[dict]:
    """Contagem por faixa. ``cortes`` são os limites superiores, em ordem."""
    contas = [0] * len(rotulos)
    for v in valores:
        for i, corte in enumerate(cortes):
            if v <= corte:
                contas[i] += 1
                break
        else:
            contas[-1] += 1
    return [{"faixa": r, "quantos": c} for r, c in zip(rotulos, contas, strict=True)]


def saltos_ate_a_infraestrutura(
    radios_por_chave: dict[str, str], pares: list[tuple[str, str]]
) -> dict[str, int | None]:
    """Distância em saltos de cada rádio até o rádio fixo mais próximo.

    Busca em largura a partir de todos os fixos ao mesmo tempo. É o número que
    diz se a malha tem profundidade demais: cada salto acrescenta latência e
    mais um equipamento que, se cair, leva junto tudo que vem depois dele.

    ``None`` para quem não alcança nenhum fixo pelos enlaces observados — que
    não é o mesmo que estar sem rede, porque a vizinhança observada é sempre
    parcial.
    """
    vizinhos: dict[str, set[str]] = defaultdict(set)
    for a, b in pares:
        vizinhos[a].add(b)
        vizinhos[b].add(a)

    distancia: dict[str, int | None] = dict.fromkeys(radios_por_chave)
    fila = deque()
    for chave, classe in radios_por_chave.items():
        if classe == "fixo":
            distancia[chave] = 0
            fila.append(chave)

    while fila:
        atual = fila.popleft()
        for viz in vizinhos.get(atual, ()):
            if viz in distancia and distancia[viz] is None:
                distancia[viz] = distancia[atual] + 1
                fila.append(viz)
    return distancia


async def panorama(conexao: AsyncConnection) -> dict[str, Any]:
    """As distribuições que o número mediano esconde.

    Um sinal mediano de −61 dBm pode ser uma malha uniforme ou metade excelente
    com metade péssima. São situações muito diferentes e a mesma mediana.
    """
    todos = await radios(conexao)
    ligacoes = await enlaces(conexao)

    rssis = [e["rssi_pior_dbm"] for e in ligacoes if e["rssi_pior_dbm"] is not None]
    sinal = _histograma(
        rssis,
        (-95.0, -85.0, -75.0, -65.0),
        ("abaixo de −95", "−95 a −85", "−85 a −75", "−75 a −65", "acima de −65"),
    )

    # Agrupado em faixas: uma linha por contagem exata dava quinze linhas e a
    # forma da distribuição se perdia no comprimento da lista. O corte em 1 é o
    # que importa — vizinho único é caminho único.
    vizinhanca = _histograma(
        [float(r["vizinhos"]) for r in todos],
        (1.0, 3.0, 6.0, 9.0, 12.0),
        ("1 vizinho", "2 a 3", "4 a 6", "7 a 9", "10 a 12", "13 ou mais"),
    )

    por_classe: dict[str, int] = defaultdict(int)
    for e in ligacoes:
        por_classe[e["classe"]] += 1
    classes = [
        {"faixa": nome, "quantos": por_classe.get(chave, 0)}
        for chave, nome in (
            ("espinha", "espinha dorsal"),
            ("distribuicao", "distribuição"),
            ("lavra", "frente de lavra"),
        )
    ]

    classe_de = {r["chave"]: r["classe"] for r in todos}
    saltos = saltos_ate_a_infraestrutura(
        classe_de, [(e["a"], e["b"]) for e in ligacoes]
    )
    conta_saltos: dict[str, int] = defaultdict(int)
    for d in saltos.values():
        conta_saltos["sem caminho" if d is None else str(d)] += 1
    ordem = sorted(
        conta_saltos, key=lambda k: (k == "sem caminho", int(k) if k.isdigit() else 0)
    )
    profundidade = [{"faixa": k, "quantos": conta_saltos[k]} for k in ordem]

    return {
        "sinal": sinal,
        "vizinhanca": vizinhanca,
        "classes": classes,
        "profundidade": profundidade,
        "sem_caminho": conta_saltos.get("sem caminho", 0),
        "salto_maximo": max(
            (d for d in saltos.values() if d is not None), default=None
        ),
    }


async def serie_no_ar(
    conexao: AsyncConnection, desde: datetime, ate: datetime, pontos: int = 60
) -> dict[str, Any]:
    """Quantos rádios estavam no ar ao longo do período.

    Sai das **transições**, não de amostras: a tabela guarda o instante exato de
    cada mudança, então a curva é reconstruída e não estimada. Amostrar de dez
    em dez minutos perderia uma queda de dois minutos registrada com precisão de
    segundo.

    A curva pode discordar do indicador de agora, e a discordância é a regra
    funcionando. Quando a coleta falha inteira, a plataforma marca o estado como
    **incerto** e **não** grava transição de queda — falha total é indício de
    coletor isolado, não de mina parada. A transição continua dizendo "estava no
    ar"; o estado corrente diz "não responde, e desconfio de mim". Por isso a
    cauda da série é devolvida marcada em vez de ser desenhada como fato.
    """
    chaves = {r["chave"] for r in await radios(conexao)}
    if not chaves:
        return {"pontos": [], "incertos_agora": 0, "cauda_incerta": False}

    mudancas = (
        await conexao.execute(
            select(transicao.c.sujeito, transicao.c.de, transicao.c.para, transicao.c.em)
            .where(transicao.c.sujeito.in_(chaves))
            .order_by(transicao.c.em)
        )
    ).all()
    correntes = {
        ln.sujeito: ln.alcancavel
        for ln in (
            await conexao.execute(
                select(estado.c.sujeito, estado.c.alcancavel).where(
                    estado.c.sujeito.in_(chaves)
                )
            )
        ).all()
    }

    por_sujeito: dict[str, list] = defaultdict(list)
    for m in mudancas:
        por_sujeito[m.sujeito].append(m)

    vivo = {
        c: estado_no_inicio(por_sujeito.get(c, []), desde, correntes.get(c, False))
        for c in chaves
    }
    no_ar = sum(1 for v in vivo.values() if v)

    passo = (ate - desde).total_seconds() / max(1, pontos)
    dentro = [m for m in mudancas if desde < m.em <= ate]
    serie: list[list[float]] = []
    i = 0
    for k in range(pontos + 1):
        instante = desde + timedelta(seconds=passo * k)
        while i < len(dentro) and dentro[i].em <= instante:
            m = dentro[i]
            if vivo.get(m.sujeito) != m.para:
                no_ar += 1 if m.para else -1
                vivo[m.sujeito] = m.para
            i += 1
        serie.append([instante.timestamp(), float(no_ar)])

    incertos = (
        await conexao.execute(
            select(func.count())
            .select_from(estado)
            .where(estado.c.sujeito.in_(chaves))
            .where(estado.c.qualidade == "incerta")
        )
    ).scalar_one()
    return {
        "pontos": serie,
        "incertos_agora": int(incertos),
        "cauda_incerta": int(incertos) > 0,
    }


# ------------------------------------------------------------------ auxiliares


def _pior(a: float | None, b: float | None) -> float | None:
    """O pior dos dois sentidos. Publicar a média esconderia o lado ruim, que
    é justamente o que decide se o enlace serve."""
    valores = [v for v in (a, b) if v is not None]
    return min(valores) if valores else None


def _melhor(a: float | None, b: float | None) -> float | None:
    valores = [v for v in (a, b) if v is not None]
    return min(valores) if valores else None  # custo: menor é melhor


def _r(v: float | None, casas: int = 1) -> float | None:
    return None if v is None else round(v, casas)


def _i(v: float | None) -> int | None:
    return None if v is None else int(v)


def _mediana(ordenados: list[float]) -> float | None:
    if not ordenados:
        return None
    meio = len(ordenados) // 2
    if len(ordenados) % 2:
        return round(ordenados[meio], 1)
    return round((ordenados[meio - 1] + ordenados[meio]) / 2, 1)


def _percentil(ordenados: list[float], p: float) -> float | None:
    if not ordenados:
        return None
    return round(ordenados[min(len(ordenados) - 1, int(p * len(ordenados)))], 1)


def _distancia(a: Radio | None, b: Radio | None) -> float | None:
    """Metros entre dois rádios, quando os dois publicam GPS."""
    if a is None or b is None:
        return None
    la, lo = a.medidas.get("geo_latitude"), a.medidas.get("geo_longitude")
    lb, lob = b.medidas.get("geo_latitude"), b.medidas.get("geo_longitude")
    if None in (la, lo, lb, lob):
        return None
    dlat = (lb - la) * 111_320.0
    dlon = (lob - lo) * 111_320.0 * math.cos(math.radians((la + lb) / 2))
    return round(math.hypot(dlat, dlon), 0)


__all__ = [
    "ASSIMETRIA_SUSPEITA_DB",
    "panorama",
    "saltos_ate_a_infraestrutura",
    "serie_no_ar",
    "POSICAO_VENCE_S",
    "FAIXAS_RSSI",
    "classe_da_frota",
    "classe_do_enlace",
    "enlaces",
    "faixa_rssi",
    "mapa",
    "radios",
    "resumo",
]
