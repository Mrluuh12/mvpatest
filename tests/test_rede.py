"""Testes da seção de rede.

O que se defende aqui é o que distingue esta seção do resto: o enlace é
**dirigido**, e juntar os dois sentidos numa linha só não pode apagar a
diferença entre eles. E a posição de um equipamento que anda tem prazo de
validade — desenhar o caminhão onde ele estava há três horas é pior que não
desenhar.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import Range

from plataforma import rede
from plataforma.db.esquema import aresta, ativo, dispositivo, estado, leitura
from plataforma.db.grafo import sujeito_do_enlace
from plataforma.db.repositorio_pg import apagar_esquema, criar_engine, criar_esquema

URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma_teste",
)
pytestmark = pytest.mark.asyncio
AGORA = datetime.now(UTC)


@pytest_asyncio.fixture
async def conexao():
    motor = criar_engine(URL)
    try:
        await apagar_esquema(motor)
        await criar_esquema(motor)
    except Exception as erro:  # noqa: BLE001
        await motor.dispose()
        pytest.skip(f"Postgres indisponível: {erro}")
    async with motor.connect() as c:
        yield c
    await apagar_esquema(motor)
    await motor.dispose()


async def _radio(c, chave, ativo_id, frota, *, lat=None, lon=None, idade_s=0):
    ja = (
        await c.execute(select(ativo.c.ativo_id).where(ativo.c.ativo_id == ativo_id))
    ).first()
    if ja is None:
        await c.execute(insert(ativo).values(ativo_id=ativo_id, frota=frota, numero="1"))
    await c.execute(
        insert(dispositivo).values(
            chave=chave, nome_bruto=chave, nome_canonico=chave,
            papel="radio_mesh", zona="corporativa", ativo_id=ativo_id,
        )
    )
    await c.execute(
        insert(estado).values(
            sujeito=chave, alcancavel=True, qualidade="boa", visto_em=AGORA
        )
    )
    if lat is not None:
        em = AGORA - timedelta(seconds=idade_s)
        for metrica, valor in (("geo_latitude", lat), ("geo_longitude", lon)):
            await c.execute(
                insert(leitura).values(
                    sujeito=chave, metrica=metrica, valor=valor, qualidade="boa",
                    rotulos={}, modulo="rajant", em=em,
                )
            )
    await c.commit()


async def _meia(c, origem, destino, **medidas):
    await c.execute(
        insert(aresta).values(
            origem_chave=origem, destino_chave=destino, tipo="peer_mesh",
            validade=Range(AGORA - timedelta(hours=1), None, bounds="[)"),
            atributos={},
        )
    )
    for metrica, valor in medidas.items():
        await c.execute(
            insert(leitura).values(
                sujeito=sujeito_do_enlace(origem, destino), metrica=metrica,
                valor=valor, qualidade="boa", rotulos={}, modulo="rajant", em=AGORA,
            )
        )
    await c.commit()


class TestEnlaceDirigido:
    async def test_os_dois_sentidos_viram_uma_linha_sem_virar_media(
        self, conexao
    ) -> None:
        """O SNR que A vê de B não é o que B vê de A. Fazer a média apagaria a
        assimetria, que é justamente o sintoma de antena torta."""
        await _radio(conexao, "a", "ERB-01", "ERB")
        await _radio(conexao, "b", "ERB-02", "ERB")
        await _meia(conexao, "a", "b", rf_snr_db=30.0, rf_rssi_dbm=-60.0)
        await _meia(conexao, "b", "a", rf_snr_db=18.0, rf_rssi_dbm=-78.0)

        (e,) = await rede.enlaces(conexao)
        assert e["bidirecional"]
        assert {e["snr_ida_db"], e["snr_volta_db"]} == {30.0, 18.0}
        assert e["assimetria_db"] == 12.0

    async def test_o_sinal_do_enlace_e_o_pior_lado(self, conexao) -> None:
        """Publicar o melhor lado faria um enlace ruim parecer bom pela metade
        que funciona."""
        await _radio(conexao, "a", "ERB-01", "ERB")
        await _radio(conexao, "b", "ERB-02", "ERB")
        await _meia(conexao, "a", "b", rf_rssi_dbm=-60.0)
        await _meia(conexao, "b", "a", rf_rssi_dbm=-90.0)

        (e,) = await rede.enlaces(conexao)
        assert e["rssi_pior_dbm"] == -90.0
        assert e["qualidade"] == "ruim"

    async def test_um_sentido_so_nao_e_falha(self, conexao) -> None:
        """Numa malha, o outro rádio pode ainda não ter relatado este vizinho."""
        await _radio(conexao, "a", "ERB-01", "ERB")
        await _radio(conexao, "b", "CA-1", "CA")
        await _meia(conexao, "a", "b", rf_snr_db=25.0)

        (e,) = await rede.enlaces(conexao)
        assert e["bidirecional"] is False
        assert e["assimetria_db"] is None, "sem os dois lados não há o que comparar"


class TestClasse:
    async def test_fixo_com_fixo_e_espinha_dorsal(self, conexao) -> None:
        assert rede.classe_do_enlace("fixo", "fixo") == "espinha"
        assert rede.classe_do_enlace("fixo", "movel") == "lavra"
        assert rede.classe_do_enlace("fixo", "semifixo") == "distribuicao"

    async def test_a_frota_decide_o_que_e_fixo(self, conexao) -> None:
        assert rede.classe_da_frota("ERB") == "fixo"
        assert rede.classe_da_frota("GST") == "fixo"
        assert rede.classe_da_frota("ERM") == "semifixo"
        assert rede.classe_da_frota("CA") == "movel"


class TestFaixas:
    @pytest.mark.parametrize(
        ("rssi", "esperado"),
        [(-50.0, "excelente"), (-70.0, "bom"), (-80.0, "regular"),
         (-90.0, "ruim"), (-110.0, "muito ruim")],
    )
    async def test_faixa_de_sinal(self, rssi: float, esperado: str) -> None:
        assert rede.faixa_rssi(rssi)[0] == esperado

    async def test_sem_medida_nao_e_ruim(self) -> None:
        """Não medir e medir mal são coisas diferentes, e pintar as duas de
        vermelho manda gente subir na torre à toa."""
        rotulo, classe = rede.faixa_rssi(None)
        assert rotulo == "sem medida"
        assert classe == "nd"


class TestMapa:
    async def test_a_posicao_vencida_e_marcada(self, conexao) -> None:
        """Um caminhão a 30 km/h anda 5 km em dez minutos. Desenhá-lo na
        posição de três horas atrás é afirmar onde ele não está."""
        await _radio(conexao, "novo", "CA-1", "CA", lat=-19.50, lon=-43.40, idade_s=30)
        await _radio(conexao, "velho", "CA-2", "CA", lat=-19.51, lon=-43.41,
                     idade_s=4 * 3600)

        m = await rede.mapa(conexao)
        por_chave = {n["chave"]: n for n in m["nos"]}
        assert por_chave["novo"]["posicao_vencida"] is False
        assert por_chave["velho"]["posicao_vencida"] is True
        assert m["posicoes_vencidas"] == 1

    async def test_radio_sem_gps_fica_de_fora_e_e_contado(self, conexao) -> None:
        await _radio(conexao, "com", "CA-1", "CA", lat=-19.5, lon=-43.4)
        await _radio(conexao, "sem", "CA-2", "CA")

        m = await rede.mapa(conexao)
        assert [n["chave"] for n in m["nos"]] == ["com"]
        assert m["sem_gps"] == 1

    async def test_a_longitude_encolhe_com_a_latitude(self, conexao) -> None:
        """Sem corrigir pelo cosseno da latitude o mapa fica esticado no eixo X
        e a distância entre rádios sai errada — a 19° S, em 21%."""
        await _radio(conexao, "o", "CA-1", "CA", lat=-19.5, lon=-43.5)
        await _radio(conexao, "l", "CA-2", "CA", lat=-19.5, lon=-43.4)
        await _radio(conexao, "n", "CA-3", "CA", lat=-19.4, lon=-43.5)

        m = await rede.mapa(conexao)
        # Mesmo delta em graus: o vão em metros tem de ser menor na longitude.
        assert m["largura_m"] < m["altura_m"]
        assert 0.93 < m["largura_m"] / m["altura_m"] < 0.95


class TestResumo:
    async def test_conta_o_vizinho_que_o_cadastro_nao_conhece(self, conexao) -> None:
        """O rádio relata o vizinho pela identidade que vê; quando ela não
        resolve, o enlace existe e o outro lado é um MAC solto. É achado de
        cadastro, e some da conta se ninguém contar."""
        await _radio(conexao, "a", "ERB-01", "ERB")
        await _meia(conexao, "a", "mac:FF:FF:FF:FF:FF:FF", rf_rssi_dbm=-70.0)

        r = await rede.resumo(conexao)
        assert r["enlaces_vizinho_fora_do_cadastro"] == 1

    async def test_banco_vazio_nao_estoura(self, conexao) -> None:
        r = await rede.resumo(conexao)
        assert r["radios_total"] == 0
        assert r["rssi_mediano_dbm"] is None, "mediana de nada é ausência, não zero"
