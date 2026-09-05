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

## 6. Ligar a exportação para o Prometheus

Sem este passo, das 61 métricas canônicas só 19 podem virar gráfico — as que
o seu exportador Rajant já entrega ao Prometheus. Tráfego de porta de switch,
latência e perda **existem no banco** e não chegam a quem guarda série.

A plataforma não passa a guardar série: ela passa a **entregar** o número.

### 6.1 Um segredo, porque nega por omissão

```bash
export PLATAFORMA_METRICAS_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
echo "$PLATAFORMA_METRICAS_TOKEN"   # anote: vai no prometheus.yml
```

Sem essa variável a rota `/metrics` responde **503 e diz o que fazer** — não
serve nada em claro por engano. Reinicie a API com ela definida.

### 6.2 O job no `prometheus.yml`

```yaml
scrape_configs:
  - job_name: plataforma
    scrape_interval: 30s
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials: "COLE_AQUI_O_TOKEN"
    static_configs:
      - targets: ["IP_DA_PLATAFORMA:8077"]
```

Recarregue o Prometheus e confira em **Status → Targets** que `plataforma`
está `UP`.

### 6.3 Conferir que chegou

```bash
curl -s -H "Authorization: Bearer $PLATAFORMA_METRICAS_TOKEN" \
     http://127.0.0.1:8077/metrics | grep '^plataforma_exportador'
```

Quatro números que a exportação publica sobre si mesma:

| Série | O que diz |
|---|---|
| `exportador_amostras` | quantas foram publicadas |
| `exportador_omitidas_velhas` | quantas ficaram de fora por validade vencida |
| `exportador_omitidas_externas` | quantas não são republicadas porque a série já vive noutro Prometheus |
| `exportador_duracao_s` | quanto custou montar a resposta |

**`amostras` em zero com `omitidas_velhas` alto quer dizer que o coletor
parou**, não que o parque sumiu. É a leitura mais útil desse painel.

Depois, no Prometheus, a pergunta que o dicionário canônico sempre prometeu:

```promql
sum by (funcao_negocio) (rate(plataforma_iface_bytes_rx[5m]))
```

### 6.4 O gráfico

Na ficha de um switch, **Personalizar tela → Acrescentar cartão → Gráfico**, e
escolha `iface_bytes_rx`. O cartão tem um campo **Porta**: vazio soma todas as
portas (e diz embaixo que somou), ou escreva `Gi0/1` para uma só.

`iface_status_oper` sem porta escolhida **recusa** e explica: somar códigos de
estado de 48 portas não significaria nada.

## 7. Relatórios

Aba **Relatórios**. O menu à esquerda agrupa quinze relatórios por pergunta;
cada um traz seus próprios parâmetros e sua própria janela padrão.

### 7.1 Os quatro que valem abrir primeiro

| Relatório | Por que este |
|---|---|
| **Quedas, uma a uma** | cada queda com hora de início, de volta e duração — o que se abre depois de um turno ruim |
| **Enlaces: qualidade** | a coluna **assimetria** compara o SNR nos dois sentidos; 6 dB ou mais costuma ser antena torta |
| **Saturação prevista** | dias até o limiar, com R² e confiança ao lado — precisa do Prometheus ligado |
| **Contradições** | o que a planilha diz e não pode ser tudo verdade ao mesmo tempo |

### 7.2 Três formatos

**CSV** e **Imprimir / PDF** ficam acima da tabela. O CSV traz título, período,
parâmetros e ressalvas em comentário, com os valores crus — dá para calcular em
cima. A impressão abre uma folha pronta: `Ctrl+P` → *Salvar como PDF*.

Não existe botão de PDF no servidor de propósito: o navegador já faz isso, e
com as fontes de quem imprime.

### 7.3 O que conferir

1. **Equipamento-hora.** Em *Dia a dia*, a coluna "Fora do ar" está em
   `equip·h`, não em dias. Num dia de 24 h com 600 equipamentos o máximo
   possível é 14.400 — se aparecer "600 d", a unidade voltou a estar errada.
2. **Previsão que não se sustenta não vira manchete.** Abra *Saturação
   prevista* logo depois de ligar a exportação. Com pouca história, a frase de
   resumo deve dizer que **nenhuma projeção se sustenta** — e não anunciar
   "satura em 0,7 dias". É a diferença entre a ferramenta ajudar e induzir.
