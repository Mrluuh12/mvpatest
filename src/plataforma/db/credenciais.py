"""Cofre de credenciais: os segredos que a plataforma **usa**.

É outra coisa que senha de usuário, e a diferença define o desenho. Senha de
usuário só precisa ser *conferida* — guarda-se um `scrypt` irreversível e
compara-se. Credencial precisa ser *apresentada* ao equipamento: a comunidade
SNMP vai dentro do pacote UDP. Logo não pode ser hash; tem de voltar em claro
na hora do uso.

Daí as quatro regras:

**A chave mora no ambiente, nunca no banco.** Quem levar o dump do Postgres
leva ciphertext. `PLATAFORMA_CHAVE` é uma chave AES-256 em base64.

**Sem chave, o cofre se recusa a operar.** Não há degradação para texto claro:
uma instalação mal configurada falha ruidosamente na partida, em vez de gravar
comunidades em claro e ninguém perceber por dois anos.

**A API nunca devolve segredo.** Nem para administrador, nem uma vez, nem
mascarado. Listar mostra nome, tipo e zona; o valor só sai por
``abrir()``, que é chamada pelo coletor, dentro do processo.

**Credencial tem zona.** Uma comunidade de OT não pode ser usada por coletor
corporativo — mesma regra dos módulos, pelo mesmo motivo.

O AAD do AES-GCM é o nome da credencial: um ciphertext copiado da linha
``snmp-ot`` para a linha ``snmp-corp`` não descriptografa. A cifra prende o
segredo ao lugar dele.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from inventario.modelo import Zona

from .esquema import credencial

VAR_CHAVE = "PLATAFORMA_CHAVE"


class CofreSemChave(RuntimeError):
    """Falta ``PLATAFORMA_CHAVE``. Não há caminho alternativo de propósito."""


class SegredoInvalido(RuntimeError):
    """O ciphertext não abre com esta chave — ou não é desta credencial."""


def gerar_chave() -> str:
    """Uma chave nova, em base64, para pôr no ambiente."""
    return base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()


def _cofre() -> AESGCM:
    bruta = os.environ.get(VAR_CHAVE)
    if not bruta:
        raise CofreSemChave(
            f"defina {VAR_CHAVE} com uma chave AES-256 em base64 "
            f"(gere com `python -c \"from plataforma.db.credenciais import "
            f'gerar_chave; print(gerar_chave())"`). Sem ela o cofre não abre, '
            f"e guardar segredo em claro não é alternativa."
        )
    try:
        chave = base64.b64decode(bruta, validate=True)
    except Exception as erro:  # noqa: BLE001
        raise CofreSemChave(f"{VAR_CHAVE} não é base64 válido: {erro}") from erro
    if len(chave) not in (16, 24, 32):
        raise CofreSemChave(
            f"{VAR_CHAVE} tem {len(chave)} bytes; AES aceita 16, 24 ou 32"
        )
    return AESGCM(chave)


async def guardar(
    conexao: AsyncConnection,
    nome: str,
    tipo: str,
    zona: Zona,
    segredo: dict,
    atributos: dict | None = None,
    por: str | None = None,
) -> None:
    """Cifra e grava. Substitui a credencial de mesmo nome."""
    nonce = os.urandom(12)
    texto = json.dumps(segredo, sort_keys=True).encode("utf-8")
    # O nome como AAD amarra o ciphertext à linha: copiado para outra
    # credencial, ele deixa de abrir.
    cifrado = _cofre().encrypt(nonce, texto, nome.encode("utf-8"))
    valores = {
        "nome": nome,
        "tipo": tipo,
        "zona": zona.value,
        "nonce": nonce,
        "segredo": cifrado,
        "atributos": atributos or {},
        "criada_em": datetime.now(UTC),
        "criada_por": por,
    }
    await conexao.execute(
        pg_insert(credencial)
        .values(**valores)
        .on_conflict_do_update(
            index_elements=["nome"],
            set_={k: v for k, v in valores.items() if k != "nome"},
        )
    )


async def abrir(conexao: AsyncConnection, nome: str, zona: Zona) -> dict | None:
    """Devolve o segredo em claro — só para quem está na zona certa.

    A zona é conferida aqui, e não só na tela: é a última linha antes de o
    segredo virar pacote na rede.
    """
    linha = (
        await conexao.execute(select(credencial).where(credencial.c.nome == nome))
    ).first()
    if linha is None:
        return None
    if linha.zona != zona.value:
        raise PermissionError(
            f"credencial {nome!r} é da zona {linha.zona!r}; "
            f"este coletor está em {zona.value!r}"
        )
    try:
        claro = _cofre().decrypt(linha.nonce, linha.segredo, nome.encode("utf-8"))
    except InvalidTag as erro:
        raise SegredoInvalido(
            f"credencial {nome!r} não abre: a chave mudou, ou o registro foi "
            f"alterado fora da plataforma"
        ) from erro
    return json.loads(claro)


async def listar(conexao: AsyncConnection) -> list[dict]:
    """Sem segredo, nem mascarado. O que a tela precisa é o resto."""
    return [
        {
            "nome": ln.nome,
            "tipo": ln.tipo,
            "zona": ln.zona,
            "atributos": ln.atributos or {},
            "criada_em": ln.criada_em,
            "criada_por": ln.criada_por,
        }
        for ln in (
            await conexao.execute(select(credencial).order_by(credencial.c.nome))
        ).all()
    ]


async def remover(conexao: AsyncConnection, nome: str) -> bool:
    r = await conexao.execute(delete(credencial).where(credencial.c.nome == nome))
    return bool(r.rowcount)


__all__ = [
    "VAR_CHAVE",
    "CofreSemChave",
    "SegredoInvalido",
    "abrir",
    "gerar_chave",
    "guardar",
    "listar",
    "remover",
]
