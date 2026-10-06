# Sistema de Navegação da Mina

Mapa da mina e rota até um local ou até um **equipamento em campo**, num PTX
dedicado dentro do veículo.

```
[Servidor Windows]  servidor_rotas.exe
   ├─ lê Modular.db (somente leitura), reextrai quando o hash muda
   ├─ lê o GPS dos rádios Rajant (BCAPI) — onde está cada equipamento
   └─ publica a API HTTP :5000
                    ↓ (malha Rajant)
[PTX dedicado]  PtxNav.exe (Mono)
   ├─ consome a API, cache em disco nas duas camadas do aufs, funciona offline
   ├─ lê o GPS próprio direto da serial (NMEA)
   └─ serve a interface em tela cheia, aberta num navegador em modo quiosque
```

O PTX é **dedicado**: não roda DISPATCH. A posição própria vem do hardware
(serial), não de farejar pacote.

---

## O que já está pronto e testado

| Parte | Estado |
|---|---|
| `servidor/servidor_rotas.py` | Servidor de rotas + API. **Ancoragem por projeção em segmento** (era no nó mais próximo) |
| `servidor/rajant_frota.py` | Posição dos equipamentos pelo GPS dos rádios Rajant |
| `servidor/extrair_malha.py` | Extrator standalone → GeoJSON + grafo |
| `ptx/PtxNav.cs` | Cliente do PTX: serial NMEA, cache nas duas camadas, frota, offline |
| `ptx/interface.html` | Interface de navegação (canvas puro, sem CDN, ES5) |
| `ptx/ptx_iniciar.sh` | Sobe o cliente e abre o navegador em quiosque |
| `ptx/instalar_ptx.sh` | Instala nas duas camadas do aufs + gancho de boot |
| `servidor/gerar_exe.bat` · `instalar_servico.bat` | Build e tarefa agendada no Windows |

**78 testes automatizados**, incluindo a interface aberta num Chromium de
verdade. Veja *Testes*, mais abaixo.

---

## 1. Servidor de rotas (Windows)

```bat
gerar_exe.bat
```

Na pasta de produção ficam quatro coisas:

```
C:\navegacao\
├── servidor_rotas.exe
├── config.json            <- copie de config.exemplo.json e preencha
├── Modular.db             <- ou aponte o caminho no config.json
└── rajant_ips_cache.json  <- se for usar a frota
```

Com o `config.json` ao lado, **abrir é dois cliques** — nenhum argumento. A
linha de comando continua existindo e vence o arquivo quando usada.

Abra `http://localhost:5000/` para conferir o mapa (essa página de bancada usa
Leaflet e precisa de internet; a do PTX não).

Para subir sozinho no arranque:

```bat
instalar_servico.bat "C:\Modular\Modular.db" 5000
```

Aponte de preferência para o banco do **servidor DISPATCH**, que atualiza
primeiro.

### API

| Endpoint | Devolve |
|---|---|
| `GET /api/saude` | vivo? versão, hash, resumo da frota |
| `GET /api/versao` | hash + data dos dados (ETag) |
| `GET /api/locais?tipo=&q=` | locais nomeados |
| `GET /api/malha` | malha viária (GeoJSON LineString) |
| `GET /api/areas` | polígonos de risco |
| `GET /api/equipamentos?tipo=&moveis=1&com_fix=1` | **frota ao vivo** |
| `GET /api/rota?de_lat=&de_lon=&para=NOME` | rota até um local |
| `GET /api/rota?de=NÓ_A&para=NÓ_B` | rota entre nós |
| `GET /api/rota?de_lat=&de_lon=&para_equip=CA-1022` | **rota até um equipamento** |

O hash vira `ETag`: o PTX que já tem a versão recebe **304 sem corpo**.
`/api/equipamentos` fica fora desse cache — é posição ao vivo.

### "no such table: GeographicRegion"

O DISPATCH tem vários `.db` e só **um** carrega a topologia. Apontar para o
errado faz o mapa e as rotas não existirem — a frota Rajant continua
funcionando normalmente, o que confunde. Para achar o certo:

```bat
servidor_rotas.exe --procurar-db C:\Modular
```

```
  SERVE   C:\Modular\data\Modular.db  (1.2 MB)
          1267 objetos, 325 estradas, 942 locais
  nao     C:\Modular\Modular.db
          faltam as tabelas: GeographicRegion, TopologicalObject, Road...
          tem: Equipment, Operator, Shift...
```

Para conferir um arquivo específico: `--conferir-db "C:\caminho\Modular.db"`
(sai com código 0 se serve).

O `/api/saude` também diz quando o banco não serve, e o servidor avisa no
arranque em vez de subir calado.

