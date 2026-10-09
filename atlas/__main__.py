"""Command line entry point: `jadx-atlas [serve] [path]` or `python -m atlas`."""

from __future__ import annotations

import argparse
import sys

from . import __version__, diff, report, server

COMMANDS = {
    "serve": (server.add_arguments, server.serve, "Open the local UI (default)"),
    "diff": (diff.add_arguments, diff.run, "Compare the attack surface of two exports"),
    "export": (report.add_arguments, report.run, "Write a report (md) or the index (json) of an export"),
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jadx-atlas", description="JADX Atlas: attack-surface map for Android apps, built on JADX output"
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
    # Without a known subcommand, `jadx-atlas <path>` serves that path (or the demo).
    if not argv or (argv[0] not in COMMANDS and argv[0] not in {"-h", "--help", "--version"}):
        argv.insert(0, "serve")
    args = build_parser().parse_args(argv)
    return args.run(args, args.command_parser)


if __name__ == "__main__":
    sys.exit(main())
