"""Testes do módulo Rajant.

O módulo não coleta rádio — lê o Prometheus que o exportador do usuário já
alimenta. O que pode dar errado, portanto, não é RF: é **junção**. Um rótulo
que não casa com o inventário some, e sumiço calado é o defeito que ninguém
percebe até alguém perguntar por que aquele caminhão nunca tem SNR.

O segundo grupo guarda a distinção que se repete em toda a plataforma:
"perguntei e está ruim" não é "não consegui perguntar". Prometheus fora do ar
não pode virar 149 rádios caídos.
"""

from __future__ import annotations

import json

import httpx
import pytest

from plataforma.dicionario import POR_NOME
from plataforma.modulos.contrato import Qualidade
from plataforma.modulos.rajant import (
    CONSULTAS,
    MANIFESTO,
    ModuloRajant,
    casar,
    normalizar,
)

#: Colisões reais do cadastro: 10.188.99.192 é de dois tratores e
#: PF-4701-RADIO RJT é o nome de dois rádios.
DISPUTADOS = [
    {"chave": "nome:TT-3708", "nome": "TT-3708-RADIO RJT", "ip": "10.188.99.192"},
    {"chave": "nome:TT-3802", "nome": "TT-3802-RADIO RJT", "ip": "10.188.99.192"},
    {"chave": "mac:AA", "nome": "PF-4701-RADIO RJT", "ip": "10.188.99.50"},
    {"chave": "mac:BB", "nome": "PF-4701-RADIO RJT", "ip": "10.188.99.51"},
]

ALVOS = [
    {"chave": "mac:02:D0:12:26:F5:B5", "nome": "CA-1001-RADIO  RJT", "ip": "10.188.99.1"},
    {"chave": "mac:02:D0:12:26:F5:C0", "nome": "CA-1002-RADIO RJT", "ip": "10.188.99.2"},
    {"chave": "nome:ERM-02-RJT", "nome": "ERM-02-RJT", "ip": ""},
]


def serie(bc: str, ip: str, valor: float) -> dict:
    return {"metric": {"bc": bc, "ip": ip}, "value": [1767225600, str(valor)]}


def responder(mapa: dict[str, list[dict]], falhar: set[str] | None = None):
    """Prometheus de mentira: devolve o que o teste mandar, por consulta."""

    def lidar(pedido: httpx.Request) -> httpx.Response:
        consulta = pedido.url.params.get("query", "")
        if falhar and consulta in falhar:
            return httpx.Response(500, text="boom")
        return httpx.Response(
            200,
            content=json.dumps(
                {"status": "success",
                 "data": {"resultType": "vector", "result": mapa.get(consulta, [])}}
            ),
            headers={"content-type": "application/json"},
        )

    return httpx.MockTransport(lidar)


def modulo(mapa, falhar=None) -> ModuloRajant:
    return ModuloRajant("http://prom:9090", transporte=responder(mapa, falhar))


class TestManifesto:
    def test_toda_metrica_esta_no_dicionario(self) -> None:
        """Se falhar, o módulo nem carrega — o contrato recusa antes de rodar."""
        for nome in MANIFESTO.produz_metricas:
            assert nome in POR_NOME

    def test_opera_fora_da_ot(self) -> None:
        """Ele faz uma chamada HTTP ao Prometheus e nunca toca num rádio.
        Quem alcança a OT é o exportador."""
        assert MANIFESTO.zona_permitida == (MANIFESTO.zona_permitida[0],)
        assert MANIFESTO.zona_permitida[0].value == "corporativa"

    def test_nao_declara_escrita(self) -> None:
        assert MANIFESTO.somente_leitura is True

    def test_consulta_agregada_diz_como_agregou(self) -> None:
        """Número que resume N enlaces sem dizer que resume é número que
        alguém vai ler como medida direta."""
        for c in CONSULTAS:
            if " by (" in c.promql:
                assert c.agregacao, f"{c.metrica} agrega e não declara como"


