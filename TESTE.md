# Como pôr para rodar e testar

Todos os comandos abaixo foram executados nesta máquina durante o
desenvolvimento. Onde a saída aparece, é a saída real.

## 0. O que precisa existir

- **Python 3.11+**
- **PostgreSQL 16** com a extensão `btree_gist` (usada pela restrição que
  impede duas arestas iguais valendo ao mesmo tempo)

```bash
psql -c "CREATE DATABASE plataforma;"
psql -d plataforma -c "CREATE EXTENSION IF NOT EXISTS btree_gist;"
psql -c "CREATE DATABASE plataforma_teste;"      # só para a suíte
psql -d plataforma_teste -c "CREATE EXTENSION IF NOT EXISTS btree_gist;"
```

## 1. Instalar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
export PLATAFORMA_BANCO="postgresql+asyncpg://USUARIO@HOST:5432/plataforma"
```

## 2. Criar o esquema e semear o inventário

```bash
alembic upgrade head
semear-banco Inventário_VLAN_Mina_Final_1.xlsx
```

A semeadura imprime um relatório com uma **invariante de conservação**:
`dispositivos_criados + conflitos + duplicados == total_de_registros`. Se a
conta não fechar, alguma linha sumiu — e o programa diz qual.

## 3. Criar a primeira conta

Não há senha padrão embutida: sistema que nasce com `admin/admin` nasce
comprometido. **Enquanto não existir conta, a plataforma fica aberta** — é a
única forma de a porta poder ser aberta pela primeira vez. Depois disso, tudo
exige sessão, inclusive as leituras.

```bash
criar-admin ana --nome "Ana Souza" --zona corporativa
```

## 4. Subir a interface

```bash
# Onde vive o Prometheus que o seu exportador Rajant alimenta.
# Sem isto a interface sobe igual, mas os gráficos dizem que não sabem
# onde perguntar — em vez de desenharem uma linha vazia.
export PLATAFORMA_PROMETHEUS="http://SEU-PROMETHEUS:9090"

uvicorn plataforma.api:app --host 0.0.0.0 --port 8000
```

Abra `http://SEU-HOST:8000`. A tela pede login. A partir daí:

- **Árvore de ativos** à esquerda, com filtro e busca
- **Contorno de estado** nas peças: verde responde, vermelho não responde,
  âmbar incerto, tracejado não sondado
- **Clique num ativo** → a tela dele; **clique num componente** → a ficha do
  aparelho
- **Personalizar tela** (canto direito) → acrescentar, remover, renomear,
  reordenar e alargar cartões. Ao salvar, escolha o alcance: só esta máquina,
  toda a frota, ou o padrão.
- **Gráfico** na ficha do aparelho, com janelas de 30m a 7d. Embaixo dele fica
  a consulta que produziu o desenho — se o número parecer estranho, dá para
  conferir na fonte.
- **Relatórios** (aba própria): disponibilidade por frota e função de negócio,
  e cobertura da coleta. Botão de baixar CSV no canto. As ressalvas aparecem
  em faixa âmbar embaixo da tabela e vão junto no CSV.

## 5. Ligar a coleta

### 5.1 ICMP — o único sinal universal

Um coletor por zona. Ele **só enxerga alvos da própria zona**: é o que impede
um processo da rede corporativa sondar equipamento de OT porque alguém
cadastrou o IP errado.

```bash
# na rede corporativa
python -m plataforma.coletor --zona corporativa

# num processo rodando DENTRO da OT
python -m plataforma.coletor --zona ot_nivel3
```

Para um ciclo só, sem virar serviço: `--uma-vez --modulo icmp`.

> Os 26 CLPs (`ot_nivel2`) nunca são sondados. Não é configuração: o manifesto
> recusa a zona no carregamento.

### 5.2 Rajant — lê o Prometheus que o seu exportador já alimenta

Não fala com rádio nenhum. O seu exportador faz isso melhor.

```bash
export PLATAFORMA_PROMETHEUS="http://SEU-PROMETHEUS:9090"
python -m plataforma.coletor --zona corporativa --uma-vez --modulo rajant
```

Saída esperada (contra 145 rádios):

