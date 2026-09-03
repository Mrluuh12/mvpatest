# Plataforma TI + OT — marcos M0 e M1

Primeira entrega da plataforma de observabilidade TI + OT: transformar a
planilha de inventário da mina em **ativos, dispositivos e arestas** — com
identidade resolvida, dialetos normalizados e função de negócio derivada.

O documento de conhecimento tratava o mapeamento de função de negócio como
*"trabalho de levantamento, não de código"*. Rodando contra os 723 ativos
reais, a derivação automática resolve **93,8%** deles. O que sobra para uma
pessoa são 44 casos, não 723.

## O que isto faz

```
planilha .xlsx  ──►  normalização  ──►  derivação  ──►  identidade  ──►  JSON
                     de dialetos        de ativo         resolvida        + relatório
```

1. **Normaliza dialetos.** `CLP` e `PLC` são a mesma coisa. `RADIO RJT`,
   `RADIO`, `RADIO-RAJANT` e `RAJANT` também. O que não é reconhecido vira
   `desconhecido` **e é relatado** — nunca descartado em silêncio.
2. **Deriva o ativo.** O nome segue `<FROTA>-<NÚMERO>-<PAPEL>`, o terceiro
   octeto do IP diz o papel e o quarto diz o veículo. Os oito dispositivos de
   um caminhão convergem sozinhos para um `Ativo`.
3. **Resolve identidade** por lista de identificadores, com precedência
   MAC → série → nome. O IP fica fora de propósito: 26 se repetem no cadastro
   real, e identificador que se repete não identifica.
4. **Gera as arestas** `embarcado_em` — as que juntam rádio, pneu, CLP e GPS
   num caminhão só.
5. **Relata o que não conseguiu.** O relatório é parte do produto: é a lista
   de trabalho humano que sobra depois que o código fez o que dava.

## Resultado contra o inventário real

```
registros_lidos                  723
ativos                           145
dispositivos                     708
arestas                          672
funcao_desconhecida               44   (6,2%)
fora_do_padrao                    36
papel_desconhecido                54
divergencias_nome_x_endereco       3
chaves_em_conflito                15
homonimos_desambiguados           14
ips_duplicados                    26
sem_identificador_forte          343
zonas_a_confirmar                329
```

**708 dispositivos + 15 conflitos = 723 registros.** A conservação é testada:
nada entra sem sair criado ou explicado.

### Achados no cadastro

Coisas que o código encontrou e que valem correção na origem:

- **15 MACs repetidos entre veículos diferentes** — `PA-5503`, `PA-5504` e
  `PA-5505` compartilham os MACs de conversor CAN, GPS, PTX e rádio. Tem cara
  de linha copiada e renomeada.
- **14 homônimos legítimos** — `TT-3503-GPS-MM2` existe em `.101.167` e em
  `.102.167`. São dois aparelhos com o mesmo nome, e fundi-los perderia um.
- **3 divergências entre nome e endereço** — o nome diz uma coisa, a sub-rede
  diz outra. Discordância costuma ser erro de digitação.
- **343 sem identificador forte** — 47% do cadastro não tem MAC, então a
  identidade cai para o nome, que é mais frágil.

## Uso

```bash
pip install -e .
semear-inventario inventario.xlsx --saida inventario.json --detalhar 20
```

O JSON vai para `--saida` (ou stdout); o relatório vai sempre para stderr, para
que os dois possam ser separados num pipeline.

## Decisões que o código materializa

| Decisão | Onde |
|---|---|
| Identidade por lista de identificadores, nunca por IP | `modelo.PRECEDENCIA_IDENTIDADE` |
| Cadastro humano não é sobrescrito por derivação | `modelo.conciliar` |
| Equipamento vence cadastro em campo de observação | `modelo.Natureza` |
| Papel de usuário vale **por zona**, não pela plataforma | `modelo.Concessao` |
| CLP nasce em zona proibida a módulos | `semeadura._zona_provisoria` |
| Recusa é visível, descarte silencioso é proibido | `Relatorio` |

