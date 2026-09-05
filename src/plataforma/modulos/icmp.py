"""Módulo de coleta por ICMP.

É o primeiro coletor e o mais abrangente: no parque real, **11 de 723 ativos
falam SNMP, mas todos os 723 respondem — ou deixam de responder — a ICMP**. É
o único sinal universal aqui, e não exige uma credencial sequer.

A implementação usa um socket só para todos os alvos, em vez de um processo
``ping`` por dispositivo. Com 708 endereços, a diferença entre as duas
abordagens é a diferença entre caber e não caber na janela de coleta.

Uma decisão que parece detalhe e não é: quando um alvo não responde, o módulo
publica ``ativo_alcancavel = 0`` e **não publica latência nenhuma**. Publicar
latência zero afirmaria que o equipamento respondeu instantaneamente — número
plausível e errado, que meses depois aparece num relatório de disponibilidade
que ninguém consegue explicar.
"""

from __future__ import annotations

import asyncio
import os
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any

from inventario.modelo import Zona

from .contrato import (
    Alvo,
    Descoberta,
    Manifesto,
    Observacao,
    Qualidade,
    ResultadoColeta,
)

TIPO_ECHO_REQUEST = 8
TIPO_ECHO_REPLY = 0
TAMANHO_CABECALHO_ICMP = 8


def _soma_verificacao(dados: bytes) -> int:
    """Soma de verificação do ICMP (RFC 1071)."""
    if len(dados) % 2:
        dados += b"\x00"
    total = sum(struct.unpack(f"!{len(dados) // 2}H", dados))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def montar_echo(identificador: int, sequencia: int, carga: bytes = b"plataforma") -> bytes:
    cabecalho = struct.pack("!BBHHH", TIPO_ECHO_REQUEST, 0, 0, identificador, sequencia)
    verificacao = _soma_verificacao(cabecalho + carga)
    cabecalho = struct.pack(
        "!BBHHH", TIPO_ECHO_REQUEST, 0, verificacao, identificador, sequencia
    )
    return cabecalho + carga


def extrair_resposta(dados: bytes, socket_bruto: bool) -> tuple[int, int] | None:
    """Devolve ``(identificador, sequencia)`` de um echo reply, ou ``None``.

    Com socket bruto o pacote vem com o cabeçalho IP na frente; com socket
    de datagrama, não. Tratar os dois casos é o que permite rodar sem
    privilégio onde o sistema deixa, e com privilégio onde não deixa.
    """
    if socket_bruto:
        if len(dados) < 20:
            return None
        deslocamento = (dados[0] & 0x0F) * 4
        dados = dados[deslocamento:]
    if len(dados) < TAMANHO_CABECALHO_ICMP:
        return None
    tipo, _codigo, _soma, identificador, sequencia = struct.unpack(
        "!BBHHH", dados[:TAMANHO_CABECALHO_ICMP]
    )
    if tipo != TIPO_ECHO_REPLY:
        return None
    return identificador, sequencia