```
  nao_resolvidos   27
  arestas_abertas  554
  arestas_fechadas 0
  ciclos           {'rajant': 1}
```

`nao_resolvidos` são vizinhos que a malha vê e a planilha não tem — **achado de
inventário**, não erro. `arestas_*` é o grafo temporal se formando.

Rode duas vezes: a segunda deve abrir **0** arestas. Se abrir mais, a
conciliação não está idempotente e é defeito.

### 5.3 SNMP — switches, roteadores, UPS, câmeras

Primeiro a chave do cofre. **Guarde-a fora do banco**; sem ela nada abre.

```bash
export PLATAFORMA_CHAVE=$(criar-credencial chave)
```

Depois a credencial. A senha é pedida no terminal, sem eco — nunca como
argumento, porque `ps` mostra a linha de comando de qualquer processo.

```bash
criar-credencial guardar snmp-mina --tipo snmp_v2c --zona corporativa
# ou, preferível onde houver suporte:
criar-credencial guardar snmp-mina --tipo snmp_v3 --usuario monitor

criar-credencial listar      # nunca mostra segredo, nem mascarado
```

E então:

```bash
export PLATAFORMA_SNMP_CREDENCIAL=snmp-mina
python -m plataforma.coletor --zona corporativa --uma-vez --modulo snmp
```

### 5.4 Syslog e traps — o canal que escuta em vez de perguntar

Todo o resto é *pull*. Este é *push*: o equipamento manda quando quer.

```bash
# 514 exige privilégio; para testar sem root, use 5140 e aponte um
# equipamento (ou o `logger`) para essa porta.
receptor-syslog --zona corporativa --porta 5140

# um teste rápido, da própria máquina:
logger -n 127.0.0.1 -P 5140 -p local7.err "teste da plataforma"
```

Um receptor por zona, como o coletor — mas por outro motivo. Ele não alcança
nada; senta numa porta e recebe. Se um receptor da rede corporativa receber
mensagem cujo IP pertence a equipamento de OT, o evento é **marcado**
(`ip_de_outra_zona`) em vez de recusado: ou a rede está fazendo ponte onde não
deveria, ou alguém está forjando, e as duas coisas são achado.

> Syslog sobre UDP **não autentica nada**. Qualquer um na rede pode mandar uma
> mensagem dizendo ser qualquer IP. Por isso todo evento carrega `confianca`, e
> a tela diz isso em letras miúdas: um evento não é prova, é o que alguém disse.

## 6. Diagnóstico dirigido — perguntar a um equipamento agora

Coleta responde *"como ele esteve"*. Diagnóstico responde *"o que está
acontecendo com ele **neste** minuto"* — e é uma pessoa que dispara, num alvo,
por um motivo. Na ficha de qualquer dispositivo há o cartão **Diagnóstico**
com quatro sondas:

| Sonda | O que responde | Perigo |
|---|---|---|
| **Ping** | o endereço responde? com que latência e que perda? | leitura |
| **Caminho** | por onde o tráfego passa até ele (traceroute) | leitura |
| **Porta TCP** | *uma* porta está aberta? | leitura |
| **Leitura SNMP** | o que ele diz num OID específico | leitura |

Três coisas para reparar, porque nenhuma é acidental:

1. **A permissão é conferida na zona do equipamento, não na sua.** Abra um
   dispositivo `corporativa` e outro `ot_nivel3` com a mesma conta. Se a sua
   concessão é só na corporativa, no segundo os botões vêm desabilitados **e
   a tela diz por quê** — `Requer a permissão diagnosticar na zona ot_nivel3`.
   Botão cinza sem explicação é a pior forma de negar.
2. **`diagnosticar` é separado de `executar_acao`.** Quem pode perguntar "este
   endereço responde?" não passa por isso a poder reiniciar o rádio de um
   caminhão carregado. Leitor não diagnostica; campo, operador, engenheiro e
   administrador sim.
3. **Toda sonda vira duas linhas de banco:** uma em `auditoria` (quem, quando,
   em quê) e uma em `diagnostico` (o resultado inteiro). Confira:

```bash
psql -d plataforma -c "select em, acao, sujeito, detalhe->>'alvo' from auditoria
                       where acao like 'diagnostico%' order by em desc limit 5"
```

