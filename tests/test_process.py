"""Subprocess effect port: ProcessResult, error normalization, SystemProcessPort.

The deterministic core never imports ``subprocess``; the port is the only
place a child process is launched. Tests here cover the port boundary in
isolation (no session). ``SystemProcessPort`` real round-trips use
``sys.executable`` so they pass on Windows and Linux.
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError

import pytest

from drei.process import (
    ProcessResult,
    ProcessTimedOut,
    SystemProcessPort,
    normalize_process_error,
)


def test_result_is_immutable() -> None:
    result = ProcessResult(argv=("a", "b"), exit_code=0, stdout="o", stderr="e")
    assert result.argv == ("a", "b")
    assert result.exit_code == 0
    assert result.stdout == "o"
    assert result.stderr == "e"
    with pytest.raises(FrozenInstanceError):
        result.exit_code = 1  # type: ignore[misc]


def test_error_token_mapping() -> None:
    assert normalize_process_error(FileNotFoundError("x")) == "not-found"
    assert normalize_process_error(PermissionError("x")) == "permission-denied"
    assert normalize_process_error(OSError("disk on fire")) == "io-error"
    assert normalize_process_error(ProcessTimedOut(("cmd",), 1.0)) == "timeout"


def test_system_port_captures_stdout_and_exit_code() -> None:
    port = SystemProcessPort()
    result = port.run((sys.executable, "-c", "import sys; sys.stdout.write('hi')"))
    assert result.exit_code == 0
    assert result.stdout == "hi"
    assert result.stderr == ""


def test_system_port_nonzero_exit_is_data_not_exception() -> None:
    port = SystemProcessPort()
    result = port.run((sys.executable, "-c", "import sys; sys.exit(3)"))
    assert result.exit_code == 3


def test_system_port_captures_stderr() -> None:
    port = SystemProcessPort()
    result = port.run((sys.executable, "-c", "import sys; sys.stderr.write('oops')"))
    assert result.stderr == "oops"
    assert result.exit_code == 0


def test_system_port_feeds_stdin() -> None:
    port = SystemProcessPort()
    result = port.run(
        (sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"),
        input_text="echo-me",
    )
    assert result.stdout == "echo-me"


def test_system_port_argv_echoed_back() -> None:
    port = SystemProcessPort()
    argv = (sys.executable, "-c", "pass")
    result = port.run(argv)
    assert result.argv == argv


def test_system_port_missing_executable_raises_not_found() -> None:
    port = SystemProcessPort()
    with pytest.raises(FileNotFoundError):
        port.run(("drei-no-such-executable-xyz-123",))


def test_system_port_timeout_raises() -> None:
    port = SystemProcessPort()
    with pytest.raises(ProcessTimedOut):
        port.run(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            timeout=1.0,
        )
