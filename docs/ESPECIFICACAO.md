# Sistema de Navegação da Mina — Especificação de Construção

> Documento para o Claude Code. Contém tudo que já foi descoberto, medido e validado.
> **Nada aqui é suposição não marcada.** O que é incerto está sinalizado com ⚠️.

---

## 1. Contexto e objetivo

Mina a céu aberto com frota gerenciada por **Modular Mining DISPATCH**. Objetivo: um sistema
de navegação que mostra o mapa da mina e traça rota até um equipamento ou local escolhido.

**Arquitetura decidida:**

```
[Servidor Windows]  servidor_rotas.exe
   ├─ lê Modular.db (somente leitura), reextrai a cada 15 min se o hash mudar
   └─ publica API HTTP :8080
                    ↓ (rede/malha Rajant)
[PTX dedicado]  aplicação gráfica
   ├─ consome a API, guarda cache em disco, funciona offline
   ├─ lê GPS local direto da serial (NMEA)
   └─ desenha mapa + rota em tela cheia
```

**IMPORTANTE — este PTX é DEDICADO:** não roda DISPATCH. Só a nossa aplicação.
Consequências:
- Não há competição por CPU/RAM. Preocupações de "leveza" são secundárias.
- **Não existe pacote de DISPATCH para farejar.** A abordagem antiga (tcpdump +
  parse de pacote UDP) **não funciona aqui**. Posição vem do hardware.
- A aplicação pode ocupar a tela inteira.

---

## 2. Fonte de posição: serial NMEA

**Confirmado pelo usuário:** `cat /dev/ttyXX3` já retorna as coordenadas GPS.
⚠️ O nome exato do device precisa ser confirmado no equipamento (`/dev/ttyS3`,
`/dev/ttyUSB3` ou similar). Tornar **configurável**, nunca fixo no código.

### Requisitos do leitor de GPS
- Abrir o device em modo raw. Se `stty` estiver disponível, configurar baud
  (testar 9600, 4800, 38400, 115200 — 9600 é o mais comum).
- Ler linha a linha. Processar `$GPRMC`/`$GNRMC` (posição + velocidade + rumo)
  e `$GPGGA`/`$GNGGA` (posição + altitude + qualidade do fix).
- **Validar o checksum NMEA** (XOR de todos os bytes entre `$` e `*`). Descartar
  linhas corrompidas — serial suja é comum em veículo.
- Aceitar os prefixos `$GP`, `$GN`, `$GL`, `$GA` (GPS, GNSS misto, GLONASS, Galileo).
- **Reconectar sozinho** se o device sumir (cabo, reboot do receptor).
- Marcar o fix como inválido se não chegar nada por >10 s. A interface precisa
  avisar o operador — navegar com posição velha é pior que não navegar.

### Conversão de coordenadas NMEA
NMEA entrega `ddmm.mmmm` (latitude) e `dddmm.mmmm` (longitude), com hemisfério separado.

```
graus = int(campo / 100) + (campo % 100) / 60
se hemisfério for S ou W → negativo
```

---

## 3. O banco: Modular.db

SQLite 3. **Abrir SEMPRE em modo somente-leitura**: `file:Modular.db?mode=ro`.
⚠️ O arquivo analisado parecia ser a cópia local de um PTX. Apontar de preferência
para o banco do servidor DISPATCH, que atualiza primeiro.

### 3.1 Tabelas relevantes (verificadas)

| Tabela | Linhas | Conteúdo |
|---|---|---|
| `TopologicalObject` | 1267 | **Todas as geometrias.** Colunas: `Id`, `Name`, `Geometry` (BLOB), `GeometryType`, `IsActive`, `IsHazard` |
| `Road` | 325 | Trechos viários. `Id` (= `TopologicalObject.Id`), `MaxSpeedLimit`, `MinSpeedLimit`, `IsClosed`, `LocationTypeID` |
| `FunctionalLocation` | 942 | Locais nomeados. `Id`, `LocationTypeID`, `Elevation` |
| `LocationType` | — | Dicionário de tipos (Crusher, Dump, Bench, Pit, Maintenance Shop, Fuel Bay, Call Point...) |
| `GeographicRegion` | 1 | `TransformationData` (XML da projeção), `BackgroundImage` (**vazio no banco analisado**) |

