#!/bin/sh
# Roda a suite inteira.
#
#   sh rodar_testes.sh                      usa o banco sintetico
#   MINA_DB=/caminho/Modular.db sh rodar_testes.sh   usa o banco de verdade
#
# Testes que dependem de ferramenta ausente (mono, node, chromium, rajant-api)
# se anunciam como pulados em vez de falhar.
set -e
cd "$(dirname "$0")"
exec python3 -m unittest discover -s testes -t testes -v "$@"