class TestJuncao:
    def test_casa_por_ip(self) -> None:
        j = casar([serie("QUALQUER-NOME", "10.188.99.1", 1)], ALVOS)
        assert j.por_chave == {"QUALQUER-NOME@10.188.99.1": "mac:02:D0:12:26:F5:B5"}
        assert (j.por_ip, j.por_nome) == (1, 0)

    def test_cai_no_nome_quando_o_ip_nao_bate(self) -> None:
        j = casar([serie("ERM-02-RJT", "10.99.99.99", 1)], ALVOS)
        assert j.por_chave == {"ERM-02-RJT@10.99.99.99": "nome:ERM-02-RJT"}
        assert (j.por_ip, j.por_nome) == (0, 1)

    def test_espaco_duplo_no_cadastro_nao_desfaz_a_juncao(self) -> None:
        """O inventário real tem ``CA-1001-RADIO  RJT`` com dois espaços."""
        j = casar([serie("CA-1001-RADIO RJT", "10.0.0.9", 1)], ALVOS)
        assert j.por_chave["CA-1001-RADIO RJT@10.0.0.9"] == "mac:02:D0:12:26:F5:B5"

    def test_ip_disputado_nao_escolhe_um_no_chute(self) -> None:
        """No cadastro real, 10.188.99.192 é de dois tratores. Um dicionário
        por compreensão deixaria o último vencer, e a temperatura de um
        apareceria pendurada no outro. Número errado é pior que ausente."""
        j = casar([serie("QUALQUER", "10.188.99.192", 1)], DISPUTADOS)
        assert j.por_chave == {}
        assert j.ambiguos == {"QUALQUER@10.188.99.192"}
        assert j.sem_inventario == set(), "não é rádio desconhecido, é cadastro duplicado"

    def test_nome_disputado_tambem_recusa(self) -> None:
        """Oito nomes canônicos se repetem entre rádios no cadastro real."""
        j = casar([serie("PF-4701-RADIO RJT", "10.0.0.1", 1)], DISPUTADOS)
        assert j.por_chave == {}
        assert j.ambiguos == {"PF-4701-RADIO RJT@10.0.0.1"}

    def test_o_nome_desempata_o_ip_disputado(self) -> None:
        """IP ambíguo não encerra a busca: se o nome resolve, casa."""
        j = casar([serie("TT-3802-RADIO RJT", "10.188.99.192", 1)], DISPUTADOS)
        assert j.por_chave == {"TT-3802-RADIO RJT@10.188.99.192": "nome:TT-3802"}
        assert j.ambiguos == set()

    def test_o_mesmo_ip_duas_vezes_no_mesmo_equipamento_nao_e_disputa(self) -> None:
        """Duas linhas de identificador para o mesmo dispositivo não são
        conflito — só repetição."""
        repetido = [ALVOS[0], dict(ALVOS[0])]
        j = casar([serie("CA-1001", "10.188.99.1", 1)], repetido)
        assert j.por_chave and not j.ambiguos

    def test_radio_fora_do_inventario_e_contado_nao_engolido(self) -> None:
        """O exportador descobre pela malha e acha rádio que a planilha não
        tem. Isso é achado de inventário — só some se for engolido."""
        j = casar([serie("BC-DESCONHECIDO", "10.50.0.1", 1)], ALVOS)
        assert j.por_chave == {}
        assert j.sem_inventario == {"BC-DESCONHECIDO@10.50.0.1"}
        assert j.ambiguos == set()

    def test_ip_vence_nome_quando_os_dois_existem(self) -> None:
        """O nome vem de Config.General.name, digitado no próprio rádio; o IP
        é o rótulo que o exportador sempre publica."""
        j = casar([serie("CA-1002-RADIO RJT", "10.188.99.1", 1)], ALVOS)
        assert j.por_chave["CA-1002-RADIO RJT@10.188.99.1"] == "mac:02:D0:12:26:F5:B5"

    @pytest.mark.parametrize(
        "entrada,esperado",
        [("CA-1001-RADIO  RJT", "CA-1001-RADIO RJT"), ("  ca-1 x ", "CA-1 X"), ("", "")],
    )
    def test_normalizacao(self, entrada: str, esperado: str) -> None:
        assert normalizar(entrada) == esperado


