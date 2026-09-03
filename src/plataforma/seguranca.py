"""Senhas e sessões.

Três decisões pequenas que carregam peso.

**A senha nunca é guardada.** Guarda-se o resultado de ``scrypt`` com sal por
usuário. ``scrypt`` está na biblioteca padrão e é caro de propósito: força
bruta contra o banco fica inviável em vez de rápida.

**A sessão guardada é o resumo do token.** O que vai para o navegador é um
segredo aleatório; o que fica no banco é o SHA-256 dele. Vazamento do banco
entrega uma lista de resumos inúteis, não sessões válidas.

**Comparação em tempo constante.** ``compare_digest`` em toda verificação —
comparar com ``==`` vaza, pelo tempo de resposta, o quanto do valor estava
certo.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: Parâmetros do scrypt. Custam alguns décimos de segundo por login e tornam
#: uma tentativa de força bruta em massa impraticável.
CUSTO_N = 2**14
CUSTO_R = 8
CUSTO_P = 1
TAMANHO = 32

DURACAO_SESSAO = timedelta(hours=12)
TAMANHO_MINIMO_SENHA = 10


class SenhaFraca(ValueError):
    """Recusa com o motivo — regra que não explica vira senha pior, não melhor."""


def gerar_sal() -> str:
    return secrets.token_hex(16)


def derivar(senha: str, sal: str) -> str:
    return hashlib.scrypt(
        senha.encode("utf-8"),
        salt=bytes.fromhex(sal),
        n=CUSTO_N,
        r=CUSTO_R,
        p=CUSTO_P,
        dklen=TAMANHO,
    ).hex()


def validar_senha(senha: str) -> None:
    """Regra mínima, dita de forma acionável."""
    if len(senha) < TAMANHO_MINIMO_SENHA:
        raise SenhaFraca(
            f"a senha precisa de ao menos {TAMANHO_MINIMO_SENHA} caracteres"
        )
    if senha.strip() != senha:
        raise SenhaFraca("a senha não pode começar ou terminar com espaço")
    if senha.lower() in {"senha123456", "12345678901", "administrador"}:
        raise SenhaFraca("essa senha é previsível demais")


def criar_credencial(senha: str) -> tuple[str, str]:
    """Devolve ``(hash, sal)`` para guardar."""
    validar_senha(senha)
    sal = gerar_sal()
    return derivar(senha, sal), sal


def conferir(senha: str, senha_hash: str, sal: str) -> bool:
    return hmac.compare_digest(derivar(senha, sal), senha_hash)


@dataclass(slots=True)
class TokenSessao:
    """O segredo entregue ao navegador e o resumo que fica no banco."""

    token: str
    resumo: str
    expira_em: datetime


def resumir(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def nova_sessao(duracao: timedelta = DURACAO_SESSAO) -> TokenSessao:
    token = secrets.token_urlsafe(32)
    return TokenSessao(token, resumir(token), datetime.now(UTC) + duracao)