### Precedência de origem

Todo valor carrega de onde veio, e a precedência depende da natureza do campo:

- **Intenção** (função de negócio, criticidade): `cadastrado` > `descoberto` > `derivado`.
  Rodar a semeadura de novo nunca apaga o que alguém corrigiu no ADM.
- **Observação** (firmware, modelo, MAC): `descoberto` > `cadastrado` > `derivado`.
  Uma pessoa digitando a versão de firmware é pior evidência do que lê-la do aparelho.

A divergência entre cadastrado e descoberto não é erro a esconder — é achado a relatar.

### Controle de acesso

O administrador é o topo: cadastro de ativos, módulos, credenciais, usuários e
dicionário vivem nele. Os demais papéis são recortes deliberados. A parte que
faz o modelo servir numa mina é a **composição papel × zona**: a mesma pessoa
pode ser operadora na zona corporativa e apenas leitora em OT.

## Desenvolvimento

```bash
pip install -e . && pip install pytest ruff
python -m pytest -q      # 118 testes
python -m ruff check src tests
```

Os testes são escritos para achar bug, não para passar: cada caso reproduz uma
inconsistência que existe de verdade nos 723 ativos. Um deles é um teste de
conservação — a rede de segurança contra o inventário encolher sem ninguém notar.

## Persistência

O inventário vive em PostgreSQL 16. Duas escolhas de esquema carregam o peso do
resto do projeto:

**Uma linha por origem, não um valor vencedor.** A tabela `campo` guarda o valor
derivado, o descoberto e o cadastrado *lado a lado*, cada um na sua linha, e o
vencedor é calculado na leitura. Isso transforma a promessa *"rodar a semeadura
de novo não apaga correção humana"* de disciplina em **impossibilidade
estrutural**: a origem faz parte da chave primária, então a escrita derivada não
tem como alcançar a linha cadastrada. De quebra, a divergência entre cadastro e
realidade deixa de ser um log passageiro e vira uma consulta.

**A aresta tem validade temporal, e o banco impede sobreposição.** Um
`EXCLUDE … USING gist` garante que a mesma aresta não exista duas vezes no mesmo
instante. Não é validação que alguém pode esquecer de chamar: é recusa do banco.
É a fundação do grafo temporal do marco M2 — *"quem era vizinho do nó X às
14h37"* já tem onde ser respondido.

```bash
export PLATAFORMA_BANCO="postgresql+asyncpg://usuario@host:5432/plataforma"
alembic upgrade head
semear-banco inventario.xlsx
```

O comando verifica a conservação ao final e **sai com código 1 se ela quebrar** —
inventário que encolhe sem ninguém notar é a pior falha possível aqui.

## API de leitura

Camada inicial sobre o inventário semeado. O repositório é um `Protocol`, não
uma classe concreta: hoje é em memória, amanhã é Postgres, e a troca fica local.

```bash
PLATAFORMA_INVENTARIO=inventario.json uvicorn plataforma.api:app --reload
```

Rotas: `/api/v1/saude`, `/sinais`, `/resumo`, `/achados`, `/ativos`,
`/ativos/{id}`, `/dispositivos`, `/distribuicao/{campo}`.

A rota `/api/v1/sinais` existe por um motivo: cada família do dicionário declara
se tem coletor e, quando não tem, **por quê**. É o que permite a interface dizer
*"aguarda o coletor ICMP"* em vez de mostrar um traço mudo — ou, pior, um zero.

## Coleta (marco M1)

Um contrato de módulo, um agendador e o primeiro coletor. O ICMP entra
primeiro porque é o único sinal universal deste parque: **11 de 723 ativos
falam SNMP; todos os 723 respondem — ou deixam de responder — a ICMP**, e não
exige credencial nenhuma.

```bash
export PLATAFORMA_BANCO="postgresql+asyncpg://usuario@host:5432/plataforma"
python -m plataforma.coletor --uma-vez        # um ciclo e sai
python -m plataforma.coletor                  # serve continuamente
```