O que a plataforma **recusa** a fazer está escrito no topo de
`src/plataforma/diagnostico.py`, com o motivo de cada recusa: varredura de
faixa de portas (é reconhecimento, e já derrubou CLP em mina), teste de banda
(satura o enlace justamente enquanto se investiga) e captura de pacotes (lê
carga útil, e carga útil tem credencial). São ausências decididas, não
pendências.

## 7. Rodar a suíte

```bash
export PLATAFORMA_BANCO_TESTE="postgresql+asyncpg://USUARIO@HOST:5432/plataforma_teste"
pytest -q
ruff check .
```

São **299 testes**. Os que precisam de Postgres se pulam sozinhos se ele não
estiver disponível, em vez de falharem em massa.

O banco de teste é separado de propósito: as fixtures apagam o esquema entre
os casos, e uma suíte que destrói o banco de desenvolvimento é uma suíte que
as pessoas param de rodar.

## 8. Onde olhar quando algo não bate

| Sintoma | Onde a resposta está |
|---|---|
| "por que este ativo não tem métrica?" | aba **Coleta** — saúde de cada módulo, alvos e falhas |
| "o que a plataforma ainda não coleta?" | aba **Cobertura** — por família, com o motivo |
| "o cadastro está errado onde?" | aba **Cadastro** — conflitos, homônimos, divergências |
| "quem mudou isto?" | cartão **Histórico de alterações** na ficha |
| "quem sondou este rádio, e o que deu?" | tabela `diagnostico` e `auditoria` |

## Verificações que valem fazer no primeiro dia

1. **Correção humana sobrevive a re-semeadura.** Edite a função de negócio de
   um ativo pela tela, rode `semear-banco` de novo com a mesma planilha, e
   confira que a sua edição continua lá. É a garantia central do M0.
2. **Coletor mudo não derruba o parque.** Desligue a rede do coletor por um
   ciclo. Os equipamentos devem virar *"sem resposta · incerto"*, e **nenhuma**
   transição de queda deve ser gravada — falha total é indício de coletor
   isolado, não de mina parada.
3. **A malha não se desfaz quando o Prometheus cai.** Derrube o Prometheus e
   rode o módulo Rajant. `arestas_fechadas` deve ser **0**.
4. **O gráfico não inventa.** Ponha um cartão de gráfico numa métrica de SNMP
   (`iface_bytes_rx`, por exemplo). Ele deve **dizer que não tem série**, não
   desenhar uma linha reta a partir da última leitura.
5. **Uma tempestade de syslog não derruba a plataforma.** Mande 400 mensagens
   de uma vez para o receptor. Ele deve gravar até o limite (120/min por
   origem), descartar o resto e **gravar um evento dizendo quantas descartou**.
   Se a conta não fechar, o kernel perdeu datagramas — UDP faz isso, e o
   receptor não tem como saber; `netstat -su` no host mostra.
6. **O relatório não esconde o que não sabe.** Abra Relatórios →
   Disponibilidade. A faixa âmbar embaixo tem de dizer quantos equipamentos
   ficaram fora da média por falta de observação. Se ela sumir, desconfie: ou
   todo o parque foi sondado, ou alguém tirou a ressalva.
7. **A sonda roda no laço que o servidor usa.** Clique **Ping** num
   dispositivo pela tela — não só pelo teste. O ping usa socket cru, e
   `sock_sendto`/`sock_recvfrom` existem no asyncio padrão e **não** no uvloop,
   que é o laço do uvicorn: escrito com eles, o módulo passa em toda a suíte e
   estoura no primeiro clique. `tests/test_diagnostico.py` roda a sonda nos
   dois laços de propósito, mas o clique é a verificação que não mente.
8. **A tela não decide autorização — desenha a decisão do servidor.**
   `GET /api/v1/eu` devolve `permissoes` por zona, calculadas pela mesma função
   que a rota usa para aceitar ou recusar. Se um botão novo nascer cinza sem
   motivo, é sinal de que alguém voltou a escrever a matriz de papéis na tela;
   `tests/test_contas.py::TestPermissoesNaTela` existe para barrar isso.
