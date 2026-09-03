"""Testes dos arranjos de tela.

O que precisa ser verdade aqui não é "a tela desenha", e sim que o **catálogo
seja um contrato**. Ele é a lista fechada de cartões que existem; se o servidor
oferece um tipo que a interface não sabe desenhar, quem personaliza a tela
escolhe um cartão e recebe um buraco. Esse é o teste que ninguém escreve e que
custa caro depois — por isso ele está aqui, lendo o JavaScript de verdade.

O resto guarda a cascata (o mecanismo que faz arrumar uma tela valer para 299
máquinas) e a recusa de cartão fora de contexto.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from plataforma.arranjos import (
    ARRANJO_ATIVO_PADRAO,
    ARRANJO_DISPOSITIVO_PADRAO,
    CATALOGO,
    POR_TIPO,
    Arranjo,
    Cartao,
    Contexto,
    TipoCartao,
    cascata,
)

APP_JS = Path(__file__).resolve().parents[1] / "src/plataforma/web/app.js"


class TestCatalogo:
    def test_todo_tipo_tem_definicao(self) -> None:
        """Um tipo sem entrada no catálogo é um cartão que ninguém consegue
        acrescentar pela tela — e que ainda assim pode ser gravado pela API."""
        assert set(POR_TIPO) == set(TipoCartao)

    def test_nao_ha_definicao_repetida(self) -> None:
        assert len({d.tipo for d in CATALOGO}) == len(CATALOGO)

    @pytest.mark.parametrize("definicao", CATALOGO, ids=lambda d: d.tipo.value)
    def test_definicao_e_utilizavel(self, definicao) -> None:
        assert definicao.titulo_padrao.strip()
        assert definicao.descricao.strip()
        assert definicao.contextos, "cartão que não serve em lugar nenhum"

    @pytest.mark.parametrize("definicao", CATALOGO, ids=lambda d: d.tipo.value)
    def test_indisponivel_explica_por_que(self, definicao) -> None:
        """Botão apagado sem motivo vira dúvida; com motivo, vira expectativa."""
        if not definicao.disponivel:
            assert definicao.motivo.strip()

    @pytest.mark.parametrize("definicao", CATALOGO, ids=lambda d: d.tipo.value)
    def test_a_interface_sabe_desenhar(self, definicao) -> None:
        """O contrato entre catálogo e tela.

        Se este teste quebra, alguém acrescentou um tipo no servidor sem o
        desenho correspondente — e a tela do usuário mostraria "tipo de cartão
        desconhecido" no lugar do conteúdo.
        """
        registro = re.search(r"const CARTOES = \{(.+?)\n  \};", APP_JS.read_text("utf-8"), re.S)
        assert registro, "registro CARTOES não encontrado em app.js"
        assert re.search(rf"^\s*{definicao.tipo.value}:", registro.group(1), re.M), (
            f"o cartão {definicao.tipo.value!r} existe no catálogo mas app.js "
            f"não sabe desenhá-lo"
        )


class TestContexto:
    def test_recusa_cartao_fora_de_contexto(self) -> None:
        """Componentes numa ficha de dispositivo daria um cartão sempre vazio:
        um rádio não tem dispositivos embarcados."""
        errado = Arranjo(
            escopo="papel:radio_mesh",
            contexto=Contexto.DISPOSITIVO,
            cartoes=(Cartao(tipo=TipoCartao.COMPONENTES),),
        )
        with pytest.raises(ValueError, match="não serve no contexto"):
            errado.validar_contexto()

    def test_a_recusa_diz_onde_o_cartao_serve(self) -> None:
        errado = Arranjo(
            escopo="disp:X", contexto=Contexto.DISPOSITIVO,
            cartoes=(Cartao(tipo=TipoCartao.ALCANCE),),
        )
        with pytest.raises(ValueError, match="ativo"):
            errado.validar_contexto()

    @pytest.mark.parametrize(
        "padrao", [ARRANJO_ATIVO_PADRAO, ARRANJO_DISPOSITIVO_PADRAO],
        ids=["ativo", "dispositivo"],
    )
    def test_os_padroes_embutidos_sao_validos(self, padrao) -> None:
        padrao.validar_contexto()


class TestCartao:
    def test_titulo_cai_no_catalogo(self) -> None:
        assert Cartao(tipo=TipoCartao.ALCANCE).titulo_efetivo() == "Alcance"

    def test_titulo_proprio_vence(self) -> None:
        c = Cartao(tipo=TipoCartao.ALCANCE, titulo="Cobertura de rádio")
        assert c.titulo_efetivo() == "Cobertura de rádio"

    @pytest.mark.parametrize("largura", [0, 5, -1])
    def test_largura_fora_da_grade_e_recusada(self, largura: int) -> None:
        """A grade tem quatro colunas; aceitar 7 produziria um cartão que
        transborda em silêncio."""
        with pytest.raises(ValidationError):
            Cartao(tipo=TipoCartao.RESUMO, largura=largura)

    def test_arranjo_vazio_e_recusado(self) -> None:
        with pytest.raises(ValidationError, match="ao menos um cartão"):
            Arranjo(escopo="ativo:X", contexto=Contexto.ATIVO, cartoes=())


class TestCascata:
    def test_do_especifico_ao_geral(self) -> None:
        assert cascata(Contexto.ATIVO, "CA-1001", "CA") == [
            "ativo:CA-1001", "frota:CA", "padrao_ativo",
        ]

    def test_dispositivo_usa_papel_como_grupo(self) -> None:
        assert cascata(Contexto.DISPOSITIVO, "CA-1001-RJT", "radio_mesh") == [
            "disp:CA-1001-RJT", "papel:radio_mesh", "padrao_dispositivo",
        ]

    def test_sem_grupo_a_cascata_encurta(self) -> None:
        """Ativo sem frota não deve inventar um escopo ``frota:None`` — que
        casaria com qualquer outro órfão."""
        assert cascata(Contexto.ATIVO, "X", None) == ["ativo:X", "padrao_ativo"]

    def test_o_padrao_e_sempre_o_ultimo(self) -> None:
        for contexto, grupo in [(Contexto.ATIVO, "CA"), (Contexto.DISPOSITIVO, "gps")]:
            assert cascata(contexto, "K", grupo)[-1].startswith("padrao_")