### Desmonte — área de exclusão

Toda semana saem os pontos do desmonte, e tudo dentro do raio precisa sair —
equipamento, carretinha e as ERMs. O ponto é cadastrado uma vez, no servidor:

```
http://<ip-do-servidor>:5000/desmonte
```

O jeito normal é **colar a lista** como ela vem da topografia:

```
CS_0020_091 (667401.967,7904915.048)
CC_0910_071 (666889.001,7905699.105)
CC_0930_074 (666516.203,7905510.856)
```

A página conta os pontos reconhecidos enquanto você cola, e diz quais linhas
não entendeu — furo faltando calado é pior que erro na cara. Também aceita um
ponto só, por E/N ou lat/lon.

**O raio vale de cada furo**, não do centro do conjunto: desmonte espalhado tem
área de exclusão maior que um círculo só.

Assim que cadastra, a página lista **o que está dentro** — ERMs separadas dos
equipamentos móveis — e ainda quem está a menos de 150 m da borda.

A distância é medida na grid da mina, que já é métrica. Nada de aproximação de
graus: 400 m aqui é limite operacional.

No PTX:

- a área aparece como círculo vermelho tracejado (um por furo), com nome e raio;
- **as ERMs dentro ficam marcadas no mapa** com anel vermelho e o nome sempre
  visível — é a resposta de *onde* ela está;
- o botão **FOGO** mostra **quantas ERMs** precisam sair e abre a lista, com
  cada uma, sua direção (N, NE, L...) e a distância a partir de você. A lista
  é só de ERM: é ela que precisa de equipe indo até lá; equipamento móvel sai
  por conta própria e não polui a tela;
- se o veículo estiver **dentro**, a tela avisa em vermelho por cima de tudo.

A lista só abre com o equipamento parado: em movimento a tela mostra o mínimo, e
ler lista dirigindo é o oposto disso.

As áreas ficam no cache do terminal: mudam uma vez por semana e são informação
de segurança, então continuam na tela mesmo com o servidor fora do ar.

O arquivo é o `desmontes.json` ao lado do programa (`--desmontes` aponta
outro), em texto, para dar para ler e corrigir na mão.

> Isto é apoio visual. O plano de fogo oficial e a liberação da equipe de
> desmonte continuam mandando.
>
> **A lista mostra o que os rádios reportam, não tudo que está fisicamente
> dentro.** Rádio desligado, sem fix ou com o `gpsSwitch` desabilitado não
> aparece; carretinha sem rádio o sistema não enxerga. Serve para conferir e
> achar o que passou batido, não para substituir a varredura da área.

### Só no tablet: retrato e avisos que saem da tela

A interface é o mesmo arquivo no PTX e no tablet. O que é só do tablet liga
quando o app se identifica — `/estado` responde `"plataforma": "android"` — e
no PTX nada muda.

**Orientação.** No menu, em *Tela*: paisagem, retrato ou automática. A
escolha fica no app e sobrevive a reinício. Paisagem é o padrão, que é como o
suporte costuma ficar na cabine; *automática* segue o sensor nas quatro
posições.

**Avisos.** Os de informação (rota recalculada, servidor fora do ar, frota do
cache) aparecem por 8 s e vão para o menu, na seção *Avisos*, com o horário. O
botão ☰ mostra quantos estão guardados. Os de **segurança — sem GPS e área de
desmonte — não saem da tela** enquanto a condição durar: não se navega sem
posição, e quem está dentro do raio de fogo tem que ver isso o tempo todo.

Um aviso só volta à tela quando o texto **muda**. Vários são repetidos a cada
leitura — o do GPS, a cada segundo —, e se cada repetição reiniciasse o tempo
nenhum sairia nunca.

### Só no tablet: guia falado

Com a rota traçada, o tablet fala e mostra na faixa de cima o que fazer no
próximo cruzamento:

| Quando | O que diz |
|---|---|
| rota nova | "Rota traçada até BRITADOR. 1,3 quilômetros." |
| ~250 m antes | "Em 250 metros, vire à direita." |
| ~50 m antes | "Vire à direita." (corta o que estiver sendo dito) |
| saiu da rota | refaz a rota sozinho e diz "Rota recalculada. 850 metros." |
| chegou | "Você chegou. BRITADOR" |
| segurança | entrar/chegar perto da área de desmonte; perder o GPS com rota |

**Cruzamento** é ponta de trecho onde chegam três ou mais trechos (pontas a
menos de 3 m contam como o mesmo nó). Curva no meio de um trecho não vira
instrução, nem desvio menor que 25° — senão a voz falaria o tempo todo e o
operador deixaria de ouvir. Menos de 60° é "mantenha-se à direita"; mais de
135°, "faça o retorno".

