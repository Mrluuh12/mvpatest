"""Testes do cofre de credenciais.

A diferença que define o desenho: senha de usuário só precisa ser *conferida*
(hash irreversível basta); credencial precisa ser *apresentada* ao equipamento
(a comunidade SNMP vai dentro do pacote). Logo é cifra reversível — e com isso
vêm obrigações que hash não tem.
"""

from __future__ import annotations

import base64
import os

import pytest
import pytest_asyncio

from inventario.modelo import Zona
from plataforma.db.credenciais import (
    VAR_CHAVE,
    CofreSemChave,
    SegredoInvalido,
    abrir,
    gerar_chave,
    guardar,
    listar,
    remover,
)
from plataforma.db.esquema import credencial
from plataforma.db.repositorio_pg import apagar_esquema, criar_engine, criar_esquema

URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma_teste",
)

pytestmark = pytest.mark.asyncio
COMUNIDADE = {"comunidade": "publico-nao-e-segredo-mas-serve"}


@pytest_asyncio.fixture
async def conexao(monkeypatch):
    monkeypatch.setenv(VAR_CHAVE, gerar_chave())
    motor = criar_engine(URL)
    try:
        await apagar_esquema(motor)
        await criar_esquema(motor)
    except Exception as erro:  # noqa: BLE001
        await motor.dispose()
        pytest.skip(f"Postgres indisponível: {erro}")
    async with motor.begin() as c:
        yield c
    await apagar_esquema(motor)
    await motor.dispose()


class TestCiclo:
    async def test_ida_e_volta(self, conexao) -> None:
        await guardar(conexao, "snmp-corp", "snmp_v2c", Zona.CORPORATIVA, COMUNIDADE)
        assert await abrir(conexao, "snmp-corp", Zona.CORPORATIVA) == COMUNIDADE

    async def test_substituir_mantem_um_registro(self, conexao) -> None:
        await guardar(conexao, "s", "snmp_v2c", Zona.CORPORATIVA, {"comunidade": "a"})
        await guardar(conexao, "s", "snmp_v2c", Zona.CORPORATIVA, {"comunidade": "b"})
        assert (await abrir(conexao, "s", Zona.CORPORATIVA))["comunidade"] == "b"
        assert len(await listar(conexao)) == 1

    async def test_credencial_inexistente_e_none_nao_erro(self, conexao) -> None:
        assert await abrir(conexao, "nao-existe", Zona.CORPORATIVA) is None

    async def test_remover(self, conexao) -> None:
        await guardar(conexao, "s", "snmp_v2c", Zona.CORPORATIVA, COMUNIDADE)
        assert await remover(conexao, "s") is True
        assert await remover(conexao, "s") is False


class TestSegredoNaoVaza:
    async def test_o_banco_nao_guarda_texto_claro(self, conexao) -> None:
        """O teste que importa: quem levar o dump leva ciphertext."""
        await guardar(conexao, "snmp-corp", "snmp_v2c", Zona.CORPORATIVA, COMUNIDADE)
        linha = (await conexao.execute(credencial.select())).first()
        cru = bytes(linha.segredo)
        assert b"publico-nao-e-segredo-mas-serve" not in cru
        assert b"comunidade" not in cru

    async def test_listar_nunca_devolve_segredo(self, conexao) -> None:
        """Nem mascarado, nem uma vez, nem para administrador."""
        await guardar(
            conexao, "s", "snmp_v3", Zona.CORPORATIVA, COMUNIDADE,
            atributos={"usuario": "monitor"},
        )
        (item,) = await listar(conexao)
        assert set(item) == {"nome", "tipo", "zona", "atributos", "criada_em", "criada_por"}
        assert item["atributos"] == {"usuario": "monitor"}

    async def test_ciphertext_de_outra_credencial_nao_abre(self, conexao) -> None:
        """O nome é o AAD: copiar o ciphertext de uma linha para outra não
        entrega o segredo. A cifra prende o segredo ao lugar dele."""
        await guardar(conexao, "snmp-ot", "snmp_v2c", Zona.OT_NIVEL3, COMUNIDADE)
        await guardar(conexao, "snmp-corp", "snmp_v2c", Zona.CORPORATIVA, {"comunidade": "x"})
        roubada = (
            await conexao.execute(
                credencial.select().where(credencial.c.nome == "snmp-ot")
            )
        ).first()
        await conexao.execute(
            credencial.update()
            .where(credencial.c.nome == "snmp-corp")
            .values(nonce=roubada.nonce, segredo=roubada.segredo)
        )
        with pytest.raises(SegredoInvalido):
            await abrir(conexao, "snmp-corp", Zona.CORPORATIVA)


class TestZona:
    async def test_coletor_de_outra_zona_e_recusado(self, conexao) -> None:
        """Última linha antes de o segredo virar pacote na rede."""
        await guardar(conexao, "snmp-ot", "snmp_v2c", Zona.OT_NIVEL3, COMUNIDADE)
        with pytest.raises(PermissionError, match="ot_nivel3"):
            await abrir(conexao, "snmp-ot", Zona.CORPORATIVA)


class TestChave:
    async def test_sem_chave_o_cofre_se_recusa(self, conexao, monkeypatch) -> None:
        """Nada de degradar para texto claro: falhar alto na partida é melhor
        que gravar comunidades em claro por dois anos."""
        monkeypatch.delenv(VAR_CHAVE, raising=False)
        with pytest.raises(CofreSemChave, match=VAR_CHAVE):
            await guardar(conexao, "s", "snmp_v2c", Zona.CORPORATIVA, COMUNIDADE)

    async def test_chave_torta_e_recusada_com_o_motivo(self, conexao, monkeypatch) -> None:
        monkeypatch.setenv(VAR_CHAVE, base64.b64encode(b"curta").decode())
        with pytest.raises(CofreSemChave, match="5 bytes"):
            await guardar(conexao, "s", "snmp_v2c", Zona.CORPORATIVA, COMUNIDADE)

    async def test_chave_trocada_nao_abre_o_que_ja_existia(
        self, conexao, monkeypatch
    ) -> None:
        """E diz por quê, em vez de devolver lixo."""
        await guardar(conexao, "s", "snmp_v2c", Zona.CORPORATIVA, COMUNIDADE)
        monkeypatch.setenv(VAR_CHAVE, gerar_chave())
        with pytest.raises(SegredoInvalido, match="chave mudou"):
            await abrir(conexao, "s", Zona.CORPORATIVA)
