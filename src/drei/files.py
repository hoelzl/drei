"""File effect port: all filesystem access behind an explicit boundary.

The deterministic command path never touches the filesystem directly; the
session calls an injected ``FilePort`` from ``SaveBuffer`` dispatch and
records the result as immutable events. The real port is used only at the
CLI boundary (startup load and saves in the shipped executable).
"""

from __future__ import annotations

from typing import Protocol


class FilePort(Protocol):
    """Effect port for file reads (startup) and writes (save)."""

    def read(self, path: str) -> str: ...

    def write(self, path: str, text: str) -> None: ...


def normalize_os_error(error: OSError) -> str:
    """Map an ``OSError`` to a normalized, Drei-owned error token.

    Raw exception text is platform- and locale-dependent; events and echo
    text carry only these tokens so replay and golden assertions are
    portable.
    """
    if isinstance(error, FileNotFoundError):
        return "not-found"
    if isinstance(error, PermissionError):
        return "permission-denied"
    return "io-error"


class SystemFilePort:
    """Production file port using the real filesystem (utf-8, as-is)."""

    def read(self, path: str) -> str:  # pragma: no cover - exercised via CLI/TermVerify
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def write(self, path: str, text: str) -> None:  # pragma: no cover
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