Junções: `Road.Id = TopologicalObject.Id` (325/325 casam) e
`FunctionalLocation.Id = TopologicalObject.Id` (942/942 casam).

### 3.2 Formato do BLOB `Geometry` — **decodificado por análise binária**

```
byte 0        : versão (0x01)
bytes 1-4     : código do tipo, uint32 little-endian
                1001 = ponto | 1002 = polilinha | 1003 = polígono
depois:
  ponto (1001)     : nada. Vértices começam no offset 5. n = 1
  polilinha (1002) : uint32 = nº de vértices. Vértices no offset 9
  polígono (1003)  : uint32 = nº de anéis, uint32 = nº de vértices.
                     Vértices no offset 13
vértices      : sequência de (double X, double Y, double Z), 24 bytes cada,
                little-endian. X=Easting, Y=Northing, Z=elevação, na grid da mina
```

Conferência de tamanho: ponto = 1+4+24 = **29** ✓ · polilinha de 4 pontos =
1+4+4+96 = **105** ✓ · polígono de 9 pontos = 1+4+4+4+216 = **229** ✓

Distribuição encontrada: `GeometryType` 1 → 940 pontos, 4 → 2 polígonos, 5 → 325 estradas.
(Atenção: `GeometryType` da coluna ≠ código dentro do blob. Usar o código do blob.)

### 3.3 A topologia do grafo está no NOME

`TopologicalObject.Name` de uma estrada tem o formato **`"NÓ_A - NÓ_B"`**, separado por
espaço-hífen-espaço. Exemplo: `"PC-02-PDE_S - PC-RMP-PDE_S-06"`.

Grafo resultante: **318 nós, 325 arestas**. Tratar como **não-direcionado**.

⚠️ Nem toda estrada tem o separador. Ignorar as que não têm ao montar o grafo,
mas ainda desenhá-las no mapa.

### 3.4 Projeção — em `GeographicRegion.TransformationData`

XML UTF-16 com `<conversion type="LEGACY">`. Campos usados:

```xml
<Type>utmWGS84 6378137</Type>   <!-- semi-eixo maior A -->
<Latitude>-18.93882038</Latitude>    <!-- phi0, graus -->
<Longitude>-43.41417951</Longitude>  <!-- lam0, graus -->
<Scale>0.99994276</Scale>            <!-- k0 -->
<Eccentricity>...</Eccentricity>     <!-- e; e2 = e² -->
<East>...</East> <North>...</North>  <!-- origem da grid -->
<Rotation>...</Rotation>             <!-- graus -->
```

É uma **Transversa de Mercator com rotação e origem deslocadas**. Algoritmo
(grid → WGS84):

1. `dx = E - baseE`, `dy = N - baseN`
2. Rotacionar: `x = cos(rot)·dx + sin(rot)·dy` · `y = -sin(rot)·dx + cos(rot)·dy`
3. TM inversa padrão com `A`, `e2`, `k0`, `phi0`, `lam0`

**Teste de regressão obrigatório** (validado em campo pelo bridge existente):

```
grid(665139.64, 7910269.13) → lat -18.893508, lon -43.432549   (tolerância 1e-4)
```

A projeção inversa (WGS84 → grid) foi implementada e validada: erro de ida-e-volta
**0,04 mm**. Necessária para ancorar a posição do GPS na malha.

### 3.5 Limitações conhecidas do dado

- **`MaxSpeedLimit` = 0.0 em todas as 325 estradas.** Não há dado de velocidade.
  O peso do Dijkstra tem que ser **distância**, não tempo.
