#!/bin/sh
# =====================================================================
#  ptx_iniciar.sh — sobe a navegacao no PTX e mantem ela de pe'.
#
#  Faz duas coisas:
#    1. roda o PtxNav.exe (le a serial, sincroniza, serve a interface);
#    2. abre a interface em tela cheia no navegador que existir no terminal.
#
#  Se um dos dois morrer, este script levanta de novo. Nao instala nada:
#  por causa do aufs, o que for instalado depois some no reboot.
#
#  POSIX sh de proposito: o PTX nao tem bash garantido.
# =====================================================================
set -u

CONF=${PTXNAV_CONF:-/etc/ptxnav.conf}
[ -r "$CONF" ] && . "$CONF"

BASE=${BASE:-/opt/ptxnav}
EXE=${EXE:-$BASE/PtxNav.exe}
SERVIDOR=${SERVIDOR:-http://10.188.111.249:5000}
PORTA=${PORTA:-8090}
SERIAL=${SERIAL:-/dev/ttyxx3}
BAUD=${BAUD:-auto}
CACHE=${CACHE:-$BASE/cache}
CACHE_PERSISTENTE=${CACHE_PERSISTENTE:-/media/realroot/opt/ptxnav/cache}
INTERVALO=${INTERVALO:-300}
NMEA_TCP=${NMEA_TCP:-0}
LOG=${LOG:-/var/log/ptxnav.log}
LOG_NAVEGADOR=${LOG_NAVEGADOR:-/var/log/ptxnav-navegador.log}
LOG_MAX_KB=${LOG_MAX_KB:-2048}
LIMITE_PARADO=${LIMITE_PARADO:-3}
ABRIR_NAVEGADOR=${ABRIR_NAVEGADOR:-1}
DISPLAY=${DISPLAY:-:0}
export DISPLAY

URL="http://127.0.0.1:$PORTA/"
CLIENTE_PID=""
NAV_PID=""
NAV_CMD=""
NAV_DESDE=0
NAV_QUEDAS=0

diz() { echo "$(date '+%Y-%m-%d %H:%M:%S') [iniciar] $*"; }

# --------------------------------------------------------------- limpeza
encerra() {
  diz "encerrando"
  [ -n "$NAV_PID" ] && kill "$NAV_PID" 2>/dev/null
  [ -n "$CLIENTE_PID" ] && kill "$CLIENTE_PID" 2>/dev/null
  exit 0
}
trap encerra INT TERM

# ------------------------------------------------ instancias orfas antes
# Secao 8 da especificacao: travamento antigo era instancia duplicada, nunca
# o parser. Antes de subir, mata o que sobrou de instalacao anterior.
mata_orfaos() {
  for p in $(ps -eo pid,args 2>/dev/null | grep -i 'PtxNav.exe\|PtxMapClient.exe\|PtxGpsBridge.exe' \
             | grep -v grep | awk '{print $1}'); do
    [ "$p" = "$$" ] && continue
    diz "matando instancia orfa: pid $p"
    kill "$p" 2>/dev/null
  done
}

# ----------------------------------------------------------- porta aberta?
# Sem netcat nem curl garantidos: le /proc/net/tcp direto.
# Passar um arquivo inexistente para o awk aborta o programa inteiro, entao a
# lista e' montada com o que existe (nem todo kernel expoe tcp6).
porta_aberta() {
  hex=$(printf '%04X' "$PORTA")
  arqs=""
  [ -r /proc/net/tcp ] && arqs="/proc/net/tcp"
  [ -r /proc/net/tcp6 ] && arqs="$arqs /proc/net/tcp6"
  [ -n "$arqs" ] || return 1
  # shellcheck disable=SC2086
  awk -v h=":$hex" '$2 ~ h && $4 == "0A" {achou=1} END {exit !achou}' $arqs 2>/dev/null
}

espera_porta() {
  if [ ! -r /proc/net/tcp ]; then
    diz "sem /proc/net/tcp para conferir a porta; esperando 5 s"
    sleep 5
    return 0
  fi
  i=0
  while [ "$i" -lt 40 ]; do
    porta_aberta && return 0
    kill -0 "$CLIENTE_PID" 2>/dev/null || return 1
    sleep 1
    i=$((i + 1))
  done
  return 1
}

# --------------------------------------------------------------- cliente
sobe_cliente() {
  [ -r "$EXE" ] || { diz "ERRO: nao achei $EXE"; return 1; }
  command -v mono >/dev/null 2>&1 || { diz "ERRO: mono nao encontrado"; return 1; }
  mkdir -p "$CACHE" "$CACHE_PERSISTENTE" 2>/dev/null
  diz "subindo o cliente (serial $SERIAL, servidor $SERVIDOR)"
  # nursery pequena: medido em 25,2 MB de RSS contra 28,3 MB no padrao
  MONO_GC_PARAMS=nursery-size=1m \
  mono "$EXE" \
    --servidor "$SERVIDOR" \
    --porta "$PORTA" \
    --serial "$SERIAL" \
    --baud "$BAUD" \
    --cache "$CACHE" \
    --cache-persistente "$CACHE_PERSISTENTE" \
    --intervalo "$INTERVALO" \
    --nmea-tcp "$NMEA_TCP" \
    --log "$LOG" \
    --log-max-kb "$LOG_MAX_KB" \
    --limite-parado "$LIMITE_PARADO" &
  CLIENTE_PID=$!
  diz "cliente no pid $CLIENTE_PID"
}

# ------------------------------------------------------------- navegador
# Ordem de preferencia da secao 6: quiosque em navegador ja' instalado.
# Nada de dillo/netsurf: a interface precisa de JavaScript de verdade.
acha_navegador() {
  for n in chromium chromium-browser chrome google-chrome google-chrome-stable; do
    if command -v "$n" >/dev/null 2>&1; then
      # --no-sandbox: o servico roda como root, e o Chromium se recusa a subir
      #   como root com o sandbox ligado — sai na hora, sem dizer nada na tela.
      # --user-data-dir: sem HOME gravavel ele tambem morre no arranque.
      # --disable-dev-shm-usage: /dev/shm de terminal embarcado e' pequeno.
      SANDBOX=""
      [ "$(id -u)" = "0" ] && SANDBOX="--no-sandbox"
      NAV_CMD="$n --kiosk --incognito --no-first-run --noerrdialogs \
--disable-infobars --disable-translate --disable-session-crashed-bubble \
--overscroll-history-navigation=0 --disable-dev-shm-usage \
--check-for-update-interval=31536000 --password-store=basic \
--user-data-dir=$BASE/navegador $SANDBOX $URL"
      return 0
    fi
  done
  for n in firefox firefox-esr; do
    if command -v "$n" >/dev/null 2>&1; then
      NAV_CMD="$n --kiosk $URL"; return 0
    fi
  done
  if command -v midori >/dev/null 2>&1; then
    NAV_CMD="midori -e Fullscreen -a $URL"; return 0
  fi
  for n in surf luakit qutebrowser epiphany epiphany-browser; do
    if command -v "$n" >/dev/null 2>&1; then
      NAV_CMD="$n $URL"; return 0
    fi
  done
  return 1
}

tem_x() {
  [ -n "${DISPLAY:-}" ] || return 1
  ls /tmp/.X11-unix/ >/dev/null 2>&1 || return 1
  return 0
}

sobe_navegador() {
  [ -n "$NAV_CMD" ] || return 1
  diz "abrindo a interface: $(echo "$NAV_CMD" | awk '{print $1}')"
  mkdir -p "$BASE/navegador" 2>/dev/null
  [ -n "${HOME:-}" ] || HOME=$BASE; export HOME
  # a saida do navegador vai para arquivo: sem isso, quando ele morre no
  # arranque nao sobra nenhuma pista do motivo
  : > "$LOG_NAVEGADOR" 2>/dev/null
  # shellcheck disable=SC2086
  $NAV_CMD >>"$LOG_NAVEGADOR" 2>&1 &
  NAV_PID=$!
  NAV_DESDE=$(data_agora)
}

data_agora() {
  # segundos desde o boot; nao depende de date +%s exotico
  awk '{print int($1)}' /proc/uptime 2>/dev/null || echo 0
}

# Navegador que morre logo depois de abrir esta com problema de verdade;
# reabrir de 5 em 5 s so' enche o log. Espaca e mostra o porque.
trata_queda_do_navegador() {
  agora=$(data_agora)
  vida=$((agora - NAV_DESDE))
  if [ "$vida" -lt 15 ]; then
    NAV_QUEDAS=$((NAV_QUEDAS + 1))
  else
    NAV_QUEDAS=0
  fi
  if [ "$NAV_QUEDAS" -ge 3 ]; then
    espera=$((NAV_QUEDAS * 20))
    [ "$espera" -gt 120 ] && espera=120
    diz "o navegador caiu $NAV_QUEDAS vezes seguidas em menos de 15 s."
    diz "ultimas linhas de $LOG_NAVEGADOR:"
    tail -5 "$LOG_NAVEGADOR" 2>/dev/null | sed 's/^/    /'
    diz "tentando de novo em ${espera}s. A interface segue em $URL"
    sleep "$espera"
  fi
  sobe_navegador
}

# ============================================================== principal
diz "=== navegacao da mina ==="
mata_orfaos
sobe_cliente || exit 1

if espera_porta; then
  diz "interface no ar em $URL"
else
  diz "AVISO: a porta $PORTA nao abriu; o cliente pode estar sem serial"
fi

if [ "$ABRIR_NAVEGADOR" = "1" ]; then
  if ! tem_x; then
    diz "AVISO: sem servidor X em DISPLAY=$DISPLAY."
    diz "       O cliente continua no ar: abra $URL de outra maquina."
    diz "       Rode ptx_grafico.sh para ver o que este terminal tem."
    ABRIR_NAVEGADOR=0
  elif ! acha_navegador; then
    diz "AVISO: nenhum navegador com JavaScript encontrado neste terminal."
    diz "       Nao adianta instalar: o aufs apaga no reboot."
    diz "       Rode ptx_grafico.sh e escolha o toolkit pela saida dele."
    ABRIR_NAVEGADOR=0
  else
    sobe_navegador
  fi
fi

# ------------------------------------------------------------ supervisao
while :; do
  sleep 5
  if ! kill -0 "$CLIENTE_PID" 2>/dev/null; then
    diz "o cliente caiu; subindo de novo"
    sobe_cliente || sleep 10
    espera_porta >/dev/null 2>&1
  fi
  if [ "$ABRIR_NAVEGADOR" = "1" ] && ! kill -0 "$NAV_PID" 2>/dev/null; then
    diz "o navegador caiu; abrindo de novo"
    trata_queda_do_navegador
  fi
done