**Fora da rota** é passar de 35 m do traçado por 3 leituras seguidas, andando,
depois de ter estado nele — a rota começa na via, e o veículo pode sair de uma
praça. Recalcula no máximo a cada 20 s.

A voz é o sintetizador do Android, sem rede. Se o tablet não tiver voz em
português, o menu avisa: instale em *Configurações › Idioma › Saída de texto
em voz*. No menu, *Voz* liga e desliga.

### Foto aérea da mina no mapa

O mapa desenha a malha viária sobre fundo liso. Com o ortofoto por baixo, o
operador reconhece a bancada, a pilha e a praça — e não só a linha da estrada.

#### Onde ficam os arquivos

Os `.tif` são **matéria-prima**: lidos uma vez, na conversão, e depois não
fazem mais parte do sistema. Não precisam morar em pasta nenhuma do programa.

O levantamento costuma vir em várias folhas, uma por área (BARRAGEM, PDE
NORTE, RAMPA 4B...). Copie a pasta inteira para o disco local do servidor — em
rede, ler gigabytes de `.tif` deixa a conversão lenta sem necessidade:

```
C:\navegacao\
├── servidor_rotas.exe
├── config.json
├── Modular.db
├── ortofotos\            <- as folhas .tif, só para converter
│   ├── BARRAGEM_120826_transparent_mosaic_group1.tif
│   ├── PDE NORTE_250826_transparent_mosaic_group1.tif
│   └── ... (todas as outras)
└── foto\                 <- gerada pela conversão; esta o servidor usa
    ├── manifesto.json
    └── 0\ 1\ 2\ ...
```

Depois de gerar a `foto\`, a pasta `ortofotos\` pode sair do servidor. Guarde
as folhas em outro lugar para quando o levantamento for refeito.

#### Convertendo

Aponte para a **pasta**, e ele converte todas as folhas num mosaico só:

```
servidor_rotas.exe --gerar-foto C:\navegacao\ortofotos
```

Ele primeiro diz o que achou em cada arquivo, e só então converte:

```
=== mina.tif ===
  tamanho   : 24000 x 18000 px, 3 amostra(s) de 8 bits
  compressao: LZW com preditor horizontal
  blocos    : ladrilhos de 256 x 256
  leitura   : ok
  posicao   : ModelPixelScale + ModelTiepoint, 0.250 m/px
  sistema   : UTM 23S, datum SIRGAS2000 (EPSG:31983)
  na grid   : E 664650 a 667854, N 7904451 a 7911279  (3.20 x 6.83 km)
