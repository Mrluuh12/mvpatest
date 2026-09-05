# Plataforma TI + OT — inventário, coleta, séries, diagnóstico e relatórios

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
python -m pytest -q      # 153 testes
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

Barra superior azul-marinho com contadores, árvore hierárquica (Minas › Frota
› tipo › ativo) mostrando o estado de cada máquina, filtros rápidos, cartões
com título e grade densa — a linguagem visual das telas de referência.

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

### Imagens

Ativos e dispositivos aceitam imagem, e o sujeito é **hierárquico**:

| Sujeito | Alcance |
|---|---|
| `disp:<chave>` | aquele aparelho |
| `papel:<papel>` | todos os dispositivos daquele papel |
| `ativo:<id>` | aquela máquina |
| `frota:<sigla>` | todos os ativos da frota |

É a cascata que torna o recurso usável: **17 fotos, uma por papel, cobrem os
708 dispositivos**. Foto específica, quando existir, tem precedência. Ao clicar
numa imagem, a escolha de abrangência aparece com as duas opções nomeadas.

O arquivo é gravado com nome derivado do conteúdo — nunca o nome que veio no
envio — e o tipo servido é o que a plataforma registrou, nunca o cabeçalho de
quem enviou. Como só se encontra pelo nome registrado no banco, não há
travessia de caminho a explorar.

A aba **Coleta** mostra a plataforma se observando. Repare na distinção que ela
preserva: um módulo com muitas falhas de alvo *e* carimbo de última coleta
presente funcionou — foram os equipamentos que não responderam.

### O cartão diz o que mediu, não o que falta

O cartão de medições do ativo mostra só o que foi de fato medido: alcance,
latência média e pior, perda, composição e a hora da última leitura. Ele já
reservou linha para RSSI, interface e temperatura, cada uma dizendo "aguarda o
módulo tal". Numa tela isso é informação; em 145 telas, repetido para sempre,
é ruído que ensina a ignorar o cartão.

O que a plataforma **não** coleta continua sendo dito — uma vez, na aba
Cobertura, que é o lugar feito para isso, com um atalho no pé do cartão. Pela
mesma razão, as Ações Rápidas saíram do arranjo padrão: quatro botões
desativados que o catálogo nem deixa repor são peso morto em toda instalação
nova. O tipo continua no catálogo, com o motivo à vista, e volta a ser
oferecido quando o subsistema de ação existir.

### Ficha do dispositivo

Clicar num componente — na tabela ou no diagrama — abre a ficha daquele
aparelho: identidade, telemetria, imagem, mudanças e histórico de alterações.
As mudanças mostradas são **daquele dispositivo**, não do ativo inteiro: numa
ficha, ver as oscilações dos outros onze aparelhos do mesmo caminhão faria
parecer que foi ele que oscilou.

O cartão de telemetria só reserva linha de RSSI para papéis que têm rádio. Num
conversor CAN, "RSSI: aguardando coletor" seria uma promessa que nunca se
cumpre.

### Telas configuráveis

Cada tela — de ativo e de dispositivo — é um **arranjo**: quais cartões
aparecem, com que nome, em que ordem e com que largura. Quem tem
`editar_painel` personaliza pela própria tela: acrescentar, remover, renomear,
reordenar e alargar, com tudo em memória até salvar. Cancelar descarta.

O arranjo se resolve pela mesma cascata das imagens, e por ter a mesma razão:

| Escopo | Alcance |
|---|---|
| `disp:<chave>` / `ativo:<id>` | aquele aparelho / aquela máquina |
| `papel:<papel>` / `frota:<sigla>` | todos do mesmo papel / da mesma frota |
| `padrao_dispositivo` / `padrao_ativo` | onde não houver arranjo mais específico |

**Um arranjo por papel cobre os 708 dispositivos.** Ao salvar, a plataforma
pergunta para onde o arranjo vale — e a barra da tela sempre diz de onde o
arranjo em uso veio, porque sem isso ninguém sabe se está mexendo na tela
daquela máquina ou na de toda a frota. Apagar um arranjo faz a cascata voltar
a valer: é assim que se desfaz um experimento, sem restaurar backup.

O **catálogo de cartões é fechado**, pela mesma razão do dicionário canônico de
métricas: se cada tela puder inventar um tipo, em dois anos são vinte telas que
não se parecem e ninguém mantém. A liberdade é de *compor*, não de improvisar.
Um cartão só é oferecido no contexto em que faz sentido — `componentes` não
entra numa ficha de dispositivo, porque um rádio não tem aparelhos embarcados,
e o arranjo que tentar isso é recusado antes de chegar ao banco.

Um teste lê o `app.js` e falha se um tipo do catálogo não tiver desenho
correspondente: sem ele, acrescentar um cartão no servidor entregaria ao
usuário um buraco onde deveria haver conteúdo.

## Módulo Rajant (marco M2)

**Ele não fala com rádio nenhum.** O exportador Prometheus do usuário já faz
isso — 98 métricas tiradas da BC API, com correções documentadas contra o BCE
User Guide. Reimplementar aquilo jogaria fora conhecimento caro e criaria uma
segunda verdade sobre os mesmos equipamentos.

O que faltava não era coleta de rádio: era **junção**. O Prometheus sabe que o
BC `CA-1001` está a 47 °C; só a plataforma sabe que ele é o rádio de um
caminhão de britagem primária, em ot_nivel3, com sete peças embarcadas.