- **10 trechos com `IsClosed = 1`.** Excluir do roteamento; ainda desenhar (em vermelho).
- **`BackgroundImage` vazio.** Não há raster de fundo. O mapa é vetorial puro.
- **O grafo tem 5 componentes desconexos**: 288, 15, 9, 4 e 2 nós. O maior cobre
  90,6%. Rota entre componentes diferentes **não existe** — retornar erro claro,
  nunca exceção.
- 72 nós têm grau 1 (becos sem saída). Normal numa mina.

---

## 4. Servidor de rotas — **JÁ CONSTRUÍDO E TESTADO**

Arquivo: `servidor_rotas.py` (Python 3, **só biblioteca padrão**, sem dependências).
Empacotar com PyInstaller (`gerar_exe.bat`) e instalar como tarefa agendada
(`instalar_servico.bat`).

### Comportamento
- Calcula SHA-256 do `Modular.db` a cada `--intervalo` (padrão 900 s).
  **Só reextrai se o hash mudou.**
- Troca a malha em memória **atomicamente** — requisição no meio da reextração
  recebe a malha antiga inteira, nunca um estado parcial.
- O hash vira `ETag`. Cliente com `If-None-Match` igual recebe **304 sem corpo**.
  Medido: 121 KB na primeira vez → **0 bytes** nas seguintes.

### API

| Endpoint | Retorna |
|---|---|
| `GET /api/saude` | `{ok, versao, hash, erro, extraido_em, ultima_checagem}` |
| `GET /api/versao` | `{hash, extraido_em, trechos, nos, locais}` + ETag |
| `GET /api/locais?tipo=&q=` | `{total, locais:[{id,nome,tipo,lat,lon,elevacao,grid_e,grid_n,ativa}]}` |
| `GET /api/malha` | GeoJSON FeatureCollection (LineString) |
| `GET /api/areas` | GeoJSON FeatureCollection (Polygon) |
| `GET /api/rota?de_lat=&de_lon=&para=NOME` | ver abaixo |
| `GET /api/rota?de=NÓ_A&para=NÓ_B` | idem, entre nós nomeados |
| `GET /` | mapa Leaflet de teste (uso em bancada, precisa de internet) |

**Resposta de `/api/rota` (sucesso):**
```json
{"ok":true,"origem":"PDE-DRENO","destino":"OFICINA","distancia_m":6871.4,
 "n_nos":47,"nos":["PDE-DRENO","PC-PDR-03","..."],
 "geometria":{"type":"LineString","coordinates":[[lon,lat],...]},
 "ancorou_em":"PDE-DRENO","dist_ate_malha_m":0.3,"destino_pedido":"OFICINA"}
```
**Falha:** `{"ok":false,"erro":"...","detalhe":"..."}` com HTTP 404 ou 400.

### Roteamento
Dijkstra com fila de prioridade. Peso = comprimento em metros do trecho (somatório
das distâncias euclidianas entre vértices consecutivos, **na grid**, que já é métrica).
Ancoragem: converte lat/lon → grid e acha o nó mais próximo por distância euclidiana.

⚠️ **Melhoria pendente:** a ancoragem hoje é no **nó** mais próximo. O correto é
projetar no **segmento** mais próximo (ponto-em-reta) e partir dali. Com o veículo
no meio de um trecho longo, a rota atual pode mandar voltar até o nó.

**Validado:** rota de 8.297 m por 57 nós entre os extremos da mina; rota de
6.871 m por 47 nós a partir de coordenada GPS real, ancorando a 0,3 m.

---

## 5. Cliente do PTX — **PARCIALMENTE CONSTRUÍDO**

