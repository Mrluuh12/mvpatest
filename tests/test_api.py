"""Testes da API de leitura.

A regra que estes testes protegem: ausência de dado é resposta, nunca zero e
nunca uma lista vazia que se confunde com "está tudo bem".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from plataforma.api import criar_app
from plataforma.repositorio import Achados, AtivoLido, DispositivoLido, RepositorioMemoria


@pytest.fixture
def cliente() -> TestClient:
    repo = RepositorioMemoria(
        ativos=[
            AtivoLido(
                ativo_id="CA-1042",
                frota="CA",
                numero="1042",
                funcao_negocio="transporte_de_minerio",
                dispositivos=["mac:AA:AA:AA:AA:AA:01", "mac:AA:AA:AA:AA:AA:02"],
            )
        ],
        dispositivos=[
            DispositivoLido(
                chave="mac:AA:AA:AA:AA:AA:01",
                nome="CA-1042-RADIO RJT",
                papel="radio_mesh",
                zona="corporativa",
                ip="10.188.99.42",
                ativo_id="CA-1042",
                identidade="mac",
            ),
            DispositivoLido(
                chave="mac:AA:AA:AA:AA:AA:02",
                nome="CA-1042-CLP",
                papel="plc",
                zona="ot_nivel2",
                ip="10.188.103.42",
                ativo_id="CA-1042",
                identidade="mac",
            ),
        ],
        resumo={"registros_lidos": 2, "ativos": 1, "dispositivos": 2},
        achados=Achados(conflitos=["mac:X -> 'A' e 'B'"]),
    )
    return TestClient(criar_app(repo))


def test_saude_relata_a_propria_plataforma(cliente: TestClient) -> None:
    corpo = cliente.get("/api/v1/saude").json()
    assert corpo["inventario_carregado"] is True
    assert corpo["modulos_registrados"] == 0


def test_ficha_traz_dispositivos_e_sinais(cliente: TestClient) -> None:
    corpo = cliente.get("/api/v1/ativos/CA-1042").json()
    assert corpo["ativo"]["funcao_negocio"] == "transporte_de_minerio"
    assert len(corpo["dispositivos"]) == 2
    assert corpo["sinais"], "a ficha precisa dizer o que ainda não tem coletor"


def test_familia_sem_coletor_diz_o_motivo_em_vez_de_devolver_zero(
    cliente: TestClient,
) -> None:
    sinais = {s["familia"]: s for s in cliente.get("/api/v1/sinais").json()}
    assert sinais["inventario"]["disponivel"] is True
    assert sinais["rf"]["disponivel"] is False
    assert sinais["rf"]["motivo"], "lacuna precisa ter explicação, não silêncio"


def test_ativo_inexistente_devolve_404(cliente: TestClient) -> None:
    assert cliente.get("/api/v1/ativos/NAO-EXISTE").status_code == 404


def test_dispositivos_filtram_por_ativo(cliente: TestClient) -> None:
    assert len(cliente.get("/api/v1/dispositivos?ativo_id=CA-1042").json()) == 2
    assert cliente.get("/api/v1/dispositivos?ativo_id=OUTRO").json() == []


def test_distribuicao_por_zona_expoe_o_recorte_de_ot(cliente: TestClient) -> None:
    assert cliente.get("/api/v1/distribuicao/zona").json() == {
        "corporativa": 1,
        "ot_nivel2": 1,
    }


def test_campo_de_distribuicao_desconhecido_devolve_404(cliente: TestClient) -> None:
    assert cliente.get("/api/v1/distribuicao/inventado").status_code == 404


def test_repositorio_vazio_nao_finge_estar_saudavel() -> None:
    cliente = TestClient(criar_app(RepositorioMemoria.vazio()))
    assert cliente.get("/api/v1/saude").json()["inventario_carregado"] is False
