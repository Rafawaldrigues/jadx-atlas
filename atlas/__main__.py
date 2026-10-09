"""Command line entry point: `jadx-atlas [serve] [path]` or `python -m atlas`."""

from __future__ import annotations

import argparse
import sys

from . import __version__, diff, server

# `export` joins this table in phase 7.
COMMANDS = {
    "serve": (server.add_arguments, server.serve, "Abre a interface local (padrão)"),
    "diff": (diff.add_arguments, diff.run, "Compara a superfície de ataque de duas exportações"),
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jadx-atlas", description="JADX Atlas — mapa local de classes Java exportadas pelo JADX"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMANDO")
    for name, (add_arguments, run, help_text) in COMMANDS.items():
        command = commands.add_parser(name, help=help_text, description=help_text)
        add_arguments(command)
        command.set_defaults(run=run, command_parser=command)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Without a known subcommand, behave like the old `app.py`: serve the path (or the demo).
    if not argv or (argv[0] not in COMMANDS and argv[0] not in {"-h", "--help", "--version"}):
        argv.insert(0, "serve")
    args = build_parser().parse_args(argv)
    return args.run(args, args.command_parser)


if __name__ == "__main__":
    sys.exit(main())
