"""O processo coletor: amarra registro, agendador e banco.

É aqui que as peças de M1 se encontram. O inventário no Postgres alimenta os
alvos; o agendador roda cada módulo no seu intervalo; o resultado volta para o
banco como estado, transição e saúde do módulo.

A zona do coletor é declarada na partida e confrontada com o manifesto de cada
módulo no carregamento. Um coletor que se diz corporativo não carrega módulo
que só opera em OT, e vice-versa — a recusa acontece antes de qualquer pacote
sair, não durante.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from inventario.modelo import Zona

from .db.coleta import gravar_coleta
from .db.esquema import dispositivo, identificador
from .db.repositorio_pg import criar_engine
from .modulos.contrato import ResultadoColeta
from .modulos.icmp import ModuloIcmp
from .modulos.registro import Agendador, Registro

VAR_BANCO = "PLATAFORMA_BANCO"
VAR_ZONA = "PLATAFORMA_ZONA"


async def alvos_do_inventario(engine: AsyncEngine, zona: Zona) -> list[dict[str, Any]]:
    """Dispositivos da zona do coletor que têm endereço IP.

    O filtro por zona não é conveniência: é o que impede um coletor da rede
    corporativa sondar equipamento de OT porque alguém cadastrou o IP errado.
    """
    consulta = (
        select(dispositivo.c.chave, identificador.c.valor.label("ip"))
        .join(identificador, identificador.c.dispositivo_chave == dispositivo.c.chave)
        .where(identificador.c.tipo == "ip")
        .where(dispositivo.c.zona == zona.value)
    )
    async with engine.connect() as conexao:
        linhas = (await conexao.execute(consulta)).all()
    return [{"chave": ln.chave, "ip": ln.ip} for ln in linhas]


class Coletor:
    """Um processo de coleta, ligado a uma zona."""

    def __init__(self, engine: AsyncEngine, zona: Zona = Zona.CORPORATIVA) -> None:
        self.engine = engine
        self.zona = zona
        self.registro = Registro(zona_do_coletor=zona)
        self.agendador = Agendador(self.registro, self._fonte, self._escoadouro)
        self.ultimo: dict[str, dict[str, int]] = {}

    async def _fonte(self, _nome: str) -> list[dict[str, Any]]:
        return await alvos_do_inventario(self.engine, self.zona)

    async def _escoadouro(self, nome: str, resultado: ResultadoColeta) -> None:
        async with self.engine.begin() as conexao:
            self.ultimo[nome] = await gravar_coleta(conexao, nome, resultado)

    def carregar_padrao(self) -> None:
        """Carrega os módulos de M1. Por ora, um só — e é o que cobre tudo."""
        self.registro.registrar(ModuloIcmp())

    async def rodar_uma_vez(self, nome: str = "icmp") -> dict[str, int]:
        await self.agendador.rodar_uma_vez(nome)
        return self.ultimo.get(nome, {})

    async def servir(self) -> None:
        await self.agendador.iniciar()
        parada = asyncio.Event()
        laco = asyncio.get_running_loop()
        for sinal in (signal.SIGINT, signal.SIGTERM):
            with __import__("contextlib").suppress(NotImplementedError):
                laco.add_signal_handler(sinal, parada.set)
        await parada.wait()
        await self.agendador.parar()


def _url(informada: str | None) -> str:
    url = informada or os.environ.get(VAR_BANCO)
    if not url:
        raise SystemExit(f"defina {VAR_BANCO} ou passe --banco")
    return url


async def _principal(url: str, zona: Zona, uma_vez: bool) -> int:
    engine = criar_engine(url)
    coletor = Coletor(engine, zona)
    coletor.carregar_padrao()
    try:
        if uma_vez:
            resumo = await coletor.rodar_uma_vez()
            print(f"módulos: {coletor.registro.nomes}  zona: {zona.value}")
            for chave, valor in resumo.items():
                print(f"  {chave:<16} {valor}")
            saude = coletor.agendador
            print(f"  {'ciclos':<16} {saude.ciclos}")
            print(f"  {'falhas':<16} {saude.falhas}")
        else:
            await coletor.servir()
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Processo coletor da plataforma.")
    p.add_argument("--banco", default=None)
    p.add_argument(
        "--zona",
        default=os.environ.get(VAR_ZONA, Zona.CORPORATIVA.value),
        choices=[z.value for z in Zona],
    )
    p.add_argument("--uma-vez", action="store_true", help="um ciclo e sai")
    args = p.parse_args(argv)
    return asyncio.run(_principal(_url(args.banco), Zona(args.zona), args.uma_vez))


if __name__ == "__main__":
    raise SystemExit(main())
