#!/usr/bin/env bash
# Shortcut: create .venv, install the package in editable mode and start JADX Atlas.
# Equivalent to: python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/jadx-atlas "$@"
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if [ ! -x .venv/bin/jadx-atlas ]; then
  .venv/bin/python -m pip install -e .
fi
exec .venv/bin/jadx-atlas "$@"