```
PLATAFORMA_PROMETHEUS=http://prometheus:9090 \
  python -m plataforma.coletor --zona corporativa --uma-vez --modulo rajant
```

Sem essa variável o módulo não carrega: módulo que falha por não estar
configurado ensina a ignorar módulo que falha de verdade.

### A agregação acontece no PromQL

São ~254 séries por rádio (52 por BC, 75 por interface, 108 por vizinho, 18
por porta) — cerca de **38 mil** no parque de 149. Trazer isso para dentro
seria reimplementar o Prometheus pior do que ele. As 18 consultas já voltam
agregadas por equipamento: **18 valores por rádio em vez de 254**. O detalhe
por vizinho fica onde já está e é bom.

### Três armadilhas que o dado real revelou

**O RSSI do Rajant não é dBm.** É escala relativa medida acima do piso de
ruído; o dBm de verdade é `State.Peer.signal`. O próprio exportador documenta
a confusão e o estrago que ela fez lá. Por isso `rf_rssi_dbm` vem de
`rajant_peer_sinal_dbm`, e `rajant_peer_rssi` fica de fora até existir uma
métrica canônica com a escala certa.

**Um BreadCrumb tem um IPv4 por rádio.** O mesmo equipamento aparece em várias
séries, com IPs diferentes, e todas resolvem para o mesmo dispositivo do
inventário — o que fazia o Postgres recusar o lote inteiro (*cannot affect row
a second time*). O módulo consolida com critério declarado: somas somam, o
resto fica com o pior caso, porque um rádio bom não compensa o outro estar
surdo.

**`rajant_online` não é `ativo_alcancavel`.** Quem responde por
disponibilidade é o ICMP; duas fontes gravando a mesma linha de estado dariam
last-write-wins, e o valor na tela dependeria de qual módulo rodou por último.
São perguntas diferentes — "o endereço responde" e "a sessão BC API abre" — e
um rádio que atende ping e recusa a API é um achado, não um empate. Vai para
`servico_disponivel`.

### Do vizinho publica-se o pior, e diz-se que é o pior

Um rádio de malha não tem "o SNR": tem N, um por vizinho. A média esconderia
justamente o enlace prestes a cair. Cada observação agregada carrega o rótulo
`agregacao`, e a ficha o exibe em português — *pior entre os vizinhos* — porque
`rf_snr_db` num PtP é o enlace e aqui é o pior de N.

### O enlace é objeto monitorável, não só relação

Um rádio de malha não tem "o SNR": tem um por vizinho. A ficha do aparelho
mostra o pior num número só, o que responde *"este rádio está bem?"*. Mas
*"qual enlace é o pior?"* só tem resposta se cada meia-aresta guardar a própria
medida — e é essa a pergunta que leva alguém à torre certa.

Cada enlace vira sujeito com chave própria, e ela é **direcional**:

```
enlace:CA-1001-RADIO RJT>02:D0:12:0E:F8:85
```

O SNR que A mede do enlace com B não é o que B mede do mesmo enlace: antenas,
alturas e ruído local diferem. Guardar os dois sob a mesma chave apagaria
metade do diagnóstico — e é justamente a assimetria que diz **de que lado** o
problema está. O `>` deixa a direção visível na própria chave.

Contra o parque real: **554 enlaces medidos, 2.216 medidas**, e a pergunta
*"quais são os cinco piores enlaces da malha?"* passou a ter resposta.

Publicar o agregado **e** o detalhe não é redundância: a ficha do rádio quer o
pior num número só; o diagnóstico quer saber qual.

### A junção recusa chave ambígua

O IP é a chave, confirmado com quem opera: o endereço que o exportador publica
é o mesmo da planilha. Mas o cadastro real tem **3 IPs e 8 nomes canônicos
disputados por rádios diferentes**:

| Chave | Disputada por |
|---|---|
| `10.188.99.192` | TT-3708-RADIO RJT, TT-3802-RADIO RJT |
| `10.188.99.194` | PA-5801-RADIO RJT, TT-3710-RADIO RJT |
| `10.188.97.19` | ERM-01-INFINET, ERM-29-INFINET |

Um `{ip: chave for ...}` teria deixado a última vencer, e a temperatura de um
trator apareceria pendurada no outro sem nada indicando por quê. **Número
errado é pior que número ausente, porque alguém age sobre ele.** O índice
recusa a chave disputada; o nome ainda pode desempatar; e o que não casa vira
recusa nomeada — *"chave disputada no cadastro, leitura descartada:
TT-3708@10.188.99.192"* — que diz exatamente qual cadastro corrigir.

### Zona limita alcance, não atribuição

O módulo declara `Alvo.SISTEMA`: fala com **um** sistema — o Prometheus — e
nunca abre conexão com um rádio. Por isso a lista de alvos dele não é
recortada pela zona do coletor: zona existe para impedir que um coletor
corporativo *alcance* equipamento de OT, e quem nunca alcança não pode ser
limitado por onde o equipamento está. O dado já cruzou a fronteira antes, no
exportador — que é onde essa decisão pertence.

Na prática isso são **22 rádios de ot_nivel3** que sairiam de zero para
cobertos sem nenhum processo novo dentro da OT: 123 alvos viram 145.

