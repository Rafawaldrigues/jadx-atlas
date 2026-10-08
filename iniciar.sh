#!/usr/bin/env bash
# Atalho: cria o .venv, instala o pacote em modo editável e abre o Atlas.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if [ ! -x .venv/bin/jadx-atlas ]; then
  .venv/bin/python -m pip install -e .
fi
exec .venv/bin/jadx-atlas "$@"