3. **Ressalva no CSV.** Baixe qualquer um e abra num editor de texto: as linhas
   `# ressalva:` têm de estar lá. Se sumirem, o número passa a viajar sozinho.

## 8. Seção Rede

Aba **Rede**. Quatro vistas sobre a malha: **Mapa**, **Enlaces**, **Rádios** e
**Ponto a ponto**.

### 8.1 O que conferir no mapa

1. **Escala.** A barra no canto inferior mostra a distância real. Se a
   proporção parecer esticada na horizontal, a correção de longitude quebrou.
2. **Posição vencida.** Pare o coletor por quinze minutos. Os rádios passam a
   ser desenhados em contorno e o rodapé conta quantos. Posição velha desenhada
   como atual é a pior mentira que um mapa pode contar.
3. **Distância contra sinal.** Baixe `/api/v1/rede/enlaces` e correlacione
   `distancia_m` com `rssi_pior_dbm`. **Tem de haver correlação negativa** —
   sinal cai com distância. Perto de zero significa que o pareamento de
   identidade está juntando rádios errados, ou que a origem do GPS não é real.

### 8.2 As distribuições abaixo do mapa

Quatro gráficos: sinal dos enlaces, vizinhos por rádio, saltos até a
infraestrutura e enlaces por classe. Dois números para olhar primeiro:

- **quantos rádios têm um vizinho só** — vizinho único é caminho único
- **quantos ficaram "sem caminho"** até um rádio fixo

E no primeiro indicador, a curva de rádios no ar das últimas 24 h. Se ela
discordar do número ao lado, confira se há rádios em estado *incerto*: a
plataforma não grava transição de queda quando a coleta falha inteira, e a
cauda tracejada é justamente isso.

### 8.3 Enlaces e ponto a ponto

A tabela de enlaces traz **SNR ida** e **SNR volta** em colunas separadas, e a
diferença entre elas em **Δ dB**. Seis ou mais costuma ser antena desalinhada.

Em **Ponto a ponto** ficam só os enlaces entre infraestrutura fixa — a espinha
dorsal. Um cartão por enlace, com os dois sentidos abertos.

### 8.4 Os dois números do rodapé que importam

| Rodapé | O que quer dizer |
|---|---|
| *"N enlaces apontam para um vizinho que o cadastro não conhece"* | o rádio vê alguém que a planilha não tem |
| *"N enlaces com um sentido só medido"* | normal em pouca quantidade; em volume, a resolução de identidade está perdendo o par de volta |

## 9. Diagnóstico dirigido — perguntar a um equipamento agora

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

## 10. Rodar a suíte

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

## 11. Onde olhar quando algo não bate

| Sintoma | Onde a resposta está |
|---|---|
| "por que este ativo não tem métrica?" | aba **Coleta** — saúde de cada módulo, alvos e falhas |
| "o que a plataforma ainda não coleta?" | aba **Cobertura** — por família, com o motivo |
| "por que este gráfico está vazio?" | a linha de procedência embaixo dele traz o PromQL exato |
| "o cadastro está errado onde?" | aba **Cadastro** — conflitos, homônimos, divergências |
| "quem mudou isto?" | cartão **Histórico de alterações** na ficha |
| "quem sondou este rádio, e o que deu?" | Relatórios → **Diagnósticos** |
| "quanto a frota ficou parada mês passado?" | Relatórios → **Por frota**, janela 30d |
| "esta porta vai saturar?" | Relatórios → **Saturação prevista** |
| "qual enlace da malha está ruim?" | Rede → **Mapa**, filtro *só problemas* |
| "a espinha dorsal está boa?" | Rede → **Ponto a ponto** |

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
9. **Coletor parado não vira linha reta.** Pare o coletor e espere cinco
   minutos. As séries `plataforma_*` param de ser publicadas, o Prometheus as
   marca obsoletas, e o gráfico **termina** em vez de continuar reto no último
   valor. Verificado contra um Prometheus real: o histórico fica, o número
   velho não. Se você vir uma reta longa depois de derrubar o coletor,
   `PLATAFORMA_METRICAS_VALIDADE_S` está alto demais para a cadência da sua
   coleta.
10. **Relatório não esconde a própria fraqueza.** Em *Saturação prevista*, a
    coluna Confiança tem de dizer "história curta" enquanto a série for mais
    nova que 24 h. Uma previsão que se apresenta como confiável sobre seis
    horas de dado é pior que previsão nenhuma.