```

A conversão reprojeta do sistema do levantamento para a grid do DISPATCH,
junta as folhas num mosaico só, recorta pela área da malha e corta em
ladrilhos de 256 px, em vários níveis de detalhe. O resultado vai para
`foto/`, ao lado do programa, e é servido em `/foto/`.

Folha que não abre é **pulada com o motivo na tela**, e as outras seguem: uma
corrompida no meio de dezenove não derruba a conversão.

A banda alfa é respeitada. Ortomosaico `transparent` (Pix4D, ODM) marca o
fora-do-voo com alfa zero; sem olhar essa banda, a borda de cada folha viraria
um retângulo preto sobre o mapa.

O nível mais fino recua sozinho para caber em 6000 ladrilhos. Ortofoto de mina
tem detalhe que nenhuma tela de tablet mostra, e cada ladrilho é tempo de
conversão aqui e megabyte de rede lá. Para forçar outro valor:
`--mais-fino 0.25`.

Não precisa instalar nada: o leitor de GeoTIFF e o codificador JPEG estão
dentro do programa. Isso é de propósito — na máquina da mina não há Python nem
pip para instalar biblioteca de imagem.

#### Convertendo em minutos, e não em horas

Decodificar LZW em Python puro custa caro: uma mina inteira leva mais de uma
hora. Com `rasterio` instalado, quem decodifica passa a ser o GDAL, em C,
lendo só a janela pedida e já reduzida — **dez vezes mais rápido**, medido:
385 ladrilhos em 59 s contra 601 s.

```
pip install rasterio numpy pillow
python servidor\mapa_foto.py C:\navegacao\ortofotos --db "C:\...\Modular.db" --saida foto
```

O `.exe` continua funcionando sem nada instalado, no caminho lento — ele
carrega o próprio Python e não enxerga o que o `pip` instalou. Para a
conversão, que é uma vez só, vale rodar pelo Python.

A geometria, o datum e o formato de saída são os mesmos nos dois caminhos:
muda quem lê o pixel, não onde ele vai parar. Um teste converte o mesmo
arquivo dos dois jeitos e cobra que as marcas caiam no mesmo pixel.

**Quando o `.tif` não abre.** TIFF com JPEG dentro não é lido (o `--gerar-foto`
avisa). Converta antes, numa máquina com GDAL:

```
gdal_translate -co COMPRESS=DEFLATE -co PREDICTOR=2 entrada.tif saida.tif
```

#### Tapando onde o levantamento não chega

`--fundo imagem.tif` acrescenta uma imagem de fundo: ela entra **por baixo** e
só aparece onde nenhum ortofoto cobre. A área do mosaico passa a incluí-la; a
resolução continua saindo do levantamento, para uma imagem grossa de tapar
buraco não rebaixar o detalhe do mapa inteiro.

Serve para qualquer GeoTIFF georreferenciado — um levantamento antigo, uma
imagem de satélite aberta. Sobre a escolha da fonte, duas coisas pesam:

- **licença.** Tiles de Google, Bing, Esri, Mapbox e HERE proíbem download em
  massa e uso offline sem contrato — é exatamente o caso de tablets numa
  operação. Sentinel-2 (Copernicus) e CBERS (INPE) são abertos e podem ser
  redistribuídos com atribuição;
- **onde vai aparecer.** Fora da cava, uma imagem de 10 m/px é contexto útil.
  Dentro da área operacional, imagem velha e grossa mostrando uma bancada que
  já mudou é pior que espaço vazio — o mapa passa a afirmar algo errado sobre
  o lugar onde a máquina anda.

**Se o sistema de coordenadas não estiver no arquivo**, informe:
`--crs epsg:31983`, `--crs utm:23S`, ou `--crs grid` quando o ortofoto já vier
na grid da mina. Datum antigo (SAD69, Córrego Alegre) é avisado: sem a
transformação de datum a foto sai deslocada dezenas de metros, então reprojete
para SIRGAS 2000 antes.

**Na tela**, a foto entra como fundo, por baixo de tudo, com a chave
`Foto aérea` no menu. Onde não há foto servida, o manifesto responde 404 e a
linha do menu nem aparece — é o caso do PTX hoje, que segue igual.

> **Hoje isto vale para o tablet.** O app baixa cada ladrilho uma vez e guarda
> em disco: ladrilho não muda depois de gerado. O PTX continua sem a foto até
> ganhar o mesmo cache.

### Mantendo o Modular.db em dia sozinho

A lavra muda e o banco muda junto. Quem recebe a versão nova são os próprios
PTX, então o servidor busca deles:

```json
"atualizar_db": {
  "hosts": "banco",
  "prefixo": "10.188.98.",
  "usuario": "mms",
  "senha": "modular",
  "caminho": "/home/mms/DE/Modular.db",
  "intervalo_h": 6,
  "esperar_no_inicio": true,
  "espera_s": 120
}
```

**A primeira busca acontece ao ligar o servidor, antes de ele abrir a porta.**
Quem liga de manhã quer o mapa de hoje, não o do último desligamento — e a
conferência do banco, na tela do arranque, passa a falar do arquivo que vai
mesmo ser usado.

A espera tem hora marcada: um PTX que aceita a conexão e não responde travaria
o programa para sempre. Passados os `espera_s` (120 s por padrão), o servidor
sobe com o banco que já tem e a busca continua sozinha — servir a malha de
ontem é melhor que não servir nada. Para não esperar nada, `esperar_no_inicio:
false` ou `--sem-espera-db`.

`"hosts": "banco"` lê a lista de terminais do próprio `Modular.db`: a tabela
`HostAddress` liga o nome do equipamento ao endereço (`CA-1001` →
`10.188.98.1`). A lista se mantém sozinha quando a mina põe ou tira terminal.
O servidor central fica de fora — o banco dele não vem de um PTX.

> Antes aqui havia `"10.188.98.1-50"`, escrito à mão. Esta mina tem **161
> terminais, até o .242**: dois terços ficavam fora da busca, e o PTX com a
> versão mais nova do banco podia nunca ser consultado. `banco` elimina a
> chance de errar o tamanho da faixa.

Ainda aceita faixa explícita, se preferir: `"10.188.98.1-254"`.

Cada rodada faz, nesta ordem:

1. testa a porta 22 dos endereços em paralelo — os desligados caem em
   1,5 s, e não em um timeout de SSH cada;
2. pergunta a **data** do arquivo em quem respondeu e escolhe o **mais
   novo** — não o primeiro: PTX parado há semanas tem banco velho;
3. baixa para um temporário;
4. **confere** antes de trocar: SQLite válido, com as tabelas de topologia e
   com estradas dentro. Download truncado nunca vira o banco de produção;
5. compara o hash — igual, não troca nada;
6. troca de forma atômica, guardando o anterior como `Modular.db.anterior`,
   e manda o servidor recarregar na hora.

Para rodar uma vez e ver o resultado: `servidor_rotas.exe --atualizar-db-agora`

O estado da última rodada aparece em `/api/saude`, em `atualizacao_db`.

> A senha fica em texto puro no `config.json`. Restrinja a permissão do
> arquivo se isso for problema na sua política.

### Rodando 24/7 sem travar

No Windows, **mande a saída para arquivo**:

```bat
servidor_rotas.exe --db "C:\Modular\Modular.db" --log C:\navegacao\servidor.log --sem-console
```

O motivo é concreto: um console em modo QuickEdit **congela o processo** se
alguém clicar dentro da janela — e um processo congelado para de coletar a
frota e para de responder aos PTX, parecendo que "caiu". Como tarefa agendada
não há console, então o problema não existe; rodando na mão, existe.

Para saber se a frota está viva, sem abrir nada:

```
http://localhost:5000/api/saude
```

```json
"frota": {"total": 140, "com_fix": 39, "leituras": 12480,
          "idade_ultima_leitura_s": 2.4}
