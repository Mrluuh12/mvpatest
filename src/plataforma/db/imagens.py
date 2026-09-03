"""Imagens de ativos e dispositivos.

O sujeito é hierárquico, e é isso que torna o recurso prático em vez de
trabalhoso: em vez de subir 708 fotos, sobe-se **uma por papel** — um rádio
Rajant, um PTX, um CLP — e todos os dispositivos daquele papel passam a
exibi-la. Foto específica de um aparelho, quando existir, tem precedência.

A mesma ideia vale para ativos: uma foto por frota cobre os 145, e a foto de
uma máquina específica sobrepõe.

Sobre segurança: o arquivo é gravado com **nome gerado por nós**, nunca com o
nome que veio no envio, e o tipo servido vem da nossa lista, nunca do
cabeçalho de quem enviou. As duas coisas juntas fecham a porta para travessia
de caminho e para conteúdo servido com tipo enganoso.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from .esquema import imagem

VAR_DIRETORIO = "PLATAFORMA_IMAGENS"
PADRAO_DIRETORIO = "dados/imagens"

#: Só estes tipos entram. A extensão gravada vem daqui, não do envio.
TIPOS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}

TAMANHO_MAXIMO = 4 * 1024 * 1024  # 4 MB


class ImagemRecusada(ValueError):
    """Envio recusado — com o motivo, para que dê para corrigir."""


@dataclass(slots=True)
class ImagemGravada:
    sujeito: str
    arquivo: str
    tipo: str
    bytes: int


def diretorio() -> Path:
    caminho = Path(os.environ.get(VAR_DIRETORIO, PADRAO_DIRETORIO))
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def sujeitos_possiveis(tipo_entidade: str, chave: str, extra: str | None = None) -> list[str]:
    """A cascata de procura, do mais específico para o mais geral."""
    if tipo_entidade == "dispositivo":
        return [f"disp:{chave}"] + ([f"papel:{extra}"] if extra else [])
    if tipo_entidade == "ativo":
        return [f"ativo:{chave}"] + ([f"frota:{extra}"] if extra else [])
    return [f"{tipo_entidade}:{chave}"]


def validar(conteudo: bytes, tipo: str) -> str:
    """Confere tipo e tamanho. Devolve a extensão que será usada."""
    if tipo not in TIPOS:
        aceitos = ", ".join(sorted(TIPOS))
        raise ImagemRecusada(f"tipo {tipo!r} não aceito — use um de: {aceitos}")
    if not conteudo:
        raise ImagemRecusada("arquivo vazio")
    if len(conteudo) > TAMANHO_MAXIMO:
        limite = TAMANHO_MAXIMO // (1024 * 1024)
        raise ImagemRecusada(
            f"arquivo tem {len(conteudo) // 1024} KB e o limite é {limite} MB"
        )
    return TIPOS[tipo]


async def guardar(
    conexao: AsyncConnection,
    sujeito: str,
    conteudo: bytes,
    tipo: str,
    enviado_por: str | None = None,
) -> ImagemGravada:
    """Grava a imagem e registra a associação. Substitui a anterior, se houver."""
    extensao = validar(conteudo, tipo)
    # Nome derivado do conteúdo: dois envios iguais reaproveitam o arquivo, e
    # nada do que veio no pedido chega ao sistema de arquivos.
    digest = hashlib.sha256(conteudo).hexdigest()[:32]
    arquivo = f"{digest}{extensao}"
    (diretorio() / arquivo).write_bytes(conteudo)

    largura = altura = None
    if tipo != "image/svg+xml":
        try:
            from PIL import Image

            with Image.open(diretorio() / arquivo) as img:
                largura, altura = img.size
        except Exception:  # noqa: BLE001 - dimensão é enfeite, não requisito
            pass

    await conexao.execute(
        pg_insert(imagem)
        .values(
            sujeito=sujeito,
            arquivo=arquivo,
            tipo=tipo,
            bytes=len(conteudo),
            largura=largura,
            altura=altura,
            enviado_em=datetime.now(UTC),
            enviado_por=enviado_por,
        )
        .on_conflict_do_update(
            index_elements=["sujeito"],
            set_={
                "arquivo": arquivo,
                "tipo": tipo,
                "bytes": len(conteudo),
                "largura": largura,
                "altura": altura,
                "enviado_em": datetime.now(UTC),
                "enviado_por": enviado_por,
            },
        )
    )
    return ImagemGravada(sujeito, arquivo, tipo, len(conteudo))


async def remover(conexao: AsyncConnection, sujeito: str) -> bool:
    """Desassocia a imagem.

    O arquivo em disco não é apagado de propósito: o nome vem do conteúdo,
    então outro sujeito pode estar apontando para ele. Limpeza de órfãos é
    tarefa periódica, não efeito colateral de um clique.
    """
    resultado = await conexao.execute(delete(imagem).where(imagem.c.sujeito == sujeito))
    return bool(resultado.rowcount)


async def mapa(conexao: AsyncConnection) -> dict[str, str]:
    """Todos os sujeitos com imagem, apontando para o nome do arquivo."""
    linhas = (await conexao.execute(select(imagem.c.sujeito, imagem.c.arquivo))).all()
    return {ln.sujeito: ln.arquivo for ln in linhas}


async def buscar(conexao: AsyncConnection, arquivo: str) -> tuple[Path, str] | None:
    """Localiza um arquivo pelo nome, validando que ele é nosso.

    A consulta ao banco não é formalidade: é o que impede alguém pedir
    ``../../etc/passwd``. Se o nome não está registrado, não existe.
    """
    linha = (
        await conexao.execute(select(imagem.c.tipo).where(imagem.c.arquivo == arquivo))
    ).first()
    if linha is None:
        return None
    caminho = diretorio() / arquivo
    return (caminho, linha.tipo) if caminho.is_file() else None
