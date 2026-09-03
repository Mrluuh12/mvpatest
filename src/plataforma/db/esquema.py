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
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
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


#: Estado corrente de cada dispositivo. Uma linha por dispositivo, sobrescrita
#: a cada coleta — é o que a tela do ativo lê.
estado = Table(
    "estado",
    metadata,
    Column("sujeito", Text, primary_key=True),
    Column("alcancavel", Boolean, nullable=False),
    Column("latencia_ms", Float),
    Column("perda_pct", Float),
    Column("jitter_ms", Float),
    Column("qualidade", String(16), nullable=False, server_default=text("'boa'")),
    Column("visto_em", DateTime(timezone=True), nullable=False),
    Index("ix_estado_alcancavel", "alcancavel"),
)


#: Só as **mudanças** de estado, nunca uma linha por amostra.
#:
#: Disponibilidade não precisa de um registro por minuto: precisa de saber
#: quando mudou. Sondando 708 dispositivos a cada minuto, guardar amostras
#: daria ~1 milhão de linhas por dia; guardar transições dá algumas dezenas.
#: E é exatamente a matéria-prima que o motor de alarmes vai querer quando
#: chegar a vez dele.
transicao = Table(
    "transicao",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("sujeito", Text, nullable=False),
    Column("de", Boolean),
    Column("para", Boolean, nullable=False),
    Column("em", DateTime(timezone=True), nullable=False),
    Index("ix_transicao_sujeito", "sujeito", "em"),
)


#: Saúde de cada módulo — as cinco séries obrigatórias, no seu estado atual.
saude_modulo = Table(
    "saude_modulo",
    metadata,
    Column("modulo", Text, primary_key=True),
    Column("ultima_coleta_ok", DateTime(timezone=True)),
    Column("alvos_total", Integer, nullable=False, server_default=text("0")),
    Column("alvos_falha", Integer, nullable=False, server_default=text("0")),
    Column("duracao_s", Float, nullable=False, server_default=text("0")),
    Column("rejeitadas", Integer, nullable=False, server_default=text("0")),
    Column("atualizado_em", DateTime(timezone=True), nullable=False),
)


#: Imagem associada a um sujeito. O sujeito é hierárquico de propósito:
#:
#:   disp:<chave>   uma foto daquele aparelho específico
#:   papel:<papel>  vale para todos os dispositivos daquele papel
#:   ativo:<id>     a foto daquela máquina
#:   frota:<CA>     vale para toda a frota
#:
#: A cascata é o que torna isto prático: subir 17 fotos, uma por papel, cobre
#: os 708 dispositivos. Subir uma por frota cobre os 145 ativos. Foto
#: específica, quando existir, tem precedência.
imagem = Table(
    "imagem",
    metadata,
    Column("sujeito", Text, primary_key=True),
    Column("arquivo", Text, nullable=False),
    Column("tipo", String(32), nullable=False),
    Column("bytes", Integer, nullable=False),
    Column("largura", Integer),
    Column("altura", Integer),
    Column("enviado_em", DateTime(timezone=True), nullable=False),
    Column("enviado_por", Text),
)


# --------------------------------------------------------------------------
# Contas, sessões e auditoria
# --------------------------------------------------------------------------


usuario = Table(
    "usuario",
    metadata,
    Column("login", Text, primary_key=True),
    Column("nome", Text, nullable=False),
    Column("senha_hash", Text, nullable=False),
    Column("sal", Text, nullable=False),
    Column("ativo", Boolean, nullable=False, server_default=text("true")),
    Column("criado_em", DateTime(timezone=True), nullable=False),
    Column("ultimo_acesso", DateTime(timezone=True)),
)


#: Um papel vale dentro de um conjunto de zonas, não na plataforma inteira.
#: É a composição que faz o modelo servir numa mina: a mesma pessoa pode ser
#: operadora na zona corporativa e apenas leitora em OT.
concessao = Table(
    "concessao",
    metadata,
    Column(
        "login",
        Text,
        ForeignKey("usuario.login", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("papel", String(24), primary_key=True),
    Column("zona", String(24), primary_key=True),
)


#: O token guardado é o **resumo** do que foi entregue ao navegador. Vazamento
#: do banco não entrega sessão a ninguém.
sessao = Table(
    "sessao",
    metadata,
    Column("resumo", Text, primary_key=True),
    Column(
        "login", Text, ForeignKey("usuario.login", ondelete="CASCADE"), nullable=False
    ),
    Column("criada_em", DateTime(timezone=True), nullable=False),
    Column("expira_em", DateTime(timezone=True), nullable=False),
    Column("origem", Text),
    Index("ix_sessao_login", "login"),
)


#: Somente escrita. Não existe rota que altere ou apague uma linha daqui —
#: auditoria que se pode editar não é auditoria.
auditoria = Table(
    "auditoria",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("em", DateTime(timezone=True), nullable=False),
    Column("login", Text),
    Column("acao", String(48), nullable=False),
    Column("sujeito", Text, nullable=False),
    Column("zona", String(24)),
    Column("detalhe", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("origem", Text),
    Index("ix_auditoria_em", "em"),
    Index("ix_auditoria_sujeito", "sujeito"),
    Index("ix_auditoria_login", "login"),
)


#: Arranjo de tela por escopo. A cascata é a mesma das imagens:
#: ``frota:CA`` antes de ``padrao_ativo``; ``papel:radio_mesh`` antes de
#: ``padrao_dispositivo``. Configura-se o tipo, não a instância — um arranjo
#: por frota serve os 299 caminhões.
arranjo = Table(
    "arranjo",
    metadata,
    Column("escopo", Text, primary_key=True),
    Column("contexto", String(16), nullable=False),
    Column("cartoes", JSONB, nullable=False),
    Column("atualizado_em", DateTime(timezone=True), nullable=False),
    Column("atualizado_por", Text),
)
