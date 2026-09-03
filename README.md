# Semeadura do inventário

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
python -m pytest -q      # 63 testes
python -m ruff check src tests
```

Os testes são escritos para achar bug, não para passar: cada caso reproduz uma
inconsistência que existe de verdade nos 723 ativos. Um deles é um teste de
conservação — a rede de segurança contra o inventário encolher sem ninguém notar.

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

## O que ainda não está aqui

- Coletor ICMP (a outra metade do marco M1)
- Canal B — ingestão de fatos estruturais e o grafo temporal
- Persistência: hoje a saída é JSON, não Postgres. **É a lacuna que destrava M1**
- Interface: o shell foi iniciado e removido — volta em M1, quando a
  persistência existir e a forma da API estiver acordada
- Subsistema de ação