@pytest.mark.asyncio
class TestColeta:
    async def test_publica_com_a_chave_do_inventario(self) -> None:
        r = await modulo({"rajant_temperatura_c": [serie("CA-1001", "10.188.99.1", 47.5)]}
                         ).coletar(ALVOS)
        (obs,) = [o for o in r.observacoes if o.metrica == "disp_temperatura_c"]
        assert obs.sujeito == "mac:02:D0:12:26:F5:B5"
        assert obs.valor == 47.5

    async def test_agregado_carrega_o_rotulo_e_a_ressalva(self) -> None:
        r = await modulo(
            {"min by (bc, ip) (rajant_peer_snr_db)": [serie("CA-1001", "10.188.99.1", 12)]}
        ).coletar(ALVOS)
        (obs,) = [o for o in r.observacoes if o.metrica == "rf_snr_db"]
        assert obs.rotulos["agregacao"] == "pior_entre_vizinhos"
        assert obs.qualidade is Qualidade.INCERTA

    async def test_serie_direta_nao_finge_ser_agregada(self) -> None:
        r = await modulo({"rajant_online": [serie("CA-1001", "10.188.99.1", 1)]}
                         ).coletar(ALVOS)
        (obs,) = [o for o in r.observacoes if o.metrica == "servico_disponivel"]
        assert "agregacao" not in obs.rotulos
        assert obs.qualidade is Qualidade.BOA

    async def test_nao_disputa_a_disponibilidade_com_o_icmp(self) -> None:
        """Duas fontes gravando a mesma linha de estado dariam
        last-write-wins: o valor mostrado dependeria de qual módulo rodou por
        último. `rajant_online` responde outra pergunta — se a sessão BC API
        abre — e vai para `servico_disponivel`."""
        assert "ativo_alcancavel" not in MANIFESTO.produz_metricas
        r = await modulo({"rajant_online": [serie("CA-1001", "10.188.99.1", 0)]}
                         ).coletar(ALVOS)
        assert [o for o in r.observacoes if o.metrica == "ativo_alcancavel"] == []

    async def test_um_bc_com_varias_interfaces_vira_um_valor_so(self) -> None:
        """Cada rádio de um BreadCrumb tem IPv4 próprio: o mesmo equipamento
        aparece em várias séries. Sem consolidar, o lote é recusado inteiro
        pelo Postgres — 'cannot affect row a second time'."""
        r = await modulo(
            {"min by (bc, ip) (rajant_peer_snr_db)": [
                serie("CA-1001", "10.188.99.1", 31.0),
                {"metric": {"bc": "CA-1001", "ip": "10.188.99.1", "radio": "wlan1"},
                 "value": [1767225600, "9.0"]},
            ]}
        ).coletar(ALVOS)
        (obs,) = [o for o in r.observacoes if o.metrica == "rf_snr_db"]
        assert obs.valor == 9.0, "fica o pior caso: é o enlace que cai primeiro"

    async def test_soma_soma_em_vez_de_pegar_o_pior(self) -> None:
        r = await modulo(
            {"sum by (bc, ip) (rajant_radio_peers_ativos)": [
                serie("CA-1001", "10.188.99.1", 3),
                {"metric": {"bc": "CA-1001", "ip": "10.188.99.1", "radio": "wlan1"},
                 "value": [1767225600, "4"]},
            ]}
        ).coletar(ALVOS)
        (obs,) = [o for o in r.observacoes if o.metrica == "malha_peers_ativos"]
        assert obs.valor == 7, "vizinhos de dois rádios são a conta do equipamento"

    async def test_uma_consulta_quebrada_nao_derruba_as_outras(self) -> None:
        """Métrica ausente num exportador mais antigo é caso normal."""
        r = await modulo(
            {"rajant_online": [serie("CA-1001", "10.188.99.1", 1)]},
            falhar={"rajant_temperatura_c"},
        ).coletar(ALVOS)
        assert any(o.metrica == "servico_disponivel" for o in r.observacoes)
        assert any("rajant_temperatura_c" in x for x in r.rejeitadas)

    async def test_prometheus_fora_do_ar_nao_derruba_o_parque(self) -> None:
        """A distinção que se repete em toda a plataforma: 'não consegui
        perguntar' não é 'perguntei e está ruim'."""
        r = await modulo({}, falhar={c.promql for c in CONSULTAS}).coletar(ALVOS)
        assert r.observacoes == ()
        assert r.alvos_falha == len(ALVOS), "o parque inteiro conta como não lido"
        assert not r.completa

    async def test_radio_sem_serie_conta_como_falha_de_cobertura(self) -> None:
        r = await modulo({"rajant_online": [serie("CA-1001", "10.188.99.1", 1)]}
                         ).coletar(ALVOS)
        assert r.alvos_total == 3
        assert r.alvos_falha == 2, "dois rádios do cadastro não apareceram"

    async def test_nan_nao_vira_zero(self) -> None:
        """O Prometheus diz 'sem dado' com NaN. Zero seria uma leitura."""
        r = await modulo({"rajant_temperatura_c": [serie("CA-1001", "10.188.99.1", float("nan"))]}
                         ).coletar(ALVOS)
        assert [o for o in r.observacoes if o.metrica == "disp_temperatura_c"] == []

    async def test_disputa_de_cadastro_aparece_nas_recusas(self) -> None:
        """Descartar a leitura é certo; descartar calado não é. Quem lê a aba
        de coleta precisa ver qual cadastro corrigir."""
        m = ModuloRajant("http://p:9090",
                         transporte=responder(
                             {"rajant_online": [serie("X", "10.188.99.192", 1)]}))
        r = await m.coletar(DISPUTADOS)
        assert r.observacoes == ()
        assert any("chave disputada" in x and "10.188.99.192" in x for x in r.rejeitadas)

    async def test_a_juncao_fica_disponivel_para_a_tela(self) -> None:
        m = modulo({"rajant_online": [serie("BC-NOVO", "10.50.0.1", 1)]})
        await m.coletar(ALVOS)
        assert m.ultima_juncao.sem_inventario == {"BC-NOVO@10.50.0.1"}
