"""Testes da persistência dos arranjos.

A propriedade que interessa é a mesma das imagens: **arrumar uma tela vale para
todas as máquinas iguais**. Sem isso, personalizar 299 caminhões é um trabalho
que ninguém termina, e o recurso não é usado.

A segunda é a reversibilidade: apagar o arranjo faz a cascata voltar a valer.
Quem experimenta precisa poder desfazer sem restaurar backup.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from plataforma.arranjos import Arranjo, Cartao, Contexto, TipoCartao
from plataforma.db.repositorio_pg import apagar_esquema, criar_engine, criar_esquema
from plataforma.db.telas import guardar, ler, listar, remover, resolver

URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma_teste",
)


@pytest_asyncio.fixture
async def conexao():
    motor = criar_engine(URL)
    try:
        await apagar_esquema(motor)
        await criar_esquema(motor)
    except Exception as erro:  # noqa: BLE001
        await motor.dispose()
        pytest.skip(f"Postgres indisponível: {erro}")
    async with motor.begin() as cx:
        yield cx
    await apagar_esquema(motor)
    await motor.dispose()


def arranjo(escopo: str, *tipos: TipoCartao, contexto: Contexto = Contexto.ATIVO) -> Arranjo:
    return Arranjo(
        escopo=escopo, contexto=contexto,
        cartoes=tuple(Cartao(tipo=t) for t in tipos),
    )


class TestGuardarELer:
    @pytest.mark.asyncio
    async def test_ida_e_volta(self, conexao) -> None:
        a = arranjo("frota:CA", TipoCartao.RESUMO, TipoCartao.ALCANCE)
        await guardar(conexao, a, por="ana")
        lido = await ler(conexao, "frota:CA")
        assert [c.tipo for c in lido.cartoes] == [TipoCartao.RESUMO, TipoCartao.ALCANCE]

    @pytest.mark.asyncio
    async def test_titulo_e_largura_sobrevivem(self, conexao) -> None:
        """Renomear é metade do recurso: o nome tem de voltar como foi salvo."""
        a = Arranjo(
            escopo="frota:CA", contexto=Contexto.ATIVO,
            cartoes=(Cartao(tipo=TipoCartao.TELEMETRIA, titulo="Pressão dos pneus", largura=3),),
        )
        await guardar(conexao, a)
        lido = await ler(conexao, "frota:CA")
        assert lido.cartoes[0].titulo == "Pressão dos pneus"
        assert lido.cartoes[0].largura == 3

    @pytest.mark.asyncio
    async def test_conteudo_do_cartao_de_texto_sobrevive(self, conexao) -> None:
        """O texto livre é o cartão onde vai procedimento e contato de
        fornecedor. Perdê-lo no ida e volta esvaziaria o recurso."""
        nota = "Torque: 650 N·m.\nFornecedor: ramal 4412."
        await guardar(
            conexao,
            Arranjo(
                escopo="frota:CA", contexto=Contexto.ATIVO,
                cartoes=(Cartao(tipo=TipoCartao.TEXTO, opcoes={"conteudo": nota}),),
            ),
        )
        lido = await ler(conexao, "frota:CA")
        assert lido.cartoes[0].opcoes["conteudo"] == nota

    @pytest.mark.asyncio
    async def test_salvar_de_novo_substitui(self, conexao) -> None:
        await guardar(conexao, arranjo("frota:CA", TipoCartao.RESUMO))
        await guardar(conexao, arranjo("frota:CA", TipoCartao.ALCANCE, TipoCartao.RESUMO))
        assert len((await ler(conexao, "frota:CA")).cartoes) == 2
        assert len(await listar(conexao)) == 1

    @pytest.mark.asyncio
    async def test_cartao_fora_de_contexto_nao_chega_ao_banco(self, conexao) -> None:
        errado = arranjo("papel:gps", TipoCartao.COMPONENTES, contexto=Contexto.DISPOSITIVO)
        with pytest.raises(ValueError, match="não serve no contexto"):
            await guardar(conexao, errado)
        assert await listar(conexao) == []


class TestCascata:
    @pytest.mark.asyncio
    async def test_arranjo_de_frota_vale_para_a_frota_toda(self, conexao) -> None:
        """A propriedade que torna o recurso usável."""
        await guardar(conexao, arranjo("frota:CA", TipoCartao.RESUMO))
        for maquina in ("CA-1001", "CA-2020", "CA-4711"):
            _, origem = await resolver(conexao, Contexto.ATIVO, maquina, "CA")
            assert origem == "frota:CA"

    @pytest.mark.asyncio
    async def test_outra_frota_nao_e_afetada(self, conexao) -> None:
        await guardar(conexao, arranjo("frota:CA", TipoCartao.RESUMO))
        _, origem = await resolver(conexao, Contexto.ATIVO, "EH-6102", "EH")
        assert origem == "padrao_ativo"

    @pytest.mark.asyncio
    async def test_o_especifico_vence_o_grupo(self, conexao) -> None:
        await guardar(conexao, arranjo("frota:CA", TipoCartao.RESUMO))
        await guardar(conexao, arranjo("ativo:CA-1001", TipoCartao.ALCANCE))
        a, origem = await resolver(conexao, Contexto.ATIVO, "CA-1001", "CA")
        assert origem == "ativo:CA-1001"
        assert a.cartoes[0].tipo is TipoCartao.ALCANCE

    @pytest.mark.asyncio
    async def test_apagar_devolve_a_cascata(self, conexao) -> None:
        """É assim que se desfaz um experimento — sem restaurar backup."""
        await guardar(conexao, arranjo("frota:CA", TipoCartao.RESUMO))
        await guardar(conexao, arranjo("ativo:CA-1001", TipoCartao.ALCANCE))
        assert await remover(conexao, "ativo:CA-1001") is True
        _, origem = await resolver(conexao, Contexto.ATIVO, "CA-1001", "CA")
        assert origem == "frota:CA"

    @pytest.mark.asyncio
    async def test_apagar_o_que_nao_existe_nao_mente(self, conexao) -> None:
        assert await remover(conexao, "frota:XX") is False

    @pytest.mark.asyncio
    async def test_papel_cobre_os_dispositivos_daquele_papel(self, conexao) -> None:
        await guardar(
            conexao,
            arranjo("papel:radio_mesh", TipoCartao.IDENTIDADE, contexto=Contexto.DISPOSITIVO),
        )
        _, origem = await resolver(conexao, Contexto.DISPOSITIVO, "QUALQUER-RJT", "radio_mesh")
        assert origem == "papel:radio_mesh"
        _, outro = await resolver(conexao, Contexto.DISPOSITIVO, "UM-GPS", "gps")
        assert outro == "padrao_dispositivo"

    @pytest.mark.asyncio
    async def test_sem_nada_salvo_cai_no_padrao_embutido(self, conexao) -> None:
        a, origem = await resolver(conexao, Contexto.ATIVO, "CA-1001", "CA")
        assert origem == "padrao_ativo"
        assert a.cartoes, "o padrão embutido não pode ser vazio"


class TestListagem:
    @pytest.mark.asyncio
    async def test_diz_quem_mexeu(self, conexao) -> None:
        """A área ADM precisa poder responder 'quem mudou esta tela'."""
        await guardar(conexao, arranjo("frota:CA", TipoCartao.RESUMO), por="ana")
        (linha,) = await listar(conexao)
        assert linha["escopo"] == "frota:CA"
        assert linha["atualizado_por"] == "ana"
        assert linha["cartoes"] == 1
        assert linha["atualizado_em"] is not None
