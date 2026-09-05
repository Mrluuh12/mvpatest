"""Testes das sondas de diagnóstico.

Sonda é leitura, mas leitura **dirigida a um alvo por uma pessoa**, e é isso
que muda o que precisa ser guardado. Não é a aritmética de um ping: são as
recusas que vêm antes da rede, e a recusa que explica em vez de deixar dar
timeout.

Também está guardado aqui o que a plataforma **não** faz. Varredura de faixa
de portas não é diagnóstico, é reconhecimento — e num equipamento de campo já
derrubou muita coisa. O teste existe para que a linha não se mova sem alguém
apagar um teste e ter de justificar.
"""

from __future__ import annotations

import asyncio

import pytest

from inventario.modelo import MATRIZ_PAPEIS, PapelUsuario, Permissao, Zona
from plataforma.diagnostico import (
    ManifestoSonda,
    Perigo,
    Registro,
    Resultado,
    SondaCaminho,
    SondaPing,
    SondaPorta,
    SondaSnmp,
    executar,
)

pytestmark = pytest.mark.asyncio


class TestPermissao:
    async def test_diagnosticar_e_separado_de_executar_acao(self) -> None:
        """Quem pode perguntar "este endereço responde?" não deveria por isso
        poder reiniciar o rádio de um caminhão em operação."""
        assert Permissao.DIAGNOSTICAR is not Permissao.EXECUTAR_ACAO
        leitor = MATRIZ_PAPEIS[PapelUsuario.LEITOR]
        assert Permissao.DIAGNOSTICAR not in leitor

    async def test_quem_esta_em_campo_diagnostica(self) -> None:
        assert Permissao.DIAGNOSTICAR in MATRIZ_PAPEIS[PapelUsuario.CAMPO]


class TestManifesto:
    async def test_nenhuma_sonda_declara_zona_proibida(self) -> None:
        r = Registro()
        r.carregar_padrao()
        for s in r.sondas.values():
            assert not set(s.manifesto.zona_permitida) & {
                Zona.OT_NIVEL0, Zona.OT_NIVEL1, Zona.OT_NIVEL2
            }

    async def test_zona_proibida_e_recusada_no_manifesto(self) -> None:
        with pytest.raises(ValueError, match="impossibilidade"):
            ManifestoSonda(
                nome="x", rotulo="X", descricao="",
                zona_permitida=(Zona.OT_NIVEL2,),
            )

    async def test_toda_sonda_tem_limite_de_tempo(self) -> None:
        """Diagnóstico que não volta não é diagnóstico: é uma aba pendurada."""
        r = Registro()
        r.carregar_padrao()
        for s in r.sondas.values():
            assert 0 < s.manifesto.limite_s <= 60

    async def test_todas_as_sondas_de_hoje_sao_leitura(self) -> None:
        """As que geram carga ou podem derrubar o alvo ainda não existem — e
        quando existirem, o grau tem de aparecer antes do botão."""
        r = Registro()
        r.carregar_padrao()
        assert {s.manifesto.perigo for s in r.sondas.values()} == {Perigo.LEITURA}


class TestRecusasAntesDaRede:
    async def _registro(self) -> Registro:
        r = Registro(zona=Zona.CORPORATIVA)
        r.carregar_padrao()
        return r

    async def test_alvo_em_zona_proibida(self) -> None:
        r = await self._registro()
        res = await executar(r, "ping", "10.0.0.1", Zona.OT_NIVEL2, {})
        assert res.ok is False
        assert "impossibilidade" in " ".join(res.linhas)

    async def test_alvo_em_outra_zona_recusa_explicando(self) -> None:
        """Um erro dizendo "preciso de um agente naquela zona" resolve; 30 s de
        timeout mandam a pessoa procurar defeito no lugar errado."""
        r = await self._registro()
        res = await executar(r, "ping", "10.0.0.1", Zona.OT_NIVEL3, {})
        assert res.ok is False
        assert "agente" in " ".join(res.linhas)

    async def test_sonda_inexistente_e_erro_de_quem_chamou(self) -> None:
        r = await self._registro()
        with pytest.raises(KeyError):
            await executar(r, "inventada", "10.0.0.1", Zona.CORPORATIVA, {})

    async def test_sonda_que_trava_e_abortada(self) -> None:
        class Travada:
            manifesto = ManifestoSonda(
                nome="travada", rotulo="T", descricao="", limite_s=0.05
            )

            async def executar(self, alvo, parametros):
                await asyncio.sleep(5)
                return Resultado(ok=True, resumo="nunca")

        r = Registro(zona=Zona.CORPORATIVA)
        r.registrar(Travada())
        res = await executar(r, "travada", "10.0.0.1", Zona.CORPORATIVA, {})
        assert res.ok is False
        assert "abortada" in res.resumo


class TestSondaPorta:
    async def test_a_porta_e_obrigatoria(self) -> None:
        """Diagnóstico é confirmar que a porta que deveria estar aberta está.
        Varrer faixa é reconhecimento, e num CLP ou rádio de campo já derrubou
        equipamento. Não há modo "todas", e não é esquecimento."""
        res = await SondaPorta().executar("127.0.0.1", {})
        assert res.ok is False
        assert "não varre faixa" in " ".join(res.linhas)

    @pytest.mark.parametrize("porta", [0, -1, 70000, "abc"])
    async def test_porta_invalida_e_recusada(self, porta) -> None:
        res = await SondaPorta().executar("127.0.0.1", {"porta": porta})
        assert res.ok is False

    async def test_recusa_e_resposta_e_isso_e_meio_diagnostico(self) -> None:
        """Conexão recusada quer dizer que o host está de pé e a porta
        fechada. Muito diferente de silêncio — e a distinção é o que separa
        "equipamento caiu" de "serviço parado"."""
        res = await SondaPorta().executar("127.0.0.1", {"porta": 9})
        assert res.ok is False
        assert "recusada" in " ".join(res.linhas)
        assert "host respondeu" in " ".join(res.linhas)