Existe `PtxMapClient.exe` (C#/Mono, 44 KB) que já faz: sincronização com cache em
disco, gravação atômica, leitura de NMEA por TCP, servidor HTTP local e página de
mapa em canvas. **Testado, inclusive com o servidor derrubado.**

**A REESCREVER** para o PTX dedicado:
1. Trocar a leitura de TCP:10110 por **leitura direta da serial**.
2. Trocar a página web por **aplicação gráfica nativa** (ver seção 6).

Reaproveitar deste código: a lógica de sincronização com ETag, a gravação atômica
do cache, o fallback offline e o algoritmo de desenho em canvas (traduzível para
qualquer toolkit).

### Princípio de projeto a manter
**Não interpretar o GeoJSON no código nativo se puder evitar.** No cliente atual
o C# só guarda bytes e entrega; quem interpreta é o JS. Isso manteve o executável
em 44 KB. Num app nativo será preciso parsear, mas guardar a forma
já projetada em coordenadas de tela, não reprojetar a cada quadro.

### Persistência — **RESTRIÇÃO CRÍTICA DO PTX**
O PTX usa **aufs**: a raiz `/` é uma união cuja camada gravável está em **RAM**.
**Tudo que se escreve em `/` desaparece no reboot.**

- O disco real está montado em **`/media/realroot`** (eMMC, ~13 GB livres).
- Descoberto na prática: uma instalação em `/opt` e `/usr/local/bin` sumiu no reboot.
- **Gravar sempre nas duas camadas:** em `/media/realroot/...` (persiste) e em
  `/...` (vale imediatamente, sem reiniciar).
- O gancho de boot também: `systemctl enable` cria symlink só na camada RAM.
  Criar o symlink à mão em `/media/realroot/etc/systemd/system/multi-user.target.wants/`.
- **Corolário:** não dá para `apt install` uma biblioteca gráfica — ela some no reboot.
  **Só usar toolkit que já esteja no terminal.**
- **Log fica em RAM de propósito** (evita desgaste do eMMC), com rotação **em tempo
  de execução**, não só na inicialização.

---

## 6. A aplicação gráfica — **A CONSTRUIR**

⚠️ **BLOQUEIO:** o toolkit ainda não foi determinado. Rodar `ptx_grafico.sh` no
equipamento antes de escolher. Ele reporta: servidor X rodando, framebuffer,
navegadores instalados, `libwebkit`, `gtk-sharp.dll`, `System.Windows.Forms.dll` +
`libgdiplus`, Qt, SDL, Java, resolução e tipo de entrada (toque ou mouse).

### Ordem de preferência
1. **Navegador em modo quiosque** apontando para `localhost` — a interface HTML já
   existe e está testada. Zero código novo. Verificar se há `chromium`, `surf`,
   `midori` ou `libwebkit` no terminal.
2. **GTK# com Mono** — Mono já existe (confirmado). Precisa de `gtk-sharp.dll`.
3. **Windows Forms com Mono** — precisa de `System.Windows.Forms.dll` + `libgdiplus`.
4. **Desenho direto em `/dev/fb0`** — último recurso, se não houver servidor X.

### Requisitos de interface
- **Tela cheia**, sem depender de gerenciador de janelas.
- **Alvos de toque grandes** (mínimo 48 px) — operador de luva.
- **Alto contraste**, legível sob sol direto e à noite.
- Sem CDN, sem internet: **todo recurso embutido**.
- Elementos: seletor de destino agrupado por tipo, botão traçar, botão limpar,
  distância em fonte grande, seta de posição própria orientada pelo rumo (COG),
  zoom, botão "centrar em mim", aviso quando o servidor está offline ou o GPS falhou.
- Locais só aparecem acima de certo zoom, e rótulos acima de outro, para não poluir.
- Trechos fechados (`IsClosed`) em vermelho, distintos dos abertos.

### Segurança operacional — **requisito, não sugestão**
Navegação em equipamento de mina é risco de distração. O sistema deve:
- Permitir escolher destino **apenas com o veículo parado** (SOG abaixo de um limiar).
- Em movimento, exibir o mínimo: direção e distância, em fonte grande, sem interação.
- **Alinhar com a área de segurança da mina antes de colocar em operação.**

---

## 7. Alimentar o Rajant BreadCrumb (opcional, já existe)

O `PtxGpsBridge` atual serve NMEA numa porta TCP para o BreadCrumb ler, o que faz a
malha Rajant conhecer a posição de cada equipamento. Num PTX dedicado isso continua
possível: reemitir na porta TCP as sentenças lidas da serial, **sem tcpdump nenhum**.

⚠️ Ler a posição dos **outros** equipamentos a partir do Rajant ainda não tem
interface definida. Investigar BC|Commander (API/exportação) ou SNMP no BreadCrumb.
Enquanto isso, o sistema funciona mostrando **a posição própria + mapa + rota**.

---

## 8. O que já foi medido (não repetir o trabalho)

| Medição | Resultado |
|---|---|
| CPU do parser antigo | 0,17 s para 60.000 pacotes (~0,014% de um núcleo) |
| RSS do Mono | 28,3 MB padrão · 25,2 MB com `MONO_GC_PARAMS=nursery-size=1m` |
| Jitter de agendamento com captura ativa | diferença dentro do ruído (p99 ~1 ms) |
| Crescimento do log | 3,8 MB/dia a 1 fix/s |
| Malha completa | 325 trechos, 1.300 vértices, 2,7 × 6,2 km |
| Payload da API | malha 121 KB · locais 164 KB · total 279 KB |
| Cliente Mono em execução | ~53 MB RSS |

**Conclusão das medições:** o gargalo nunca foi o parser. Se houver travamento,
suspeitar de **instâncias duplicadas/órfãs** acumuladas de instalações anteriores
(cada par mono+tcpdump = ~33 MB e uma captura extra em softirq).

---

## 9. Arquivos existentes

| Arquivo | Estado |
|---|---|
| `servidor_rotas.py` | **Pronto e testado.** Servidor completo |
| `gerar_exe.bat` / `instalar_servico.bat` | **Prontos.** Build e serviço no Windows |
| `extrair_malha.py` | **Pronto.** Extrator standalone → GeoJSON + grafo |
| `PtxMapClient.cs` + `Pagina.cs` | **Pronto**, a adaptar para serial + GUI nativa |
| `PtxGpsBridge.cs` | Bridge por captura. **Não serve no PTX dedicado** |
| `ptx_grafico.sh` | Sonda de ambiente gráfico. **Rodar antes de codar a GUI** |
| `ptx_inventario.sh` | Inventário de bancos/mapas no PTX |
| `malha_viaria.geojson`, `locais.geojson`, `areas.geojson`, `grafo.json` | Dados já extraídos |

---

## 10. Ordem sugerida de construção

1. **Confirmar o device serial** e escrever o leitor NMEA com checksum e reconexão.
2. **Rodar `ptx_grafico.sh`** e escolher o toolkit com base no resultado.
3. **Subir o servidor de rotas** no Windows e validar pelo navegador (`http://ip:8080/`).
4. **Cliente**: sincronização + cache persistente nas duas camadas do aufs + offline.
5. **GUI**: mapa, posição própria, seletor de destino, rota.
6. **Serviço**: systemd com symlink de boot gravado em `/media/realroot`.
7. **Teste de reboot** — o critério de aceite é `PPID = 1` e a aplicação subindo sozinha.
8. **Melhorar a ancoragem** para projeção em segmento (seção 4).

---

## 11. Convenções

- Mensagens de erro e interface **em português**.
- Toda leitura de banco em modo `ro`.
- Nada de CDN nem dependência de internet no PTX.
- Todo caminho de arquivo e endereço de servidor **configurável por argumento**.
- Scripts de shell em **POSIX `sh`** (o PTX não tem bash garantido) e **sem CRLF**.
- Falha de rede nunca pode derrubar a interface: degradar para o cache.
