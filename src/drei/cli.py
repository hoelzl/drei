"""Command-line entry point for Drei."""

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version

from drei import identity
from drei.files import SystemFilePort, VisitRejected


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

    import os

    from drei.pump import DEFAULT_AGENT_ARGV
    from drei.terminal import SystemTerminalPort, run_editor

    # One flag occurrence per argument rather than one space-separated string:
    # an agent path with a space in it is ordinary on both platforms, and
    # splitting would break it while shell-style quoting rules differ between
    # them. `--agent-command` is repeated instead, which has no quoting rules.
    agent_argv = tuple(args.agent_command) if args.agent_command else DEFAULT_AGENT_ARGV
    result = run_editor(
        SystemTerminalPort(),
        file_port=file_port,
        file_path=file_path,
        agent_argv=agent_argv,
        # The agent works where the user is. Read here rather than inside the
        # pump: the working directory is an environment fact, and the pump is
        # an adapter that should be handed its inputs.
        agent_cwd=os.getcwd(),
    )
    if isinstance(result, VisitRejected):
        print(f"drei: {result.path}: {result.error}", file=sys.stderr)
        raise SystemExit(2)
