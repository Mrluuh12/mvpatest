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
    assert corpo["erro_de_recarga"] is None


def test_falha_de_recarga_aparece_em_vez_de_servir_dado_velho_em_silencio(
    cliente: TestClient,
) -> None:
    """Dado velho servido sem aviso é dado errado."""
    corpo = cliente.get("/api/v1/saude").json()
    assert "erro_de_recarga" in corpo, "a falha precisa ser observável"


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


class TestShell:
    """A interface é cliente da API. Estes testes garantem que ela é servida."""

    def test_raiz_entrega_o_shell(self, cliente: TestClient) -> None:
        resposta = cliente.get("/")
        assert resposta.status_code == 200
        assert "AMPS" in resposta.text
        assert "anglo-american.png" in resposta.text, "a marca fica no cabeçalho"

    def test_estaticos_existem(self, cliente: TestClient) -> None:
        for arquivo in ("app.css", "app.js"):
            assert cliente.get(f"/estatico/{arquivo}").status_code == 200

    def test_shell_nao_pede_recurso_inexistente(self, cliente: TestClient) -> None:
        """Console limpo importa numa ferramenta que fica aberta o dia todo."""
        html = cliente.get("/").text
        assert 'rel="icon"' in html, "sem favicon, todo carregamento gera um 404"
        for arquivo in ("anglo-american.png", "anglo-marca.png"):
            assert (
                cliente.get(f"/estatico/{arquivo}").status_code == 200
            ), f"{arquivo} referenciado e ausente daria 404 em toda visita"


class TestArranjosSemBanco:
    """Instalação recém-aberta, ainda sem banco.

    É o estado em que a plataforma passa os primeiros minutos de vida, e é
    onde uma tela que depende de configuração salva quebraria. Aqui ela tem de
    funcionar com os padrões embutidos.
    """

    def test_catalogo_e_a_lista_fechada(self, cliente: TestClient) -> None:
        tipos = {c["tipo"] for c in cliente.get("/api/v1/catalogo").json()}
        assert {"resumo", "telemetria", "identidade", "texto"} <= tipos

    def test_cartao_indisponivel_vem_com_motivo(self, cliente: TestClient) -> None:
        for c in cliente.get("/api/v1/catalogo").json():
            if not c["disponivel"]:
                assert c["motivo"], f"{c['tipo']} apagado sem dizer por quê"

    def test_arranjo_cai_no_padrao_embutido(self, cliente: TestClient) -> None:
        r = cliente.get("/api/v1/arranjo", params={"contexto": "ativo", "chave": "CA-1"})
        assert r.status_code == 200
        assert r.json()["origem"] == "embutido"
        assert r.json()["arranjo"]["cartoes"], "tela sem cartão nenhum"

    def test_contexto_invalido_e_recusado(self, cliente: TestClient) -> None:
        r = cliente.get("/api/v1/arranjo", params={"contexto": "planeta", "chave": "X"})
        assert r.status_code == 422

    def test_salvar_sem_banco_nao_finge_que_salvou(self, cliente: TestClient) -> None:
        """Sem banco não há onde guardar. Responder 200 faria a interface
        mostrar sucesso e perder a edição no primeiro recarregamento."""
        r = cliente.put(
            "/api/v1/arranjos/frota:CA",
            json={"escopo": "frota:CA", "contexto": "ativo",
                  "cartoes": [{"tipo": "resumo"}]},
        )
        assert r.status_code >= 400


class TestExportacaoProm:
    """A rota que o Prometheus raspa nega por omissão, como o resto.

    E não é uma exceção ao porteiro de login: é uma credencial diferente para
    um cliente diferente. Um raspador não tem navegador nem cookie.
    """

    def test_sem_token_configurado_nao_serve_e_ensina_a_ligar(
        self, cliente: TestClient, monkeypatch
    ) -> None:
        monkeypatch.delenv("PLATAFORMA_METRICAS_TOKEN", raising=False)
        r = cliente.get("/metrics")
        assert r.status_code == 503
        detalhe = r.json()["detail"]
        assert "PLATAFORMA_METRICAS_TOKEN" in detalhe
        assert "bearer_token" in detalhe, "a recusa diz o que pôr no prometheus.yml"

    def test_token_errado_e_recusado(self, cliente: TestClient, monkeypatch) -> None:
        monkeypatch.setenv("PLATAFORMA_METRICAS_TOKEN", "certo")
        r = cliente.get("/metrics", headers={"Authorization": "Bearer errado"})
        assert r.status_code == 401

    def test_token_certo_passa_pelo_porteiro_de_login(
        self, cliente: TestClient, monkeypatch
    ) -> None:
        """Chega até a rota — que então recusa por falta de banco, e é outra
        recusa: prova que o cookie não era o que faltava."""
        monkeypatch.setenv("PLATAFORMA_METRICAS_TOKEN", "certo")
        r = cliente.get("/metrics", headers={"Authorization": "Bearer certo"})
        assert r.status_code == 503
        assert "banco" in r.json()["detail"]
