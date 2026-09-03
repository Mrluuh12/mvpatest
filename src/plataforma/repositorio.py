"""Acesso aos dados do inventário.

O repositório é um protocolo, não uma classe concreta, de propósito: hoje o
inventário vive em memória, carregado do JSON que a semeadura produz; amanhã
vive em Postgres. A troca precisa ser local — mexer numa implementação, não na
API nem na interface.

É a mesma ideia do contrato de módulo aplicada para dentro: quem consome fala
com a forma, nunca com o motor.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class DispositivoLido(BaseModel):
    chave: str
    nome: str
    papel: str
    zona: str
    ip: str = ""
    ativo_id: str = ""
    identidade: str = "nenhum"
    fabricante: str = ""
    criticidade: str = ""


class AtivoLido(BaseModel):
    ativo_id: str
    frota: str
    numero: str
    funcao_negocio: str
    dispositivos: list[str] = []


class Achados(BaseModel):
    conflitos: list[str] = []
    homonimos: list[str] = []
    divergencias: list[str] = []
    papel_desconhecido: list[str] = []
    fora_do_padrao: list[str] = []


class Repositorio(Protocol):
    """O que a API precisa saber fazer com o inventário."""

    def ativos(self) -> list[AtivoLido]: ...
    def ativo(self, ativo_id: str) -> AtivoLido | None: ...
    def dispositivos(self, ativo_id: str | None = None) -> list[DispositivoLido]: ...
    def dispositivo(self, chave: str) -> DispositivoLido | None: ...
    def resumo(self) -> dict: ...
    def achados(self) -> Achados: ...
    def distribuicao(self, campo: str) -> dict[str, int]: ...


class RepositorioMemoria:
    """Implementação em memória, semeada a partir do JSON da semeadura.

    Serve enquanto o inventário couber folgado na memória — e a esta escala
    (145 ativos, 708 dispositivos) ele cabe com três ordens de grandeza de
    sobra. Trocar por Postgres é decisão de quando, não de se.
    """

    def __init__(
        self,
        ativos: list[AtivoLido],
        dispositivos: list[DispositivoLido],
        resumo: dict,
        achados: Achados,
    ) -> None:
        self._ativos = {a.ativo_id: a for a in ativos}
        self._dispositivos = {d.chave: d for d in dispositivos}
        self._resumo = resumo
        self._achados = achados

    # -- construção ------------------------------------------------------

    @classmethod
    def vazio(cls) -> RepositorioMemoria:
        return cls([], [], {"registros_lidos": 0, "ativos": 0, "dispositivos": 0}, Achados())

    @classmethod
    def de_arquivo(cls, caminho: str | Path) -> RepositorioMemoria:
        """Carrega do JSON compacto produzido por ``plataforma.semear``."""
        bruto = json.loads(Path(caminho).read_text(encoding="utf-8"))
        ativos = [
            AtivoLido(
                ativo_id=a["id"],
                frota=a["fr"],
                numero=a["nu"],
                funcao_negocio=a["fn"],
                dispositivos=a["d"],
            )
            for a in bruto["ativos"]
        ]
        dispositivos = [
            DispositivoLido(
                chave=d["k"],
                nome=d["n"],
                papel=d["p"],
                zona=d["z"],
                ip=d.get("ip", ""),
                ativo_id=d.get("a", ""),
                identidade=d.get("id", "nenhum"),
                fabricante=d.get("f", ""),
                criticidade=d.get("c", ""),
            )
            for d in bruto["dispositivos"]
        ]
        return cls(ativos, dispositivos, bruto["resumo"], Achados(**bruto.get("achados", {})))

    # -- consultas -------------------------------------------------------

    def ativos(self) -> list[AtivoLido]:
        return sorted(self._ativos.values(), key=lambda a: (a.frota, a.numero))

    def ativo(self, ativo_id: str) -> AtivoLido | None:
        return self._ativos.get(ativo_id)

    def dispositivos(self, ativo_id: str | None = None) -> list[DispositivoLido]:
        todos = self._dispositivos.values()
        if ativo_id is None:
            return sorted(todos, key=lambda d: d.nome)
        return sorted((d for d in todos if d.ativo_id == ativo_id), key=lambda d: d.nome)

    def dispositivo(self, chave: str) -> DispositivoLido | None:
        return self._dispositivos.get(chave)

    def resumo(self) -> dict:
        return dict(self._resumo)

    def achados(self) -> Achados:
        return self._achados

    def distribuicao(self, campo: str) -> dict[str, int]:
        """Contagem por ``papel``, ``zona`` (dispositivos) ou ``frota`` (ativos)."""
        if campo == "frota":
            return dict(Counter(a.frota for a in self._ativos.values()).most_common())
        if campo in {"papel", "zona"}:
            return dict(
                Counter(getattr(d, campo) for d in self._dispositivos.values()).most_common()
            )
        raise ValueError(f"campo desconhecido: {campo!r}")
