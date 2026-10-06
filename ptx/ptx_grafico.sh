#!/bin/sh
# =====================================================================
#  ptx_grafico.sh — o que existe no PTX para desenhar uma janela?
#
#  SOMENTE LEITURA. Nao instala nem altera nada.
#      sh ptx_grafico.sh
#
#  IMPORTANTE: por causa do aufs, so' serve o que JA' esta no terminal.
#  Instalar depois nao adianta: some no reboot.
# =====================================================================
RR=""
for m in $(awk '$1 ~ /^\/dev\/(mmcblk|sd|nvme)/ {print $2}' /proc/mounts 2>/dev/null); do
  [ "$m" = "/" ] && continue
  [ -d "$m/etc" ] && [ -d "$m/usr" ] && { RR=$m; break; }
done
BUSCA="/usr /opt /lib"
[ -n "$RR" ] && BUSCA="$BUSCA $RR/usr $RR/opt $RR/lib"

tem(){ command -v "$1" >/dev/null 2>&1 && echo "  SIM   $1 -> $(command -v $1)" || echo "  nao   $1"; }
lib(){ f=$(find $BUSCA -name "$1" 2>/dev/null | head -1); \
       [ -n "$f" ] && echo "  SIM   $1 -> $f" || echo "  nao   $1"; }

echo "=========== AMBIENTE GRAFICO — $(hostname) ==========="
echo "camada persistente: ${RR:-nao encontrada}"
echo

echo "### 1. TEM SERVIDOR GRAFICO RODANDO? ###"
echo "  DISPLAY=${DISPLAY:-(vazio)}"
ls /tmp/.X11-unix/ 2>/dev/null | sed 's/^/  socket X: /' || echo "  socket X: nenhum"
achou=$(ps -eo pid,args 2>/dev/null | grep -iE "Xorg|Xvfb|Xwayland|weston" | grep -v grep | cut -c1-90)
[ -n "$achou" ] && echo "$achou" | sed 's/^/  /' || echo "  nenhum servidor X rodando"
echo "  framebuffer: $([ -e /dev/fb0 ] && echo '/dev/fb0 EXISTE' || echo 'sem /dev/fb0')"
[ -e /sys/class/graphics/fb0/virtual_size ] && \
  echo "  resolucao fb: $(cat /sys/class/graphics/fb0/virtual_size 2>/dev/null)"
command -v xrandr >/dev/null 2>&1 && xrandr 2>/dev/null | grep '\*' | head -3 | sed 's/^/  /'
echo

echo "### 2. O QUE E' A APLICACAO DO DISPATCH? ###"
ps -eo pid,args 2>/dev/null | grep -iE 'dispatch|modular|mms|java|qt|python' | grep -v grep | cut -c1-100 | sed 's/^/  /'
echo "  (se for java/qt, o toolkit dela ja' esta instalado e podemos reusar)"
echo

echo "### 3. GERENCIADOR DE JANELAS ###"
ps -eo args 2>/dev/null | grep -iE '[m]etacity|[o]penbox|[f]luxbox|[i]cewm|[m]atchbox|[x]fwm|[k]win|[m]utter|[j]wm' | cut -c1-70 | sed 's/^/  /' \
  || echo "  nenhum WM detectado (app pode estar em tela cheia sem WM)"
echo

echo "### 4. NAVEGADOR JA' INSTALADO? ###"
echo "  (o caminho mais barato: nossa pagina ja' esta pronta)"
for b in chromium chromium-browser google-chrome firefox firefox-esr epiphany midori surf luakit qutebrowser dillo netsurf; do tem "$b"; done
lib "libwebkit*gtk*.so*"
lib "libQt*WebEngine*.so*"
echo

echo "### 5. TOOLKITS PARA APP NATIVA ###"
echo "  --- GTK (para GTK# com Mono) ---"
lib "libgtk-x11-2.0.so*"
lib "libgtk-3.so*"
lib "gtk-sharp.dll"
lib "gdk-sharp.dll"
echo "  --- Windows Forms via Mono ---"
lib "System.Windows.Forms.dll"
lib "libgdiplus.so*"
echo "  --- Qt ---"
lib "libQt5Widgets.so*"
lib "libQt5Gui.so*"
lib "libQtGui.so*"
echo "  --- Java (Swing/JavaFX) ---"
tem java
echo "  --- SDL / desenho direto ---"
lib "libSDL2*.so*"
lib "libSDL-1.2.so*"
echo

echo "### 6. MONO: O QUE ESTA' DISPONIVEL ###"
tem mono
tem mcs
tem gmcs
if command -v mono >/dev/null 2>&1; then
  echo "  versao: $(mono --version 2>/dev/null | head -1)"
  GAC=$(find $BUSCA -type d -name gac 2>/dev/null | head -1)
  echo "  GAC: ${GAC:-nao encontrado}"
  if [ -n "$GAC" ]; then
    echo "  assemblies graficos no GAC:"
    ls "$GAC" 2>/dev/null | grep -iE 'gtk|gdk|winforms|windows.forms|drawing|glib|atk|pango|cairo' | sed 's/^/    /'
  fi
fi
echo

echo "### 7. FONTES (app grafica sem fonte nao desenha texto) ###"
find $BUSCA -name '*.ttf' -o -name '*.pcf*' 2>/dev/null | head -5 | sed 's/^/  /'
echo "  total de fontes: $(find $BUSCA \( -name '*.ttf' -o -name '*.pcf*' \) 2>/dev/null | wc -l)"
echo

echo "### 8. ENTRADA (toque ou mouse?) ###"
cat /proc/bus/input/devices 2>/dev/null | grep -i '^N: Name' | head -8 | sed 's/^/  /'
echo
echo "=================================================="
echo "Me mande esta saida inteira."