class TestSondaPing:
    async def test_responde_sobre_a_propria_maquina(self) -> None:
        res = await SondaPing().executar("127.0.0.1", {"tentativas": 3})
        assert res.ok is True
        assert res.dados["perda_pct"] == 0.0

    async def test_uma_resposta_so_nao_afirma_estabilidade(self) -> None:
        """Jitter com uma amostra seria zero, e zero aqui é afirmação de
        estabilidade que não foi observada."""
        res = await SondaPing().executar("127.0.0.1", {"tentativas": 1})
        assert res.dados["jitter_ms"] is None
        assert any("uma resposta só" in x for x in res.linhas)


class TestSondaCaminho:
    async def test_chega_em_si_mesmo_num_salto(self) -> None:
        res = await SondaCaminho().executar("127.0.0.1", {"saltos_max": 3})
        assert res.ok is True
        assert res.dados["saltos"][0]["ip"] == "127.0.0.1"

    async def test_salto_mudo_e_explicado_como_normal(self, monkeypatch) -> None:
        """Um `*` no meio parece falha e manda alguém caçar defeito onde não
        há: muita coisa simplesmente não responde a TTL expirado.

        A lista de saltos é injetada porque depender de um endereço ser
        inalcançável faz um teste que passa ou falha conforme a rede de quem
        roda — e teste instável acaba desligado.
        """
        from plataforma.diagnostico import Salto

        sonda = SondaCaminho()
        monkeypatch.setattr(
            sonda, "_percorrer",
            lambda alvo, maximo: [
                Salto(1, "10.0.0.1", 1.2), Salto(2), Salto(3, "10.0.0.9", 8.4)
            ],
        )
        res = await sonda.executar("10.0.0.9", {"saltos_max": 5})
        assert res.ok is True
        assert " 2  *" in res.linhas
        assert any("nem todo equipamento responde" in x for x in res.linhas)

    async def test_nao_chegar_ao_alvo_e_dito(self, monkeypatch) -> None:
        from plataforma.diagnostico import Salto

        sonda = SondaCaminho()
        monkeypatch.setattr(sonda, "_percorrer", lambda a, m: [Salto(1, "10.0.0.1", 1.0)])
        res = await sonda.executar("10.9.9.9", {"saltos_max": 1})
        assert res.ok is False
        assert "não chegou" in res.resumo


class TestSondaSnmp:
    async def test_sem_credencial_ensina_a_cadastrar(self) -> None:
        """Recusa que não ensina a corrigir custa uma tarde de alguém."""
        res = await SondaSnmp(sessao=None).executar("10.0.0.1", {})
        assert res.ok is False
        assert "criar-credencial" in " ".join(res.linhas)

    async def test_separa_problema_de_rede_de_problema_de_credencial(self) -> None:
        class Recusa:
            async def escalares(self, alvo, oids):
                raise RuntimeError("agente recusou: authorizationError")

        res = await SondaSnmp(sessao=Recusa()).executar("10.0.0.1", {})
        assert res.ok is False
        assert "authorizationError" in " ".join(res.linhas)

    async def test_le_o_oid_pedido(self) -> None:
        class Responde:
            async def escalares(self, alvo, oids):
                return {oids[0]: "Cisco IOS Software"}

        res = await SondaSnmp(sessao=Responde()).executar(
            "10.0.0.1", {"oid": "1.3.6.1.2.1.1.1.0"}
        )
        assert res.ok is True
        assert "Cisco IOS" in " ".join(res.linhas)


class TestLacoDeEventos:
    """O ping tem de funcionar no laço que roda em produção, não só no de teste.

    ``sock_sendto``/``sock_recvfrom`` existem no asyncio padrão e **não** no
    uvloop, que é o laço do uvicorn. Escrito com eles, o módulo passava em tudo
    e estourava ``NotImplementedError`` no primeiro clique da tela. Este teste
    roda a sonda nos dois laços de propósito: é a diferença que o defeito
    ocupava.
    """

    def _sondar(self, laco) -> dict:
        from plataforma.modulos.icmp import sondar

        try:
            return laco.run_until_complete(sondar(["127.0.0.1"], tentativas=2, timeout_s=1.0))
        finally:
            laco.close()

    @pytest.mark.parametrize("fabrica", ["asyncio", "uvloop"])
    async def test_ping_responde_nos_dois_lacos(self, fabrica: str) -> None:
        from plataforma.modulos.icmp import abrir_socket

        try:
            s, _ = abrir_socket()
            s.close()
        except PermissionError:
            pytest.skip("sem permissão para socket ICMP neste ambiente")

        if fabrica == "uvloop":
            uvloop = pytest.importorskip("uvloop")
            laco = uvloop.new_event_loop()
        else:
            laco = asyncio.new_event_loop()

        sondas = await asyncio.to_thread(self._sondar, laco)
        sonda = sondas["127.0.0.1"]
        assert sonda.enviados == 2
        assert sonda.recebidos >= 1, f"laço {fabrica} não recebeu resposta do loopback"
