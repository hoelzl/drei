"""Command-line entry point for Drei."""

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version

from drei import identity
from drei.files import SystemFilePort


def main(argv: Sequence[str] | None = None) -> None:
    """Parse the command line and launch the editor or report identity."""
    parser = argparse.ArgumentParser(prog="drei")
    parser.add_argument(
        "--version",
        action="version",
        version=f"drei {version('drei')} — {identity()}",
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="file to open (missing file starts an empty buffer visiting it)",
    )
    args = parser.parse_args(argv)

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("drei: stdin and stdout must be TTYs", file=sys.stderr)
        raise SystemExit(2)

    file_port = SystemFilePort()
    file_path: str | None = args.file
    initial_text = ""
    if file_path is not None:
        try:
            initial_text = file_port.read(file_path)
        except FileNotFoundError:
            # Emacs find-file semantics: a missing file opens an empty
            # buffer that still visits the path.
            initial_text = ""
        except UnicodeDecodeError:
            print(f"drei: {file_path}: not a utf-8 text file", file=sys.stderr)
            raise SystemExit(2) from None
        except OSError as error:
            print(f"drei: {file_path}: {error.strerror or error}", file=sys.stderr)
            raise SystemExit(2) from error

    from drei.terminal import SystemTerminalPort, run_editor

    run_editor(
        SystemTerminalPort(),
        file_port=file_port,
        file_path=file_path,
        initial_text=initial_text,
    )
