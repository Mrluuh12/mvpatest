"""Testes de normalização e derivação.

Escritos para achar bug, não para passar: cada caso aqui reproduz uma
inconsistência que existe de verdade nos 723 ativos do cadastro.
"""

from __future__ import annotations

import pytest

from inventario.derivacao import derivar
from inventario.dialetos import papel_do_dialeto
from inventario.modelo import Papel


class TestDialetos:
    @pytest.mark.parametrize("dialeto", ["CLP", "PLC", "clp", " plc "])
    def test_clp_e_plc_sao_a_mesma_coisa(self, dialeto: str) -> None:
        """O dialeto mais caro do cadastro: 15 CLP e 11 PLC, um só equipamento."""
        papel, reconhecido = papel_do_dialeto(dialeto)
        assert reconhecido
        assert papel is Papel.PLC

    @pytest.mark.parametrize(
        "dialeto", ["RADIO RJT", "RADIO", "RADIO-RAJANT", "RADIO  RJT", "RAJANT"]
    )
    def test_as_cinco_grafias_de_rajant_colapsam(self, dialeto: str) -> None:
        papel, reconhecido = papel_do_dialeto(dialeto)
        assert reconhecido
        assert papel is Papel.RADIO_MESH

    @pytest.mark.parametrize(
        ("dialeto", "esperado"),
        [
            ("GPS-MM2", Papel.GPS),
            ("MM2-SLAVE", Papel.GPS),
            ("IMX2", Papel.ENDPOINT_IMX),
            ("CAMERA-01", Papel.CAMERA),
            ("SW IE4000", Papel.SWITCH),
            ("SWITCH IE4000", Papel.SWITCH),
            ("ETH-CAN-CONVERTER", Papel.CONVERSOR_CAN),
            ("MEMS", Papel.GATEWAY_PNEU),
            ("PTX-HUB", Papel.HUB_PTX),
            ("PTX", Papel.IHM_BORDO),
        ],
    )
    def test_sufixos_e_variantes(self, dialeto: str, esperado: Papel) -> None:
        papel, reconhecido = papel_do_dialeto(dialeto)
        assert reconhecido
        assert papel is esperado

    def test_ptx_hub_nao_e_confundido_com_ptx(self) -> None:
        """Os dois começam igual e são coisas diferentes — 33 contra 97 ativos."""
        assert papel_do_dialeto("PTX-HUB")[0] is Papel.HUB_PTX
        assert papel_do_dialeto("PTX")[0] is Papel.IHM_BORDO

    def test_desconhecido_e_sinalizado_nao_silenciado(self) -> None:
        papel, reconhecido = papel_do_dialeto("TRAQUITANA-9000")
        assert papel is Papel.DESCONHECIDO
        assert reconhecido is False


class TestDerivacao:
    def test_nome_no_padrao_gera_ativo_e_papel(self) -> None:
        d = derivar("CA-1001-RADIO RJT", "10.188.99.1")
        assert d.aderente_ao_padrao
        assert d.frota == "CA"
        assert d.numero == "1001"
        assert d.ativo_id == "CA-1001"
        assert d.papel is Papel.RADIO_MESH
        assert d.funcao_negocio == "transporte_de_minerio"
        assert not d.divergencias

    def test_dispositivos_do_mesmo_veiculo_convergem_para_um_ativo(self) -> None:
        """O quarto octeto é a identidade do veículo — oito equipamentos, um caminhão."""
        nomes_ips = [
            ("CA-1001-RADIO RJT", "10.188.99.1"),
            ("CA-1001-PTX", "10.188.98.1"),
            ("CA-1001-MEMS", "10.188.101.1"),
            ("CA-1001-CLP", "10.188.103.1"),
            ("CA-1001-IMX", "10.188.107.1"),
        ]
        assert {derivar(n, ip).ativo_id for n, ip in nomes_ips} == {"CA-1001"}

    def test_divergencia_entre_nome_e_endereco_e_relatada(self) -> None:
        """Nome diz CLP, endereço é sub-rede de rádio: erro de cadastro provável.

        O comportamento correto não é escolher em silêncio — é registrar.
        """
        d = derivar("CA-1001-CLP", "10.188.99.1")
        assert d.divergencias, "divergência precisa ser relatada"
        assert d.papel is Papel.PLC, "o nome continua sendo a fonte primária"
        assert d.fonte_papel == "nome"

    def test_endereco_cobre_lacuna_quando_o_nome_nao_diz_nada(self) -> None:
        d = derivar("CA-1001-COISA-ESTRANHA", "10.188.103.1")
        assert d.papel is Papel.PLC
        assert d.fonte_papel == "sub-rede"
        assert d.avisos

    def test_subrede_ambigua_nao_e_usada_como_autoridade(self) -> None:
        """`.101` mistura MEMS e GPS no cadastro real: mapa que erra 30% é pior que nenhum."""
        d = derivar("CA-1001-TRECO", "10.188.101.1")
        assert d.papel is Papel.DESCONHECIDO

    def test_nome_fora_do_padrao_e_relatado(self) -> None:
        d = derivar("MTAFBRCMDW094", "10.188.120.3")
        assert not d.aderente_ao_padrao
        assert d.ativo_id is None
        assert d.avisos

    def test_erb_usa_dois_digitos(self) -> None:
        d = derivar("ERB-02-BASE ASTRA", "10.188.96.40")
        assert d.ativo_id == "ERB-02"
        assert d.papel is Papel.RADIO_PTP
        assert d.funcao_negocio == "rede_infraestrutura"

    def test_ip_invalido_nao_derruba_a_derivacao(self) -> None:
        for ip in [None, "", "sem-ip", "10.188.99", "10.188.x.1"]:
            d = derivar("CA-1001-RADIO RJT", ip)
            assert d.papel is Papel.RADIO_MESH

    def test_registro_sem_nome_nao_explode(self) -> None:
        d = derivar("", "10.188.99.1")
        assert d.papel is Papel.DESCONHECIDO
        assert d.avisos
