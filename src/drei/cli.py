"""Command-line entry point for Drei."""

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version

from drei import identity


def main(argv: Sequence[str] | None = None) -> None:
    """Parse the command line and launch the editor or report identity."""
    parser = argparse.ArgumentParser(prog="drei")
    parser.add_argument(
        "--version",
        action="version",
        version=f"drei {version('drei')} — {identity()}",
    )
    parser.parse_args(argv)

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("drei: stdin and stdout must be TTYs", file=sys.stderr)
        raise SystemExit(2)

    from drei.terminal import SystemTerminalPort, run_editor

    run_editor(SystemTerminalPort())
