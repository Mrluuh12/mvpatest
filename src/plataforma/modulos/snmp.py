"""Módulo SNMP declarativo.

O módulo não conhece fabricante nenhum: ele executa um **perfil**
(`perfis_snmp.py`), que diz quais OIDs ler e para que métrica canônica cada um
vai. Suportar um switch novo é escrever quinze linhas de configuração.

Duas coisas que separam este módulo dos outros
----------------------------------------------

**Ele precisa de credencial.** É o primeiro que precisa, e por isso o cofre
existe. A credencial é aberta pelo *coletor*, que tem banco, e entregue pronta
ao módulo — que continua sem tocar em Postgres, como os demais. A zona é
conferida na abertura, que é a última linha antes de o segredo virar pacote
UDP.

**Ele publica métrica de interface, e interface não é dispositivo.** Um switch
de 48 portas tem 48 conjuntos de contadores. O sujeito da leitura passa a ser
``chave/porta`` — o modelo de domínio sempre teve Interface entre Dispositivo
e aresta, e é aqui que ela aparece pela primeira vez.

**Nunca escreve.** Não há `set_cmd` neste arquivo, e não é por esquecimento:
SNMP de escrita em equipamento de rede de mina é como se derruba uma frota. O
manifesto declara somente leitura e o código não tem o caminho.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from inventario.modelo import Zona
from plataforma.modulos.contrato import (
    Alvo,
    Descoberta,
    Manifesto,
    Observacao,
    ResultadoColeta,
    filtrar_observacoes,
)
from plataforma.modulos.perfis_snmp import PERFIS, Perfil, Tabela, perfil_para

PORTA_PADRAO = 161


@dataclass
class Credencial:
    """O que o coletor entrega ao módulo, já aberto do cofre."""

    tipo: str = "snmp_v2c"
    comunidade: str = ""
    usuario: str = ""
    senha_auth: str = ""
    senha_priv: str = ""
    protocolo_auth: str = "sha"
    protocolo_priv: str = "aes"


class Sessao(Protocol):
    """O que o módulo precisa de um transporte SNMP.

    É um protocolo, e não a classe do pysnmp, para o teste poder responder sem
    rede — a lógica que erra de verdade é o mapeamento, não o UDP.
    """

    async def escalares(self, alvo: str, oids: list[str]) -> dict[str, Any]: ...

    async def tabela(
        self, alvo: str, oid: str, colunas: list[int]
    ) -> dict[str, dict[int, Any]]: ...


@dataclass
class Falha:
    alvo: str
    motivo: str


@dataclass
class Colheita:
    observacoes: list[Observacao] = field(default_factory=list)
    falhas: list[Falha] = field(default_factory=list)


def sujeito_da_porta(chave: str, porta: str) -> str:
    """Interface é um sujeito próprio, derivado do dispositivo.

    Sem isso, as 48 portas de um switch disputariam a mesma linha de leitura e
    a última venceria — o mesmo defeito que já apareceu no Rajant, aqui
    multiplicado por 48.
    """
    return f"{chave}/{porta}"


def _numero(valor: Any) -> float | None:
    """Converte o que o agente devolveu, ou desiste dizendo que desistiu.

    Um agente pode responder `noSuchObject` ou uma string; virar zero seria
    inventar leitura.
    """
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


async def colher(
    sessao: Sessao, alvo: dict[str, Any], perfil: Perfil
) -> Colheita:
    """Executa um perfil contra um alvo."""
    c = Colheita()
    chave, ip = alvo["chave"], alvo.get("ip", "")
    if not ip:
        c.falhas.append(Falha(chave, "sem endereço IP no cadastro"))
        return c

    try:
        brutos = await sessao.escalares(ip, list(perfil.escalares))
    except Exception as erro:  # noqa: BLE001
        c.falhas.append(Falha(chave, f"escalares: {erro}"))
        return c

    for oid, metrica in perfil.escalares.items():
        if (v := _numero(brutos.get(oid))) is None:
            continue
        c.observacoes.append(
            Observacao(
                sujeito=chave,
                metrica=metrica,
                valor=v * perfil.fatores.get(oid, 1.0),
                rotulos={"fonte": "snmp", "oid": oid},
            )
        )

    # As tabelas são lidas **antes** de virar observação porque o nome da porta
    # e os contadores dela vivem em tabelas diferentes: `ifName` está na
    # ifXTable, `ifOperStatus` na ifTable. As duas são indexadas pelo mesmo
    # ifIndex — a ifXTable existe justamente como extensão da ifTable. Emitir
    # tabela a tabela fazia a mesma porta física virar dois sujeitos,
    # `sw-01/Gi0/1` e `sw-01/1`, e ninguém casaria os dois depois.
    colhidas: list[tuple[Tabela, dict[str, dict[int, Any]]]] = []
    nomes: dict[str, str] = {}
    for tab in perfil.tabelas:
        numeros = [col.numero for col in tab.colunas]
        try:
            linhas = await sessao.tabela(ip, tab.oid, numeros)
        except Exception as erro:  # noqa: BLE001
            # Tabela ausente não invalida o alvo: um roteador pode não ter
            # ifXTable. O escalar já colhido continua valendo.
            c.falhas.append(Falha(chave, f"tabela {tab.oid}: {erro}"))
            continue
        colhidas.append((tab, linhas))
        for col in tab.colunas:
            if col.rotulo != "porta":
                continue
            for indice, celulas in linhas.items():
                if col.numero in celulas:
                    nomes[indice] = str(celulas[col.numero])

    for tab, linhas in colhidas:
        for indice, celulas in linhas.items():
            # Sem ifName — agente antigo, ou ifXTable ausente —, o índice é o
            # nome. Feio, mas estável: é o mesmo em todas as tabelas.
            porta = nomes.get(indice, indice)
            for col in tab.colunas:
                if not col.metrica or col.numero not in celulas:
                    continue
                if (v := _numero(celulas[col.numero])) is None:
                    continue
                c.observacoes.append(
                    Observacao(
                        sujeito=sujeito_da_porta(chave, porta),
                        metrica=col.metrica,
                        valor=v * col.fator,
                        rotulos={"fonte": "snmp", "porta": porta, "dispositivo": chave},
                    )
                )
    return c


MANIFESTO = Manifesto(
    nome="snmp",
    versao="1.0.0",
    fabricante="generico",
    alvo=Alvo.DISPOSITIVO,
    descoberta=Descoberta.DELEGADA,
    intervalo_metricas_s=120,
    produz_metricas=tuple(dict.fromkeys(m for p in PERFIS for m in p.metricas())),
    somente_leitura=True,
    zona_permitida=(Zona.CORPORATIVA, Zona.OT_NIVEL3),
    papeis_alvo=("switch", "roteador", "ups", "camera", "servidor"),
)


class ModuloSnmp:
    """Executa perfis SNMP conforme o contrato de módulo."""

    def __init__(
        self, sessao: Sessao, intervalo_s: int = 120, concorrencia: int = 20
    ) -> None:
        self.manifesto = MANIFESTO
        self.sessao = sessao
        self.intervalo_s = intervalo_s
        #: Alvos consultados ao mesmo tempo. Em série, um parque mudo custa a
        #: soma de todos os timeouts: 36 alvos x 11 operacoes x 2 s dao oito
        #: minutos por ciclo, num modulo cujo intervalo e de dois. O limite
        #: existe porque o outro extremo -- todos de uma vez -- abriria
        #: centenas de sockets UDP e afogaria o proprio coletor.
        self.concorrencia = concorrencia

    async def coletar(self, alvos: list[dict[str, Any]]) -> ResultadoColeta:
        inicio = time.perf_counter()
        brutas: list[Observacao] = []
        falhas: list[Falha] = []

        limite = asyncio.Semaphore(self.concorrencia)

        async def um(alvo: dict[str, Any]) -> Colheita:
            async with limite:
                return await colher(
                    self.sessao, alvo, perfil_para(alvo.get("papel", ""))
                )

        for colheita in await asyncio.gather(*(um(a) for a in alvos)):
            brutas.extend(colheita.observacoes)
            falhas.extend(colheita.falhas)

        observacoes, rejeitadas = filtrar_observacoes(brutas)
        # Alvo sem nenhuma observação é alvo que não respondeu — mesmo que
        # alguma tabela tenha falhado sozinha.
        respondeu = {o.rotulos.get("dispositivo") or o.sujeito for o in observacoes}
        return ResultadoColeta(
            observacoes=observacoes,
            alvos_total=len(alvos),
            alvos_falha=len([a for a in alvos if a["chave"] not in respondeu]),
            duracao_s=time.perf_counter() - inicio,
            rejeitadas=tuple(f"{f.alvo}: {f.motivo}" for f in falhas) + rejeitadas,
        )




# ---------------------------------------------------------------------------
# Transporte real. Fica no fim de propósito: tudo acima é testável sem rede.
# ---------------------------------------------------------------------------


class SessaoPysnmp:
    """Sessão SNMP sobre pysnmp, v2c ou v3.

    O v3 é o que se deve usar onde houver: v2c manda a comunidade em claro no
    fio, e numa rede de mina isso é uma senha viajando por rádio. O módulo
    aceita os dois porque parque de campo raramente é uniforme — mas a escolha
    fica registrada na credencial, não escondida no código.
    """

    def __init__(
        self, credencial: Credencial, porta: int = PORTA_PADRAO,
        timeout_s: float = 2.0, tentativas: int = 1,
    ) -> None:
        self.credencial = credencial
        self.porta = porta
        self.timeout_s = timeout_s
        self.tentativas = tentativas
        self._motor = None

    def _auth(self):
        from pysnmp.hlapi.v3arch.asyncio import (
            CommunityData,
            UsmUserData,
            usmAesCfb128Protocol,
            usmHMACSHAAuthProtocol,
        )

        c = self.credencial
        if c.tipo == "snmp_v3":
            return UsmUserData(
                c.usuario,
                authKey=c.senha_auth or None,
                privKey=c.senha_priv or None,
                authProtocol=usmHMACSHAAuthProtocol if c.senha_auth else None,
                privProtocol=usmAesCfb128Protocol if c.senha_priv else None,
            )
        # mpModel=1 é v2c. O v1 não tem contador de 64 bits, então nem é opção
        # para quem precisa de octetos que não viram a zero.
        return CommunityData(c.comunidade, mpModel=1)

    def _engine(self):
        from pysnmp.hlapi.v3arch.asyncio import SnmpEngine

        if self._motor is None:
            self._motor = SnmpEngine()
        return self._motor

    async def _alvo(self, ip: str):
        from pysnmp.hlapi.v3arch.asyncio import UdpTransportTarget

        return await UdpTransportTarget.create(
            (ip, self.porta), timeout=self.timeout_s, retries=self.tentativas
        )

    async def escalares(self, alvo: str, oids: list[str]) -> dict[str, Any]:
        from pysnmp.hlapi.v3arch.asyncio import (
            ContextData,
            ObjectIdentity,
            ObjectType,
            get_cmd,
        )

        if not oids:
            return {}
        erro, estado, _indice, ligacoes = await get_cmd(
            self._engine(),
            self._auth(),
            await self._alvo(alvo),
            ContextData(),
            *[ObjectType(ObjectIdentity(o)) for o in oids],
        )
        if erro:
            raise RuntimeError(str(erro))
        if estado:
            raise RuntimeError(f"agente recusou: {estado.prettyPrint()}")
        return {str(nome): valor for nome, valor in ligacoes}

    async def tabela(
        self, alvo: str, oid: str, colunas: list[int]
    ) -> dict[str, dict[int, Any]]:
        from pysnmp.hlapi.v3arch.asyncio import (
            ContextData,
            ObjectIdentity,
            ObjectType,
            bulk_walk_cmd,
        )

        linhas: dict[str, dict[int, Any]] = {}
        for coluna in colunas:
            raiz = f"{oid}.{coluna}"
            async for erro, estado, _i, ligacoes in bulk_walk_cmd(
                self._engine(),
                self._auth(),
                await self._alvo(alvo),
                ContextData(),
                0,
                25,
                ObjectType(ObjectIdentity(raiz)),
                lexicographicMode=False,
            ):
                if erro:
                    raise RuntimeError(str(erro))
                if estado:
                    raise RuntimeError(f"agente recusou: {estado.prettyPrint()}")
                for nome, valor in ligacoes:
                    texto = str(nome)
                    if not texto.startswith(raiz + "."):
                        continue
                    indice = texto[len(raiz) + 1 :]
                    linhas.setdefault(indice, {})[coluna] = valor
        return linhas


__all__ = [
    "MANIFESTO",
    "SessaoPysnmp",
    "Colheita",
    "Credencial",
    "Falha",
    "ModuloSnmp",
    "Sessao",
    "colher",
    "sujeito_da_porta",
]
