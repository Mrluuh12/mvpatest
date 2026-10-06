#!/bin/sh
# =====================================================================
#  compilar.sh — gera o PtxNav.exe.
#
#  Roda na maquina de desenvolvimento (precisa de mcs, do Mono).
#  No PTX so' entra o .exe pronto.
#
#      sh compilar.sh [saida.exe]
#
#  A interface (interface.html) e' embutida no executavel: o terminal nao
#  tem internet e nao pode depender de arquivo solto.
# =====================================================================
set -e
AQUI=$(dirname "$0")
SAIDA=${1:-"$AQUI/PtxNav.exe"}

command -v mcs >/dev/null 2>&1 || {
  echo "ERRO: mcs nao encontrado. Instale o compilador do Mono." >&2
  exit 1
}

echo "gerando Pagina.cs a partir de interface.html"
{
  echo '// GERADO POR compilar.sh A PARTIR DE interface.html — NAO EDITAR A MAO.'
  echo '// Para mudar a interface, edite interface.html e compile de novo.'
  echo 'static class Pagina'
  echo '{'
  echo '    public const string HTML = @"'
  sed 's/"/""/g' "$AQUI/interface.html"
  printf '";\n'
  echo '}'
} > "$AQUI/Pagina.cs"

echo "compilando $SAIDA"
mcs -optimize+ -out:"$SAIDA" "$AQUI/PtxNav.cs" "$AQUI/Pagina.cs"

echo "pronto: $SAIDA ($(wc -c < "$SAIDA") bytes)"
echo
echo "teste rapido:"
echo "  mono $SAIDA --ajuda"
