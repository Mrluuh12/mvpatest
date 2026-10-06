#!/bin/sh
# =====================================================================
#  instalar_ptx.sh — instala a navegacao no PTX, nas DUAS camadas.
#
#  POR QUE DUAS CAMADAS
#    O PTX usa aufs: a raiz / e' uma uniao cuja camada gravavel esta' em RAM.
#    Tudo que se escreve em / desaparece no reboot. O disco de verdade esta'
#    montado em /media/realroot. Entao gravamos:
#      - em /media/realroot/...  para sobreviver ao reboot
#      - em /...                 para valer agora, sem reiniciar
#
#    O mesmo vale para o gancho de boot: "systemctl enable" cria o symlink
#    so' na camada RAM. Aqui ele e' criado a mao nas duas.
#
#  USO
#    sh instalar_ptx.sh --servidor http://10.188.98.200:5000 --serial /dev/ttyS3
#    sh instalar_ptx.sh --remover
#
#  So' precisa do que ja' existe no terminal. Nao instala biblioteca nenhuma.
# =====================================================================
set -u

# Padroes da mina. Passe --servidor / --serial para mudar.
SERVIDOR="http://10.188.111.249:5000"
SERIAL="/dev/ttyxx3"
BAUD="auto"
PORTA="8090"
NMEA_TCP="0"
REALROOT=""
REMOVER=0
FORCAR_CONF=0
SEMENTE=""
AQUI=$(cd "$(dirname "$0")" && pwd)

diz() { echo "  $*"; }
erro() { echo "ERRO: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --servidor)  SERVIDOR=$2; shift 2 ;;
    --serial)    SERIAL=$2;   shift 2 ;;
    --baud)      BAUD=$2;     shift 2 ;;
    --porta)     PORTA=$2;    shift 2 ;;
    --nmea-tcp)  NMEA_TCP=$2; shift 2 ;;
    --realroot)  REALROOT=$2; shift 2 ;;
    --remover)   REMOVER=1;   shift ;;
    --forcar-conf) FORCAR_CONF=1; shift ;;
    --semear)    SEMENTE=$2;  shift 2 ;;
    -h|--ajuda|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
    *) erro "argumento desconhecido: $1" ;;
  esac
done

[ "$(id -u)" = "0" ] || erro "rode como root"

# ------------------------------------------- acha a camada que persiste
if [ -z "$REALROOT" ]; then
  for m in $(awk '$1 ~ /^\/dev\/(mmcblk|sd|nvme)/ {print $2}' /proc/mounts 2>/dev/null); do
    [ "$m" = "/" ] && continue
    if [ -d "$m/etc" ] && [ -d "$m/usr" ]; then REALROOT=$m; break; fi
  done
fi
[ -z "$REALROOT" ] && [ -d /media/realroot/etc ] && REALROOT=/media/realroot

echo "=========== instalacao da navegacao da mina ==========="
if [ -n "$REALROOT" ]; then
  diz "camada persistente: $REALROOT"
else
  diz "AVISO: nao achei a camada persistente."
  diz "       A instalacao vale so' ate' o proximo reboot."
  diz "       Confira com: mount | grep realroot"
fi

BASE=/opt/ptxnav
UNIT=/etc/systemd/system/ptxnav.service
WANTS=/etc/systemd/system/multi-user.target.wants/ptxnav.service
CONF=/etc/ptxnav.conf

# lista de prefixos onde tudo e' gravado (vazio = raiz)
PREFIXOS=""
[ -n "$REALROOT" ] && PREFIXOS="$REALROOT"
PREFIXOS="$PREFIXOS ."      # "." representa a raiz corrente

caminho() { # $1 = prefixo, $2 = caminho absoluto
  if [ "$1" = "." ]; then echo "$2"; else echo "$1$2"; fi
}

# ======================================================== remocao
if [ "$REMOVER" = "1" ]; then
  echo
  diz "parando o servico"
  command -v systemctl >/dev/null 2>&1 && systemctl stop ptxnav 2>/dev/null
  for p in $PREFIXOS; do
    for alvo in "$(caminho "$p" "$WANTS")" "$(caminho "$p" "$UNIT")"; do
      [ -e "$alvo" ] && { rm -f "$alvo"; diz "removido $alvo"; }
    done
    d=$(caminho "$p" "$BASE")
    [ -d "$d" ] && { rm -rf "$d"; diz "removido $d"; }
  done
  command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload 2>/dev/null
  diz "a configuracao $CONF foi mantida (apague a mao se quiser)"
  echo "removido."
  exit 0
fi

# ======================================================== verificacoes
[ -r "$AQUI/PtxNav.exe" ] || erro "nao achei $AQUI/PtxNav.exe (rode compilar.sh antes)"
echo
diz "DICA: rode o laudo antes de instalar, se ainda nao rodou:"
diz "  mono $AQUI/PtxNav.exe --testar --serial $SERIAL --servidor $SERVIDOR"
[ -r "$AQUI/ptx_iniciar.sh" ] || erro "nao achei $AQUI/ptx_iniciar.sh"
command -v mono >/dev/null 2>&1 || diz "AVISO: mono nao encontrado no PATH"
if [ "$SERIAL" = "auto" ]; then
  diz "serial: auto (o proprio programa procura o GPS nas seriais)"