def abrir_socket() -> tuple[socket.socket, bool]:
    """Abre o socket ICMP, preferindo o não privilegiado.

    ``SOCK_DGRAM`` dispensa privilégio, mas depende de ``ping_group_range``
    estar configurado no host. Onde não estiver, cai para ``SOCK_RAW``, que
    exige ``CAP_NET_RAW`` — requisito que precisa constar no empacotamento do
    coletor, e não ser descoberto em produção.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
        return sock, False
    except PermissionError:
        return socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP), True


@dataclass(slots=True)
class Sonda:
    """O que se sabe de um alvo depois da sondagem."""

    alvo: str
    enviados: int = 0
    latencias_ms: list[float] = field(default_factory=list)

    @property
    def recebidos(self) -> int:
        return len(self.latencias_ms)

    @property
    def alcancavel(self) -> bool:
        return self.recebidos > 0

    @property
    def perda_pct(self) -> float:
        if not self.enviados:
            return 100.0
        return 100.0 * (self.enviados - self.recebidos) / self.enviados

    @property
    def latencia_media_ms(self) -> float | None:
        if not self.latencias_ms:
            return None
        return sum(self.latencias_ms) / len(self.latencias_ms)

    @property
    def jitter_ms(self) -> float | None:
        """Variação média entre medições consecutivas.

        Precisa de pelo menos duas respostas: com uma só não existe variação a
        medir, e devolver zero seria afirmar estabilidade que não foi observada.
        """
        if len(self.latencias_ms) < 2:
            return None
        difs = [
            abs(b - a) for a, b in zip(self.latencias_ms, self.latencias_ms[1:], strict=False)
        ]
        return sum(difs) / len(difs)


async def _pronto(laco: asyncio.AbstractEventLoop, fd: int, *, escrita: bool) -> None:
    """Espera o descritor ficar pronto, por prontidão em vez de por método.

    ``sock_sendto`` e ``sock_recvfrom`` existem no asyncio padrão e **não
    existem no uvloop** — que é o laço que o uvicorn usa em produção. Escrito
    com eles, este módulo passava em todo teste e estourava
    ``NotImplementedError`` na primeira sonda disparada pela tela. Foi assim
    que o defeito apareceu: não num teste, num clique.

    ``add_reader``/``add_writer`` os dois laços implementam, e o socket já é
    não-bloqueante — o caminho comum nem chega a esperar.
    """
    futuro = laco.create_future()
    registrar = laco.add_writer if escrita else laco.add_reader
    remover = laco.remove_writer if escrita else laco.remove_reader
    registrar(fd, lambda: futuro.done() or futuro.set_result(None))
    try:
        await futuro
    finally:
        remover(fd)


async def enviar_para(
    laco: asyncio.AbstractEventLoop, sock: socket.socket, pacote: bytes, alvo: str
) -> None:
    while True:
        try:
            sock.sendto(pacote, (alvo, 0))
            return
        except BlockingIOError:
            await _pronto(laco, sock.fileno(), escrita=True)


async def receber_de(
    laco: asyncio.AbstractEventLoop, sock: socket.socket, tamanho: int = 2048
) -> tuple[bytes, Any]:
    while True:
        try:
            return sock.recvfrom(tamanho)
        except BlockingIOError:
            await _pronto(laco, sock.fileno(), escrita=False)


async def sondar(
    alvos: list[str], tentativas: int = 3, timeout_s: float = 1.0
) -> dict[str, Sonda]:
    """Sonda todos os alvos, em rodadas, por um socket só."""
    sondas = {alvo: Sonda(alvo=alvo) for alvo in alvos}
    if not alvos:
        return sondas

    sock, bruto = abrir_socket()
    sock.setblocking(False)
    identificador = os.getpid() & 0xFFFF
    laco = asyncio.get_running_loop()

    try:
        for sequencia in range(1, tentativas + 1):
            pacote = montar_echo(identificador, sequencia)
            envio: dict[str, float] = {}
            for alvo in alvos:
                try:
                    await enviar_para(laco, sock, pacote, alvo)
                    envio[alvo] = time.perf_counter()
                    sondas[alvo].enviados += 1
                except OSError:
                    # Endereço inválido ou rota inexistente: conta como envio
                    # feito e sem resposta, que é o que o operador percebe.
                    sondas[alvo].enviados += 1

            limite = laco.time() + timeout_s
            pendentes = set(envio)
            while pendentes and (restante := limite - laco.time()) > 0:
                try:
                    dados, origem = await asyncio.wait_for(
                        receber_de(laco, sock), restante
                    )
                except TimeoutError:
                    break
                except OSError:
                    break

                resposta = extrair_resposta(dados, bruto)
                if resposta is None:
                    continue
                _ident, seq = resposta
                endereco = origem[0]
                if seq != sequencia or endereco not in pendentes:
                    continue
                decorrido = (time.perf_counter() - envio[endereco]) * 1000.0
                sondas[endereco].latencias_ms.append(decorrido)
                pendentes.discard(endereco)
    finally:
        sock.close()

    return sondas


MANIFESTO = Manifesto(
    nome="icmp",
    versao="1.0.0",
    fabricante="generico",
    alvo=Alvo.DISPOSITIVO,
    descoberta=Descoberta.DELEGADA,
    intervalo_metricas_s=60,
    produz_metricas=(
        "ativo_alcancavel",
        "ativo_latencia_ms",
        "ativo_perda_pacote_pct",
        "ativo_jitter_ms",
    ),
    somente_leitura=True,
    # Um eco ICMP não lê nem escreve nada no equipamento: pergunta se o
    # endereço responde. É o que permite declarar ot_nivel3 sem afrouxar
    # nada — o coletor continua preso à sua zona, e sondar a OT exige um
    # processo rodando **dentro** dela. Os níveis 0 a 2 continuam recusados
    # pelo próprio manifesto, e nenhuma declaração os alcança.
    zona_permitida=(Zona.CORPORATIVA, Zona.OT_NIVEL3),
)


class ModuloIcmp:
    """Coletor ICMP conforme o contrato de módulo."""

    def __init__(self, tentativas: int = 3, timeout_s: float = 1.0) -> None:
        self.manifesto = MANIFESTO
        self.tentativas = tentativas
        self.timeout_s = timeout_s

    async def coletar(self, alvos: list[dict[str, Any]]) -> ResultadoColeta:
        inicio = time.perf_counter()
        por_ip = {a["ip"]: a for a in alvos if a.get("ip")}
        sondas = await sondar(list(por_ip), self.tentativas, self.timeout_s)

        observacoes: list[Observacao] = []
        falhas = 0
        for ip, sonda in sondas.items():
            sujeito = por_ip[ip].get("chave", ip)
            observacoes.append(
                Observacao(
                    sujeito=sujeito,
                    metrica="ativo_alcancavel",
                    valor=1.0 if sonda.alcancavel else 0.0,
                )
            )
            observacoes.append(
                Observacao(
                    sujeito=sujeito,
                    metrica="ativo_perda_pacote_pct",
                    valor=sonda.perda_pct,
                )
            )
            if not sonda.alcancavel:
                falhas += 1
                # Sem latência: ausência de dado é informação; zero seria mentira.
                continue
            observacoes.append(
                Observacao(
                    sujeito=sujeito,
                    metrica="ativo_latencia_ms",
                    valor=sonda.latencia_media_ms or 0.0,
                    qualidade=Qualidade.BOA if sonda.perda_pct == 0 else Qualidade.INCERTA,
                )
            )
            if (jitter := sonda.jitter_ms) is not None:
                observacoes.append(
                    Observacao(sujeito=sujeito, metrica="ativo_jitter_ms", valor=jitter)
                )

        return ResultadoColeta(
            observacoes=tuple(observacoes),
            alvos_total=len(por_ip),
            alvos_falha=falhas,
            duracao_s=time.perf_counter() - inicio,
        )
