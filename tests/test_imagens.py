"""Testes das imagens.

Duas propriedades importam aqui. A primeira é a **cascata**: subir uma foto por
papel cobre todos os dispositivos daquele papel, e é isso que torna o recurso
usável — a alternativa seria subir 708 arquivos.

A segunda é que nada vindo do envio chega ao sistema de arquivos: o nome é
gerado por nós e o tipo servido é o que **nós** registramos.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from plataforma.db.imagens import (
    TIPOS,
    ImagemRecusada,
    buscar,
    guardar,
    mapa,
    remover,
    sujeitos_possiveis,
    validar,
)
from plataforma.db.repositorio_pg import apagar_esquema, criar_engine, criar_esquema

URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma_teste",
)

# PNG de 1×1 pixel, suficiente para exercitar o caminho inteiro.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001od0a2db40000000049454e44ae426082"
    .replace("od", "60")
)



@pytest_asyncio.fixture
async def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATAFORMA_IMAGENS", str(tmp_path))
    motor = criar_engine(URL)
    try:
        await apagar_esquema(motor)
        await criar_esquema(motor)
    except Exception as erro:  # noqa: BLE001
        await motor.dispose()
        pytest.skip(f"Postgres indisponível: {erro}")
    yield motor
    await apagar_esquema(motor)
    await motor.dispose()


class TestValidacao:
    @pytest.mark.parametrize("tipo", sorted(TIPOS))
    def test_tipos_aceitos(self, tipo: str) -> None:
        assert validar(b"conteudo", tipo).startswith(".")

    def test_tipo_recusado_diz_quais_servem(self) -> None:
        """Recusa que não ensina a corrigir custa uma tarde de alguém."""
        with pytest.raises(ImagemRecusada, match="image/png"):
            validar(b"x", "application/pdf")

    def test_arquivo_vazio_e_recusado(self) -> None:
        with pytest.raises(ImagemRecusada, match="vazio"):
            validar(b"", "image/png")

    def test_arquivo_grande_demais_e_recusado_com_o_limite(self) -> None:
        with pytest.raises(ImagemRecusada, match="limite"):
            validar(b"x" * (5 * 1024 * 1024), "image/png")


class TestCascata:
    def test_dispositivo_procura_do_especifico_para_o_geral(self) -> None:
        assert sujeitos_possiveis("dispositivo", "mac:AA", "radio_mesh") == [
            "disp:mac:AA",
            "papel:radio_mesh",
        ]

    def test_ativo_procura_maquina_depois_frota(self) -> None:
        assert sujeitos_possiveis("ativo", "CA-1001", "CA") == ["ativo:CA-1001", "frota:CA"]


@pytest.mark.asyncio
class TestGravacao:
    async def test_guarda_e_encontra(self, engine, tmp_path) -> None:
        async with engine.begin() as c:
            gravada = await guardar(c, "papel:radio_mesh", PNG, "image/png")
        assert (tmp_path / gravada.arquivo).is_file()

        async with engine.connect() as c:
            achado = await buscar(c, gravada.arquivo)
        assert achado is not None
        _caminho, tipo = achado
        assert tipo == "image/png"

    async def test_nome_do_arquivo_vem_do_conteudo_nao_do_envio(self, engine) -> None:
        """Nada que veio no pedido chega ao sistema de arquivos."""
        async with engine.begin() as c:
            a = await guardar(c, "papel:gps", PNG, "image/png")
            b = await guardar(c, "papel:plc", PNG, "image/png")
        assert a.arquivo == b.arquivo, "conteúdo igual reaproveita o arquivo"
        assert a.arquivo.endswith(".png")
        assert "/" not in a.arquivo and ".." not in a.arquivo

    async def test_arquivo_nao_registrado_nao_e_servido(self, engine) -> None:
        """É a consulta ao banco que fecha a porta para travessia de caminho."""
        async with engine.connect() as c:
            assert await buscar(c, "../../etc/passwd") is None
            assert await buscar(c, "inexistente.png") is None

    async def test_novo_envio_substitui_a_associacao(self, engine) -> None:
        async with engine.begin() as c:
            await guardar(c, "papel:gps", PNG, "image/png")
            await guardar(c, "papel:gps", PNG + b"\x00", "image/png")
        async with engine.connect() as c:
            registrado = await mapa(c)
        assert len(registrado) == 1, "um sujeito tem uma imagem, não uma pilha"

    async def test_remover_desassocia(self, engine) -> None:
        async with engine.begin() as c:
            await guardar(c, "papel:gps", PNG, "image/png")
        async with engine.begin() as c:
            assert await remover(c, "papel:gps") is True
        async with engine.begin() as c:
            assert await remover(c, "papel:gps") is False
        async with engine.connect() as c:
            assert await mapa(c) == {}