As zonas proibidas continuam de fora em qualquer caso, e essa linha não se
move por tipo de módulo: nem atribuir leitura a um controlador de processo a
plataforma faz.

### Onde a leitura para

A tabela `leitura` guarda **a última** leitura de cada par (sujeito, métrica).
Não é série histórica e não quer ser: responde "quanto está agora", que é o
que a ficha precisa. "Como estava ontem às 14h" continua sendo pergunta para o
Prometheus, que guarda a série muito melhor — e duplicá-la aqui criaria duas
verdades sobre o mesmo número. O tamanho é equipamentos × métricas,
substituído a cada ciclo; não cresce com o tempo.

### A tela parou de prometer

`/api/v1/sinais` dizia "aguarda o módulo Rajant" para sempre, mesmo depois de
o módulo estar publicando. Promessa desatualizada é pior que promessa: ensina
a não confiar na tela. Agora a família é apurada do banco — disponível quando
existe leitura dela —, e só as que a linha de base dá como ausentes são
promovidas, para que o ICMP não perca o crédito da disponibilidade.

## Canal de fatos e grafo temporal (marco M2)

A tabela `aresta` sempre teve `TSTZRANGE` e restrição de exclusão, mas até
aqui só recebia o que veio da planilha — 672 arestas `embarcado_em`, nenhuma
observada. É o canal de fatos que a preenche com o que a rede está fazendo, e
é o que separa a plataforma de um painel: uma lista de equipamentos com
números ao lado vira uma **rede que muda no tempo**.

Métrica responde *quanto*; relação responde *quem estava ligado a quem*. São
canais separados porque mudam em ritmos diferentes: a métrica é substituída a
cada ciclo, a relação dura horas e o que interessa nela é justamente quando
começou e quando deixou de valer.

```
rádio: CA-1001-RADIO RJT

  em 23:44:07   CA-1015, 02:D0:12:DB:F6:5C, CA-1044-RADIO RJT
  agora         CA-1015, 02:D0:12:0E:F8:85, 02:D0:12:E8:F4:52

  perdeu 2, ganhou 2, manteve 1
```

### Três propriedades, e a segunda evita gravar uma mentira

**Conciliar é idempotente.** Rodar de novo com a mesma vizinhança não cria
linha nem fecha nada. Sem isso, 149 rádios a cada minuto seriam 200 mil linhas
por dia dizendo a mesma coisa; com isso, a tabela cresce com as **mudanças**.

**Só fecha aresta quando a leitura foi completa.** Se o Prometheus caiu ou
metade das consultas falhou, a ausência de um vizinho significa "não
perguntei", não "o enlace caiu". Fechar tudo escreveria na história que a
malha inteira se desfez num instante — e **aresta fechada é fato datado**, que
fica. É o mesmo raciocínio da suspeita de isolamento, aplicado ao grafo.
Verificado: com o Prometheus derrubado no meio, as 554 arestas continuaram
abertas e nenhuma foi fechada.

Pela mesma razão, o fechamento se limita a quem foi lido no ciclo: uma coleta
completa de 22 rádios não pode fechar as arestas dos outros 127 só por não os
ter mencionado.

**Identidade ambígua não vira aresta.** O módulo relata o que viu — um IP, um
`mac:...` —, e quem resolve é a plataforma, que conhece os identificadores. Um
MAC ou IP disputado por dois equipamentos não resolve para nenhum: pendurar o
enlace no equipamento errado é pior do que não ter o enlace. MAC é comparado
sem separador e em maiúsculas, porque o cadastro guarda `00:01:B9:66:A1:AE` e
o equipamento pode publicar `00-01-b9-66-a1-ae` — perder um enlace por causa
de um hífen seria um defeito difícil de enxergar.

### Cobertura depois deste bloco

O ICMP passa a declarar `ot_nivel3`. Um eco não lê nem escreve nada no
equipamento — pergunta se o endereço responde —, e o coletor continua preso à
sua zona, então sondar a OT exige um processo rodando **dentro** dela. Os
níveis 0 a 2 seguem recusados pelo próprio manifesto.

| Zona | Equipamentos | Com estado |
|---|---|---|
| corporativa | 388 | 368 |
| ot_nivel3 | 294 | **286** (era 0) |
| ot_nivel2 | 26 | 0, e para sempre |

## Gráficos

A plataforma **não guarda série** — ela pergunta para quem tem. Isso foi
decisão, não esquecimento: o Prometheus do exportador já guarda, e muito
melhor; duplicar criaria duas verdades sobre o mesmo número.

Três origens, e a diferença entre elas é o desenho:

| Origem | O quê | Forma |
|---|---|---|
| **Prometheus** | as métricas que o módulo Rajant lê | linha |
| **Transições** | disponibilidade | faixas |
| **Nenhuma** | o resto (SNMP, ICMP) | uma frase dizendo por quê |

**A consulta fica à vista, embaixo do gráfico.** `min by (bc, ip)
(rajant_peer_snr_db{ip="10.188.99.1"}) [passo 90s]` — quem desconfiar do
número pode conferir. Gráfico que não pode ser conferido é gráfico em que
ninguém confia depois da primeira surpresa. E a consulta é **montada** da
mesma tabela que a coleta usa, então cartão e gráfico não podem divergir: se
um mostra o pior SNR entre vizinhos, o outro mostra o mesmo.

### Disponibilidade não é uma linha

