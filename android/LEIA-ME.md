# App Android — Navegação da Mina

A mesma aplicação do PTX, num tablet, usando o GPS interno dele.

## A ideia

A tela é a **mesma `interface.html`** que roda no PTX. O WebView do Android é
Chromium — o mesmo motor onde ela já está testada — então o comportamento é o
mesmo em campo. O Kotlin substitui só o que o `PtxNav.exe` fazia:

| PTX | Android |
|---|---|
| GPS pela serial NMEA | `LocationManager` do tablet (GPS e rede) |
| `HttpListener` respondendo `/pos`, `/malha`... | `shouldInterceptRequest` no WebView |
| cache nas duas camadas do aufs | `filesDir` do app |
| roda como serviço do systemd | Activity em tela cheia |

Uma dependência só (`androidx.webkit`, pelo `WebViewAssetLoader`). Nada de
appcompat: `AppCompatActivity` exige um tema `Theme.AppCompat`, e quando esse
acoplamento falha o erro acontece dentro do `super.onCreate` — antes de
qualquer try/catch — e o app fecha sem dizer nada.

Não há servidor HTTP dentro do app: nenhuma porta aberta, nenhum socket. As
requisições da página são atendidas direto no WebView.

`LocationManager`, e não `FusedLocationProvider`, porque tablet de campo
costuma vir sem Google Play Services — e o app não pode depender disso para
funcionar na cava.

## Compilar

Precisa do Android Studio (ou do SDK + Gradle). Não dá para compilar sem o
SDK do Android.

**JDK 21** — não use 25 nem 26 (veja *Se o build falhar*).

```sh
cd android
./gradlew assembleDebug
# app/build/outputs/apk/debug/app-debug.apk
```

O mapa já vai **dentro do APK**, em `app/src/main/assets/semente/`, então o
tablet abre com a malha desenhada antes de falar com o servidor. Para
atualizar essa cópia quando a lavra mudar:

```sh
python3 servidor/extrair_malha.py --db dados/Modular.db \
        --semear android/app/src/main/assets/semente
```

O build copia `ptx/interface.html` para os assets automaticamente. **Não edite
a cópia em `app/src/main/assets/`**: ela é sobrescrita, e duas telas diferentes
acabariam se comportando diferente no campo.

## Se o build falhar

**`IllegalArgumentException: 25.0.2`** (ou outro número acima de 21), no meio
de um stack trace do daemon do Kotlin:

O Gradle está rodando num JDK novo demais. O toolchain do Android aqui
(AGP 8.5 / Kotlin 1.9) vai até o **JDK 21** — e nenhum toolchain do Android
compila com 25 ou 26.

```
Settings > Build, Execution, Deployment > Build Tools > Gradle
Gradle JDK > Download JDK...
   Version: 21                        <- é esta a linha que importa
   Vendor : Eclipse Temurin (ou Azul Zulu)
```

O Android Studio recente já vem com um JBR novo demais, então o "embutido"
pode não servir: às vezes é preciso baixar o 21 mesmo. Depois de baixar,
selecione-o no campo **Gradle JDK**.

Depois: **Build > Clean Project**. A pasta `app/build` guarda o cache
incremental do Kotlin, e ele fica corrompido quando a compilação morre no
meio — é o que a mensagem "Could not flush incremental caches" está dizendo.

O `build.gradle.kts` da raiz agora barra isso antes de começar, com essa
instrução na tela, em vez de deixar o erro aparecer lá dentro.

**O app instala mas fecha na hora de abrir**

**Antes de qualquer coisa: confira qual APK está no aparelho.** A tela de
falha e o menu (**Estado > Versão**) mostram a versão e o momento do build.
Se o carimbo não mudou depois de recompilar, o aparelho está com o APK
antigo, e consertar o código não adianta.

Instalação limpa, quando houver dúvida:

```sh
adb uninstall br.com.mina.navegacao
```

ou desinstale pelo próprio tablet. Depois **Build > Clean Project** e Run.

Uma falha durante o arranque aparece **na própria tela**, com a pilha e a
data, pronta para copiar. Também fica em `filesDir/ultima_falha.txt`.

A tela de falha mostra o que aconteceu **naquela abertura**. Se a execução
anterior tinha caído, isso não trava o arranque: o resumo aparece no menu, em
**Estado > Última falha**. Confira sempre a **data e as linhas** da pilha — foi
fácil confundir um relatório antigo com um problema novo.

Isso vale inclusive para falhas dentro do `super.onCreate`, que nenhum
try/catch da Activity alcança: quem grava é a classe `NavegacaoApp`, que roda
antes de qualquer tela.

Se mesmo na segunda abertura não aparecer nada, com o tablet ligado por USB:

```sh
adb logcat -s AndroidRuntime:E
```

**`Android resource linking failed`**: alguma referência `@tipo/nome` do
manifesto não existe em `res/`. `testes/teste_android.py` confere todas.

## Configurar

Endereço do servidor: padrão `http://10.188.111.249:5000`, alterável pelo menu
do próprio app (☰ → Servidor). Fica gravado no aparelho.

O nome do equipamento sai do modelo do tablet; para fixar outro, use
`Config.callsign`.

## Permissões

Localização precisa ser concedida na primeira abertura. Sem ela o app abre,
mostra o mapa do cache e avisa que está sem posição.

O `res/xml/rede.xml` libera HTTP simples só para as faixas privadas
(10.x, 172.16.x, 192.168.x): o servidor da mina fala HTTP puro, a internet
inteira não precisa disso.

## O que ainda não foi testado

O Kotlin **não foi compilado nem rodado em tablet** — não há SDK do Android na
máquina onde ele foi escrito. O que está provado:

- a interface roda em cima das respostas do app, no Chromium
  (`testes/teste_android.py`), incluindo mapa, trava por velocidade, fix
  vencendo por idade, desmonte e troca de servidor pelo menu;
- os campos que o Kotlin devolve batem com os que a interface consome — o
  teste lê os campos direto do `Posicao.kt` e do `Sincronizacao.kt` e compara;
- a `interface.html` dos assets é byte a byte igual à do PTX.

Falta o que só o aparelho responde: permissão, GPS de verdade, comportamento
com a tela travada e consumo de bateria.
