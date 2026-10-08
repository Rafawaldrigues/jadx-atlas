#!/usr/bin/env python3
"""Compatibility shim: the server now lives in `atlas.server`. Prefer `jadx-atlas`."""
from atlas.__main__ import main
from atlas.server import BASE, Handler, State  # noqa: F401  (re-exported for older imports)

if __name__ == "__main__":
    main()
