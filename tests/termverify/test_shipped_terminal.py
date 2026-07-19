"""TermVerify terminal evidence for the shipped `drei` executable.

Drives the real `drei` process through TermVerify's ConPTY adapter on
Windows: wait for the cooperation readiness marker, insert text, move
backward/forward, send C-g, and assert clean exit plus frame evidence.

The semantic oracle remains the direct tests; this scenario proves the
shipped terminal integration (raw mode, key decoding, frame writes,
readiness cooperation, clean exit) end to end.

Platform support: ConPTY is Windows-only in TermVerify 0.1.0, so the
scenario skips on other platforms. CI runs it on the Windows leg of the
matrix via the default `pytest --cov` invocation.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from termverify import (
    ClockConfiguration,
    EpochCompleted,
    ExitStatus,
    FilesystemConfiguration,
    KeyInput,
    ManualTime,
    NetworkConfiguration,
    Observation,
    RunConfiguration,
    RunFinished,
    Started,
    Stop,
    TerminalConfiguration,
    TerminalResult,
    TextInput,
)
from termverify.conpty import ConptyAdapter, ConptyBinding
from termverify.cooperation import CooperationConstraintPorts

pytestmark = [
    pytest.mark.termverify,
    pytest.mark.skipif(sys.platform != "win32", reason="ConPTY is Windows-only"),
]

_COLUMNS = 40
_ROWS = 8


def _configuration() -> RunConfiguration:
    return RunConfiguration(
        seed=42,
        clock=ClockConfiguration(initial_ms=0),
        locale="en-US",
        timezone="UTC",
        terminal=TerminalConfiguration(columns=_COLUMNS, rows=_ROWS, capabilities=()),
        filesystem=FilesystemConfiguration(root_id="drei-root"),
        network=NetworkConfiguration.deny(),
    )


@contextmanager
def _reaped(adapter: ConptyAdapter) -> Iterator[ConptyAdapter]:
    """Never leak a child past a failure (cleanup, not evidence)."""
    try:
        yield adapter
    finally:
        child = adapter._child  # noqa: SLF001 - cleanup-only access
        if child is not None:
            child.close(force=True)


def _frame_lines(observation: Observation) -> tuple[str, ...]:
    assert observation.frame is not None, observation
    return tuple(observation.frame.lines)


def _adapter(tmp_path: Path) -> ConptyAdapter:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(exist_ok=True)
    return ConptyAdapter(
        [sys.executable, "-c", "from drei.cli import main; main()"],
        binding=ConptyBinding(),
        abort_deadline_ms=10_000,
        constraint_ports=CooperationConstraintPorts({"drei-root": str(sandbox)}),
    )


def test_shipped_editor_terminal_scenario(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-first-slice", _configuration())
        assert type(started) is Started, started

        # Initial readiness: the editor rendered its first frame. The body is
        # empty and the modeline identifies Drei and the scratch buffer.
        initial_lines = _frame_lines(started.observation)
        assert any("Drei: scratch" in line for line in initial_lines), initial_lines

        # Insert "hi" one key at a time (each key is its own quiescent epoch).
        inserted_lines = initial_lines
        for char in "hi":
            inserted = adapter.dispatch(TextInput(ManualTime(0), char))
            assert type(inserted) is EpochCompleted, inserted
            inserted_lines = _frame_lines(inserted.observation)
        assert any(line.startswith("hi") for line in inserted_lines), inserted_lines

        # C-b then C-f: bounded movement through the production key path.
        for chord in (("Control", "b"), ("Control", "f")):
            moved = adapter.dispatch(KeyInput(ManualTime(0), chord))
            assert type(moved) is EpochCompleted, moved
            moved_lines = _frame_lines(moved.observation)
            assert any(line.startswith("hi") for line in moved_lines), moved_lines

        # C-g: keyboard quit exits the editor cleanly (native end-of-stream).
        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "g")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final
        assert final.observation is not None
        process = final.observation.process
        assert process is not None
        assert process.state == "exited", process


def test_shipped_editor_stop_is_clean(tmp_path: Path) -> None:
    """A TermVerify stop after readiness also terminates the run cleanly."""
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-first-slice-stop", _configuration())
        assert type(started) is Started, started

        stopped = adapter.stop(Stop(ManualTime(0)))
        # Stop terminates the run; the outcome is a terminal RunFinished
        # whose exit kind depends on the platform stop mechanism.
        assert isinstance(stopped.outcome, RunFinished), stopped
