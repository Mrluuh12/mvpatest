"""Esquema do banco.

Duas escolhas aqui carregam o peso do resto do projeto.

**Uma linha por origem, não um valor vencedor.** A tabela ``campo`` guarda o
valor derivado, o descoberto e o cadastrado *lado a lado*, cada um na sua
linha. O vencedor é calculado na leitura, pela precedência.

Isso transforma a promessa "rodar a semeadura de novo não apaga correção
humana" de disciplina em **impossibilidade estrutural**: a semeadura só toca a
linha ``derivado``; a linha ``cadastrado`` está fora do alcance dela. E a
divergência entre cadastro e realidade deixa de ser um log passageiro para
virar uma consulta — as duas afirmações continuam no banco.

**A aresta tem validade temporal e o banco impede sobreposição.** O
``EXCLUDE`` com GiST garante que a mesma aresta não exista duas vezes no mesmo
instante. Não é validação de aplicação que alguém pode esquecer de chamar: é
recusa do banco.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSTZRANGE, ExcludeConstraint

metadata = MetaData()


ativo = Table(
    "ativo",
    metadata,
    Column("ativo_id", Text, primary_key=True),
    Column("frota", Text, nullable=False),
    Column("numero", Text, nullable=False),
    Column("site", Text, nullable=False, server_default=text("'mina'")),
    Column("criado_em", DateTime(timezone=True), server_default=text("now()")),
)


dispositivo = Table(
    "dispositivo",
    metadata,
    Column("chave", Text, primary_key=True),
    Column("nome_bruto", Text, nullable=False),
    Column("nome_canonico", Text, nullable=False),
    Column("papel", Text, nullable=False),
    Column("zona", Text, nullable=False),
    Column("ativo_id", Text, ForeignKey("ativo.ativo_id", ondelete="SET NULL")),
    Column("criado_em", DateTime(timezone=True), server_default=text("now()")),
    Index("ix_dispositivo_ativo", "ativo_id"),
    Index("ix_dispositivo_papel", "papel"),
    Index("ix_dispositivo_zona", "zona"),
)


identificador = Table(
    "identificador",
    metadata,
    Column(
        "dispositivo_chave",
        Text,
        ForeignKey("dispositivo.chave", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("tipo", String(16), primary_key=True),
    Column("valor", Text, primary_key=True),
    # Permite achar um dispositivo por qualquer identificador que se conheça —
    # é o que a reconciliação entre módulos usa.
    Index("ix_identificador_busca", "tipo", "valor"),
)


#: Um valor por origem. A precedência decide o vencedor **na leitura**.
campo = Table(
    "campo",
    metadata,
    Column("sujeito", Text, primary_key=True),  # 'ativo:CA-1042' | 'disp:mac:…'
    Column("nome", Text, primary_key=True),  # 'funcao_negocio', 'firmware'
    Column("origem", String(16), primary_key=True),  # derivado|descoberto|cadastrado
    Column("natureza", String(16), nullable=False),  # intencao|observacao
    Column("valor", JSONB, nullable=False),
    Column("em", DateTime(timezone=True), nullable=False),
    Index("ix_campo_sujeito", "sujeito"),
)


aresta = Table(
    "aresta",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("origem_chave", Text, nullable=False),
    Column("destino_chave", Text, nullable=False),
    Column("tipo", String(32), nullable=False),
    # [inicio, fim) — fim NULL (infinito) significa "ainda vale".
    Column("validade", TSTZRANGE, nullable=False),
    Column("atributos", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Index("ix_aresta_validade", "validade", postgresql_using="gist"),
    Index("ix_aresta_origem", "origem_chave", "tipo"),
    Index("ix_aresta_destino", "destino_chave", "tipo"),
    # A mesma aresta não pode existir duas vezes no mesmo instante. Exige a
    # extensão btree_gist para comparar texto com `=` dentro de um índice GiST.
    ExcludeConstraint(
        ("origem_chave", "="),
        ("destino_chave", "="),
        ("tipo", "="),
        ("validade", "&&"),
        name="aresta_sem_sobreposicao",
        using="gist",
    ),
)


#: Achados da última semeadura. Guardados porque são a lista de trabalho
#: humano — some do terminal, mas não pode sumir do sistema.
achado = Table(
    "achado",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("categoria", String(32), nullable=False),
    Column("descricao", Text, nullable=False),
    Column("em", DateTime(timezone=True), nullable=False),
    Index("ix_achado_categoria", "categoria"),
)
