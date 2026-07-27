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
        "--agent-command",
        action="append",
        default=None,
        metavar="ARG",
        help=(
            "one argument of the command that launches the ACP agent; repeat "
            "once per argument (default: 'hermes acp'). The child is spawned "
            "lazily on the first C-c a, so this costs nothing unused."
        ),
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
            # TODO: [tech-debt] TD-12 — a missing path with a trailing
            # separator lands here too, and the empty basename then mints a
            # buffer literally named "" that no typed C-x b name matches:
            # the startup boundary never got TD-3's refusal. Fix is to apply
            # the empty-basename check before this read. See
            # docs/technical-debt.md.
            # Emacs find-file semantics: a missing file opens an empty
            # buffer that still visits the path.
            initial_text = ""
        except UnicodeDecodeError:
            print(f"drei: {file_path}: not a utf-8 text file", file=sys.stderr)
            raise SystemExit(2) from None
        except OSError as error:
            # TODO: [tech-debt] TD-9 — this prints a raw, locale-dependent
            # strerror and exits 2, while C-x C-f on the same file yields a
            # normalized OpenFailed token: two vocabularies for one
            # operation, violating the normalized-token rule in process.py.
            # Fix is to route startup through the session's visit path. See
            # docs/technical-debt.md.
            print(f"drei: {file_path}: {error.strerror or error}", file=sys.stderr)
            raise SystemExit(2) from error

    import os

    from drei.pump import DEFAULT_AGENT_ARGV
    from drei.terminal import SystemTerminalPort, run_editor

    # One flag occurrence per argument rather than one space-separated string:
    # an agent path with a space in it is ordinary on both platforms, and
    # splitting would break it while shell-style quoting rules differ between
    # them. `--agent-command` is repeated instead, which has no quoting rules.
    agent_argv = tuple(args.agent_command) if args.agent_command else DEFAULT_AGENT_ARGV
    run_editor(
        SystemTerminalPort(),
        file_port=file_port,
        file_path=file_path,
        initial_text=initial_text,
        agent_argv=agent_argv,
        # The agent works where the user is. Read here rather than inside the
        # pump: the working directory is an environment fact, and the pump is
        # an adapter that should be handed its inputs.
        agent_cwd=os.getcwd(),
    )