else
  [ -e "$SERIAL" ] || diz "AVISO: $SERIAL nao existe agora; considere SERIAL=auto"
fi

# ======================================================== instalacao
echo
diz "instalando os arquivos"
for p in $PREFIXOS; do
  d=$(caminho "$p" "$BASE")
  mkdir -p "$d" "$d/cache" || erro "nao consegui criar $d"
  cp "$AQUI/PtxNav.exe" "$d/PtxNav.exe" || erro "falhou copiar para $d"
  cp "$AQUI/ptx_iniciar.sh" "$d/ptx_iniciar.sh"
  # cache semeado: o terminal ja' abre com o mapa, sem depender de alcancar
  # o servidor na primeira vez
  if [ -n "$SEMENTE" ]; then
    for arq in malha.json locais.json areas.json hash.txt; do
      [ -r "$SEMENTE/$arq" ] && cp "$SEMENTE/$arq" "$d/cache/$arq"
    done
    diz "  cache semeado em $d/cache"
  fi
  [ -r "$AQUI/ptx_grafico.sh" ] && cp "$AQUI/ptx_grafico.sh" "$d/ptx_grafico.sh"
  chmod +x "$d/ptx_iniciar.sh" 2>/dev/null
  diz "  $d"
done

# ------------------------------------------------------- configuracao
escreve_conf() {
  cat > "$1" <<FIM
# Configuracao da navegacao da mina. Lida por ptx_iniciar.sh.
# Editou aqui? Reinicie o servico: systemctl restart ptxnav

# Servidor de rotas (Windows, onde roda servidor_rotas.exe)
SERVIDOR=$SERVIDOR

# Device do GPS. "auto" faz o programa varrer as seriais e achar sozinho —
# e' o recomendado, porque o nome do device muda de terminal para terminal.
# Para fixar, rode antes:  mono $BASE/PtxNav.exe --procurar-gps
SERIAL=$SERIAL
BAUD=$BAUD

# Interface local
PORTA=$PORTA

# Reemitir NMEA nesta porta TCP para o BreadCrumb Rajant (0 = nao reemitir)
NMEA_TCP=$NMEA_TCP

# Cache: um em RAM (rapido, some no reboot) e um no disco de verdade
CACHE=$BASE/cache
CACHE_PERSISTENTE=${REALROOT:-/media/realroot}$BASE/cache

# Segundos entre sincronizacoes com o servidor
INTERVALO=300

# Log fica em RAM de proposito, para nao gastar o eMMC
LOG=/var/log/ptxnav.log
LOG_MAX_KB=2048

# Abaixo desta velocidade (km/h) o veiculo conta como parado e o operador
# pode escolher destino. Acima, a interface mostra so' direcao e distancia.
LIMITE_PARADO=3

# Tela onde a interface abre
DISPLAY=:0
FIM
}

echo
for p in $PREFIXOS; do
  c=$(caminho "$p" "$CONF")
  if [ -e "$c" ] && [ "$FORCAR_CONF" = "0" ]; then
    diz "mantendo a configuracao existente: $c"
  else
    escreve_conf "$c" && diz "configuracao: $c"
  fi
done

# ---------------------------------------------------------- servico
escreve_unit() {
  cat > "$1" <<FIM
[Unit]
Description=Navegacao da mina (PTX)
After=network.target
Wants=network.target

[Service]
Type=simple
Environment=PTXNAV_CONF=$CONF
ExecStart=$BASE/ptx_iniciar.sh
Restart=always
RestartSec=10
KillMode=mixed
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
FIM
}

echo
if command -v systemctl >/dev/null 2>&1; then
  for p in $PREFIXOS; do
    u=$(caminho "$p" "$UNIT")
    w=$(caminho "$p" "$WANTS")
    mkdir -p "$(dirname "$u")" "$(dirname "$w")"
    escreve_unit "$u" && diz "unit: $u"
    # O symlink e' feito A MAO: "systemctl enable" so' escreveria na camada
    # RAM, e o gancho de boot sumiria no proximo reboot.
    rm -f "$w"
    ln -s "$UNIT" "$w" && diz "gancho de boot: $w -> $UNIT"
  done
  systemctl daemon-reload 2>/dev/null
  diz "reiniciando o servico"
  systemctl restart ptxnav 2>/dev/null || systemctl start ptxnav 2>/dev/null
else
  diz "AVISO: sem systemctl neste terminal."
  diz "       Coloque a linha abaixo no arranque do sistema (rc.local ou equivalente):"
  diz "         $BASE/ptx_iniciar.sh &"
fi

# ========================================================= conferencia
echo
echo "=========== confira ==========="
diz "estado do servico : systemctl status ptxnav"
diz "log da aplicacao  : tail -f /var/log/ptxnav.log"
diz "interface local   : http://127.0.0.1:$PORTA/"
diz "posicao lida      : cat $SERIAL   (tem que sair \$GPRMC/\$GNGGA)"
echo
diz "TESTE DE ACEITE (secao 10, item 7): reinicie o terminal e confirme"
diz "que a aplicacao sobe sozinha:"
diz "  reboot"
diz "  ps -eo pid,ppid,args | grep PtxNav | grep -v grep"
diz "  -> a coluna PPID tem que ser 1"
echo
