"""Subprocess effect port: all child-process launching behind an explicit boundary.

The deterministic command path never imports ``subprocess`` or touches a
pipe directly; the session calls an injected ``ProcessPort`` and records the
result as immutable events. The real port is used only at the system
boundary (integration tests, the future ACP launcher) — mirroring how
``FilePort`` walls off the filesystem in ``files.py``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Immutable outcome of one run-to-completion child process.

    ``exit_code`` is data, not an error channel: a non-zero exit is a normal
    result (parity probes and agent runs inspect it), never an exception.
    """

    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


class ProcessTimedOut(Exception):
    """The child did not finish within ``timeout`` seconds."""

    def __init__(self, argv: tuple[str, ...], timeout: float) -> None:
        super().__init__(f"process timed out after {timeout}s: {argv!r}")
        self.argv = argv
        self.timeout = timeout


class ProcessPort(Protocol):
    """Effect port for launching a child process and capturing its output."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        """Run ``argv`` to completion and capture the result.

        ``argv`` is a tuple (never a shell string — no shell interpolation).
        ``input_text`` is fed to the child's stdin. ``timeout`` bounds the
        wait; expiry raises :class:`ProcessTimedOut`. Launch-time OS errors
        propagate as ``OSError`` subclasses for ``normalize_process_error``.
        """
        ...


def normalize_process_error(error: OSError | ProcessTimedOut) -> str:
    """Map a launch error to a normalized, Drei-owned token.

    Raw exception text is platform- and locale-dependent; events and echo
    text carry only these tokens so replay and golden assertions are
    portable. Mirrors ``files.normalize_os_error``.
    """
    if isinstance(error, ProcessTimedOut):
        return "timeout"
    if isinstance(error, FileNotFoundError):
        return "not-found"
    if isinstance(error, PermissionError):
        return "permission-denied"
    return "io-error"


class SystemProcessPort:
    """Production process port over ``subprocess.run`` (utf-8 text capture)."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        try:
            completed = subprocess.run(
                list(argv),
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise ProcessTimedOut(
                argv, timeout if timeout is not None else 0.0
            ) from error
        return ProcessResult(
            argv=argv,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
