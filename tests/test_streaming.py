"""Streaming process port: the long-lived child behind an explicit boundary.

Design 0005 D1. ``ProcessPort`` stays run-to-completion; this is a second
port at the same architectural level, for a child Drei holds a conversation
with. The real round-trips use ``sys.executable`` so they pass on Windows and
Linux; the escalation and partial-write paths are driven through a fake
``Popen`` because provoking them with a real child is platform-dependent.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from drei.streaming import SystemStreamingProcessPort

_ECHO_LINES = (
    "import sys\n"
    "for line in sys.stdin.buffer:\n"
    "    sys.stdout.buffer.write(line)\n"
    "    sys.stdout.buffer.flush()\n"
)


def _read_until(process: object, needle: bytes, *, limit: int = 200) -> bytes:
    """Read chunks until ``needle`` is buffered. A pipe splits where it likes."""
    seen = b""
    for _ in range(limit):
        chunk = process.read()  # type: ignore[attr-defined]
        if not chunk:
            break
        seen += chunk
        if needle in seen:
            break
    return seen


class TestSystemStreamingProcessPort:
    """Layer 3 of design 0005's verification: the port against a real child."""

    def test_write_then_read_round_trips_through_a_live_child(self) -> None:
        port = SystemStreamingProcessPort()
        process = port.spawn((sys.executable, "-c", _ECHO_LINES))
        try:
            process.write(b"ping\n")
            assert _read_until(process, b"ping\n").startswith(b"ping\n")
            # Still live: this is a conversation, not a run-to-completion.
            assert process.poll() is None
            process.write(b"pong\n")
            assert b"pong\n" in _read_until(process, b"pong\n")
        finally:
            process.terminate()

    def test_read_returns_empty_bytes_at_end_of_stream(self) -> None:
        port = SystemStreamingProcessPort()
        process = port.spawn(
            (sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'bye')")
        )
        try:
            assert _read_until(process, b"bye") == b"bye"
            # End of stream is b"", and it stays b"" — the reader thread uses
            # exactly this to decide the child is gone.
            assert process.read() == b""
            assert process.read() == b""
        finally:
            process.terminate()

    def test_stderr_never_reaches_the_wire(self) -> None:
        port = SystemStreamingProcessPort()
        process = port.spawn(
            (
                sys.executable,
                "-c",
                "import sys; sys.stderr.buffer.write(b'diag'); "
                "sys.stdout.buffer.write(b'wire')",
            )
        )
        try:
            assert _read_until(process, b"wire") == b"wire"
            diagnostics = b""
            while chunk := process.read_stderr():
                diagnostics += chunk
            assert diagnostics == b"diag"
        finally:
            process.terminate()

    def test_poll_reports_the_exit_status_once_the_child_is_gone(self) -> None:
        port = SystemStreamingProcessPort()
        process = port.spawn((sys.executable, "-c", "import sys; sys.exit(3)"))
        try:
            assert process.read() == b""  # blocks until the pipe closes
            process.terminate()  # reaps
            assert process.poll() == 3
        finally:
            process.terminate()

    def test_terminate_ends_a_child_that_would_otherwise_run_forever(self) -> None:
        port = SystemStreamingProcessPort()
        process = port.spawn((sys.executable, "-c", "import time; time.sleep(300)"))
        process.terminate()
        assert process.poll() is not None

    def test_terminate_is_idempotent(self) -> None:
        port = SystemStreamingProcessPort()
        process = port.spawn((sys.executable, "-c", "pass"))
        process.terminate()
        first = process.poll()
        process.terminate()
        assert process.poll() == first

    def test_spawn_runs_the_child_in_the_requested_directory(
        self, tmp_path: Path
    ) -> None:
        """``session/new`` sends a cwd, so the child has to be launched in one."""
        port = SystemStreamingProcessPort()
        process = port.spawn(
            (sys.executable, "-c", "import os,sys; sys.stdout.write(os.getcwd())"),
            cwd=str(tmp_path),
        )
        try:
            seen = b""
            while chunk := process.read():
                seen += chunk
            assert Path(seen.decode()).resolve() == tmp_path.resolve()
        finally:
            process.terminate()

    def test_missing_executable_raises_for_normalization(self) -> None:
        """The port does not normalize; ``normalize_process_error`` does, so the
        launcher reports the same token ``run_process`` does."""
        port = SystemStreamingProcessPort()
        with pytest.raises(FileNotFoundError):
            port.spawn(("drei-no-such-executable-xyz-123",))


class _FakePipe:
    """A pipe that accepts one byte per ``write`` — the partial write a real
    ``FileIO`` is permitted to do and almost never does."""

    def __init__(self) -> None:
        self.written = b""
        self.closed = False

    def write(self, data: bytes) -> int:
        self.written += bytes(data[:1])
        return 1

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakePopen:
    """Enough of ``Popen`` to drive the paths a real child cannot be made to
    take portably: a child that survives ``terminate()``, and a partial write."""

    def __init__(self, *, survives_terminate: bool = False) -> None:
        self.stdin = _FakePipe()
        self.stdout = _FakePipe()
        self.stderr = _FakePipe()
        self._survives = survives_terminate
        self.returncode: int | None = None
        self.terminated = 0
        self.killed = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1
        if not self._survives:
            self.returncode = -15

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0.0)
        return self.returncode


class TestTerminationEscalation:
    def test_a_child_that_ignores_termination_is_killed(self) -> None:
        from drei.streaming import SystemAgentProcess

        popen = _FakePopen(survives_terminate=True)
        process = SystemAgentProcess(popen, grace=0.0)  # type: ignore[arg-type]
        process.terminate()
        assert (popen.terminated, popen.killed) == (1, 1)
        assert process.poll() == -9

    def test_a_cooperative_child_is_never_killed(self) -> None:
        from drei.streaming import SystemAgentProcess

        popen = _FakePopen()
        process = SystemAgentProcess(popen, grace=0.0)  # type: ignore[arg-type]
        process.terminate()
        assert (popen.terminated, popen.killed) == (1, 0)

    def test_terminate_closes_the_pipes(self) -> None:
        """A leaked pipe outlives the child on Windows and keeps the handle
        alive; the reader thread would then block on a stream nobody writes."""
        from drei.streaming import SystemAgentProcess

        popen = _FakePopen()
        process = SystemAgentProcess(popen, grace=0.0)  # type: ignore[arg-type]
        process.terminate()
        assert (popen.stdin.closed, popen.stdout.closed, popen.stderr.closed) == (
            True,
            True,
            True,
        )

    def test_a_partial_write_is_completed_rather_than_truncated(self) -> None:
        """A short write on a full pipe would otherwise send half a JSON-RPC
        frame — the decoder would then wait forever for a newline that the
        rest of the frame was supposed to carry."""
        from drei.streaming import SystemAgentProcess

        popen = _FakePopen()
        process = SystemAgentProcess(popen, grace=0.0)  # type: ignore[arg-type]
        process.write(b'{"jsonrpc":"2.0"}\n')
        assert popen.stdin.written == b'{"jsonrpc":"2.0"}\n'