```

`leituras` tem que subir e `idade_ultima_leitura_s` tem que ficar perto do
`intervalo`. Se `leituras` parar de crescer, a frota travou. O servidor também
escreve uma linha de resumo por minuto, e só quando algo muda.

### Frota Rajant

Cada equipamento carrega um BreadCrumb, e o rádio sabe onde está. O servidor
mantém uma sessão BCAPI autenticada por rádio e pede só a subárvore `gps`.

```bat
dist\servidor_rotas.exe --db "C:\Modular\Modular.db" ^
  --rajant-cache rajant_ips_cache.json --rajant-senha SENHA
```

Ou, para não deixar a senha na linha de comando, um `rajant.json` ao lado:

```json
{"cache": "rajant_ips_cache.json", "senha": "SENHA_DO_RADIO", "intervalo": 10}
```

e `--rajant-config rajant.json`. O `instalar_servico.bat` usa esse arquivo
automaticamente se ele existir.

Precisa de `pip install --no-deps rajant-api && pip install protobuf`. **Sem
esses pacotes o servidor sobe igual**, apenas sem a camada de frota — e
`/api/equipamentos` explica o que falta em vez de quebrar.

O tipo do equipamento sai do prefixo do nome do rádio: `CA`→caminhão,
`PA`→pá, `PF`→perfuratriz, `TT`→trator, `EH`→escavadeira, `ERM`→repetidora,
`ERB`→torre. Repetidora e torre não andam: aparecem no mapa mas ficam fora da
lista de destinos móveis.

---

## 2. Cliente do PTX

Construa numa máquina com Mono e leve só o `.exe`:

```sh
sh ptx/compilar.sh          # gera ptx/PtxNav.exe (interface embutida)
```

No terminal, como root:

```sh
sh instalar_ptx.sh --servidor http://10.188.98.200:5000 --serial /dev/ttyS3
```

O instalador grava **nas duas camadas do aufs** (`/media/realroot/...` e `/...`)
e cria o symlink de boot **à mão** — `systemctl enable` escreveria só na camada
em RAM, e o gancho sumiria no reboot.

Teste de aceite depois de reiniciar:

```sh
ps -eo pid,ppid,args | grep PtxNav | grep -v grep     # PPID tem que ser 1
```

Configuração em `/etc/ptxnav.conf` (o reinstalar preserva o arquivo).

### O device da serial

⚠️ **Confirme no equipamento antes**: `cat /dev/ttyS3` tem que cuspir
`$GPRMC`/`$GNGGA`. Se for outro device, mude `SERIAL` em `/etc/ptxnav.conf`.
Nada no código é fixo.

Com `BAUD=auto` o cliente testa 9600, 4800, 38400 e 115200 e fica na primeira
que der sentença com checksum válido.

---

## 3. A interface

Tela cheia, alvos de toque de 56 px, alto contraste, tema dia/noite.

- Mapa vetorial: vias, trechos **fechados em vermelho tracejado**, áreas de
  risco, locais e rótulos aparecendo conforme o zoom.
- Seta da posição própria, orientada pelo rumo.
- **Frota no mapa**: cada equipamento com cor por estado (andando, parado,
  posição velha/offline) e nome com a velocidade.
- Seletor de destino com **equipamentos e ERMs** — os 940 pontos topográficos
  da mina ficam fora da busca (e fora do mapa, salvo pelo botão PTS).
- Rota até equipamento **se refaz sozinha** a cada 15 s — o alvo anda.
- O último trecho vai tracejado quando o destino está fora da via.

### Abertura e menu

O terminal abre numa tela de boas-vindas que faz a checagem antes de navegar:
mapa, GPS, servidor e frota, cada um com OK ou X, mais o nome do equipamento.
O operador vê de cara se dá para navegar, em vez de descobrir pelo mapa vazio.

O botão **☰** abre o menu com:

- **camadas** — pontos da mina, área de desmonte, tema dia/noite;
- **servidor** — o endereço atual, com campo para trocar e botão de testar. A
  troca vale na hora e fica gravada nas duas camadas do aufs, sem precisar
  abrir terminal nem mexer no `ptxnav.conf`;
- **diagnóstico** — estado do GPS, device e velocidade da serial, sentenças
  boas e ruins, tamanho do mapa e hora da última sincronização.

### Segurança operacional

- Destino só pode ser escolhido com o **veículo parado** (abaixo de
  `LIMITE_PARADO`, padrão 3 km/h).
- Em movimento a tela mostra **só direção e distância**, em fonte enorme, sem
  interação possível.
- Fix com mais de 10 s sem sentença é marcado inválido e a interface avisa —
  navegar com posição velha é pior que não navegar.

> **Antes de operar**: alinhar com a área de segurança da mina.

### O toolkit gráfico

A especificação deixou a escolha em aberto até rodar `ptx/ptx_grafico.sh` no
equipamento. A opção preferida — **navegador em modo quiosque apontando para o
localhost** — está construída e testada de ponta a ponta (Chromium). O
`ptx_iniciar.sh` procura, nesta ordem: chromium, chrome, firefox, midori, surf,
luakit, qutebrowser, epiphany.

Se o terminal não tiver nenhum navegador com JavaScript, o script diz isso no
log e **mantém o cliente no ar** — a interface continua acessível de outra
máquina em `http://<ip-do-ptx>:8090/`. Aí é caso de rodar o `ptx_grafico.sh` e
decidir entre GTK#, Windows Forms ou `/dev/fb0`; o algoritmo de desenho do
`interface.html` é traduzível para qualquer um deles.

