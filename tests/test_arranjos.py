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
    TipoOpcao,
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


class TestOpcoes:
    """As opções são **tipadas** para a tela saber desenhar o controle certo.

    Sem o tipo, a opção seria só um nome numa lista e a interface teria de
    conhecer cada cartão por dentro — o acoplamento que o catálogo existe para
    evitar. Com ele, acrescentar um cartão com opções não exige tocar em
    JavaScript nenhum, e é isso que estes testes guardam.
    """

    #: Os tipos que `controleDeOpcao` em app.js sabe desenhar.
    DESENHAVEIS = {"metrica", "janela", "inteiro", "texto", "escolha"}

    @pytest.mark.parametrize("definicao", CATALOGO, ids=lambda d: d.tipo.value)
    def test_todo_tipo_de_opcao_tem_controle_na_tela(self, definicao) -> None:
        for o in definicao.opcoes:
            assert o.tipo.value in self.DESENHAVEIS, (
                f"{definicao.tipo.value}.{o.nome} usa {o.tipo.value!r}, que a "
                f"tela não sabe desenhar"
            )

    @pytest.mark.parametrize("definicao", CATALOGO, ids=lambda d: d.tipo.value)
    def test_toda_opcao_tem_rotulo_de_gente(self, definicao) -> None:
        """`severidade_minima` num formulário é nome de coluna, não pergunta."""
        for o in definicao.opcoes:
            assert o.rotulo and o.rotulo != o.nome

    @pytest.mark.parametrize("definicao", CATALOGO, ids=lambda d: d.tipo.value)
    def test_escolha_traz_as_escolhas(self, definicao) -> None:
        for o in definicao.opcoes:
            if o.tipo is TipoOpcao.ESCOLHA:
                assert o.escolhas, f"{o.nome} é escolha e não oferece nenhuma"
            else:
                assert not o.escolhas

    def test_o_grafico_abre_numa_metrica_que_tem_serie(self) -> None:
        """Abrir num cartão que diz "sem série" seria estrear com um buraco."""
        from plataforma.series import tem_serie

        (metrica,) = [
            o for o in POR_TIPO[TipoCartao.GRAFICO].opcoes if o.nome == "metrica"
        ]
        assert tem_serie(str(metrica.padrao))

    def test_opcao_do_padrao_embutido_existe_no_catalogo(self) -> None:
        """Um arranjo padrão que preencha opção inexistente é configuração que
        nunca vai a lugar nenhum."""
        for padrao in (ARRANJO_ATIVO_PADRAO, ARRANJO_DISPOSITIVO_PADRAO):
            for c in padrao.cartoes:
                validas = {o.nome for o in POR_TIPO[c.tipo].opcoes}
                assert set(c.opcoes) <= validas, f"{c.tipo.value}: {set(c.opcoes) - validas}"


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

    @pytest.mark.parametrize(
        "padrao", [ARRANJO_ATIVO_PADRAO, ARRANJO_DISPOSITIVO_PADRAO],
        ids=["ativo", "dispositivo"],
    )
    def test_o_padrao_nao_carrega_cartao_indisponivel(self, padrao) -> None:
        """Cartão que o catálogo recusa acrescentar não pode vir de fábrica.

        Era o caso das Ações Rápidas: quatro botões desativados em toda tela de
        ativo, que ninguém conseguia repor porque o catálogo os marca como
        indisponíveis. Peso morto que só some quando o marco M4 chegar.
        """
        assert padrao.indisponiveis() == []


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
