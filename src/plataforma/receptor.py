"""Receptor de syslog e trap — o processo que escuta em vez de perguntar.

Todo o resto da plataforma é *pull*: o coletor decide quando perguntar e para
quem. Aqui é *push*, e a inversão traz problemas que os outros canais não têm.

Zona, aqui, quer dizer outra coisa
----------------------------------

No coletor, a zona impede **alcançar** equipamento de OT. Um receptor não
alcança nada: ele senta numa porta e recebe o que chegar. O risco é outro — se
um receptor na rede corporativa recebe uma mensagem cujo IP pertence a um
equipamento de OT, ou a rede está fazendo ponte onde não deveria, ou alguém
está forjando. Nos dois casos é achado, e o evento fica marcado
``confianca="ip_de_outra_zona"`` em vez de entrar como se fosse normal.

Gravação em lote, não por mensagem
----------------------------------

Uma escrita no banco por datagrama transformaria uma tempestade de syslog numa
tempestade de transações. O receptor acumula e descarrega em lotes — e o
tamanho do lote é limitado porque memória também acaba.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from inventario.modelo import ZONAS_PROIBIDAS, Zona
from plataforma.db.eventos import Vazao, gravar
from plataforma.db.repositorio_pg import criar_engine
from plataforma.eventos import Evento, analisar_syslog

VAR_BANCO = "PLATAFORMA_BANCO"
VAR_ZONA = "PLATAFORMA_ZONA"

PORTA_SYSLOG = 514
PORTA_TRAP = 162

#: Mensagens acumuladas antes de uma descarga forçada. Segura uma rajada sem
#: deixar a memória crescer sem limite.
LOTE_MAXIMO = 500
#: Segundos entre descargas quando o lote não enche.
INTERVALO_DESCARGA = 5.0


class ProtocoloSyslog(asyncio.DatagramProtocol):
    """Recebe datagramas e enfileira. Não toca no banco: quem grava é o laço.

    **Syslog é UDP, e UDP perde.** Numa rajada, o kernel descarta datagramas
    antes de qualquer código nosso rodar — e não há como o receptor saber
    quantos. Medido aqui: 400 mensagens numa rajada, 294 chegaram. O buffer
    grande abaixo reduz muito isso, mas não elimina, e fingir o contrário
    seria a plataforma mentindo sobre a própria cobertura. Quem precisa da
    conta exata olha ``netstat -su`` no host, ou usa syslog sobre TCP.
    """

    def __init__(self, fila: asyncio.Queue, vazao: Vazao) -> None:
        self.fila = fila
        self.vazao = vazao
        self.recebidos = 0
        #: Enfileirados que não couberam. Diferente do descarte por limite:
        #: aqui a plataforma não deu conta, e isso tem de aparecer.
        self.sem_fila = 0

    def connection_made(self, transporte) -> None:
        import socket

        sock = transporte.get_extra_info("socket")
        if sock is None:
            return
        # 8 MB de buffer de recepção. O padrão do sistema costuma ser de
        # centenas de kB, que uma tempestade de syslog enche em milissegundos.
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)

    def datagram_received(self, dados: bytes, endereco) -> None:
        agora = datetime.now(UTC)
        origem = endereco[0]
        self.recebidos += 1
        if not self.vazao.aceita(origem, agora):
            return
        try:
            self.fila.put_nowait(analisar_syslog(dados, origem, agora))
        except asyncio.QueueFull:
            # Nunca em silêncio: fila cheia é a plataforma ficando para trás,
            # e é exatamente o que precisa aparecer quando aparece.
            self.sem_fila += 1


class Receptor:
    """Um processo de escuta, ligado a uma zona."""

    def __init__(
        self,
        engine: AsyncEngine,
        zona: Zona = Zona.CORPORATIVA,
        porta_syslog: int = PORTA_SYSLOG,
    ) -> None:
        if zona in ZONAS_PROIBIDAS:
            # Mesma linha dos módulos: nem escutar o que um controlador de
            # processo diz a plataforma faz.
            raise ValueError(
                f"zona {zona.value!r} é proibida: lá vivem os controladores de "
                f"processo. Não é configuração, é impossibilidade."
            )
        self.engine = engine
        self.zona = zona
        self.porta_syslog = porta_syslog
        self.fila: asyncio.Queue = asyncio.Queue(maxsize=LOTE_MAXIMO * 20)
        self.vazao = Vazao()
        self.gravados = 0
        self.descartados = 0
        self.protocolo: ProtocoloSyslog | None = None

    async def _zonas_por_ip(self) -> dict[str, str]:
        from plataforma.db.esquema import dispositivo, identificador

        async with self.engine.connect() as conexao:
            linhas = (
                await conexao.execute(
                    select(identificador.c.valor, dispositivo.c.zona)
                    .join(
                        dispositivo,
                        dispositivo.c.chave == identificador.c.dispositivo_chave,
                    )
                    .where(identificador.c.tipo == "ip")
                )
            ).all()
        return {ln.valor: ln.zona for ln in linhas}

    def marcar_zona(self, eventos: list[Evento], zonas: dict[str, str]) -> None:
        """Marca o que veio de fora da zona deste receptor.

        Não recusa: registra. Um evento recusado em silêncio some; um evento
        marcado vira pergunta — a rede está fazendo ponte, ou alguém está
        forjando?
        """
        for e in eventos:
            zona = zonas.get(e.origem_ip)
            if zona is not None and zona != self.zona.value:
                e.confianca = "ip_de_outra_zona"
                e.atributos["zona_do_cadastro"] = zona

    async def _descarregar(self) -> None:
        lote: list[Evento] = []
        while not self.fila.empty() and len(lote) < LOTE_MAXIMO:
            lote.append(self.fila.get_nowait())

        if self.protocolo is not None and self.protocolo.sem_fila:
            quantos, self.protocolo.sem_fila = self.protocolo.sem_fila, 0
            lote.append(
                Evento(
                    origem_ip="0.0.0.0",  # noqa: S104
                    severidade="alerta",
                    mensagem=(
                        f"{quantos} mensagens perdidas porque a fila do receptor "
                        f"encheu — a plataforma não deu conta do volume"
                    ),
                    confianca="plataforma",
                    atributos={"perdidas_na_fila": quantos},
                )
            )
        for origem, quantos in self.vazao.descartes().items():
            self.descartados += quantos
            # O descarte vira evento. Silêncio aqui seria a plataforma
            # escondendo que ficou cega justo quando havia mais o que ver.
            lote.append(
                Evento(
                    origem_ip=origem,
                    tipo="syslog",
                    severidade="aviso",
                    mensagem=(
                        f"{quantos} mensagens descartadas por exceder o limite de "
                        f"{self.vazao.limite}/min — origem em tempestade"
                    ),
                    confianca="plataforma",
                    atributos={"descartadas": quantos},
                )
            )
        if not lote:
            return
        zonas = await self._zonas_por_ip()
        self.marcar_zona(lote, zonas)
        async with self.engine.begin() as conexao:
            resultado = await gravar(conexao, lote)
        self.gravados += resultado["gravados"]

    async def servir(self) -> None:
        laco = asyncio.get_running_loop()
        transporte, self.protocolo = await laco.create_datagram_endpoint(
            lambda: ProtocoloSyslog(self.fila, self.vazao),
            local_addr=("0.0.0.0", self.porta_syslog),  # noqa: S104
        )
        print(
            f"receptor de syslog na porta {self.porta_syslog}, zona "
            f"{self.zona.value}",
            flush=True,
        )
        parada = asyncio.Event()
        for sinal in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                laco.add_signal_handler(sinal, parada.set)
        try:
            while not parada.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(parada.wait(), INTERVALO_DESCARGA)
                await self._descarregar()
        finally:
            await self._descarregar()
            transporte.close()


def _url(informada: str | None) -> str:
    url = informada or os.environ.get(VAR_BANCO)
    if not url:
        raise SystemExit(f"defina {VAR_BANCO} ou passe --banco")
    return url


async def _principal(url: str, zona: Zona, porta: int) -> int:
    engine = criar_engine(url)
    try:
        await Receptor(engine, zona, porta).servir()
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Receptor de syslog da plataforma.")
    p.add_argument("--banco", default=None)
    p.add_argument(
        "--zona",
        default=os.environ.get(VAR_ZONA, Zona.CORPORATIVA.value),
        choices=[z.value for z in Zona],
    )
    p.add_argument(
        "--porta", type=int, default=PORTA_SYSLOG,
        help="514 exige privilégio; use 5140 para testar sem root",
    )
    args = p.parse_args(argv)
    return asyncio.run(_principal(_url(args.banco), Zona(args.zona), args.porta))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["INTERVALO_DESCARGA", "LOTE_MAXIMO", "ProtocoloSyslog", "Receptor", "main"]
