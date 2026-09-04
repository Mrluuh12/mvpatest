"""Eventos: o que o equipamento conta por conta própria.

É o **terceiro canal**, e ele é diferente dos outros dois em natureza, não em
grau. Métrica responde *quanto*; relação responde *quem com quem*; evento
responde *o que aconteceu, segundo o próprio equipamento*. Os dois primeiros
a plataforma vai buscar; este chega sem ser chamado.

Isso muda três coisas de uma vez
--------------------------------

**A confiança é outra.** Syslog sobre UDP não tem autenticação nenhuma:
qualquer um na rede pode mandar uma mensagem dizendo ser qualquer IP. A
plataforma atribui pelo IP de origem porque não há alternativa, e **registra
que foi assim** — `confianca="ip_de_origem"`. Um evento não é prova de nada;
é o que alguém disse. Trap com SNMPv3 é outra história, e por isso ganha
confiança diferente.

**O volume não é nosso.** Uma porta oscilando manda milhares de mensagens por
minuto, e nós não escolhemos quantas. Sem limite por origem, um equipamento
defeituoso enche o disco e leva junto o que importa — por isso o receptor
conta e descarta, e o descarte vira um evento próprio em vez de silêncio.

**A hora é do remetente.** O carimbo dentro da mensagem vem do relógio do
equipamento, que pode estar errado em horas. Guardamos os dois: `em` (o que
ele disse) e `recebido_em` (o que nós vimos). Quando divergem muito, é achado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

#: Severidades do syslog (RFC 5424, tabela 2). O nome importa mais que o
#: número: "3" não diz nada numa tela, "erro" diz.
SEVERIDADES = (
    "emergencia", "alerta", "critico", "erro",
    "aviso", "atencao", "informativo", "depuracao",
)

#: Facilidades (RFC 5424, tabela 1). Guardadas porque separam "o switch
#: falando de si" de "o switch repassando log de outro serviço".
FACILIDADES = (
    "kernel", "usuario", "correio", "daemon", "seguranca", "syslog",
    "impressora", "noticias", "uucp", "relogio", "seguranca2", "ftp",
    "ntp", "auditoria", "alerta", "relogio2",
    "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7",
)

#: `<PRI>` é obrigatório nos dois formatos e é por onde se começa.
_PRI = re.compile(rb"^<(\d{1,3})>")
#: RFC 5424: `<PRI>1 TIMESTAMP HOST APP PROCID MSGID ...`
_RFC5424 = re.compile(
    rb"^<\d{1,3}>1 (\S+) (\S+) (\S+) (\S+) (\S+) (?:\[.*?\]|-)\s*(.*)$", re.S
)
#: Quando o 5424 vem levemente fora do padrão — falta um campo, sobra um
#: espaço —, ainda dá para salvar o que importa: a versão, o carimbo e o
#: remetente. Perder a hora em silêncio por causa de um campo a menos seria
#: descartar o dado bom junto com a formatação ruim.
_RFC5424_FROUXO = re.compile(rb"^<\d{1,3}>1 (\S+) (\S+)\s*(.*)$", re.S)
#: RFC 3164: `<PRI>MMM DD HH:MM:SS HOST TAG: MSG` — e o número de sequência
#: que a Cisco enfia na frente, que quebra parser ingênuo.
_RFC3164 = re.compile(
    rb"^<\d{1,3}>(?:\d+: )?"
    rb"([A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2})?\s*"
    rb"(\S+)?\s*(.*)$",
    re.S,
)
_MESES = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


@dataclass
class Evento:
    """Uma coisa que um equipamento disse."""

    origem_ip: str
    tipo: str = "syslog"  # syslog | trap
    severidade: str = "informativo"
    facilidade: str = "local0"
    mensagem: str = ""
    #: Nome que o remetente deu a si mesmo. Não é identidade: é o que ele
    #: alegou, e serve de desempate quando o IP não resolve.
    remetente: str = ""
    #: O que ele disse que era a hora. `None` quando não mandou ou não deu
    #: para ler — e nesse caso `recebido_em` é tudo que se tem.
    em: datetime | None = None
    recebido_em: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: Varbinds do trap, ou campos estruturados do syslog.
    atributos: dict = field(default_factory=dict)
    #: Como a origem foi estabelecida. Nunca é "certeza".
    confianca: str = "ip_de_origem"

    @property
    def relogio_divergente_s(self) -> float | None:
        """Quanto o relógio do remetente difere do nosso, em segundos.

        Vale olhar: um equipamento com relógio errado em horas produz um
        histórico que não casa com nenhum outro, e o defeito passa despercebido
        até alguém tentar correlacionar.
        """
        if self.em is None:
            return None
        return abs((self.recebido_em - self.em).total_seconds())


def _hora_3164(bruto: bytes, referencia: datetime) -> datetime | None:
    """`MMM DD HH:MM:SS` — sem ano, que é o defeito do formato de 1984.

    O ano vem da referência. Numa virada de ano, uma mensagem de 31/12 chegando
    em 01/01 seria datada com o ano novo; por isso, se a data resultante ficar
    no futuro, recua-se um ano.
    """
    try:
        texto = bruto.decode("ascii")
        mes = _MESES[texto[:3]]
        dia = int(texto[4:6])
        hora, minuto, segundo = (int(x) for x in texto[7:].split(":"))
    except (ValueError, KeyError, IndexError):
        return None
    try:
        quando = datetime(referencia.year, mes, dia, hora, minuto, segundo, tzinfo=UTC)
    except ValueError:
        return None
    if quando > referencia and (quando - referencia).days > 1:
        quando = quando.replace(year=referencia.year - 1)
    return quando


def _hora_5424(bruto: bytes) -> datetime | None:
    texto = bruto.decode("ascii", "replace")
    if texto == "-":
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def analisar_syslog(
    dados: bytes, origem_ip: str, recebido_em: datetime | None = None
) -> Evento:
    """Lê uma mensagem de syslog, nos dois formatos que existem no campo.

    Nunca levanta exceção. Mensagem que não casa com formato nenhum vira
    evento mesmo assim, com o texto inteiro: equipamento que fala errado ainda
    está falando, e descartar em silêncio é perder o aviso que interessa
    justamente no dia ruim.
    """
    agora = recebido_em or datetime.now(UTC)
    e = Evento(origem_ip=origem_ip, recebido_em=agora)

    casa_pri = _PRI.match(dados)
    if casa_pri:
        pri = int(casa_pri.group(1))
        e.severidade = SEVERIDADES[pri & 0x07]
        facilidade = pri >> 3
        e.facilidade = (
            FACILIDADES[facilidade] if facilidade < len(FACILIDADES) else f"?{facilidade}"
        )
    else:
        # Sem PRI não é syslog conforme, mas chega assim mesmo. Guarda-se o
        # texto e diz-se que o formato era desconhecido.
        e.mensagem = dados.decode("utf-8", "replace").strip()
        e.atributos["formato"] = "sem_pri"
        return e

    if (m := _RFC5424.match(dados)) is not None:
        e.em = _hora_5424(m.group(1))
        e.remetente = m.group(2).decode("utf-8", "replace").strip("-")
        e.atributos["aplicacao"] = m.group(3).decode("utf-8", "replace").strip("-")
        e.mensagem = m.group(6).decode("utf-8", "replace").strip()
        e.atributos["formato"] = "rfc5424"
        return e

    if (m := _RFC5424_FROUXO.match(dados)) is not None:
        quando = _hora_5424(m.group(1))
        if quando is not None:
            e.em = quando
            e.remetente = m.group(2).decode("utf-8", "replace").strip("-")
            e.mensagem = m.group(3).decode("utf-8", "replace").strip()
            e.atributos["formato"] = "rfc5424_torto"
            return e

    m = _RFC3164.match(dados)
    if m is not None:
        if m.group(1):
            e.em = _hora_3164(m.group(1), agora)
        e.remetente = (m.group(2) or b"").decode("utf-8", "replace")
        e.mensagem = m.group(3).decode("utf-8", "replace").strip()
        e.atributos["formato"] = "rfc3164"
        # Sem carimbo de hora, o "hostname" que o regex pegou era a primeira
        # palavra da mensagem — devolvê-la ao texto evita perder conteúdo.
        if not m.group(1) and e.remetente:
            e.mensagem = f"{e.remetente} {e.mensagem}".strip()
            e.remetente = ""
        return e

    e.mensagem = dados.decode("utf-8", "replace").strip()
    e.atributos["formato"] = "desconhecido"
    return e


#: Traps que a plataforma entende sem MIB nenhuma — os genéricos do RFC 1157,
#: que todo equipamento manda e que dizem o que mais importa.
TRAPS_GENERICOS = {
    "1.3.6.1.6.3.1.1.5.1": ("coldStart", "atencao", "reiniciou a frio"),
    "1.3.6.1.6.3.1.1.5.2": ("warmStart", "informativo", "reiniciou a quente"),
    "1.3.6.1.6.3.1.1.5.3": ("linkDown", "erro", "porta caiu"),
    "1.3.6.1.6.3.1.1.5.4": ("linkUp", "atencao", "porta subiu"),
    "1.3.6.1.6.3.1.1.5.5": ("authenticationFailure", "aviso",
                            "tentativa de acesso recusada"),
}


def descrever_trap(oid: str, varbinds: dict) -> tuple[str, str, str]:
    """Nome, severidade e frase em português para um trap.

    Trap desconhecido não vira lixo: fica com o OID como nome e severidade
    informativa. Um OID que ninguém traduziu ainda é melhor que um evento
    descartado, porque é assim que se descobre o que traduzir.
    """
    if oid in TRAPS_GENERICOS:
        nome, severidade, frase = TRAPS_GENERICOS[oid]
        if porta := varbinds.get("ifIndex") or varbinds.get("ifDescr"):
            frase = f"{frase} ({porta})"
        return nome, severidade, frase
    return oid, "informativo", f"trap não traduzido: {oid}"


__all__ = [
    "FACILIDADES",
    "SEVERIDADES",
    "TRAPS_GENERICOS",
    "Evento",
    "analisar_syslog",
    "descrever_trap",
]