A tabela guarda o instante exato de cada mudança, então a resposta certa são
**faixas**, não pontos de dez em dez minutos — que perderiam uma queda de dois
minutos registrada com precisão de segundo.

E o estado incerto é **hachurado**, não âmbar. Vermelho e âmbar têm ΔE 4,6 em
deuteranopia: indistinguíveis. Nas pastilhas isso passa porque há texto junto
e a cor é reforço; num desenho ela seria o único sinal.

### Criar e configurar um gráfico

**Personalizar tela** → **+ Cartão** → *Gráfico*. Cada cartão com opções ganha
um painel embaixo, em modo de edição: qual métrica, qual janela abre, quantas
linhas, a partir de que severidade.

O painel é dirigido pelo **tipo** da opção declarada no catálogo, não pelo tipo
do cartão. Sem isso, a interface teria de conhecer cada cartão por dentro — o
acoplamento que o catálogo existe para evitar. Com isso, acrescentar um cartão
com opções não exige tocar em JavaScript nenhum, e há teste que falha se
alguém declarar um tipo de opção que a tela não sabe desenhar.

O seletor de métrica só oferece **as que têm série**. Deixar escolher uma que
só tem última leitura seria convidar a criar um cartão que diz "sem série"
para sempre.

**Janela padrão e janela de agora são coisas diferentes.** A do painel é o que
a tela abre e fica salva no arranjo; os botões no topo do gráfico são a escolha
de quem está olhando neste momento e moram fora do arranjo. Escrevê-las no
mesmo lugar fazia a escolha do botão ser descartada na repintura seguinte,
quando o arranjo é relido do servidor — o botão simplesmente não funcionava.

### Ausência não vira linha reta

Métrica que só tem a última leitura devolve a frase, não um desenho. Uma linha
feita de um ponto só parece informação — e é pior que gráfico nenhum.

## Eventos: o terceiro canal (syslog e traps)

Métrica responde *quanto*; relação responde *quem com quem*; evento responde
*o que aconteceu, segundo o próprio equipamento*. Os dois primeiros a
plataforma vai buscar. Este chega sem ser chamado — e a inversão traz três
problemas que os outros não têm.

**A confiança é outra.** Syslog sobre UDP não autentica nada: qualquer um na
rede pode mandar uma mensagem dizendo ser qualquer IP. A plataforma atribui
pelo IP de origem porque não há alternativa, e **registra que foi assim**. Um
evento não é prova; é o que alguém disse, e a tela diz isso.

**O volume não é nosso.** Uma porta oscilando manda milhares de mensagens por
minuto. Sem limite por origem, um equipamento defeituoso enche a tabela e leva
junto o que importa. O receptor conta, descarta o excedente e **grava um evento
dizendo quantos descartou** — silêncio aqui seria a plataforma escondendo que
ficou cega justo quando havia mais o que ver.

**A hora é do remetente.** O carimbo dentro da mensagem vem do relógio do
equipamento, que pode estar errado em horas. Guardam-se os dois: o que ele
disse e o que nós vimos. Divergência grande é achado — relógio errado estraga
qualquer correlação depois.

### UDP perde, e o receptor não tem como saber

Medido aqui: 400 mensagens numa rajada, **294 chegaram**. O kernel descartou o
resto antes de qualquer código nosso rodar. Subir o buffer de recepção para
8 MB fechou a conta em 400/400 no mesmo teste — mas não elimina o problema, e
fingir que sim seria a plataforma mentindo sobre a própria cobertura. Quem
precisa da conta exata olha `netstat -su`, ou usa syslog sobre TCP.

O que **pode** ser visto é contado: fila cheia vira evento de alerta próprio.

### O parser aguenta o campo

Três formatos, porque é o que chega: RFC 5424, RFC 3164, e RFC 3164 com o
número de sequência que a Cisco enfia na frente e que quebra parser ingênuo.
Um 5424 levemente fora do padrão ainda entrega hora e remetente — perder o
carimbo por causa de um campo a menos é descartar o dado bom junto com a
formatação ruim. E mensagem que não casa com formato nenhum vira evento mesmo
assim: equipamento que fala errado ainda está falando, e o aviso que interessa
costuma vir no dia em que tudo está estranho.

## Cofre de credenciais

O SNMP é o primeiro módulo que precisa de segredo, e é por isso que o cofre
existe. Ele é **outra coisa** que senha de usuário, e a diferença define o
desenho: senha só precisa ser *conferida* (um `scrypt` irreversível basta);
credencial precisa ser *apresentada* ao equipamento — a comunidade SNMP viaja
dentro do pacote UDP. Logo não pode ser hash: tem de voltar em claro na hora
do uso.

Daí quatro regras:

- **A chave mora no ambiente, nunca no banco.** AES-256-GCM; quem levar o dump
  leva ciphertext. Há teste que lê a coluna crua e falha se a comunidade
  aparecer nela.
- **Sem chave, o cofre se recusa a operar.** Não há degradação para texto
  claro. Uma instalação mal configurada falha na partida, com a receita da
  correção na mensagem — em vez de gravar comunidades em claro por dois anos.
- **A API nunca devolve segredo.** Nem mascarado, nem para administrador.
  Listar mostra nome, tipo, zona e atributos não sensíveis (a porta, o usuário
  do v3).
- **O nome da credencial é o AAD da cifra.** Um ciphertext copiado da linha
  `snmp-ot` para a linha `snmp-corp` não abre — a cifra prende o segredo ao
  lugar dele.