### Quando o servidor cai

- O mapa continua, vindo do cache em disco.
- A rota até **local fixo** é calculada no próprio terminal, pela malha em
  cache. Confere com a do servidor dentro de **0,004% em média** (medido em 60
  rotas sobre o banco real).
- A rota até **equipamento** também é calculada no terminal, usando a última
  posição conhecida da frota — com a idade dessa posição escrita na tela, em
  vez de mostrar posição velha como se fosse atual.

---

## 3b. App Android (tablet)

A mesma aplicação num tablet, usando o GPS interno. A tela é a **mesma
`interface.html`** — o WebView do Android é Chromium, o mesmo motor onde ela já
está testada. O Kotlin substitui só o que o `PtxNav.exe` fazia: posição pelo
`LocationManager`, sincronização e cache em `filesDir`, e as requisições da
página atendidas por `shouldInterceptRequest` (sem abrir porta no aparelho).

```sh
cd android && ./gradlew assembleDebug
```

Detalhes e o que ainda não foi testado em aparelho: `android/LEIA-ME.md`.

## 4. Como testar no PTX

Teste **antes de instalar**. Cada etapa tem um sinal claro de passou/não passou,
e nenhuma delas altera o terminal.

### Etapa 1 — o que o terminal tem

Copie a pasta `ptx/` para o terminal (pendrive, `scp`) e rode:

```sh
sh ptx_grafico.sh
```

Ele diz se há servidor X, qual navegador existe, se há `gtk-sharp`, Qt, SDL, e
onde está a camada persistente. **Me mande essa saída** — é ela que decide o
toolkit se não houver navegador.

### Etapa 2 — achar o device do GPS

Não adivinhe o nome. `/dev/ttyS0..S3` existem em quase toda máquina Linux
**mesmo sem placa serial atrás** — abrir um desses dá erro, e é o engano mais
comum. Deixe o programa procurar:

```sh
mono PtxNav.exe --procurar-gps
```

Ele varre todas as seriais do terminal, testa as quatro velocidades em cada uma
e diz onde encontrou NMEA, já com a posição decodificada e as linhas prontas
para o `/etc/ptxnav.conf`:

```
  /dev/ttyS3                      nao abre (existe no /dev mas nao ha hardware atras dele)
  /dev/ttyUSB0                    *** GPS AQUI ***  9600 baud
                                  $GPRMC,123519.00,A,1853.61048,S,04325.95294,W,...
                                  posicao: -18.893508, -43.432549

 Poe isso no /etc/ptxnav.conf:
   SERIAL=/dev/ttyUSB0
   BAUD=9600
```

Consoles virtuais (`/dev/tty0..63`) ficam de fora de propósito — abrir aquilo
mexeria com o terminal da própria máquina.

