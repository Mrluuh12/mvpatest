# Plataforma TI + OT — marcos M0 a M3 e área ADM

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

## O que ainda não está aqui

- Módulos de outros fabricantes: Astra/InfiNet (18 rádios PtP/PtMP), MEMS
  Michelin (46 gateways de pneu), PTX (97 IHM de bordo)
- Gráficos e relatórios. A série histórica **não** será reimplementada aqui: o
  Prometheus do exportador já a guarda, e duplicá-la criaria duas verdades
  sobre o mesmo número. Falta o cartão de gráfico e o endpoint de consulta.
- Subsistema de ação (marco M4)
- Motor de alarmes — adiado a pedido, e ainda é a decisão certa: sem série e
  sem grafo, um alarme só saberia dizer "não responde"

Os testes usam um banco separado (`plataforma_teste`) de propósito: as
fixtures apagam o esquema entre casos, e uma suíte que destrói o banco de
desenvolvimento é uma suíte que as pessoas param de rodar.