Credencial tem zona, e ela é conferida na abertura: é a última linha antes de
o segredo virar pacote na rede.

```bash
export PLATAFORMA_CHAVE=$(python -c \
  "from plataforma.db.credenciais import gerar_chave; print(gerar_chave())")
export PLATAFORMA_SNMP_CREDENCIAL=snmp-mina
```

## Módulo SNMP declarativo (marco M3)

O módulo não conhece fabricante nenhum: executa um **perfil**, que diz quais
OIDs ler e para que métrica canônica cada um vai. Suportar um switch novo é
escrever quinze linhas de configuração, não uma classe.

**Nunca escreve.** Não há `set_cmd` no arquivo, e não é esquecimento: SNMP de
escrita em equipamento de rede de mina é como se derruba uma frota.

### Três armadilhas que o perfil evita

**Contador de 32 bits.** `ifInOctets` vira a zero a cada 4 GB — num enlace de
1 Gb/s são 34 segundos. Uma série montada sobre ele mostra quedas que nunca
aconteceram. O perfil de rede lê `ifHCInOctets` da ifXTable, e o dicionário
canônico já pedia isso em letras miúdas.

**`sysUpTime` está em centésimos.** É o erro clássico do primeiro coletor: um
equipamento ligado há 3 dias vira 300 dias no painel. O fator está declarado,
à vista.

**Interface não é dispositivo.** Um switch de 48 portas tem 48 conjuntos de
contadores; o sujeito da leitura passa a ser `chave/porta`. E o nome da porta
está na ifXTable enquanto o estado dela está na ifTable, indexadas pelo mesmo
`ifIndex` — emitir tabela a tabela fazia a mesma porta virar dois sujeitos,
`sw-01/Gi0/1` e `sw-01/1`. Foi um agente SNMP de verdade que revelou isso; um
mock teria repetido o meu engano.

### Concorrência não é otimização, é viabilidade

Em série, um parque mudo custa a soma de todos os timeouts: 36 alvos × 11
operações × 2 s dão **oito minutos por ciclo**, num módulo cujo intervalo é de
dois. Com um semáforo de 20, a mesma coleta contra o inventário real levou
**8,7 s**. O limite existe porque o outro extremo — todos de uma vez — abriria
centenas de sockets UDP e afogaria o próprio coletor.

## Contas, autorização e auditoria

A primeira conta é criada por quem instala. **Não há senha padrão embutida** —
sistema que nasce com `admin/admin` nasce comprometido.

```bash
criar-admin ana --nome "Ana Souza"            # pergunta a senha
criar-admin ot --zona ot_nivel3               # administrador só de OT
```

### A trava que não dá para esquecer de ligar

A plataforma fica aberta **até existir a primeira conta** — antes disso não há
como entrar, e exigir login trancaria a instalação para fora. Assim que existe
um usuário, **tudo passa a exigir sessão, inclusive as leituras**: elas expõem
o inventário inteiro, cada endereço e cada zona.

### Autorização negada por padrão

A concessão vale **onde foi concedida**. Um administrador da zona corporativa
não é administrador de OT por consequência — e a mensagem de erro diz qual
zona faltou.

Duas decisões de zona que merecem estar escritas:

- **Metadado de ativo é atributo de negócio, não de rede.** Editar a função de
  negócio de um caminhão exige permissão na zona corporativa, mesmo que ele
  tenha dispositivos em OT. Exigir OT ali seria burocracia sem ganho.
- **Mover um dispositivo de zona exige permissão nas duas.** Sem isso, quem
  administra só a corporativa poderia trazer um CLP para dentro dela e, em
  seguida, agir sobre ele. É a porta de escalonamento mais óbvia deste modelo,
  e ela fica fechada.

### Auditoria

Toda escrita grava a linha de auditoria **na mesma transação** da mudança: ou
as duas acontecem, ou nenhuma. Não existe rota que altere ou apague uma linha
de auditoria — auditoria que se pode editar não é auditoria.

Inclusive **tentativa de login recusada** fica registrada, que é justamente o
que se quer ver depois.

### Senhas

Nunca guardadas. Guarda-se `scrypt` com sal por usuário, e a comparação é em
tempo constante. O token de sessão entregue ao navegador **não** é o que fica
no banco: lá fica o SHA-256 dele, então vazamento do banco entrega resumos
inúteis, não sessões válidas.

## Relatórios

O catálogo é onde se perde ou se ganha a comparação com o SolarWinds, que traz
**mais de cem** modelos prontos, organizados em Availability, Current Node
Status, Historical, Top N, Inventory, Events e Capacity Forecasting, com
agendamento, envio por e-mail e categorias de limitação por usuário.

Aqui são **quinze**, e a aposta não é competir em número: é que cada um
**declare o que ficou de fora dele**. Um relatório que não declara suas
ausências é um relatório que vai ser citado errado numa reunião — e uma vez
citado errado, ninguém mais confia na ferramenta inteira.

| Categoria | Relatórios |
|---|---|
| **Disponibilidade** | por frota e função · dia a dia · piores equipamentos · quedas uma a uma |
| **Capacidade** | previsão de saturação · portas por tráfego e erro |
| **Desempenho** | qualidade dos enlaces · enlaces instáveis |
| **Inventário** | cobertura da coleta · o parque |
| **Eventos** | por gravidade · quem mais falou |
| **Governança** | alterações · diagnósticos · contradições do cadastro |

