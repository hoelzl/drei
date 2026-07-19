"""Bootstrap command-line entry point for Drei."""

import argparse
from importlib.metadata import version

from drei import identity


def main() -> None:
    """Parse the bootstrap command line."""
    parser = argparse.ArgumentParser(prog="drei")
    parser.add_argument(
        "--version",
        action="version",
        version=f"drei {version('drei')} — {identity()}",
    )
    parser.parse_args()
