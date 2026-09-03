"""Testes de origem, identidade, acesso e semeadura."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from inventario.modelo import (
    ZONAS_PROIBIDAS,
    Concessao,
    Natureza,
    Origem,
    Papel,
    PapelUsuario,
    Permissao,
    TipoIdentificador,
    Usuario,
    Valor,
    Zona,
    conciliar,
)
from inventario.semeadura import semear

AGORA = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


class TestPrecedenciaDeOrigem:
    def test_cadastro_humano_vence_derivacao_em_campo_de_intencao(self) -> None:
        """Rodar a semeadura de novo não pode apagar o que alguém corrigiu."""
        humano = Valor(valor="britagem", origem=Origem.CADASTRADO, em=AGORA)
        derivado = Valor(
            valor="transporte_de_minerio",
            origem=Origem.DERIVADO,
            em=AGORA + timedelta(days=30),
        )
        vencedor, _ = conciliar(humano, derivado, Natureza.INTENCAO)
        assert vencedor.valor == "britagem"

    def test_equipamento_vence_cadastro_em_campo_de_observacao(self) -> None:
        """Pessoa digitando firmware é pior evidência do que ler do aparelho."""
        digitado = Valor(valor="5.9.1", origem=Origem.CADASTRADO, em=AGORA)
        lido = Valor(valor="5.9.2.0", origem=Origem.DESCOBERTO, em=AGORA)
        vencedor, divergencia = conciliar(digitado, lido, Natureza.OBSERVACAO)
        assert vencedor.valor == "5.9.2.0"
        assert divergencia, "cadastro x realidade divergindo é achado, não ruído"

    def test_mesma_origem_o_mais_recente_ganha(self) -> None:
        velho = Valor(valor=1, origem=Origem.DESCOBERTO, em=AGORA)
        novo = Valor(valor=2, origem=Origem.DESCOBERTO, em=AGORA + timedelta(minutes=1))
        vencedor, _ = conciliar(velho, novo, Natureza.OBSERVACAO)
        assert vencedor.valor == 2

    def test_valores_iguais_nao_geram_divergencia(self) -> None:
        a = Valor(valor="ME4", origem=Origem.CADASTRADO, em=AGORA)
        b = Valor(valor="ME4", origem=Origem.DESCOBERTO, em=AGORA)
        _, divergencia = conciliar(a, b, Natureza.OBSERVACAO)
        assert not divergencia


class TestControleDeAcesso:
    def test_administrador_tem_tudo(self) -> None:
        adm = Usuario(
            login="adm",
            nome="Admin",
            concessoes=(Concessao(papel=PapelUsuario.ADMINISTRADOR, zonas=frozenset(Zona)),),
        )
        for permissao in Permissao:
            assert adm.pode(permissao, Zona.CORPORATIVA)

    def test_operador_nao_gere_usuarios_nem_credenciais(self) -> None:
        op = Usuario(
            login="op",
            nome="Operador",
            concessoes=(
                Concessao(papel=PapelUsuario.OPERADOR, zonas=frozenset({Zona.CORPORATIVA})),
            ),
        )
        assert op.pode(Permissao.EXECUTAR_ACAO, Zona.CORPORATIVA)
        assert not op.pode(Permissao.GERIR_USUARIOS, Zona.CORPORATIVA)
        assert not op.pode(Permissao.GERIR_CREDENCIAIS, Zona.CORPORATIVA)
        assert not op.pode(Permissao.CADASTRAR_ATIVO, Zona.CORPORATIVA)

    def test_papel_vale_por_zona_nao_pela_plataforma_inteira(self) -> None:
        """A mesma pessoa: operadora na corporativa, apenas leitora em OT."""
        pessoa = Usuario(
            login="ana",
            nome="Ana",
            concessoes=(
                Concessao(papel=PapelUsuario.OPERADOR, zonas=frozenset({Zona.CORPORATIVA})),
                Concessao(papel=PapelUsuario.LEITOR, zonas=frozenset({Zona.OT_NIVEL3})),
            ),
        )
        assert pessoa.pode(Permissao.EXECUTAR_ACAO, Zona.CORPORATIVA)
        assert not pessoa.pode(Permissao.EXECUTAR_ACAO, Zona.OT_NIVEL3)
        assert pessoa.pode(Permissao.VER, Zona.OT_NIVEL3)

    def test_zona_nao_concedida_nega_por_padrao(self) -> None:
        pessoa = Usuario(
            login="bia",
            nome="Bia",
            concessoes=(
                Concessao(papel=PapelUsuario.ENGENHEIRO, zonas=frozenset({Zona.CORPORATIVA})),
            ),
        )
        assert not pessoa.pode(Permissao.VER, Zona.OT_NIVEL3)

    def test_usuario_inativo_nao_pode_nada(self) -> None:
        ex = Usuario(
            login="ex",
            nome="Ex",
            ativo=False,
            concessoes=(
                Concessao(papel=PapelUsuario.ADMINISTRADOR, zonas=frozenset(Zona)),
            ),
        )
        assert not ex.pode(Permissao.VER, Zona.CORPORATIVA)


def _registro(nome: str, ip: str, mac: str | None = None, classe: str = "IT") -> dict:
    return {"Name": nome, "IP": ip, "Mac": mac, "Class": classe, "Asset Id": None}


class TestSemeadura:
    def test_um_caminhao_agrega_seus_dispositivos(self) -> None:
        s = semear(
            [
                _registro("CA-1042-RADIO RJT", "10.188.99.42", "00:11:22:33:44:01"),
                _registro("CA-1042-PTX", "10.188.98.42", "00:11:22:33:44:02"),
                _registro("CA-1042-MEMS", "10.188.101.42", "00:11:22:33:44:03"),
            ]
        )
        assert set(s.ativos) == {"CA-1042"}
        assert len(s.ativos["CA-1042"].dispositivos) == 3
        assert s.relatorio.arestas_criadas == 3
        assert all(a.tipo.value == "embarcado_em" for a in s.arestas)

    def test_mac_e_preferido_como_chave(self) -> None:
        s = semear([_registro("CA-1-PTX", "10.188.98.1", "AA:BB:CC:DD:EE:FF")])
        (dispositivo,) = s.dispositivos.values()
        assert dispositivo.identidade() == (TipoIdentificador.MAC, "AA:BB:CC:DD:EE:FF")
        assert dispositivo.chave.startswith("mac:")

    def test_sem_mac_cai_para_nome_e_isso_e_sinalizado(self) -> None:
        """47% do cadastro real não tem MAC — a fraqueza precisa ficar visível."""
        s = semear([_registro("CA-1-PTX", "10.188.98.1", None)])
        assert s.relatorio.sem_identificador_forte == ["CA-1-PTX"]

    def test_mac_malformado_e_ignorado_e_nao_vira_chave(self) -> None:
        s = semear([_registro("CA-1-PTX", "10.188.98.1", "não-é-mac")])
        (dispositivo,) = s.dispositivos.values()
        assert dispositivo.chave.startswith("nome:")

    def test_ips_duplicados_sao_detectados(self) -> None:
        """26 IPs se repetem no cadastro real; IP sozinho não identifica nada."""
        s = semear(
            [
                _registro("CA-1-PTX", "10.188.98.1", "00:11:22:33:44:01"),
                _registro("CA-2-PTX", "10.188.98.1", "00:11:22:33:44:02"),
            ]
        )
        assert s.relatorio.ips_duplicados == {"10.188.98.1": 2}
        assert s.relatorio.dispositivos_criados == 2, "IP repetido não pode fundir ativos"

    def test_chave_repetida_com_nomes_diferentes_vira_conflito(self) -> None:
        s = semear(
            [
                _registro("CA-1-PTX", "10.188.98.1", "AA:AA:AA:AA:AA:AA"),
                _registro("CA-9-GPS-MM2", "10.188.104.9", "AA:AA:AA:AA:AA:AA"),
            ]
        )
        assert s.relatorio.chaves_em_conflito
        assert s.relatorio.dispositivos_criados == 1

    def test_homonimos_com_ips_distintos_sao_dois_dispositivos(self) -> None:
        """`TT-3503-GPS-MM2` existe em `.101.167` **e** `.102.167` no cadastro real.

        Mesmo nome não é o mesmo aparelho. Fundir os dois é perder um ativo.
        """
        s = semear(
            [
                _registro("TT-3503-GPS-MM2", "10.188.101.167"),
                _registro("TT-3503-GPS-MM2", "10.188.102.167"),
            ]
        )
        assert s.relatorio.dispositivos_criados == 2
        assert s.relatorio.homonimos_desambiguados

    def test_linha_repetida_e_relatada_nunca_descartada_em_silencio(self) -> None:
        s = semear(
            [
                _registro("CA-1-PTX", "10.188.98.1"),
                _registro("CA-1-PTX", "10.188.98.1"),
            ]
        )
        assert s.relatorio.dispositivos_criados == 1
        assert s.relatorio.linhas_duplicadas, "descarte silencioso é proibido"

    def test_nenhum_registro_desaparece_sem_constar_no_relatorio(self) -> None:
        """Teste de conservação: tudo que entra sai criado ou explicado.

        É a rede de segurança contra a pior falha possível aqui — o inventário
        encolher e ninguém notar.
        """
        registros = [
            _registro("CA-1-PTX", "10.188.98.1", "AA:AA:AA:AA:AA:AA"),
            _registro("CA-9-GPS-MM2", "10.188.104.9", "AA:AA:AA:AA:AA:AA"),  # conflito
            _registro("TT-3-GPS-MM2", "10.188.101.3"),
            _registro("TT-3-GPS-MM2", "10.188.102.3"),  # homônimo
            _registro("CA-2-PTX", "10.188.98.2"),
            _registro("CA-2-PTX", "10.188.98.2"),  # duplicata
        ]
        r = semear(registros).relatorio
        explicados = (
            r.dispositivos_criados
            + len(r.chaves_em_conflito)
            + len(r.linhas_duplicadas)
        )
        assert explicados == len(registros)

    def test_clp_cai_em_zona_proibida_a_modulos(self) -> None:
        """Errar para o lado restritivo é barato; para o permissivo, não."""
        s = semear([_registro("TT-3503-CLP", "10.188.103.167", "00:11:22:33:44:05", "OT")])
        (dispositivo,) = s.dispositivos.values()
        assert dispositivo.papel is Papel.PLC
        assert dispositivo.zona in ZONAS_PROIBIDAS

    def test_ativo_ot_da_planilha_fica_em_ot_ate_confirmacao(self) -> None:
        s = semear([_registro("EH-6552-PTX", "10.188.98.237", "00:11:22:33:44:06", "OT")])
        (dispositivo,) = s.dispositivos.values()
        assert dispositivo.zona is Zona.OT_NIVEL3
        assert s.relatorio.zonas_a_confirmar == 1

    def test_funcao_de_negocio_nasce_derivada_nunca_cadastrada(self) -> None:
        s = semear([_registro("CA-1-PTX", "10.188.98.1", "00:11:22:33:44:07")])
        assert s.ativos["CA-1"].funcao_negocio.origem is Origem.DERIVADO

    def test_lista_vazia_nao_explode(self) -> None:
        s = semear([])
        assert s.relatorio.resumo()["dispositivos"] == 0

    @pytest.mark.parametrize("campo", ["Mac", "IP", "Class"])
    def test_campos_ausentes_nao_derrubam_a_semeadura(self, campo: str) -> None:
        registro = _registro("CA-1-PTX", "10.188.98.1", "00:11:22:33:44:08")
        registro[campo] = None
        assert semear([registro]).relatorio.dispositivos_criados == 1