Contra o parque real: **367 dispositivos sondados em 3 s**. Os outros 341
ficam de fora porque estão em zona OT — o filtro de zona é o que impede um
coletor corporativo alcançar controlador por engano.

### Quatro decisões que este marco materializa

**Zona do coletor × zona do módulo.** O manifesto declara onde pode operar; o
processo declara onde está. Não batendo, o módulo não carrega — e a recusa
acontece antes de qualquer pacote sair. Declarar os níveis 0 a 2 do Purdue é
inválido em *qualquer* manifesto: não é configuração, é impossibilidade.

**Ausência não vira zero.** Quem não responde recebe `ativo_alcancavel = 0` e
**nenhuma latência**. Zero afirmaria resposta instantânea de um equipamento
mudo — número plausível e errado, que reaparece meses depois num relatório de
disponibilidade indefensável. No banco, a coluna fica nula.

**Transições, não amostras.** O estado corrente é sobrescrito; o histórico
recebe uma linha só quando o estado muda. Sondando 708 dispositivos por
minuto, a diferença é entre ~1 milhão de linhas por dia e algumas dezenas — e
a disponibilidade de qualquer janela continua calculável.

**Falha total é suspeita de isolamento.** Se *todos* os alvos falham de uma
vez, a explicação mais provável não é que o parque caiu: é que o coletor ficou
sem rede. Nesse caso nenhuma transição é registrada e o estado fica marcado
como incerto. Sem isso, uma única falha do coletor fabricaria 367 incidentes
falsos.

### Auto-observação obrigatória

O agendador — não o módulo — emite as cinco séries da família `modulo`,
inclusive e principalmente quando a coleta falha. Módulo que morre em silêncio
faz as métricas simplesmente pararem, e ausência de dado ruim é
indistinguível de ausência de problema.

O carimbo `ultima_coleta_ok` só avança quando houve sucesso. Repare na
distinção que ele preserva: numa coleta com `alvos_falha = 367` e carimbo
presente, o módulo funcionou e os alvos é que não responderam — *"perguntei e
está ruim"*, não *"não consegui perguntar"*.

## Interface (marco M1)

O shell da aplicação e a lente do ativo, servidos pela própria API.

```bash
export PLATAFORMA_BANCO="postgresql+asyncpg://usuario@host:5432/plataforma"
uvicorn plataforma.api:app --host 0.0.0.0 --port 8000
```

Sem etapa de build, de propósito: a barreira para alguém da equipe abrir e
corrigir precisa ser baixa. Migrar para um framework depois é decisão local.

A regra que atravessa a interface: **a tela nunca inventa número.** Um
dispositivo pode estar em três situações, e as três são visualmente distintas:

| Situação | O que significa |
|---|---|
| `responde` | sondado, respondeu |
| `sem resposta` | sondado, não respondeu |
| `não sondado` | fora da zona do coletor — nem foi perguntado |

Confundir as duas últimas é o que transforma um relatório de disponibilidade em
ficção. Quando falta latência, a célula mostra travessão, não zero.

A aba **Cobertura** lê `/api/v1/sinais` e diz, para cada família do dicionário,
se há coletor e — quando não há — por quê. Uma lacuna vira informação em vez de
mistério.

A aba **Módulos** mostra a plataforma se observando. Repare na distinção que ela
preserva: um módulo com muitas falhas de alvo *e* carimbo de última coleta
presente funcionou — foram os equipamentos que não responderam.

## O que ainda não está aqui

- Canal B — ingestão de fatos estruturais e o grafo temporal (marco M2)
- Módulo SNMP declarativo e séries históricas (marco M3)
- Subsistema de ação (marco M4)

Os testes usam um banco separado (`plataforma_teste`) de propósito: as
fixtures apagam o esquema entre casos, e uma suíte que destrói o banco de
desenvolvimento é uma suíte que as pessoas param de rodar.
