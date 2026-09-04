"""Módulo Rajant: lê o Prometheus que o exportador já alimenta.

**Este módulo não fala com rádio nenhum.** O exportador do usuário já faz
isso, e faz melhor: 98 métricas tiradas da BC API, com correções documentadas
contra o BCE User Guide. Reimplementar aquilo seria jogar fora conhecimento
caro e introduzir uma segunda verdade sobre os mesmos equipamentos.

O que falta não é coleta de rádio — é **junção**. O Prometheus sabe que o BC
chamado ``CA-1001`` está a 47 °C; só a plataforma sabe que ele é o rádio de um
caminhão que faz britagem primária, na zona ot_nivel3, com mais sete peças
embarcadas. É essa junção que este módulo faz.

Três decisões que o código materializa
--------------------------------------

**A agregação acontece no PromQL, não aqui.** São ~254 séries por rádio
(52 por BC, 75 por interface de rádio, 108 por vizinho, 18 por porta) — cerca
de 38 mil no parque de 149. Trazer isso para dentro seria reimplementar o
Prometheus pior do que ele. As consultas abaixo já voltam agregadas por
equipamento: ~14 valores por rádio em vez de 254. O detalhe por vizinho fica
onde ele já está e é bom — no Prometheus —, e vira meia-aresta quando o canal
de fatos existir.

**Do vizinho publica-se o pior, e diz-se que é o pior.** Um rádio de malha não
tem "o SNR": tem N, um por vizinho. A média esconderia justamente o enlace
prestes a cair, que é o único que interessa para prever queda. Cada observação
agregada carrega o rótulo ``agregacao``, porque ``rf_snr_db`` num PtP é o
enlace e aqui é o pior de N — conflar os dois calado custaria caro depois.

**O RSSI do Rajant não é dBm.** É escala relativa, medida acima do piso de
ruído; o dBm de verdade é ``State.Peer.signal``. O próprio exportador
documenta a confusão e o estrago que ela fez lá. Por isso ``rf_rssi_dbm`` vem
de ``rajant_peer_sinal_dbm``, e ``rajant_peer_rssi`` fica de fora até existir
uma métrica canônica com a escala certa.

Zona
----
O módulo faz **uma** chamada HTTP, para o Prometheus. Nunca toca num rádio.
Por isso opera na zona corporativa: quem alcança a OT é o exportador, que roda
onde precisa rodar. A separação é a razão de o módulo ser um leitor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from inventario.modelo import TipoAresta, Zona
from plataforma.modulos.contrato import (
    Alvo,
    Descoberta,
    Manifesto,
    Observacao,
    Qualidade,
    Relacao,
    ResultadoColeta,
    filtrar_observacoes,
)


@dataclass(frozen=True)
class Consulta:
    """Uma pergunta ao Prometheus e o nome canônico da resposta.

    A PromQL é **montada**, não escrita: guardar a string pronta impediria
    filtrar por equipamento sem cirurgia de texto, e é exatamente isso que o
    gráfico de um aparelho precisa fazer.
    """

    #: Nome da métrica no exportador, sem agregação nem filtro.
    fonte: str
    metrica: str
    #: `min`, `max`, `sum` — vazio quando a série já é uma por equipamento.
    agregador: str = ""
    #: Como N virou 1, em português, para a tela poder dizer.
    agregacao: str = ""
    #: Multiplica o valor bruto — usado onde a unidade do exportador difere
    #: da unidade canônica.
    fator: float = 1.0

    def promql(self, filtro: str = "") -> str:
        """A consulta pronta, opcionalmente restrita a um equipamento."""
        alvo = f"{self.fonte}{{{filtro}}}" if filtro else self.fonte
        return f"{self.agregador} by (bc, ip) ({alvo})" if self.agregador else alvo


#: O que a plataforma traz para dentro. Lista curta de propósito: o que cabe
#: numa ficha de equipamento e no cálculo de saúde. Gráfico de série histórica
#: continua sendo pergunta para o Prometheus, que a guarda melhor.
CONSULTAS: tuple[Consulta, ...] = (
    # -- uma série por BC: nada a agregar ---------------------------------
    # `rajant_online` NÃO vira `ativo_alcancavel`. Quem responde por
    # disponibilidade é o ICMP, e duas fontes gravando a mesma linha de estado
    # dariam last-write-wins — o valor mostrado dependeria de qual módulo
    # rodou por último. Além disso as perguntas são outras: o ICMP pergunta
    # "o endereço responde", este pergunta "a sessão BC API abre". Um rádio
    # que atende ping e recusa a API é um achado, não um empate.
    Consulta("rajant_online", "servico_disponivel"),
    Consulta("rajant_ping_rtt_ms", "servico_tempo_resposta_ms"),
    Consulta("rajant_uptime_s", "ativo_uptime_s"),
    Consulta("rajant_temperatura_c", "disp_temperatura_c"),
    Consulta("rajant_cpu_load_pct", "disp_cpu_pct"),
    Consulta("rajant_bateria_pct", "disp_bateria_pct"),
    Consulta("rajant_gps_lat", "geo_latitude"),
    Consulta("rajant_gps_lon", "geo_longitude"),
    Consulta("rajant_gps_altitude_m", "geo_altitude_m"),
    Consulta("rajant_gps_vel_kmh", "geo_velocidade_kmh"),
    # -- por interface de rádio: um BC tem de 2 a 4 ------------------------
    Consulta("rajant_radio_ruido_dbm", "rf_ruido_dbm", "max", "pior_entre_radios"),
    Consulta("rajant_radio_txpower_dbm", "rf_potencia_tx_dbm", "max", "maior_entre_radios"),
    Consulta("rajant_radio_clientes", "rf_clientes_associados", "sum", "soma_dos_radios"),
    Consulta("rajant_radio_peers_ativos", "malha_peers_ativos", "sum", "soma_dos_radios"),
    # -- por vizinho: aqui a compressão vale 108 séries para 3 -------------
    Consulta("rajant_peer_snr_db", "rf_snr_db", "min", "pior_entre_vizinhos"),
    Consulta("rajant_peer_sinal_dbm", "rf_rssi_dbm", "min", "pior_entre_vizinhos"),
    Consulta("rajant_peer_custo", "malha_custo_link", "min", "melhor_entre_vizinhos"),
    Consulta("rajant_peer_taxa_mbps", "rf_capacidade_estimada_mbps", "max",
             "melhor_entre_vizinhos"),
)

#: Da métrica canônica de volta à consulta — é o que o gráfico precisa para
#: perguntar "como estava o SNR deste rádio nas últimas 6 horas".
POR_METRICA: dict[str, Consulta] = {c.metrica: c for c in CONSULTAS}


#: A vizinhança da malha, série a série — esta **não** agrega, porque o que
#: interessa aqui é justamente quem é o outro lado. É a única consulta cara do
#: módulo (~2.700 séries), e é a que preenche o grafo temporal.
CONSULTA_VIZINHOS = "rajant_peer_ativo == 1"


MANIFESTO = Manifesto(
    nome="rajant",
    versao="1.0.0",
    fabricante="rajant",
    alvo=Alvo.SISTEMA,  # fala com UM sistema (o Prometheus) e cobre N ativos
    descoberta=Descoberta.DELEGADA,
    intervalo_metricas_s=60,
    produz_metricas=tuple(dict.fromkeys(c.metrica for c in CONSULTAS)),
    somente_leitura=True,
    zona_permitida=(Zona.CORPORATIVA,),
    papeis_alvo=("radio_mesh",),
    produz_relacoes=(TipoAresta.PEER_MESH.value,),
)


def normalizar(nome: str) -> str:
    """Reduz um nome à forma comparável.

    ``CA-1001-RADIO  RJT`` e ``CA-1001-RADIO RJT`` são o mesmo equipamento; um
    espaço duplo no cadastro não pode desfazer a junção.
    """
    return " ".join(str(nome or "").split()).upper()


#: Métricas em que "melhor" é o menor número: custo de enlace e ruído. Nas
#: demais, consolidar interfaces do mesmo equipamento pelo menor valor é
#: escolher o pior caso — que é o que interessa para prever queda.
MENOR_E_MELHOR = frozenset({"malha_custo_link", "rf_ruido_dbm"})


def consolidar(consulta: Consulta, atual: float, novo: float) -> float:
    """Junta duas séries do mesmo equipamento num número só.

    Somas continuam somando (clientes e vizinhos de dois rádios são a conta do
    equipamento). O resto fica com o pior caso, porque um rádio bom não
    compensa o outro estar surdo — quem cai é o enlace ruim.
    """
    if consulta.agregacao.startswith("soma"):
        return atual + novo
    if consulta.metrica in MENOR_E_MELHOR:
        return max(atual, novo)
    return min(atual, novo)


class Prometheus:
    """Cliente mínimo da API de consulta instantânea."""

    def __init__(self, url: str, timeout_s: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s

    async def instantanea(self, promql: str, cliente: httpx.AsyncClient) -> list[dict]:
        resposta = await cliente.get(
            f"{self.url}/api/v1/query",
            params={"query": promql},
            timeout=self.timeout_s,
        )
        resposta.raise_for_status()
        corpo = resposta.json()
        if corpo.get("status") != "success":
            raise RuntimeError(corpo.get("error", "consulta recusada pelo Prometheus"))
        return corpo["data"]["result"]


@dataclass
class Juncao:
    """Resultado de casar as séries do Prometheus com o inventário.

    Três desfechos, e nenhum deles é silêncio:

    ``por_chave``   casou, e diz por qual critério (IP ou nome).
    ``ambiguos``    o inventário tem o equipamento, mas **mais de um** responde
                    pela mesma chave. Atribuir a leitura a um deles no chute
                    seria pendurar uma temperatura real no caminhão errado —
                    número errado é pior que número ausente, porque alguém age
                    sobre ele.
    ``sem_inventario``
                    o exportador descobriu pela malha um rádio que a planilha
                    não tem. É achado de inventário, e só some se for engolido.
    """

    por_chave: dict[str, str]
    sem_inventario: set[str]
    ambiguos: set[str]
    por_ip: int = 0
    por_nome: int = 0

    def absorver(self, outra: Juncao) -> None:
        self.por_chave.update(outra.por_chave)
        self.sem_inventario |= outra.sem_inventario
        self.ambiguos |= outra.ambiguos
        self.por_ip += outra.por_ip
        self.por_nome += outra.por_nome


def _indice(pares: list[tuple[str, str]]) -> tuple[dict[str, str], set[str]]:
    """Índice que **recusa** chave ambígua em vez de deixar a última vencer.

    Um ``{k: v for ...}` teria descartado o primeiro de cada par calado. No
    inventário real isso são 3 IPs e 8 nomes disputados por rádios diferentes
    — equipamentos que nunca receberiam leitura, sem nada indicando por quê.
    """
    mapa: dict[str, str] = {}
    ambiguos: set[str] = set()
    for busca, valor in pares:
        if not busca:
            continue
        if busca in mapa and mapa[busca] != valor:
            ambiguos.add(busca)
        mapa[busca] = valor
    for chave in ambiguos:
        mapa.pop(chave, None)
    return mapa, ambiguos


def casar(series: list[dict], alvos: list[dict[str, Any]]) -> Juncao:
    """Casa o rótulo do Prometheus com a chave do inventário.

    IP primeiro, confirmado com o usuário: o endereço que o exportador publica
    é o mesmo da planilha. O nome é a queda, porque vem de
    ``Config.General.name``, digitado no próprio rádio, e nada garante que
    alguém o tenha escrito igual ao cadastro.

    Quando o IP é disputado, o nome ainda pode desempatar — e só quando os dois
    falham a série vira achado, nunca sumiço.
    """
    por_ip, ips_ambiguos = _indice([(a.get("ip", ""), a["chave"]) for a in alvos])
    por_nome, nomes_ambiguos = _indice(
        [(normalizar(a.get("nome", "")), a["chave"]) for a in alvos]
    )

    j = Juncao(por_chave={}, sem_inventario=set(), ambiguos=set())
    for serie in series:
        rotulos = serie.get("metric", {})
        ip, bc = rotulos.get("ip", ""), rotulos.get("bc", "")
        identidade = f"{bc}@{ip}"
        if identidade in j.por_chave or identidade in j.sem_inventario:
            continue
        if chave := por_ip.get(ip):
            j.por_chave[identidade] = chave
            j.por_ip += 1
        elif chave := por_nome.get(normalizar(bc)):
            j.por_chave[identidade] = chave
            j.por_nome += 1
        elif ip in ips_ambiguos or normalizar(bc) in nomes_ambiguos:
            j.ambiguos.add(identidade)
        else:
            j.sem_inventario.add(identidade)
    return j


class ModuloRajant:
    """Leitor do Prometheus conforme o contrato de módulo."""

    def __init__(
        self,
        url_prometheus: str,
        timeout_s: float = 10.0,
        transporte: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.manifesto = MANIFESTO
        self.prometheus = Prometheus(url_prometheus, timeout_s)
        #: Injetável para o teste poder responder sem Prometheus de verdade.
        self.transporte = transporte
        #: Última junção, para a aba de coleta poder mostrar o que não casou.
        self.ultima_juncao: Juncao | None = None

    async def _vizinhanca(
        self, cliente: httpx.AsyncClient, juncao: Juncao, alvos: list[dict[str, Any]]
    ) -> tuple[tuple[Relacao, ...], bool]:
        """Quem está falando com quem, agora.

        O rótulo ``peer`` traz o IPv4 do vizinho — ou ``mac:...`` quando o
        State.Peer não publica endereço. Os dois servem: a plataforma resolve
        identidade, e o MAC é justamente o identificador mais forte que ela
        tem. O módulo não tenta resolver nada; relata o que viu.
        """
        try:
            series = await self.prometheus.instantanea(CONSULTA_VIZINHOS, cliente)
        except Exception:  # noqa: BLE001
            return (), False

        parcial = casar(series, alvos)
        juncao.absorver(parcial)

        vistas: set[tuple[str, str]] = set()
        relacoes: list[Relacao] = []
        for serie in series:
            rotulos = serie.get("metric", {})
            identidade = f"{rotulos.get('bc', '')}@{rotulos.get('ip', '')}"
            origem = juncao.por_chave.get(identidade)
            destino = (rotulos.get("peer") or "").strip()
            if not origem or not destino:
                continue
            # Um BC com três rádios vê o mesmo vizinho por mais de uma
            # interface: é um enlace só entre os dois equipamentos.
            if (origem, destino) in vistas:
                continue
            vistas.add((origem, destino))
            relacoes.append(
                Relacao(
                    origem=origem,
                    destino=destino,
                    tipo=TipoAresta.PEER_MESH,
                    atributos={"radio": rotulos.get("radio", "")},
                )
            )
        return tuple(relacoes), True

    async def coletar(self, alvos: list[dict[str, Any]]) -> ResultadoColeta:
        inicio = time.perf_counter()
        brutas: list[Observacao] = []
        recusas: list[str] = []
        juncao = Juncao(por_chave={}, sem_inventario=set(), ambiguos=set())
        consultas_falhas = 0
        relacoes: tuple[Relacao, ...] = ()
        vizinhanca_lida = False

        async with httpx.AsyncClient(transport=self.transporte) as cliente:
            for consulta in CONSULTAS:
                try:
                    series = await self.prometheus.instantanea(consulta.promql(), cliente)
                except Exception as erro:  # noqa: BLE001
                    # Uma consulta que falha não invalida as outras: métrica
                    # ausente no exportador antigo é caso normal, não pane.
                    consultas_falhas += 1
                    recusas.append(f"{consulta.promql()}: {erro}")
                    continue

                parcial = casar(series, alvos)
                juncao.absorver(parcial)

                # Um BreadCrumb tem um IPv4 por rádio: o mesmo equipamento
                # aparece em várias séries, com IPs diferentes. O exportador
                # marca a interface primária em `rajant_bc_primario`, mas nem
                # toda versão a publica — então a consolidação é feita aqui, e
                # o critério é dito: para o pior caso do enlace, o pior valor.
                por_equipamento: dict[str, float] = {}
                for serie in series:
                    rotulos = serie.get("metric", {})
                    identidade = f"{rotulos.get('bc', '')}@{rotulos.get('ip', '')}"
                    chave = juncao.por_chave.get(identidade)
                    if chave is None:
                        continue
                    try:
                        valor = float(serie["value"][1])
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
                    if valor != valor:  # NaN: o Prometheus diz "sem dado" assim
                        continue
                    if chave in por_equipamento:
                        valor = consolidar(consulta, por_equipamento[chave], valor)
                    por_equipamento[chave] = valor

                for chave, valor in por_equipamento.items():
                    marcas = {"fonte": "prometheus"}
                    if consulta.agregacao:
                        marcas["agregacao"] = consulta.agregacao
                    brutas.append(
                        Observacao(
                            sujeito=chave,
                            metrica=consulta.metrica,
                            valor=valor * consulta.fator,
                            # Agregado não é medida direta: quem lê precisa
                            # saber que o número resume N enlaces.
                            qualidade=(
                                Qualidade.INCERTA if consulta.agregacao else Qualidade.BOA
                            ),
                            rotulos=marcas,
                        )
                    )

            relacoes, vizinhanca_lida = await self._vizinhanca(cliente, juncao, alvos)

        self.ultima_juncao = juncao
        observacoes, rejeitadas = filtrar_observacoes(brutas)
        avisos = tuple(
            f"chave disputada no cadastro, leitura descartada: {e}"
            for e in sorted(juncao.ambiguos)
        )
        # Alvo do módulo é o Prometheus, mas o que interessa relatar é
        # cobertura: rádio do inventário que nenhuma consulta alcançou.
        alcancados = set(juncao.por_chave.values())
        # Equipamento com série no Prometheus mas chave disputada conta como
        # não lido — porque não foi lido. A ambiguidade fica em `ambiguos`
        # para a tela poder dizer qual é o cadastro a corrigir.
        falhas = len([a for a in alvos if a["chave"] not in alcancados])
        if consultas_falhas == len(CONSULTAS):
            # Prometheus fora do ar: nenhum rádio caiu, o leitor é que não leu.
            falhas = len(alvos)
        return ResultadoColeta(
            observacoes=observacoes,
            relacoes=relacoes,
            # Só uma leitura inteira autoriza a plataforma a fechar aresta. Com
            # a consulta falhando, a ausência de um vizinho significa "não
            # perguntei" — e fechar tudo gravaria que a malha se desfez.
            relacoes_completas=vizinhanca_lida,
            alvos_total=len(alvos),
            alvos_falha=falhas,
            duracao_s=time.perf_counter() - inicio,
            rejeitadas=tuple(recusas) + avisos + rejeitadas,
        )


__all__ = ["CONSULTAS", "MANIFESTO", "Consulta", "Juncao", "ModuloRajant", "casar", "normalizar"]