### Etapa 3 — o laudo automático

```sh
mono PtxNav.exe --testar --serial /dev/ttyS3 --servidor http://10.188.98.200:5000
```

Confere as três coisas que costumam estar erradas em campo e imprime um laudo:

1. **A serial**: acha a velocidade sozinha, lê 8 s, conta linhas boas e ruins,
   mostra quais sentenças chegaram e a posição decodificada. Se o device não
   existir, ele lista os que existem.
2. **O servidor**: tempo de resposta, versão dos dados, tamanho da malha e
   quantos rádios Rajant estão com posição.
3. **O aufs**: se a camada "persistente" está mesmo em outra montagem. Este é o
   teste que importa — se ela cair na raiz, a gravação funciona, o teste
   passaria, e **tudo sumiria no reboot**.

Sai com código 0 quando está tudo certo. Exemplo de laudo bom:

```
--- 1. GPS na serial ----------------------------
  ok     velocidade: 9600 baud
  ok     55 linhas, 54 com checksum bom, 1 descartadas
         sentencas: GPRMC=27, GNGGA=27
  ok     posicao: -18.893508, -43.432549   (0 km/h)

--- 2. Servidor de rotas ------------------------
  ok     respondeu em 140 ms
  ok     malha viaria: 118 KB
  ok     frota Rajant: 42 radios, 39 com posicao

--- 3. Cache nas duas camadas (aufs) ------------
  ok     camada em RAM    /opt/ptxnav/cache
         montagem: /media/realroot
  ok     camada que persiste /media/realroot/opt/ptxnav/cache
```

### Etapa 3b — semear o mapa (opcional, mas evita tela vazia)

Um PTX que ainda não alcançou o servidor abre sem mapa nenhum. Para não
depender disso, gere o cache pronto **no Windows** e leve junto:

```bat
python extrair_malha.py --db "C:\Modular\Modular.db" --semear C:\navegacao\semente
```

e no terminal:

```sh
sh instalar_ptx.sh --semear /tmp/navegacao-mina/semente
```

O `hash.txt` gerado é o mesmo que o servidor publica, então a primeira
sincronização responde **304** e nada é baixado à toa.

### Etapa 4 — rodar sem instalar

```sh
mono PtxNav.exe --servidor http://10.188.98.200:5000 --serial /dev/ttyS3                 --cache /tmp/cache --cache-persistente /tmp/cache2                 --log /tmp/ptxnav.log
```

Deixe rodando e abra, **de outra máquina**, `http://<ip-do-ptx>:8090/`. Se o
mapa aparecer aí, o cliente está bom e o que falta é só o navegador local.

Confira também:

| O quê | Onde |
|---|---|
| posição própria | `http://<ip-do-ptx>:8090/pos` — `tem_fix` tem que ser `true` |
| estado geral | `http://<ip-do-ptx>:8090/estado` — `linhas_boas` subindo |
| frota | `http://<ip-do-ptx>:8090/frota` |

### Etapa 5 — a interface no próprio terminal

```sh
sh ptx_iniciar.sh
```

Ele sobe o cliente e procura um navegador. Se não achar nenhum, diz isso no log
e **mantém o cliente no ar** — volte à etapa 1.

Na tela, confira: mapa desenhado, seta azul na sua posição, seletor com
equipamentos e locais, botão **IR** traçando a rota, distância em fonte grande.

### Etapa 6 — instalar e testar o reboot

```sh
sh instalar_ptx.sh --servidor http://10.188.98.200:5000 --serial /dev/ttyS3
reboot
```

Depois que voltar, o critério de aceite da especificação:

```sh
ps -eo pid,ppid,args | grep PtxNav | grep -v grep
```

A coluna **PPID tem que ser 1**. E o cache tem que estar lá:

```sh
ls -l /media/realroot/opt/ptxnav/cache/
```

Se sumiu, a camada persistente estava errada — volte à etapa 3.

### Etapa 7 — em campo, com o veículo

Este é o teste que nenhum banco de bancada substitui:

- Com o equipamento **parado**, escolha um destino e trace. A rota tem que
  sair do ponto onde você está, **não de um nó atrás**.
- Ande. Acima de 3 km/h a tela tem que virar **só distância e seta**, sem
  interação possível.
- Pare. A tela normal tem que voltar.
- Escolha um **equipamento** como destino e confira, contra o BC Commander,
  se a posição bate. Ande com ele e veja a rota se refazer sozinha.
- Desligue o servidor. O mapa tem que continuar, a rota até local fixo tem
  que ser calculada no terminal, e a rota até equipamento tem que dizer
  claramente que depende do servidor.

