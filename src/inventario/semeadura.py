"""Semeadura do inventário: registros da planilha viram ativos, dispositivos e arestas.

O que sai daqui tem origem ``DERIVADO``. É de propósito: a área ADM é onde o
cadastro vive, e rodar a semeadura de novo **nunca** pode apagar o que uma
pessoa corrigiu à mão. Quem garante isso é a precedência de origem do módulo
``modelo``, não a boa vontade de quem chama.

O relatório devolvido junto é parte do produto, não um extra de depuração: ele
é a lista de trabalho humano que sobra depois que o código fez o que dava para
fazer sozinho.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from .derivacao import FUNCAO_DESCONHECIDA, derivar
from .modelo import (
    Aresta,
    Ativo,
    Dispositivo,
    Identificador,
    Origem,
    Papel,
    TipoAresta,
    TipoIdentificador,
    Valor,
    Zona,
)

_MAC = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


@dataclass(slots=True)
class Relatorio:
    """O que sobrou para uma pessoa fazer — e o que o cadastro tem de errado."""

    total_registros: int = 0
    ativos_criados: int = 0
    dispositivos_criados: int = 0
    arestas_criadas: int = 0
    fora_do_padrao: list[str] = field(default_factory=list)
    papel_desconhecido: list[str] = field(default_factory=list)
    funcao_desconhecida: list[str] = field(default_factory=list)
    divergencias: list[str] = field(default_factory=list)
    chaves_em_conflito: list[str] = field(default_factory=list)
    homonimos_desambiguados: list[str] = field(default_factory=list)
    linhas_duplicadas: list[str] = field(default_factory=list)
    ips_duplicados: dict[str, int] = field(default_factory=dict)
    sem_identificador_forte: list[str] = field(default_factory=list)
    zonas_a_confirmar: int = 0

    def resumo(self) -> dict[str, Any]:
        pct = (
            100 * len(self.funcao_desconhecida) / self.dispositivos_criados
            if self.dispositivos_criados
            else 0.0
        )
        return {
            "registros_lidos": self.total_registros,
            "ativos": self.ativos_criados,
            "dispositivos": self.dispositivos_criados,
            "arestas": self.arestas_criadas,
            "fora_do_padrao": len(self.fora_do_padrao),
            "papel_desconhecido": len(self.papel_desconhecido),
            "funcao_desconhecida": len(self.funcao_desconhecida),
            "funcao_desconhecida_pct": round(pct, 1),
            "divergencias_nome_x_endereco": len(self.divergencias),
            "chaves_em_conflito": len(self.chaves_em_conflito),
            "homonimos_desambiguados": len(self.homonimos_desambiguados),
            "linhas_duplicadas": len(self.linhas_duplicadas),
            "ips_duplicados": len(self.ips_duplicados),
            "sem_identificador_forte": len(self.sem_identificador_forte),
            "zonas_a_confirmar": self.zonas_a_confirmar,
        }


@dataclass(slots=True)
class Semeadura:
    ativos: dict[str, Ativo]
    dispositivos: dict[str, Dispositivo]
    arestas: list[Aresta]
    relatorio: Relatorio


def _normalizar_mac(bruto: str | None) -> str | None:
    if not bruto:
        return None
    texto = str(bruto).strip().upper().replace("-", ":")
    return texto if _MAC.match(texto) else None


def _zona_provisoria(papel: Papel, classe: str | None) -> tuple[Zona, bool]:
    """Zona derivada, sempre conservadora.

    Errar para o lado restritivo é barato: no máximo um módulo deixa de coletar
    e alguém confirma. Errar para o lado permissivo pode colocar tráfego ativo
    onde não devia. Por isso o CLP cai direto numa zona proibida a módulos, e
    tudo que a planilha marca como OT fica no nível 3 até alguém do ADM
    confirmar.
    """
    if papel is Papel.PLC:
        return Zona.OT_NIVEL2, True
    if (classe or "").strip().upper() == "OT":
        return Zona.OT_NIVEL3, True
    return Zona.CORPORATIVA, False


def _identificadores(registro: dict[str, Any], nome_canonico: str) -> list[Identificador]:
    ids: list[Identificador] = []
    if mac := _normalizar_mac(registro.get("Mac")):
        ids.append(Identificador(tipo=TipoIdentificador.MAC, valor=mac))
    if serie := (registro.get("Serial") or "").strip():
        ids.append(Identificador(tipo=TipoIdentificador.SERIE, valor=serie))
    ids.append(Identificador(tipo=TipoIdentificador.NOME, valor=nome_canonico))
    if ip := (registro.get("IP") or "").strip():
        ids.append(Identificador(tipo=TipoIdentificador.IP, valor=ip))
    if asset := (registro.get("Asset Id") or "").strip():
        ids.append(Identificador(tipo=TipoIdentificador.ASSET_ID, valor=asset))
    return ids


def semear(registros: list[dict[str, Any]]) -> Semeadura:
    """Converte registros crus em inventário derivado."""
    rel = Relatorio(total_registros=len(registros))
    ativos: dict[str, Ativo] = {}
    dispositivos: dict[str, Dispositivo] = {}
    arestas: list[Aresta] = []
    membros: dict[str, list[str]] = defaultdict(list)

    contagem_ip = Counter(
        ip for r in registros if (ip := (r.get("IP") or "").strip())
    )
    rel.ips_duplicados = {ip: n for ip, n in contagem_ip.items() if n > 1}

    for registro in registros:
        d = derivar(registro.get("Name") or "", registro.get("IP"))

        if not d.aderente_ao_padrao:
            rel.fora_do_padrao.append(d.nome_bruto)
        if d.papel is Papel.DESCONHECIDO:
            rel.papel_desconhecido.append(d.nome_bruto)
        rel.divergencias.extend(f"{d.nome_bruto}: {m}" for m in d.divergencias)

        ids = _identificadores(registro, d.nome_canonico)
        zona, confirmar = _zona_provisoria(d.papel, registro.get("Class"))
        rel.zonas_a_confirmar += int(confirmar)

        provisorio = Dispositivo(
            chave="",
            nome_bruto=d.nome_bruto,
            nome_canonico=d.nome_canonico,
            papel=d.papel,
            zona=zona,
            identificadores=tuple(ids),
            ativo_id=d.ativo_id,
        )
        identidade = provisorio.identidade()
        if identidade is None:
            rel.sem_identificador_forte.append(d.nome_bruto)
            continue
        tipo, valor = identidade
        if tipo is TipoIdentificador.NOME:
            rel.sem_identificador_forte.append(d.nome_bruto)
        chave = f"{tipo.value}:{valor}"

        if (existente := dispositivos.get(chave)) is not None:
            # Colisão de chave. Nenhum caminho aqui descarta um registro em
            # silêncio: perder linha sem avisar é a pior categoria de falha,
            # porque o inventário fica menor e ninguém percebe.
            ip_novo = (registro.get("IP") or "").strip()
            ip_antigo = next(
                (
                    i.valor
                    for i in existente.identificadores
                    if i.tipo is TipoIdentificador.IP
                ),
                "",
            )
            if existente.nome_canonico != d.nome_canonico:
                # Mesmo identificador forte para nomes diferentes: conflito real,
                # que só o ADM resolve. Ex.: MACs repetidos entre PA-5503 e PA-5504.
                rel.chaves_em_conflito.append(
                    f"{chave} -> {existente.nome_bruto!r} e {d.nome_bruto!r}"
                )
                continue
            if ip_novo and ip_novo != ip_antigo:
                # Mesmo nome, endereços diferentes: são **dois** dispositivos.
                # Ex.: TT-3503-GPS-MM2 existe em .101.167 e em .102.167.
                chave = f"{chave}@{ip_novo}"
                rel.homonimos_desambiguados.append(
                    f"{d.nome_bruto!r}: {ip_antigo or '(sem ip)'} e {ip_novo}"
                )
            else:
                rel.linhas_duplicadas.append(f"{d.nome_bruto!r} em {ip_novo or '(sem ip)'}")
                continue

        dispositivos[chave] = provisorio.model_copy(update={"chave": chave})

        if d.ativo_id:
            membros[d.ativo_id].append(chave)
            if d.ativo_id not in ativos:
                if d.funcao_negocio == FUNCAO_DESCONHECIDA:
                    rel.funcao_desconhecida.append(d.ativo_id)
                ativos[d.ativo_id] = Ativo(
                    ativo_id=d.ativo_id,
                    frota=d.frota or "",
                    numero=d.numero or "",
                    funcao_negocio=Valor(valor=d.funcao_negocio, origem=Origem.DERIVADO),
                )
            arestas.append(
                Aresta(
                    origem_chave=chave,
                    destino_chave=d.ativo_id,
                    tipo=TipoAresta.EMBARCADO_EM,
                )
            )
        else:
            rel.funcao_desconhecida.append(d.nome_bruto)

    for ativo_id, chaves in membros.items():
        ativos[ativo_id] = ativos[ativo_id].model_copy(
            update={"dispositivos": tuple(sorted(chaves))}
        )

    rel.ativos_criados = len(ativos)
    rel.dispositivos_criados = len(dispositivos)
    rel.arestas_criadas = len(arestas)
    return Semeadura(ativos, dispositivos, arestas, rel)
