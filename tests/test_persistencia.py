"""Testes da persistência.

O que estes testes protegem é a promessa mais fácil de quebrar do projeto:
**rodar a semeadura de novo não pode apagar o que uma pessoa corrigiu no ADM.**

Ela não é defendida por disciplina de quem escreve o código, e sim pela forma
da tabela — a origem faz parte da chave primária, então a escrita derivada não
tem como alcançar a linha cadastrada. Estes testes existem para garantir que
essa propriedade continue verdadeira quando alguém mexer no esquema.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from inventario.modelo import Natureza
from inventario.semeadura import semear
from plataforma.db.esquema import aresta, campo
from plataforma.db.repositorio_pg import (
    RepositorioPostgres,
    apagar_esquema,
    aplicar_semeadura,
    cadastrar_campo,
    campos_vencedores,
    criar_engine,
    criar_esquema,
    divergencias,
    sujeito_ativo,
    sujeito_dispositivo,
)

URL = os.environ.get(
    "PLATAFORMA_BANCO_TESTE",
    "postgresql+asyncpg://postgres@localhost:5432/plataforma",
)

pytestmark = pytest.mark.asyncio


def registro(nome: str, ip: str, mac: str | None = None, classe: str = "IT") -> dict:
    return {"Name": nome, "IP": ip, "Mac": mac, "Class": classe, "Asset Id": None}


PARQUE = [
    registro("CA-1042-RADIO RJT", "10.188.99.42", "00:11:22:33:44:01"),
    registro("CA-1042-PTX", "10.188.98.42", "00:11:22:33:44:02"),
    registro("CA-1042-CLP", "10.188.103.42", "00:11:22:33:44:03", "OT"),
    registro("EH-6552-RADIO RJT", "10.188.99.237", "00:11:22:33:44:04"),
]


@pytest_asyncio.fixture
async def engine():
    motor = criar_engine(URL)
    try:
        # Apagar antes de criar não é zelo excessivo: sem isso o teste passa a
        # depender do que a execução anterior deixou, e um teste que depende de
        # estado anterior não está testando o código — está testando a sorte.
        await apagar_esquema(motor)
        await criar_esquema(motor)
    except Exception as erro:  # noqa: BLE001 - ambiente sem banco é skip, não falha
        await motor.dispose()
        pytest.skip(f"Postgres indisponível: {erro}")
    yield motor
    await apagar_esquema(motor)
    await motor.dispose()


class TestSemeaduraPersistida:
    async def test_grava_ativos_dispositivos_e_arestas(self, engine) -> None:
        async with engine.begin() as conexao:
            contagem = await aplicar_semeadura(conexao, semear(PARQUE))
        assert contagem == {"ativos": 2, "dispositivos": 4, "arestas": 4}

        repo = await RepositorioPostgres.carregar(engine)
        assert {a.ativo_id for a in repo.ativos()} == {"CA-1042", "EH-6552"}
        assert len(repo.dispositivos("CA-1042")) == 3
        assert repo.resumo()["arestas_abertas"] == 4

    async def test_e_idempotente(self, engine) -> None:
        """Rodar de novo com a mesma entrada não duplica nada."""
        semeada = semear(PARQUE)
        async with engine.begin() as conexao:
            await aplicar_semeadura(conexao, semeada)
        async with engine.begin() as conexao:
            segunda = await aplicar_semeadura(conexao, semeada)

        assert segunda["arestas"] == 0, "arestas já abertas não são reabertas"
        repo = await RepositorioPostgres.carregar(engine)
        assert repo.resumo()["dispositivos"] == 4
        assert repo.resumo()["arestas_abertas"] == 4

    async def test_sobrevive_a_reinicio(self, engine) -> None:
        """Carregar de novo, sem semear, devolve o mesmo parque."""
        async with engine.begin() as conexao:
            await aplicar_semeadura(conexao, semear(PARQUE))
        primeiro = await RepositorioPostgres.carregar(engine)
        segundo = await RepositorioPostgres.carregar(engine)
        assert primeiro.resumo() == segundo.resumo()


class TestCadastroHumanoSobrevive:
    async def test_semeadura_posterior_nao_apaga_correcao_do_adm(self, engine) -> None:
        """A garantia central do marco M0.

        Alguém corrige a função de negócio na área ADM; a semeadura roda de
        novo com o valor derivado antigo; a correção continua valendo.
        """
        async with engine.begin() as conexao:
            await aplicar_semeadura(conexao, semear(PARQUE))
            await cadastrar_campo(
                conexao, sujeito_ativo("CA-1042"), "funcao_negocio", "britagem"
            )

        async with engine.begin() as conexao:
            await aplicar_semeadura(conexao, semear(PARQUE))

        repo = await RepositorioPostgres.carregar(engine)
        assert repo.ativo("CA-1042").funcao_negocio == "britagem"
        assert repo.ativo("EH-6552").funcao_negocio == "carregamento"

    async def test_as_duas_versoes_continuam_no_banco(self, engine) -> None:
        """Não é sobrescrita: derivado e cadastrado coexistem, lado a lado.

        É o que permite responder "o que o código deduziu?" depois de alguém
        ter corrigido — e o que torna a divergência consultável.
        """
        async with engine.begin() as conexao:
            await aplicar_semeadura(conexao, semear(PARQUE))
            await cadastrar_campo(
                conexao, sujeito_ativo("CA-1042"), "funcao_negocio", "britagem"
            )

        async with engine.connect() as conexao:
            linhas = (
                await conexao.execute(
                    select(campo.c.origem, campo.c.valor).where(
                        campo.c.sujeito == sujeito_ativo("CA-1042")
                    )
                )
            ).all()
        assert {ln.origem: ln.valor for ln in linhas} == {
            "derivado": "transporte_de_minerio",
            "cadastrado": "britagem",
        }

    async def test_equipamento_vence_cadastro_em_campo_de_observacao(
        self, engine
    ) -> None:
        """Pessoa digitando firmware perde para o firmware lido do aparelho."""
        alvo = sujeito_dispositivo("mac:00:11:22:33:44:01")
        async with engine.begin() as conexao:
            await cadastrar_campo(
                conexao, alvo, "firmware", "5.9.1", Natureza.OBSERVACAO
            )
            await conexao.execute(
                text(
                    "INSERT INTO campo (sujeito, nome, origem, natureza, valor, em) "
                    "VALUES (:s, 'firmware', 'descoberto', 'observacao', '\"5.9.2.0\"', now())"
                ),
                {"s": alvo},
            )

        async with engine.connect() as conexao:
            vencedores = await campos_vencedores(conexao, alvo)
            achados = await divergencias(conexao)

        assert vencedores[alvo]["firmware"] == ("5.9.2.0", "descoberto")
        assert any(d["campo"] == "firmware" for d in achados), (
            "cadastro discordando da realidade é achado, não ruído"
        )


class TestGrafoTemporal:
    async def test_banco_recusa_aresta_duplicada_no_mesmo_instante(self, engine) -> None:
        """A garantia é do banco, não da aplicação.

        Validação que mora só no código é validação que alguém esquece de
        chamar. Esta é uma restrição de exclusão: não há caminho que a burle.
        """
        async with engine.begin() as conexao:
            await aplicar_semeadura(conexao, semear(PARQUE))

        with pytest.raises(IntegrityError):
            async with engine.begin() as conexao:
                await conexao.execute(
                    text(
                        "INSERT INTO aresta (origem_chave, destino_chave, tipo, validade) "
                        "VALUES ('mac:00:11:22:33:44:01', 'CA-1042', 'embarcado_em', "
                        "tstzrange(now(), NULL, '[)'))"
                    )
                )

    async def test_aresta_fechada_libera_o_periodo_seguinte(self, engine) -> None:
        """Fechada a aresta, a mesma relação pode voltar a existir depois.

        É o que permite um nó reassociar-se ao mesmo vizinho mais tarde sem
        que o histórico anterior atrapalhe.
        """
        async with engine.begin() as conexao:
            await aplicar_semeadura(conexao, semear(PARQUE))
            await conexao.execute(
                text(
                    "UPDATE aresta SET validade = tstzrange(lower(validade), now(), '[)') "
                    "WHERE destino_chave = 'CA-1042'"
                )
            )
            depois = datetime.now(UTC) + timedelta(minutes=1)
            await conexao.execute(
                text(
                    "INSERT INTO aresta (origem_chave, destino_chave, tipo, validade) "
                    "VALUES ('mac:00:11:22:33:44:01', 'CA-1042', 'embarcado_em', "
                    "tstzrange(:d, NULL, '[)'))"
                ),
                {"d": depois},
            )

        async with engine.connect() as conexao:
            total = await conexao.scalar(
                select(func.count())
                .select_from(aresta)
                .where(aresta.c.destino_chave == "CA-1042")
            )
            abertas = await conexao.scalar(
                select(func.count())
                .select_from(aresta)
                .where(aresta.c.destino_chave == "CA-1042")
                .where(func.upper_inf(aresta.c.validade))
            )
        assert total == 4, "o histórico é preservado, não sobrescrito"
        assert abertas == 1

    async def test_quem_estava_ligado_em_dado_instante(self, engine) -> None:
        """A pergunta que justifica o grafo temporal existir."""
        async with engine.begin() as conexao:
            await aplicar_semeadura(conexao, semear(PARQUE))

        async with engine.connect() as conexao:
            quantas = await conexao.scalar(
                text(
                    "SELECT count(*) FROM aresta "
                    "WHERE destino_chave = 'CA-1042' AND validade @> now()"
                )
            )
        assert quantas == 3