> Alinhe a etapa 7 com a área de segurança da mina antes de fazer.

### Quando alguma coisa falha

| Sintoma | Provável causa |
|---|---|
| `Invalid handle to path "/dev/ttyS3"` | a porta existe no `/dev` mas não há hardware atrás dela. Rode `--procurar-gps` |
| `stty falhou` | mesmo caso acima: não é uma serial de verdade |
| `/dev/ttyS3 nao existe` | outro device; rode `--procurar-gps` |
| navegador reabrindo em laço | Chromium não sobe como root sem `--no-sandbox` (corrigido); veja `/var/log/ptxnav-navegador.log` |
| NMEA chega mas `SEM FIX` | receptor sob cobertura; leve a céu aberto |
| linhas ruins > boas | velocidade errada ou cabo ruim |
| latitude positiva | o receptor não está mandando o hemisfério |
| `nao alcancei o servidor` | IP, serviço parado, ou firewall do Windows |
| frota com 0 fixes | senha do rádio, ou `gpsSwitch` desligado no BC Commander |
| sumiu tudo no reboot | camada persistente errada (etapa 2, item 3) |
| terminal travando | instância órfã de instalação antiga — `ptx_iniciar.sh` mata as que acha |
| mapa vazio, sem rota | `SERVIDOR` apontando para `127.0.0.1`: o servidor de rotas roda no **Windows**, não no PTX |

---

## 5. Testes automatizados

```sh
sh rodar_testes.sh                                  # banco sintético
MINA_DB=/caminho/Modular.db sh rodar_testes.sh      # banco de verdade
```

Sem o `Modular.db`, os testes montam um banco sintético a partir de
`dados/grafo.json` — mesmo esquema, mesmo formato de BLOB, mesmos 325 trechos
e 318 nós. Nada precisa de rede, rádio ou GPS de verdade.

| Arquivo | O que cobre |
|---|---|
| `teste_servidor.py` | projeção, BLOB, Dijkstra, ancoragem em segmento, API |
| `teste_frota.py` | NMEA dos rádios, unidades de velocidade, BCAPI contra rádio falso, `/api/equipamentos` |
| `teste_rota_offline.py` | a rota do terminal comparada com a do servidor |
| `teste_cliente_ptx.py` | cliente Mono contra serial virtual (pty) e servidor real |
| `teste_interface.py` | a interface aberta no Chromium: mapa, rota, travas |
| `radio_falso.py` | rádio Rajant falso: TLS, framing, sha384, protobuf |

O que depende de ferramenta ausente (mono, node, chromium, rajant-api) é
**pulado**, não falha.

---

## 6. Decisões que valem registrar

**Ancoragem por segmento.** A versão anterior ancorava o veículo no nó mais
próximo. Com a máquina no meio de um trecho longo isso mandava voltar até o nó.
Agora o ponto é projetado no segmento mais próximo, dos dois lados da rota, com
nó virtual no Dijkstra — sem tocar no grafo compartilhado, que continua sendo
lido por várias requisições ao mesmo tempo.

**Unidade da velocidade do rádio.** O state do Rajant traz `gpsSpeedKnots` **e**
`gpsSpeedKph`. Procurar por "speed" e multiplicar por 1,852 sempre — o jeito
comum — dobra a leitura no firmware que preenche km/h. Aqui a unidade é lida
pelo nome do campo. Importa: é essa velocidade que trava a interface em
movimento.

**Fix inválido é fix inválido.** `gpsQuality = 0` e `gpsStatus = V` fazem a
posição ser descartada, no rádio e na serial.

**Cache da frota fica em RAM.** Posição de equipamento muda a cada ciclo;
gravar em disco a cada leitura gastaria o eMMC à toa. Malha e locais, que mudam
raramente, vão para as duas camadas.

**Peso do Dijkstra é distância.** `MaxSpeedLimit` é 0,0 nos 325 trechos do
banco: não há dado de tempo para usar.

**5 componentes desconexos** (288, 15, 9, 4 e 2 nós). Rota entre componentes
diferentes não existe — a resposta é um erro explicado, nunca uma exceção.

---

## 7. O que ficou de fora

- **Toolkit nativo** (GTK#/Windows Forms/framebuffer): só faz sentido decidir
  com a saída do `ptx_grafico.sh` rodado no equipamento. O caminho de navegador
  está pronto e testado.
- **Confirmar o device da serial** no PTX (`/dev/ttyS3` é o palpite da
  especificação; o código não fixa nada).
- **`BackgroundImage` vazio** no banco: não há raster de fundo. O mapa é
  vetorial puro.
- Os `.bat` do Windows foram **reconstruídos** — a especificação os dá como
  prontos, mas eles não vieram no material enviado.
