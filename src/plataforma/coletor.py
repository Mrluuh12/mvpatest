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

from inventario.modelo import ZONAS_PROIBIDAS, Zona

from .db.coleta import gravar_coleta
from .db.esquema import dispositivo, identificador
from .db.repositorio_pg import criar_engine
from .modulos.contrato import Alvo, ResultadoColeta
from .modulos.icmp import ModuloIcmp
from .modulos.rajant import ModuloRajant
from .modulos.registro import Agendador, Registro

VAR_BANCO = "PLATAFORMA_BANCO"
VAR_ZONA = "PLATAFORMA_ZONA"
#: URL do Prometheus que o exportador Rajant alimenta. Ausente = módulo não
#: carrega.
VAR_PROMETHEUS = "PLATAFORMA_PROMETHEUS"
#: Nome da credencial SNMP no cofre. Ausente = módulo não carrega.
VAR_CREDENCIAL_SNMP = "PLATAFORMA_SNMP_CREDENCIAL"


async def alvos_do_inventario(
    engine: AsyncEngine,
    zona: Zona,
    papeis: tuple[str, ...] = (),
    conecta_no_alvo: bool = True,
) -> list[dict[str, Any]]:
    """Dispositivos que o módulo deve cobrir, com endereço IP.

    O filtro por zona não é conveniência: é o que impede um coletor da rede
    corporativa sondar equipamento de OT porque alguém cadastrou o IP errado.

    ``conecta_no_alvo=False`` desliga esse filtro — e só ele. Vale para módulo
    de ``Alvo.SISTEMA``, que **não abre conexão com o equipamento**: fala com
    um sistema só (o Prometheus, um historiador, uma API de fabricante) e
    atribui o que lê. Zona limita o que o módulo *alcança*; quem nunca alcança
    o equipamento não pode ser limitado por onde ele está. O dado já cruzou a
    fronteira antes, no exportador, que é onde essa decisão pertence.

    As zonas proibidas continuam de fora em qualquer caso. Essa linha não se
    move por tipo de módulo: nem atribuir leitura a um controlador de processo
    a plataforma faz.

    ``papeis`` restringe ao que o módulo declara cobrir. Sem isso, um módulo
    de rádio receberia todos os alvos da zona e reportaria como falha de
    cobertura todo switch e toda câmera que nunca foram problema dele.
    """
    consulta = (
        select(
            dispositivo.c.chave,
            dispositivo.c.nome_canonico.label("nome"),
            dispositivo.c.papel,
            identificador.c.valor.label("ip"),
        )
        .join(identificador, identificador.c.dispositivo_chave == dispositivo.c.chave)
        .where(identificador.c.tipo == "ip")
    )
    if conecta_no_alvo:
        consulta = consulta.where(dispositivo.c.zona == zona.value)
    else:
        consulta = consulta.where(
            dispositivo.c.zona.notin_([z.value for z in ZONAS_PROIBIDAS])
        )
    if papeis:
        consulta = consulta.where(dispositivo.c.papel.in_(list(papeis)))
    async with engine.connect() as conexao:
        linhas = (await conexao.execute(consulta)).all()
    return [
        {"chave": ln.chave, "nome": ln.nome, "papel": ln.papel, "ip": ln.ip}
        for ln in linhas
    ]


class Coletor:
    """Um processo de coleta, ligado a uma zona."""

    def __init__(self, engine: AsyncEngine, zona: Zona = Zona.CORPORATIVA) -> None:
        self.engine = engine
        self.zona = zona
        self.registro = Registro(zona_do_coletor=zona)
        self.agendador = Agendador(self.registro, self._fonte, self._escoadouro)
        self.ultimo: dict[str, dict[str, int]] = {}

    async def _fonte(self, nome: str) -> list[dict[str, Any]]:
        if nome not in self.registro:
            return await alvos_do_inventario(self.engine, self.zona)
        manifesto = self.registro.obter(nome).manifesto
        return await alvos_do_inventario(
            self.engine,
            self.zona,
            manifesto.papeis_alvo,
            conecta_no_alvo=manifesto.alvo is not Alvo.SISTEMA,
        )

    async def _escoadouro(self, nome: str, resultado: ResultadoColeta) -> None:
        async with self.engine.begin() as conexao:
            self.ultimo[nome] = await gravar_coleta(conexao, nome, resultado)

    async def carregar_padrao(self) -> None:
        """Carrega os módulos disponíveis para esta zona.

        Cada um entra só se estiver configurado. Carregar sempre faria toda
        instalação acumular falha de módulo que ninguém pediu — e módulo que
        falha por não estar configurado ensina a ignorar módulo que falha de
        verdade.
        """
        self.registro.registrar(ModuloIcmp())
        if url := os.environ.get(VAR_PROMETHEUS):
            self.registro.registrar(ModuloRajant(url))
        await self._carregar_snmp()

    async def _carregar_snmp(self) -> None:
        """O SNMP é o primeiro módulo que precisa de segredo.

        A credencial é aberta **aqui**, pelo coletor, que tem banco — o módulo
        continua sem tocar em Postgres, como os demais. A zona é conferida na
        abertura, que é a última linha antes de o segredo virar pacote UDP.
        """
        nome = os.environ.get(VAR_CREDENCIAL_SNMP)
        if not nome:
            return
        from .db.credenciais import CofreSemChave, abrir, listar
        from .modulos.snmp import Credencial, ModuloSnmp, SessaoPysnmp

        try:
            async with self.engine.connect() as conexao:
                segredo = await abrir(conexao, nome, self.zona)
                # A porta não é segredo: fica em claro nos atributos, para a
                # tela poder mostrá-la sem descriptografar nada.
                atributos = next(
                    (c["atributos"] for c in await listar(conexao) if c["nome"] == nome),
                    {},
                )
        except (CofreSemChave, PermissionError) as erro:
            # Falha alto: um SNMP que silenciosamente não carrega é pior que um
            # que não existe, porque a tela diz que a família está coberta.
            raise SystemExit(f"credencial {nome!r}: {erro}") from erro
        if segredo is None:
            raise SystemExit(f"credencial {nome!r} não existe no cofre")
        self.registro.registrar(
            ModuloSnmp(
                SessaoPysnmp(
                    Credencial(**segredo), porta=int(atributos.get("porta", 161))
                )
            )
        )

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


async def _principal(url: str, zona: Zona, uma_vez: bool, modulo: str) -> int:
    engine = criar_engine(url)
    coletor = Coletor(engine, zona)
    await coletor.carregar_padrao()
    try:
        if uma_vez:
            resumo = await coletor.rodar_uma_vez(modulo)
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
    p.add_argument(
        "--modulo", default="icmp", help="qual módulo rodar com --uma-vez"
    )
    args = p.parse_args(argv)
    return asyncio.run(
        _principal(_url(args.banco), Zona(args.zona), args.uma_vez, args.modulo)
    )


if __name__ == "__main__":
    raise SystemExit(main())