### Quedas, uma a uma — o que a decisão de guardar transições comprou

Cada queda vira uma linha com hora de início, hora de volta e duração. Com
amostragem de minuto em minuto, uma queda de dois minutos apareceria como
"dois pontos ruins"; aqui aparece como um intervalo com começo e fim exatos.

A queda que ainda não terminou sai com o fim vazio e é dita como tal: fechar a
duração no instante do relatório inventaria um retorno que não houve.

### Previsão de saturação — o método do SolarWinds, com o aviso junto

Ajusta uma reta ao histórico e diz em quantos dias a porta cruza o limiar. A
documentação da própria SolarWinds avisa do defeito: *"uses a linear approach…
a single big change will impact heavily on the trend"*. Uma obra, uma mudança
de rota, e qualquer degrau vira "satura em quatro dias".

O que este relatório acrescenta é **o tamanho do histórico e a qualidade do
ajuste na mesma linha do número** — colunas de R² e de confiança, e a regra que
importa: **a frase de resumo só anuncia uma saturação quando a projeção se
sustenta.** Com ajuste fraco ela diz outra coisa: *"3 portas têm projeção de
saturação, e nenhuma delas se sustenta"*. O resumo é a linha que as pessoas
leem, muitas vezes a única; anunciar "0,7 dias" ali a partir de um R² de 0,15 é
como o número vira meta de reunião enquanto a ressalva fica na tabela que
ninguém abriu.

Três casos devolvem "sem data" em vez de um número: já passou do limiar, não
está crescendo, ou cresce tão devagar que a data cairia fora de qualquer
horizonte de planejamento. Um número gigante seria tecnicamente certo e
praticamente uma mentira.

### Enlaces — o relatório que uma ferramenta de nós não faz

Uma plataforma de rede vê nós e portas. Numa malha Rajant o que para a
operação é o **enlace**, que existe por horas e some quando o caminhão entra na
cava. Dois relatórios saem de o enlace ser objeto de primeira classe, com
sujeito próprio e validade:

**Qualidade** traz SNR, sinal, capacidade e custo de cada enlace aberto — e a
coluna de **assimetria**, a diferença entre o SNR nos dois sentidos. Ela existe
porque o enlace é dirigido: `A→B` e `B→A` são linhas diferentes porque são
medições diferentes. Assimetria de 6 dB ou mais raramente é ruído; costuma ser
antena desalinhada, potência desigual ou obstáculo de um lado só. Uma
modelagem simétrica faria a média das duas e apagaria justamente o sinal.

**Instabilidade** conta quem mais abriu e fechou. Nenhum equipamento caiu e a
malha esteve ruim mesmo assim — o modo de falha que um relatório por nó não
mostra.

### Equipamento-hora, e por que a unidade importa

Somar o tempo fora do ar de 654 equipamentos num dia de 24 h dá 654 "dias".
Lido como duração isso é absurdo na cara, e foi assim que a primeira versão
saiu. Não é duração: é **volume de indisponibilidade**, do mesmo jeito que
homem-hora não é hora. Onde a coluna soma equipamentos diferentes a unidade é
`equip·h` e está escrita no cabeçalho; onde a linha é de um equipamento só, aí
sim é duração e sai como `5 d 06 h`.

### Três formatos, uma regra

A ressalva viaja com o dado. O **CSV** leva título, período, parâmetros usados
e ressalvas em comentário no topo — e os valores **crus**, porque `"94,32%"`
como texto quebra qualquer fórmula e a planilha existe para se calcular em cima
dela. A **impressão** leva as ressalvas no rodapé da folha, repete o cabeçalho
a cada página e não quebra linha no meio.

Não há gerador de PDF aqui de propósito. Toda biblioteca de PDF em Python traz
peso e superfície de manutenção para resolver um problema que o navegador já
resolve: `Ctrl+P → salvar como PDF` produz o arquivo com as fontes e a
paginação do sistema de quem imprime. Uma dependência a menos.

### Parâmetros são do relatório, não da planilha depois

Cada relatório declara seus parâmetros com tipo — frota, zona, quantas linhas,
limiar de aviso, ordenação — e a tela desenha o controle certo sem conhecer
relatório nenhum. A recusa nomeia o campo: *"'Quantas linhas': 'muitas' não é
número"*, porque um `422` sozinho manda a pessoa adivinhar.

A janela também é do relatório. Sete dias serve para disponibilidade e deixa a
previsão vazia, porque a série é mais nova que a janela — e a tabela vazia
parecia defeito enquanto a janela padrão era a mesma para todos.

### O defeito que os relatórios revelaram

O estado no início da janela caía no estado **corrente** quando não havia
transição anterior — ignorando que a primeira transição *dentro* da janela já
diz de onde veio. Um equipamento que passou 12 h de pé e caiu no meio era
contado como caído desde o início: **0% em vez de 50%**.

Estava em três lugares — relatório, gráfico de disponibilidade e cálculo de
disponibilidade — e é o pior tipo de defeito: acertava nos equipamentos que
nunca mudaram e errava exatamente naqueles sobre os quais o relatório é feito.
O cálculo virou uma função só, `estado_no_inicio`, com os três casos
ordenados e testados.

### O que ainda não tem

