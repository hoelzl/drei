"""Streaming subprocess port: the long-lived child Drei converses with.

Design 0005 D1. This does **not** replace or widen :class:`~drei.process.ProcessPort`,
which stays blocking run-to-completion: conflating "run a tool and collect its
output" with "hold a conversation with a child process" would make the simple,
heavily-used port carry lifecycle concerns it does not need. Two ports at the
same architectural level, like ``FilePort`` and ``TerminalPort``.

The port does no framing. Bytes go in and bytes come out; ``JsonRpcDecoder``
is chunk-safe and explicitly documents that the pump feeds it "whatever the
pipe delivered".

**Departure from design 0005's illustrative shape, adopted here.** The record
sketched ``read_available()`` — "bytes ready now, never blocking on more" —
and left the signatures to the slice that has tests. :meth:`AgentProcess.read`
blocks instead, because a never-blocking read has no way to *wait*: every
caller would have to invent a poll interval, and the caller here is a reader
thread that is allowed to block. This is the contract ``TerminalPort.read_key``
already has, read by the same kind of thread.
"""

from __future__ import annotations

import contextlib
import subprocess
from typing import Protocol


class AgentProcess(Protocol):
    """One live child process, spoken to over its stdio pipes."""

    def write(self, data: bytes) -> None:
        """Send ``data`` to the child, completely.

        Raises ``OSError`` (typically ``BrokenPipeError``) if the child is
        gone. Callers normalize; the port does not decide what a dead peer
        means.
        """
        ...

    def read(self, size: int = ...) -> bytes:
        """Block until at least one byte of wire output is available.

        Returns ``b""`` at end of stream, and keeps returning it — the reader
        thread uses exactly that to decide the child is gone.
        """
        ...

    def read_stderr(self, size: int = ...) -> bytes:
        """As :meth:`read`, for diagnostics. Never enters the wire decoder."""
        ...

    def poll(self) -> int | None:
        """The exit status, or ``None`` while the child is still running."""
        ...

    def terminate(self) -> None:
        """End the child cooperatively, then forcibly. Idempotent."""
        ...


class StreamingProcessPort(Protocol):
    """Effect port for launching a child Drei will hold a conversation with."""

    def spawn(self, argv: tuple[str, ...], *, cwd: str | None = ...) -> AgentProcess:
        """Launch ``argv`` with its stdio piped.

        ``argv`` is a tuple (never a shell string — no shell interpolation).
        Launch-time OS errors propagate as ``OSError`` subclasses, for
        ``normalize_process_error``, exactly as ``ProcessPort.run`` does.
        """
        ...


_CHUNK = 65536


class _PopenLike(Protocol):
    """The narrow slice of ``subprocess.Popen`` this adapter uses.

    Declared so the termination-escalation and partial-write paths can be
    driven by a fake: a child that survives ``terminate()`` and a pipe that
    accepts one byte per write are both platform-dependent to provoke for
    real, and neither may go untested — one leaks processes, the other sends
    half a JSON-RPC frame.
    """

    stdin: object
    stdout: object
    stderr: object

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = ...) -> int: ...


class SystemAgentProcess:
    """Production :class:`AgentProcess` over one ``subprocess.Popen``."""

    def __init__(self, popen: _PopenLike, *, grace: float = 2.0) -> None:
        self._popen = popen
        self._grace = grace

    def write(self, data: bytes) -> None:
        # A pipe is permitted to accept fewer bytes than offered. It almost
        # never does — and a short write here would put half a frame on the
        # wire, after which the peer's decoder waits forever for a newline the
        # rest of the frame was carrying.
        view = memoryview(data)
        stdin = self._popen.stdin
        while view:
            written = stdin.write(view)  # type: ignore[attr-defined]
            view = view[written:]
        stdin.flush()  # type: ignore[attr-defined]

    def read(self, size: int = _CHUNK) -> bytes:
        return self._read_from(self._popen.stdout, size)

    def read_stderr(self, size: int = _CHUNK) -> bytes:
        return self._read_from(self._popen.stderr, size)

    @staticmethod
    def _read_from(stream: object, size: int) -> bytes:
        data = stream.read(size)  # type: ignore[attr-defined]
        # An unbuffered stream returns None only for a non-blocking fd, which
        # this adapter never creates; b"" keeps "nothing more is coming" a
        # single value for the caller either way.
        return bytes(data) if data else b""

    def poll(self) -> int | None:
        return self._popen.poll()

    def terminate(self) -> None:
        if self._popen.poll() is None:
            self._popen.terminate()
            try:
                self._popen.wait(timeout=self._grace)
            except subprocess.TimeoutExpired:
                # A child that ignores the cooperative signal still holds the
                # pipes; leaving it is worse than a garbled terminal (0005 D6).
                self._popen.kill()
                self._popen.wait()
        for stream in (self._popen.stdin, self._popen.stdout, self._popen.stderr):
            _close_quietly(stream)


def _close_quietly(stream: object) -> None:
    """Closing a pipe whose peer is already gone is normal, not an error."""
    with contextlib.suppress(OSError):
        stream.close()  # type: ignore[attr-defined]


class SystemStreamingProcessPort:
    """Production streaming port over ``subprocess.Popen``."""

    def spawn(self, argv: tuple[str, ...], *, cwd: str | None = None) -> AgentProcess:
        popen = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Unbuffered: a buffered reader's read(n) blocks until it has all
            # n bytes or end of stream, which would hold a complete frame
            # hostage waiting for the next one. Raw pipes return what one read
            # delivered, which is what a streaming peer needs.
            bufsize=0,
            cwd=cwd,
        )
        return SystemAgentProcess(popen)  # type: ignore[arg-type]
