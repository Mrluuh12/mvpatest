"""Sondas de diagnóstico — o que transforma "está ruim" em "é aqui".

Isto é uma forma de interação nova na plataforma. Coleta é agendada, em lote e
sem ninguém olhando; diagnóstico é **uma pessoa apertando um botão e
esperando**. Disso vêm três consequências que o contrato materializa.

O que se recusa a construir, e por quê
--------------------------------------

Nem tudo que se chama de diagnóstico é leitura inofensiva. Três coisas da
lista habitual ficam de fora **por decisão**, não por falta de tempo:

* **Varredura de portas.** Diagnóstico é testar *uma* porta que se sabe qual
  deveria estar aberta. Varrer faixa é reconhecimento — e num CLP ou num rádio
  de campo já derrubou equipamento em muita mina. A sonda de porta aqui exige
  a porta.
* **Teste de banda.** Saturar o enlace para medir o enlace deixa a produção
  sem rede justamente enquanto se investiga. Se um dia existir, é com janela
  aprovada e aviso, não num botão.
* **Captura de pacotes.** Lê carga útil, e carga útil tem credencial. É outro
  nível de autorização, não uma sonda.

Recusar dizendo o motivo vale mais que recusar em silêncio: quem lê isto sabe
o que pedir e sob que condições.

Zona
----
A sonda sai de onde a plataforma está. Alvo em outra zona é **recusado com a
explicação**, não deixado dar timeout — um erro que diz "preciso de um agente
naquela zona" resolve; um timeout de 30 s manda a pessoa procurar defeito no
lugar errado.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from inventario.modelo import ZONAS_PROIBIDAS, Zona
from plataforma.arranjos import Opcao, TipoOpcao
from plataforma.modulos.icmp import abrir_socket, montar_echo

VAR_ZONA = "PLATAFORMA_ZONA"


class Perigo(StrEnum):
    """Quanto a sonda mexe com o alvo. Fica no manifesto para a tela poder
    avisar antes, e não depois."""

    LEITURA = "leitura"  # pergunta e escuta; não gera carga apreciável
    CARGA = "carga"  # gera tráfego que concorre com a produção
    INVASIVA = "invasiva"  # pode derrubar o alvo


class ManifestoSonda(BaseModel):
    nome: str
    rotulo: str
    descricao: str
    perigo: Perigo = Perigo.LEITURA
    #: Parâmetros que o operador informa, tipados como as opções de cartão —
    #: a tela desenha o controle sem conhecer a sonda por dentro.
    parametros: tuple[Opcao, ...] = ()
    #: Zonas onde a sonda pode operar. As proibidas nunca entram.
    zona_permitida: tuple[Zona, ...] = (Zona.CORPORATIVA, Zona.OT_NIVEL3)
    #: Segundos além dos quais a sonda é abortada. Diagnóstico que não volta
    #: não é diagnóstico: é uma aba pendurada.
    limite_s: float = 20.0

    def model_post_init(self, _ctx) -> None:
        if proibidas := sorted(z.value for z in self.zona_permitida if z in ZONAS_PROIBIDAS):
            raise ValueError(
                f"sonda {self.nome!r} declara zona {proibidas}: lá vivem os "
                f"controladores de processo. Não é configuração, é impossibilidade."
            )


class Resultado(BaseModel):
    ok: bool
    resumo: str
    #: Linhas para a tela mostrar, na ordem. Texto, não estrutura, porque
    #: diagnóstico é para ser lido por gente.
    linhas: tuple[str, ...] = ()
    dados: dict[str, Any] = Field(default_factory=dict)
    duracao_s: float = 0.0


class Sonda(Protocol):
    manifesto: ManifestoSonda

    async def executar(self, alvo: str, parametros: dict) -> Resultado: ...


# --------------------------------------------------------------------------
# Sondas
# --------------------------------------------------------------------------


class SondaPing:
    """O de sempre, e continua sendo o primeiro a rodar."""

    manifesto = ManifestoSonda(
        nome="ping",
        rotulo="Ping",
        descricao="O endereço responde? Com que latência e que perda?",
        parametros=(
            Opcao(nome="tentativas", rotulo="Quantos pacotes", tipo=TipoOpcao.INTEIRO,
                  padrao=5),
        ),
        limite_s=15.0,
    )

    async def executar(self, alvo: str, parametros: dict) -> Resultado:
        from plataforma.modulos.icmp import sondar

        inicio = time.perf_counter()
        tentativas = max(1, min(int(parametros.get("tentativas", 5)), 20))
        sondas = await sondar([alvo], tentativas=tentativas, timeout_s=1.0)
        s = sondas.get(alvo)
        duracao = time.perf_counter() - inicio
        if s is None or not s.alcancavel:
            return Resultado(
                ok=False,
                resumo=f"{alvo} não respondeu a {tentativas} pacotes",
                linhas=("100% de perda",),
                duracao_s=duracao,
            )
        return Resultado(
            ok=True,
            resumo=f"{alvo} responde em {s.latencia_media_ms:.2f} ms",
            linhas=(
                f"enviados {s.enviados}, recebidos {s.recebidos}",
                f"perda {s.perda_pct:.0f}%",
                f"latência média {s.latencia_media_ms:.2f} ms",
                *(
                    (f"jitter {s.jitter_ms:.2f} ms",)
                    if s.jitter_ms is not None
                    else ("jitter indisponível: uma resposta só não tem variação",)
                ),
            ),
            dados={
                "latencia_ms": s.latencia_media_ms,
                "perda_pct": s.perda_pct,
                "jitter_ms": s.jitter_ms,
            },
            duracao_s=duracao,
        )


@dataclass
class Salto:
    numero: int
    endereco: str = ""
    latencia_ms: float | None = None

    def __str__(self) -> str:
        if not self.endereco:
            return f"{self.numero:2d}  *"
        return f"{self.numero:2d}  {self.endereco:<18} {self.latencia_ms:.2f} ms"


class SondaCaminho:
    """Traceroute — por onde passa, e onde o tempo aparece.

    É a resposta ao NetPath: o valor não é a lista de endereços, é ver o salto
    em que a latência salta. Sem isso, "a rede está lenta" não tem endereço.
    """

    manifesto = ManifestoSonda(
        nome="caminho",
        rotulo="Caminho",
        descricao="Por onde o tráfego passa até o alvo, e a latência de cada salto.",
        parametros=(
            Opcao(nome="saltos_max", rotulo="Saltos no máximo", tipo=TipoOpcao.INTEIRO,
                  padrao=15),
        ),
        limite_s=30.0,
    )

    async def executar(self, alvo: str, parametros: dict) -> Resultado:
        inicio = time.perf_counter()
        maximo = max(1, min(int(parametros.get("saltos_max", 15)), 30))
        saltos = await asyncio.to_thread(self._percorrer, alvo, maximo)
        duracao = time.perf_counter() - inicio
        chegou = any(s.endereco == alvo for s in saltos)
        mudos = sum(1 for s in saltos if not s.endereco)
        linhas = [str(s) for s in saltos]
        if mudos:
            # Salto mudo é rotina: muita coisa não responde a TTL expirado. Sem
            # dizer isso, um `*` no meio parece falha e manda alguém caçar
            # defeito onde não há.
            linhas.append(
                f"{mudos} salto(s) sem resposta — normal: nem todo equipamento "
                f"responde a TTL expirado"
            )
        return Resultado(
            ok=chegou,
            resumo=(
                f"{len(saltos)} saltos até {alvo}"
                if chegou
                else f"não chegou a {alvo} em {maximo} saltos"
            ),
            linhas=tuple(linhas),
            dados={"saltos": [{"n": s.numero, "ip": s.endereco,
                               "ms": s.latencia_ms} for s in saltos]},
            duracao_s=duracao,
        )

    def _percorrer(self, alvo: str, maximo: int) -> list[Salto]:
        saltos: list[Salto] = []
        for ttl in range(1, maximo + 1):
            salto = Salto(numero=ttl)
            sock, bruto = abrir_socket()
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
                sock.settimeout(2.0)
                identificador = (os.getpid() + ttl) & 0xFFFF
                partida = time.perf_counter()
                sock.sendto(montar_echo(identificador, ttl), (alvo, 0))
                while True:
                    dados, origem = sock.recvfrom(1024)
                    salto.endereco = origem[0]
                    salto.latencia_ms = (time.perf_counter() - partida) * 1000
                    break
            except (TimeoutError, OSError):
                pass
            finally:
                with contextlib.suppress(OSError):
                    sock.close()
            saltos.append(salto)
            if salto.endereco == alvo:
                break
        return saltos


class SondaPorta:
    """Teste de **uma** porta TCP, e é de propósito que seja uma.

    Diagnóstico é confirmar que a porta que deveria estar aberta está. Varrer
    faixa é reconhecimento, e num equipamento de campo já derrubou muita
    coisa — por isso a porta é obrigatória e não há modo "todas".
    """

    manifesto = ManifestoSonda(
        nome="porta",
        rotulo="Porta TCP",
        descricao="A porta que deveria estar aberta está? Uma porta por vez, "
                  "de propósito.",
        parametros=(
            Opcao(nome="porta", rotulo="Porta", tipo=TipoOpcao.INTEIRO, padrao=22,
                  ajuda="uma só; varredura de faixa é reconhecimento, não diagnóstico"),
        ),
        limite_s=10.0,
    )

    async def executar(self, alvo: str, parametros: dict) -> Resultado:
        inicio = time.perf_counter()
        try:
            porta = int(parametros.get("porta", 0))
        except (TypeError, ValueError):
            porta = 0
        if not 1 <= porta <= 65535:
            return Resultado(
                ok=False,
                resumo="informe uma porta entre 1 e 65535",
                linhas=("a porta é obrigatória: esta sonda não varre faixa",),
            )
        try:
            fluxo = asyncio.open_connection(alvo, porta)
            leitor, escritor = await asyncio.wait_for(fluxo, timeout=5.0)
            escritor.close()
            with contextlib.suppress(Exception):
                await escritor.wait_closed()
            aberta, motivo = True, "conexão estabelecida"
        except TimeoutError:
            aberta, motivo = False, "sem resposta em 5 s (filtrada ou inalcançável)"
        except ConnectionRefusedError:
            # Recusa é resposta: o host está de pé e a porta fechada. Muito
            # diferente de silêncio, e a distinção é meio diagnóstico.
            aberta, motivo = False, "conexão recusada — o host respondeu, a porta está fechada"
        except OSError as erro:
            aberta, motivo = False, f"erro de rede: {erro}"
        return Resultado(
            ok=aberta,
            resumo=f"{alvo}:{porta} {'aberta' if aberta else 'não aberta'}",
            linhas=(motivo,),
            dados={"porta": porta, "aberta": aberta},
            duracao_s=time.perf_counter() - inicio,
        )


class SondaSnmp:
    """Uma leitura SNMP pontual, com a credencial do cofre.

    Serve para a pergunta que o coletor não responde: *"o equipamento está
    respondendo SNMP agora, com esta credencial?"*. Sem ela, uma métrica
    ausente pode ser rede, credencial ou perfil errado, e não há como separar.
    """

    manifesto = ManifestoSonda(
        nome="snmp",
        rotulo="Leitura SNMP",
        descricao="Lê um OID agora, com a credencial cadastrada. Separa "
                  "problema de rede de problema de credencial.",
        parametros=(
            Opcao(nome="oid", rotulo="OID", tipo=TipoOpcao.TEXTO,
                  padrao="1.3.6.1.2.1.1.1.0",
                  ajuda="1.3.6.1.2.1.1.1.0 é o sysDescr: o que o equipamento diz ser"),
        ),
        limite_s=15.0,
    )

    def __init__(self, sessao=None) -> None:
        self.sessao = sessao

    async def executar(self, alvo: str, parametros: dict) -> Resultado:
        inicio = time.perf_counter()
        if self.sessao is None:
            return Resultado(
                ok=False,
                resumo="sem credencial SNMP configurada",
                linhas=(
                    "defina PLATAFORMA_SNMP_CREDENCIAL e cadastre a credencial "
                    "com `criar-credencial guardar`",
                ),
            )
        oid = str(parametros.get("oid") or "1.3.6.1.2.1.1.1.0").strip()
        try:
            valores = await self.sessao.escalares(alvo, [oid])
        except Exception as erro:  # noqa: BLE001
            return Resultado(
                ok=False,
                resumo=f"{alvo} não respondeu SNMP",
                linhas=(str(erro),),
                duracao_s=time.perf_counter() - inicio,
            )
        bruto = valores.get(oid)
        return Resultado(
            ok=bruto is not None,
            resumo=f"{alvo} respondeu SNMP" if bruto is not None else "OID sem valor",
            linhas=(f"{oid} = {bruto}",),
            dados={"oid": oid, "valor": str(bruto)},
            duracao_s=time.perf_counter() - inicio,
        )


@dataclass
class Registro:
    """As sondas disponíveis, e a zona de onde elas saem."""

    zona: Zona = Zona.CORPORATIVA
    sondas: dict[str, Sonda] = field(default_factory=dict)

    def registrar(self, sonda: Sonda) -> None:
        if self.zona not in sonda.manifesto.zona_permitida:
            raise ValueError(
                f"sonda {sonda.manifesto.nome!r} não opera em {self.zona.value!r}"
            )
        self.sondas[sonda.manifesto.nome] = sonda

    def carregar_padrao(self, sessao_snmp=None) -> None:
        for s in (SondaPing(), SondaCaminho(), SondaPorta(), SondaSnmp(sessao_snmp)):
            with contextlib.suppress(ValueError):
                self.registrar(s)


def zona_da_plataforma() -> Zona:
    return Zona(os.environ.get(VAR_ZONA, Zona.CORPORATIVA.value))


async def executar(
    registro: Registro, nome: str, alvo: str, zona_do_alvo: Zona, parametros: dict
) -> Resultado:
    """Roda uma sonda, com as duas recusas que precisam vir antes da rede."""
    sonda = registro.sondas.get(nome)
    if sonda is None:
        raise KeyError(nome)
    if zona_do_alvo in ZONAS_PROIBIDAS:
        return Resultado(
            ok=False,
            resumo=f"alvo em {zona_do_alvo.value}: proibido",
            linhas=(
                "lá vivem os controladores de processo. Não é configuração, "
                "é impossibilidade.",
            ),
        )
    if zona_do_alvo != registro.zona:
        # Recusa que explica, em vez de timeout que engana: um erro dizendo
        # "preciso de um agente naquela zona" resolve; 30 s de espera mandam a
        # pessoa procurar defeito no lugar errado.
        return Resultado(
            ok=False,
            resumo=f"alvo em {zona_do_alvo.value}, plataforma em {registro.zona.value}",
            linhas=(
                "a sonda sai de onde a plataforma está e não atravessa zona. "
                f"Para diagnosticar {zona_do_alvo.value} é preciso um agente "
                f"rodando lá dentro.",
            ),
        )
    try:
        return await asyncio.wait_for(
            sonda.executar(alvo, parametros), timeout=sonda.manifesto.limite_s
        )
    except TimeoutError:
        return Resultado(
            ok=False,
            resumo=f"a sonda passou de {sonda.manifesto.limite_s:.0f}s e foi abortada",
            linhas=("diagnóstico que não volta não é diagnóstico",),
        )


__all__ = [
    "ManifestoSonda",
    "Perigo",
    "Registro",
    "Resultado",
    "Salto",
    "Sonda",
    "SondaCaminho",
    "SondaPing",
    "SondaPorta",
    "SondaSnmp",
    "executar",
    "zona_da_plataforma",
]