Agendamento e envio por e-mail, que o SolarWinds tem. Depende de SMTP
configurado, o que é conversa de infraestrutura junto com o kit de instalação —
e um relatório que chega por e-mail sem ninguém ter pedido é o começo da pasta
de filtros que ninguém lê.

## Exportação para o Prometheus — as 61 métricas viram gráfico

A plataforma guarda a **última** leitura de cada métrica, não a série. Foi
decisão, não esquecimento: o Prometheus já guarda série, e muito melhor;
duplicá-la criaria duas verdades sobre o mesmo número.

A consequência só apareceu no gráfico. Das 61 métricas canônicas, **19**
podiam virar linha — as que passavam pelo Prometheus do exportador Rajant.
Tráfego de porta de switch, que é o gráfico mais aberto de qualquer plataforma
de rede, não existia. O dado estava no banco; faltava chegar a quem guarda
série.

A saída não foi guardar série aqui. Foi **entregar o número a quem guarda**: a
rota `/metrics` publica no formato de exposição, o Prometheus raspa, e a série
continua tendo um dono só.

### O que não é publicado, e por quê

**O que já veio de um Prometheus.** O módulo Rajant lê do Prometheus do
exportador do cliente. Republicar aquilo fecharia um laço — o mesmo número
entrando de novo com outro nome. Quem declara isso é o manifesto do módulo, no
campo `serie_externa`, porque só ele sabe de onde leu.

**Leitura velha.** Uma amostra raspada vale como se fosse de agora, e é assim
que um coletor parado vira uma linha reta que parece saudável. Passada a
validade (5 minutos por omissão), a leitura simplesmente não sai: o Prometheus
marca a série obsoleta e o gráfico **termina**, que é a verdade. Verificado
contra um Prometheus real — o histórico fica, o número velho não.

Quantas ficaram de fora é publicado como métrica. Omissão silenciosa seria o
mesmo defeito com outra roupa.

### A identidade de negócio viaja com o número

Cada amostra leva `ativo`, `frota`, `funcao_negocio`, `zona`, `papel`, `nome`,
`ip`, `modulo` e `qualidade` — e `porta`, quando é uma interface. É o que faz
a consulta que o dicionário canônico sempre prometeu finalmente responder:

```promql
sum by (funcao_negocio) (rate(plataforma_iface_bytes_rx[5m]))
```

A função de negócio passa pelo **mesmo resolvedor de precedência** que a tela
usa, e não por um `SELECT` à parte: se a correção humana não vencesse aqui, ela
não chegaria ao gráfico.

### Disponibilidade sai daqui também, com uma ressalva escrita

Alcance, latência, perda e jitter não moram em `leitura` — moram em `estado`,
porque viram transição. Sem lê-los, o gráfico de latência continuaria
impossível.

Sobre `ativo_alcancavel` há uma ressalva, e ela vai no `HELP` da série para
quem consulta de fora não descobrir por acidente: o registro **exato** de cada
queda está na tabela de transições, com precisão de segundo, e é dela que saem
o relatório e o gráfico de disponibilidade. A série publicada é *amostrada* na
cadência da raspagem — serve para alarme e correlação, não para contar
percentual.

### O gráfico ganhou dimensão

Uma métrica de interface tem uma série por porta. O cartão tem um campo
**Porta**: vazio agrega e **diz que agregou**; com o nome de uma, mostra só
ela. Onde agregar não significaria nada — somar códigos de estado de 48
portas — o cartão recusa e pede a porta, em vez de desenhar um número
inventado.

Contador vira taxa: `iface_bytes_rx` desenhado cru é uma rampa que só sobe, e
ninguém lê tráfego nela.

### O que a medição contra um Prometheus real ensinou

A janela do `rate()` tem piso de 4 minutos, e o número veio de medir. Contra um
agente SNMP servindo tráfego conhecido — verificado direto no contador, 91,65
contra 91,00 MB/s configurados, 0,7% de erro —, a janela do `rate` decide a
fidelidade:

| Janela | Medido | Erro |
|---|---|---|
| `[60s]` | 15,11 MB/s | **+20,9%** |
| `[120s]` | 12,95 MB/s | +3,6% |
| `[240s]` | 12,09 MB/s | −3,3% |
| `[600s]` | 12,07 MB/s | −3,4% |

O tráfego real era 12,50 MB/s. E com raspagem de 60 s, uma janela de 60 s teria
um ponto só: o `rate` devolveria vazio e o gráfico ficaria em branco sem nada
explicar.

Janela curta não é mais fiel, é mais barulhenta. Errar para o lado do liso
mostra a tendência certa; errar para o lado do curto mostra um número
inventado, ou nada.

### Nega por omissão, como o resto

`/metrics` sem `PLATAFORMA_METRICAS_TOKEN` definido responde **503 dizendo o
que fazer**, inclusive o que pôr no `prometheus.yml`. Com token errado, 401.
Passa pelo porteiro de login porque tem credencial própria — um raspador não
tem navegador nem cookie —, e isso está escrito ao lado da lista de rotas
abertas, para não parecer um furo.

## Diagnóstico dirigido (camada 5)

Os módulos de coleta perguntam a **todo mundo, sempre, do mesmo jeito**. Isso
responde "como este rádio esteve nas últimas 24 h" e não responde "o que está
acontecendo com ele agora, enquanto o caminhão está parado esperando".
Diagnóstico é o outro modo: **uma pessoa, um alvo, um motivo, agora**.

