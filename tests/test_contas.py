"""Testes de contas, autorização e auditoria.

O que estes testes defendem: **nenhuma escrita acontece sem conta autenticada,
permissão conferida naquela zona, e rastro gravado.** E dois casos que só
aparecem quando alguém tenta atacar: escalonamento por procuração e login
falho que não deixa vestígio.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import insert, select, update

from inventario.modelo import (
    MATRIZ_PAPEIS,
    Concessao,
    PapelUsuario,
    Permissao,
    Usuario,
    Zona,
)
from plataforma.autorizacao import permissoes_efetivas
from plataforma.db import contas
from plataforma.db.esquema import auditoria, sessao, usuario
from plataforma.db.repositorio_pg import apagar_esquema, criar_engine, criar_esquema
from plataforma.seguranca import (
    SenhaFraca,
    conferir,
    criar_credencial,
    nova_sessao,
    resumir,
    validar_senha,
)

URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma_teste",
)
SENHA = "uma-senha-bem-longa"


@pytest_asyncio.fixture
async def engine():
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


class TestSenha:
    def test_senha_nunca_vira_o_proprio_hash(self) -> None:
        h, sal = criar_credencial(SENHA)
        assert SENHA not in h
        assert conferir(SENHA, h, sal)
        assert not conferir(SENHA + "x", h, sal)

    def test_mesma_senha_gera_hashes_diferentes(self) -> None:
        """Sal por usuário: uma tabela pré-computada não serve para duas contas."""
        h1, _ = criar_credencial(SENHA)
        h2, _ = criar_credencial(SENHA)
        assert h1 != h2

    @pytest.mark.parametrize("ruim", ["curta", " espaço-na-ponta ", "senha123456"])
    def test_senha_fraca_e_recusada_com_motivo(self, ruim: str) -> None:
        with pytest.raises(SenhaFraca) as erro:
            validar_senha(ruim)
        assert str(erro.value), "a recusa precisa dizer o que corrigir"


class TestSessao:
    def test_o_que_vai_ao_navegador_nao_e_o_que_fica_no_banco(self) -> None:
        """Vazamento do banco entrega resumos inúteis, não sessões válidas."""
        t = nova_sessao()
        assert t.resumo != t.token
        assert t.resumo == resumir(t.token)


@pytest.mark.asyncio
class TestContas:
    async def _ana(self, engine, zonas=(Zona.CORPORATIVA,)) -> None:
        async with engine.begin() as c:
            await contas.criar_usuario(
                c, "ana", "Ana", SENHA,
                [(PapelUsuario.ADMINISTRADOR, list(zonas))], por="instalacao",
            )

    async def test_login_devolve_token_e_resolve_conta(self, engine) -> None:
        await self._ana(engine)
        async with engine.begin() as c:
            token = await contas.autenticar(c, "ana", SENHA)
        assert token
        async with engine.connect() as c:
            conta = await contas.resolver(c, token)
        assert conta and conta.usuario.login == "ana"

    async def test_senha_errada_nao_abre_sessao(self, engine) -> None:
        await self._ana(engine)
        async with engine.begin() as c:
            assert await contas.autenticar(c, "ana", "outra-coisa-longa") is None

    async def test_login_inexistente_nao_abre_sessao(self, engine) -> None:
        async with engine.begin() as c:
            assert await contas.autenticar(c, "fantasma", SENHA) is None

    async def test_tentativa_recusada_deixa_rastro(self, engine) -> None:
        """Login falho é exatamente o que uma auditoria precisa guardar."""
        await self._ana(engine)
        async with engine.begin() as c:
            await contas.autenticar(c, "ana", "senha-errada-longa")
        async with engine.connect() as c:
            acoes = [ln.acao for ln in (await c.execute(select(auditoria))).all()]
        assert "sessao.recusada" in acoes

    async def test_usuario_desativado_nao_entra_e_perde_a_sessao(self, engine) -> None:
        await self._ana(engine)
        async with engine.begin() as c:
            token = await contas.autenticar(c, "ana", SENHA)
        async with engine.begin() as c:
            await contas.desativar_usuario(c, "ana", por="chefe")
        async with engine.connect() as c:
            assert await contas.resolver(c, token) is None
            restantes = (await c.execute(select(sessao))).all()
        assert restantes == [], "desativar encerra as sessões abertas"

    async def test_sessao_expirada_e_recusada_e_apagada(self, engine) -> None:
        await self._ana(engine)
        async with engine.begin() as c:
            token = await contas.autenticar(c, "ana", SENHA)
            await c.execute(
                update(sessao).values(expira_em=datetime.now(UTC) - timedelta(minutes=1))
            )
        async with engine.begin() as c:
            assert await contas.resolver(c, token) is None
        async with engine.connect() as c:
            assert (await c.execute(select(sessao))).all() == []

    async def test_token_inventado_nao_resolve(self, engine) -> None:
        async with engine.connect() as c:
            assert await contas.resolver(c, "token-que-eu-inventei") is None
            assert await contas.resolver(c, None) is None

    async def test_desativar_e_reversivel_mas_o_historico_fica(self, engine) -> None:
        """Desativa em vez de apagar: a auditoria precisa continuar
        apontando para alguém que existe."""
        await self._ana(engine)
        async with engine.begin() as c:
            await contas.desativar_usuario(c, "ana", por="chefe")
        async with engine.connect() as c:
            linha = (await c.execute(select(usuario))).one()
        assert linha.login == "ana" and linha.ativo is False


@pytest.mark.asyncio
class TestAutorizacao:
    async def _conta(self, engine, papel, zonas) -> contas.Autenticado:
        async with engine.begin() as c:
            await contas.criar_usuario(c, "p", "P", SENHA, [(papel, zonas)])
            token = await contas.autenticar(c, "p", SENHA)
        async with engine.connect() as c:
            return await contas.resolver(c, token)

    async def test_sem_conta_e_negado(self, engine) -> None:
        with pytest.raises(contas.NaoAutenticado):
            contas.exigir(None, Permissao.VER, Zona.CORPORATIVA)

    async def test_administrador_da_corporativa_nao_e_administrador_de_ot(
        self, engine
    ) -> None:
        """A concessão vale onde foi concedida — não por consequência."""
        conta = await self._conta(engine, PapelUsuario.ADMINISTRADOR, [Zona.CORPORATIVA])
        assert contas.exigir(conta, Permissao.GERIR_USUARIOS, Zona.CORPORATIVA)
        with pytest.raises(contas.NaoAutorizado) as erro:
            contas.exigir(conta, Permissao.GERIR_USUARIOS, Zona.OT_NIVEL3)
        assert "ot_nivel3" in str(erro.value), "o erro diz qual zona faltou"

    async def test_operador_nao_gere_usuarios(self, engine) -> None:
        conta = await self._conta(engine, PapelUsuario.OPERADOR, [Zona.CORPORATIVA])
        assert contas.exigir(conta, Permissao.EXECUTAR_ACAO, Zona.CORPORATIVA)
        with pytest.raises(contas.NaoAutorizado):
            contas.exigir(conta, Permissao.GERIR_USUARIOS, Zona.CORPORATIVA)

    async def test_papeis_diferentes_por_zona_na_mesma_pessoa(self, engine) -> None:
        async with engine.begin() as c:
            await contas.criar_usuario(
                c, "mista", "Mista", SENHA,
                [
                    (PapelUsuario.OPERADOR, [Zona.CORPORATIVA]),
                    (PapelUsuario.LEITOR, [Zona.OT_NIVEL3]),
                ],
            )
            token = await contas.autenticar(c, "mista", SENHA)
        async with engine.connect() as c:
            conta = await contas.resolver(c, token)
        assert contas.exigir(conta, Permissao.EXECUTAR_ACAO, Zona.CORPORATIVA)
        assert contas.exigir(conta, Permissao.VER, Zona.OT_NIVEL3)
        with pytest.raises(contas.NaoAutorizado):
            contas.exigir(conta, Permissao.EXECUTAR_ACAO, Zona.OT_NIVEL3)


@pytest.mark.asyncio
class TestAuditoria:
    async def test_toda_criacao_de_conta_deixa_rastro(self, engine) -> None:
        async with engine.begin() as c:
            await contas.criar_usuario(
                c, "ana", "Ana", SENHA, [(PapelUsuario.LEITOR, [Zona.CORPORATIVA])],
                por="chefe",
            )
        async with engine.connect() as c:
            linhas = await contas.historico(c, "usuario:ana")
        assert linhas and linhas[0]["acao"] == "usuario.criar"
        assert linhas[0]["login"] == "chefe"

    async def test_historico_filtra_por_sujeito(self, engine) -> None:
        async with engine.begin() as c:
            await contas.registrar(c, "ativo.editar", "ativo:CA-1", login="ana")
            await contas.registrar(c, "ativo.editar", "ativo:CA-2", login="ana")
        async with engine.connect() as c:
            assert len(await contas.historico(c, "ativo:CA-1")) == 1
            assert len(await contas.historico(c)) == 2

    async def test_auditoria_e_somente_escrita_no_esquema(self, engine) -> None:
        """Não há rota que altere ou apague. O teste guarda a intenção:
        auditoria que se pode editar não é auditoria."""
        async with engine.begin() as c:
            await c.execute(
                insert(auditoria).values(
                    em=datetime.now(UTC), acao="teste", sujeito="x", detalhe={}
                )
            )
        async with engine.connect() as c:
            assert len((await c.execute(select(auditoria))).all()) == 1


class TestPermissoesNaTela:
    """A tela desenha o que o servidor já decidiu — e nada além disso.

    Havia aqui uma duplicação silenciosa: ``app.js`` carregava a própria cópia
    da matriz de papéis, para saber se desenhava o botão habilitado. Cópia de
    regra de autorização sai de sincronia, e saiu — ``diagnosticar`` nasceu, o
    servidor passou a aceitar, e o botão continuou cinza sem mensagem nenhuma.
    Estes testes existem para que a cópia não volte.
    """

    def _usuario(self, *concessoes) -> Usuario:
        return Usuario(
            login="p",
            nome="P",
            concessoes=tuple(
                Concessao(papel=papel, zonas=frozenset(zonas)) for papel, zonas in concessoes
            ),
        )

    def test_traz_toda_permissao_do_papel_sem_lista_escrita_a_mao(self) -> None:
        """O teste não enumera permissões: pergunta à mesma matriz. Se amanhã
        nascer outra, ela aparece aqui sozinha — que é o ponto."""
        u = self._usuario((PapelUsuario.ENGENHEIRO, [Zona.OT_NIVEL3]))
        efetivas = permissoes_efetivas(u)
        assert set(efetivas["ot_nivel3"]) == {
            p.value for p in MATRIZ_PAPEIS[PapelUsuario.ENGENHEIRO]
        }

    def test_zona_sem_concessao_vem_vazia_e_nao_ausente(self) -> None:
        """Vazia é resposta: "aqui você não pode nada". Ausente faria a tela
        confundir zona sem permissão com zona que ela não conhece."""
        u = self._usuario((PapelUsuario.ADMINISTRADOR, [Zona.CORPORATIVA]))
        efetivas = permissoes_efetivas(u)
        assert efetivas["ot_nivel3"] == []
        assert set(efetivas) == {z.value for z in Zona}, "toda zona é respondida"

    def test_papeis_diferentes_por_zona_nao_se_misturam(self) -> None:
        u = self._usuario(
            (PapelUsuario.OPERADOR, [Zona.CORPORATIVA]),
            (PapelUsuario.LEITOR, [Zona.OT_NIVEL3]),
        )
        efetivas = permissoes_efetivas(u)
        assert Permissao.EXECUTAR_ACAO.value in efetivas["corporativa"]
        assert efetivas["ot_nivel3"] == [Permissao.VER.value]

    def test_usuario_desativado_nao_pode_nada_em_lugar_nenhum(self) -> None:
        u = self._usuario((PapelUsuario.ADMINISTRADOR, list(Zona))).model_copy(
            update={"ativo": False}
        )
        assert all(not v for v in permissoes_efetivas(u).values())

    def test_a_tela_nao_guarda_copia_da_matriz(self) -> None:
        """Guarda de regressão. Na tela, um nome de permissão só pode aparecer
        como pergunta — ``pode("diagnosticar")`` — nunca dentro de uma lista.
        Lista é matriz, e matriz na tela é a cópia que sai de sincronia. Se
        este teste falhar, a duplicação voltou: some com ela, não com o teste.
        """
        js = (Path(__file__).resolve().parents[1] / "src/plataforma/web/app.js").read_text(
            encoding="utf-8"
        )
        soltas = [
            p.value
            for p in Permissao
            for m in re.finditer(rf'"{p.value}"', js)
            if not js[max(0, m.start() - 5) : m.start()].endswith("pode(")
        ]
        assert not soltas, f"permissão fora de pode() em app.js: {soltas}"
