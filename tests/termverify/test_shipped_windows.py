"""TermVerify terminal evidence for A.2: multiple buffers and windows.

Drives the real `drei` process through TermVerify's ConPTY adapter:
C-x 2 splits the frame into two panes (two modelines), C-x o switches
focus (each window keeps its own point), C-x b switches buffers with the
MRU default, and C-x 1 collapses back to one pane. The semantic oracle
remains the direct tests; this proves the shipped terminal integration
of the new key paths end to end.

Platform support: ConPTY is Windows-only in TermVerify 0.1.0, so the
scenario skips on other platforms (same as the other shipped scenarios).
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
    TerminalConfiguration,
    TerminalResult,
    TextInput,
)
from termverify.conpty import ConptyAdapter, ConptyBinding
from termverify.cooperation import CooperationConstraintPorts
from test_shipped_terminal import _frame_lines

pytestmark = [
    pytest.mark.termverify,
    pytest.mark.skipif(sys.platform != "win32", reason="ConPTY is Windows-only"),
]

_COLUMNS = 40
_ROWS = 12


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


def _adapter(tmp_path: Path) -> ConptyAdapter:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(exist_ok=True)
    return ConptyAdapter(
        [sys.executable, "-c", "from drei.cli import main; main()"],
        binding=ConptyBinding(),
        abort_deadline_ms=10_000,
        constraint_ports=CooperationConstraintPorts({"drei-root": str(sandbox)}),
    )


def _dispatch_key(adapter: ConptyAdapter, chord: tuple[str, str] | str) -> Observation:
    if isinstance(chord, tuple):
        result = adapter.dispatch(KeyInput(ManualTime(0), chord))
    else:
        # A bare printable character is text input, not a key chord.
        result = adapter.dispatch(TextInput(ManualTime(0), chord))
    assert type(result) is EpochCompleted, result
    assert result.observation is not None
    return result.observation


def _dispatch_text(adapter: ConptyAdapter, char: str) -> Observation:
    result = adapter.dispatch(TextInput(ManualTime(0), char))
    assert type(result) is EpochCompleted, result
    assert result.observation is not None
    return result.observation


def _modeline_count(lines: tuple[str, ...]) -> int:
    return sum(1 for line in lines if "Drei:" in line)


def test_shipped_editor_windows_scenario(tmp_path: Path) -> None:
    """C-x 2 splits the frame; both panes show the buffer with a modeline
    each; C-x o switches focus; C-x 1 collapses back to a single pane."""
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-windows", _configuration())
        assert type(started) is Started, started
        initial_lines = _frame_lines(started.observation)
        assert _modeline_count(initial_lines) == 1, initial_lines

        for char in "hi":
            _dispatch_text(adapter, char)

        # C-x 2: two stacked panes over the same buffer — two modelines,
        # the buffer text visible in both.
        _dispatch_key(adapter, ("Control", "x"))
        split_lines = _frame_lines(_dispatch_key(adapter, "2"))
        assert _modeline_count(split_lines) == 2, split_lines
        assert sum(1 for line in split_lines if line.startswith("hi")) == 2, split_lines

        # C-x o: focus moves to the other window (both still show "hi").
        _dispatch_key(adapter, ("Control", "x"))
        other_lines = _frame_lines(_dispatch_key(adapter, "o"))
        assert _modeline_count(other_lines) == 2, other_lines

        # Typing lands in the focused (bottom) window's BUFFER; both panes
        # render the shared text. (Window-point independence — the top
        # window keeps its own point — is proven in the unit/property
        # tests; TermVerify's frame model carries no cursor position.)
        edited = _dispatch_text(adapter, "!")
        edited_lines = _frame_lines(edited)
        assert sum(1 for line in edited_lines if line.startswith("hi!")) == 2, (
            edited_lines
        )

        # C-x 1: collapse to the focused window — one modeline again.
        _dispatch_key(adapter, ("Control", "x"))
        collapsed_lines = _frame_lines(_dispatch_key(adapter, "1"))
        assert _modeline_count(collapsed_lines) == 1, collapsed_lines

        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "g")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_editor_switch_buffer_scenario(tmp_path: Path) -> None:
    """C-x b creates/switches buffers by name; empty input selects the MRU
    other buffer; the modeline tracks the current buffer throughout."""
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-switch-buffer", _configuration())
        assert type(started) is Started, started
        initial_lines = _frame_lines(started.observation)
        assert any("Drei: scratch" in line for line in initial_lines), initial_lines

        # C-x b alpha RET: creates and selects "alpha".
        _dispatch_key(adapter, ("Control", "x"))
        prompted = _dispatch_key(adapter, "b")
        prompted_lines = _frame_lines(prompted)
        assert any("Switch to buffer:" in line for line in prompted_lines), (
            prompted_lines
        )
        for char in "alpha":
            _dispatch_text(adapter, char)
        accepted_lines = _frame_lines(_dispatch_text(adapter, "\x0d"))
        assert any("Drei: alpha" in line for line in accepted_lines), accepted_lines
        assert not any("Switch to buffer:" in line for line in accepted_lines), (
            accepted_lines
        )

        # Type into alpha, then C-x b RET (empty input = MRU: back to
        # scratch) — scratch's content is empty, alpha's edit survives.
        for char in "AA":
            _dispatch_text(adapter, char)
        _dispatch_key(adapter, ("Control", "x"))
        _dispatch_key(adapter, "b")
        back_lines = _frame_lines(_dispatch_text(adapter, "\x0d"))
        assert any("Drei: scratch" in line for line in back_lines), back_lines
        assert not any(line.startswith("AA") for line in back_lines), back_lines

        # C-x b RET once more: MRU is now alpha — its edit is still there.
        _dispatch_key(adapter, ("Control", "x"))
        _dispatch_key(adapter, "b")
        alpha_lines = _frame_lines(_dispatch_text(adapter, "\x0d"))
        assert any("Drei: alpha" in line for line in alpha_lines), alpha_lines
        assert any(line.startswith("AA") for line in alpha_lines), alpha_lines

        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "g")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final
