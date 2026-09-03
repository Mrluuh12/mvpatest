"""Registro e agendamento de módulos.

Três garantias vivem aqui, e todas existem porque a alternativa já custou caro
em algum lugar:

**Isolamento de falha.** Um módulo que estoura não derruba os outros. A
exceção é capturada, vira contagem de falha e o ciclo seguinte acontece.

**Auto-observação obrigatória.** As cinco séries da família ``modulo`` são
emitidas pelo agendador, não pelo módulo — inclusive, e principalmente, quando
a coleta falha. Módulo que morre em silêncio faz as métricas simplesmente
pararem, e ausência de dado ruim é indistinguível de ausência de problema.

**Zona do coletor casa com zona do módulo.** Um manifesto declara onde pode
operar; o processo coletor declara onde está. Se não baterem, o módulo não
carrega. É o par que impede um coletor da rede corporativa ser usado para
alcançar OT por engano.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from inventario.modelo import Zona

from .contrato import Modulo, Observacao, Qualidade, ResultadoColeta

#: Assinatura de quem fornece os alvos de um módulo a cada ciclo.
FonteDeAlvos = Callable[[str], Awaitable[list[dict[str, Any]]]]

#: Assinatura de quem recebe o que foi coletado.
Escoadouro = Callable[[str, ResultadoColeta], Awaitable[None]]


class ZonaIncompativel(ValueError):
    """O módulo não pode operar na zona onde este coletor está."""


class Registro:
    """Guarda os módulos carregados e valida a zona no carregamento."""

    def __init__(self, zona_do_coletor: Zona = Zona.CORPORATIVA) -> None:
        self.zona = zona_do_coletor
        self._modulos: dict[str, Modulo] = {}

    def registrar(self, modulo: Modulo) -> None:
        manifesto = modulo.manifesto
        if not manifesto.pode_operar_em(self.zona):
            permitidas = [z.value for z in manifesto.zona_permitida]
            raise ZonaIncompativel(
                f"módulo {manifesto.nome!r} declara operar em {permitidas}, "
                f"mas este coletor está em {self.zona.value!r}"
            )
        self._modulos[manifesto.nome] = modulo

    def __contains__(self, nome: str) -> bool:
        return nome in self._modulos

    def __len__(self) -> int:
        return len(self._modulos)

    @property
    def nomes(self) -> list[str]:
        return sorted(self._modulos)

    def obter(self, nome: str) -> Modulo:
        return self._modulos[nome]

    def todos(self) -> list[Modulo]:
        return [self._modulos[n] for n in self.nomes]


def _auto_observacao(
    nome: str,
    resultado: ResultadoColeta,
    ok: bool,
    momento: datetime,
) -> list[Observacao]:
    """As cinco séries que todo módulo publica sobre si mesmo."""
    sujeito = f"modulo:{nome}"
    series = [
        Observacao(
            sujeito=sujeito,
            metrica="modulo_alvos_total",
            valor=float(resultado.alvos_total),
            em=momento,
        ),
        Observacao(
            sujeito=sujeito,
            metrica="modulo_alvos_falha",
            valor=float(resultado.alvos_falha),
            em=momento,
        ),
        Observacao(
            sujeito=sujeito,
            metrica="modulo_duracao_coleta_s",
            valor=resultado.duracao_s,
            em=momento,
        ),
        Observacao(
            sujeito=sujeito,
            metrica="modulo_amostras_rejeitadas_total",
            valor=float(len(resultado.rejeitadas)),
            em=momento,
        ),
    ]
    if ok:
        series.append(
            Observacao(
                sujeito=sujeito,
                metrica="modulo_ultima_coleta_ok_timestamp",
                valor=momento.timestamp(),
                em=momento,
            )
        )
    return series


async def executar_ciclo(
    modulo: Modulo, alvos: list[dict[str, Any]]
) -> tuple[ResultadoColeta, Exception | None]:
    """Roda uma coleta com isolamento de falha.

    Devolve sempre um resultado utilizável. Quando o módulo estoura, o
    resultado descreve a falha — com ``alvos_falha`` igual ao total, para que a
    plataforma saiba que **não conseguiu perguntar**, em vez de concluir que
    todos os equipamentos estão mal.
    """
    nome = modulo.manifesto.nome
    inicio = time.perf_counter()
    try:
        resultado = await modulo.coletar(alvos)
    except Exception as erro:  # noqa: BLE001 - isolar falha é o propósito desta função
        falho = ResultadoColeta(
            alvos_total=len(alvos),
            alvos_falha=len(alvos),
            duracao_s=time.perf_counter() - inicio,
            rejeitadas=(f"{nome} falhou: {erro!r}",),
        )
        return falho, erro
    return resultado, None


class Agendador:
    """Roda cada módulo no seu intervalo, sem que um atrapalhe o outro."""

    def __init__(
        self,
        registro: Registro,
        fonte: FonteDeAlvos,
        escoadouro: Escoadouro,
    ) -> None:
        self.registro = registro
        self.fonte = fonte
        self.escoadouro = escoadouro
        self._tarefas: list[asyncio.Task] = []
        self.ciclos: dict[str, int] = {}
        self.falhas: dict[str, int] = {}

    async def rodar_uma_vez(self, nome: str) -> ResultadoColeta:
        """Um ciclo completo de um módulo: alvos, coleta, auto-observação, entrega."""
        modulo = self.registro.obter(nome)
        momento = datetime.now(UTC)
        try:
            alvos = await self.fonte(nome)
        except Exception as erro:  # noqa: BLE001 - falha da fonte também é falha do ciclo
            alvos = []
            resultado, falhou = (
                ResultadoColeta(rejeitadas=(f"fonte de alvos falhou: {erro!r}",)),
                erro,
            )
        else:
            resultado, falhou = await executar_ciclo(modulo, alvos)

        self.ciclos[nome] = self.ciclos.get(nome, 0) + 1
        if falhou is not None:
            self.falhas[nome] = self.falhas.get(nome, 0) + 1

        completo = resultado.model_copy(
            update={
                "observacoes": resultado.observacoes
                + tuple(_auto_observacao(nome, resultado, falhou is None, momento))
            }
        )
        await self.escoadouro(nome, completo)
        return completo

    async def _laco(self, nome: str) -> None:
        intervalo = self.registro.obter(nome).manifesto.intervalo_metricas_s
        while True:
            comeco = asyncio.get_running_loop().time()
            with contextlib.suppress(asyncio.CancelledError):
                await self.rodar_uma_vez(nome)
            # Dorme o que sobra do intervalo. Se a coleta estourou a janela, o
            # ciclo seguinte começa já — e `modulo_duracao_coleta_s` denuncia.
            resto = intervalo - (asyncio.get_running_loop().time() - comeco)
            await asyncio.sleep(max(resto, 0))

    async def iniciar(self) -> None:
        self._tarefas = [
            asyncio.create_task(self._laco(nome), name=f"coleta:{nome}")
            for nome in self.registro.nomes
        ]

    async def parar(self) -> None:
        for tarefa in self._tarefas:
            tarefa.cancel()
        for tarefa in self._tarefas:
            with contextlib.suppress(asyncio.CancelledError):
                await tarefa
        self._tarefas = []


__all__ = [
    "Agendador",
    "Registro",
    "ZonaIncompativel",
    "executar_ciclo",
    "Qualidade",
]