Quatro sondas, todas de leitura: **Ping**, **Caminho** (traceroute), **Porta
TCP** e **Leitura SNMP**. Cada uma declara um `ManifestoSonda` com o perigo que
representa, o limite de tempo e as zonas em que pode rodar.

### O que ele recusa a fazer, e por quê

Está no topo de `src/plataforma/diagnostico.py`, escrito lá para que remover a
recusa custe apagar a justificativa:

- **Varredura de faixa de portas.** Diagnóstico é testar *uma* porta. Varrer
  faixa é reconhecimento — e num CLP ou num rádio de campo já derrubou
  equipamento em muita mina. `SondaPorta` aceita uma porta, não um intervalo.
- **Teste de banda.** Saturar o enlace para medir o enlace deixa a produção sem
  rede justamente enquanto se investiga.
- **Captura de pacotes.** Lê carga útil, e carga útil tem credencial.

São ausências decididas, não pendências. O teste que as guarda existe para que
a linha não se mova sem alguém ter de apagar um teste.

### Duas recusas acontecem antes de qualquer pacote

Zona proibida (`ot_nivel0/1/2`) é recusada na carga do manifesto — não é
configuração, é impossibilidade. E sondar um alvo em outra zona a partir daqui
não vira timeout de 15 s: vira uma frase que ensina o que falta —
*"preciso de um agente naquela zona"*. Recusa que não explica é recusa que a
pessoa contorna errado.

### `diagnosticar` é uma permissão própria

Não cabia dentro de `executar_acao`. Quem pode perguntar *"este endereço
responde?"* não deveria por isso poder reiniciar o rádio de um caminhão em
operação. Leitor não diagnostica; campo, operador, engenheiro e administrador
sim — e a permissão é conferida **na zona do equipamento**, não na do usuário.

Toda sonda deixa dois rastros: `auditoria` (quem, quando, em quê) e
`diagnostico` (o resultado inteiro, para comparar com a próxima vez).

### A tela desenha a autorização, não a decide

`GET /api/v1/eu` devolve `permissoes` por zona, calculadas pela mesma função
que a rota usa para aceitar ou recusar (`Usuario.pode`). Antes, `app.js`
carregava a própria cópia da matriz de papéis — e cópia de regra de autorização
sai de sincronia. Saiu: `diagnosticar` nasceu, o servidor passou a aceitar, e o
botão continuou cinza sem mensagem nenhuma. A cópia foi embora; um teste barra
o retorno dela.

Isto não afrouxa nada. A tela desenha o que já foi decidido; quem decide,
a cada pedido, continua sendo o servidor. Uma tela adulterada consegue
habilitar o botão — não consegue fazer a rota aceitar.

### O defeito que só o clique revelou

O ping usa socket cru. `sock_sendto` e `sock_recvfrom` existem no asyncio
padrão e **não existem no uvloop**, que é o laço que o uvicorn usa em produção.
Escrito com eles, o módulo ICMP passava em toda a suíte e estourava
`NotImplementedError` na primeira sonda disparada pela tela — quer dizer: o
coletor mais abrangente da plataforma, o único que fala com os 708
equipamentos, estava quebrado no processo que de fato roda.

A correção troca método por prontidão: `add_reader`/`add_writer`, que os dois
laços implementam. E o teste agora roda a sonda **nos dois laços de propósito**,
porque a diferença entre eles era exatamente o tamanho do defeito.

## O que ainda não está aqui

- Módulos de outros fabricantes: Astra/InfiNet (18 rádios PtP/PtMP), MEMS
  Michelin (46 gateways de pneu), PTX (97 IHM de bordo)
- Backup e comparação de configuração de switches (o NCM do SolarWinds)
- Qual porta do switch tem qual MAC (o UDT do SolarWinds)
- Gráfico com mais de uma série sobreposta (o PerfStack do SolarWinds). Hoje
  cada cartão desenha uma métrica; comparar duas exige dois cartões.
- Gráfico no ativo e na frota: o cartão só existe no contexto de dispositivo,
  então não há como ver "o tráfego da frota CA" numa tela só.
- Eventos marcados sobre a linha. Syslog e transições já existem em cartões
  separados; cruzá-los é onde o valor aparece — *"o SNR caiu exatamente quando
  o rádio registrou perda de associação"* — e hoje quem cruza é quem olha.
- Subsistema de ação — camada 6 (marco M4). Antes de qualquer linha de código
  aqui, uma conversa sobre o que pode ser desligado e por quem.
- Motor de alarmes — adiado a pedido. A razão original já não vale: hoje há
  série, grafo e eventos, então um alarme teria de que falar. O que falta é a
  parte difícil, que nunca foi a comparação com o limiar: suprimir o alarme do
  filho quando o pai caiu, e não acordar ninguém às 3 h por um rádio de um
  caminhão que está na oficina.
- **Fabricante e modelo em 0 de 708 dispositivos.** É a dívida silenciosa mais
  cara do cadastro: sem modelo, todo perfil SNMP específico é chute, e a
  descoberta automática não tem como se ancorar em nada.

Os testes usam um banco separado (`plataforma_teste`) de propósito: as
fixtures apagam o esquema entre casos, e uma suíte que destrói o banco de
desenvolvimento é uma suíte que as pessoas param de rodar.
