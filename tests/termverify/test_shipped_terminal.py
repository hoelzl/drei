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


def _adapter(tmp_path: Path, argv_file: Path | None = None) -> ConptyAdapter:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(exist_ok=True)
    argv = [sys.executable, "-c", "from drei.cli import main; main()"]
    if argv_file is not None:
        argv.append(str(argv_file))
    return ConptyAdapter(
        argv,
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


def test_shipped_editor_save_scenario(tmp_path: Path) -> None:
    """Open a file via CLI arg, edit, C-x C-s, assert content on disk.

    The file lives under the delivered sandbox root and is passed as an
    absolute CLI path; no TERMVERIFY_FS_ROOT resolution in the subject.
    """
    sandbox = tmp_path / "sandbox"
    target = sandbox / "notes.txt"
    adapter = _adapter(tmp_path, argv_file=target)

    with _reaped(adapter):
        started = adapter.start("drei-save-scenario", _configuration())
        assert type(started) is Started, started

        # Visiting a missing file: empty buffer, modeline shows the basename.
        initial_lines = _frame_lines(started.observation)
        assert any("Drei: notes.txt --" in line for line in initial_lines), (
            initial_lines
        )

        for char in "hi":
            inserted = adapter.dispatch(TextInput(ManualTime(0), char))
            assert type(inserted) is EpochCompleted, inserted

        # C-x C-s saves through the production key path.
        pending = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        assert type(pending) is EpochCompleted, pending
        saved = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "s")))
        assert type(saved) is EpochCompleted, saved
        saved_lines = _frame_lines(saved.observation)
        assert any("Wrote" in line for line in saved_lines), saved_lines
        assert any("Drei: notes.txt --" in line for line in saved_lines), saved_lines

        # The file exists on disk with the buffer content.
        assert target.read_text(encoding="utf-8") == "hi"

        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "g")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_editor_kill_yank_scenario(tmp_path: Path) -> None:
    """C-k C-k joins lines via the append chain; C-y restores through ConPTY.

    Multi-line content arrives via a file (keys can't insert a newline yet);
    the file is created host-side under the sandbox before the child starts.
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = sandbox / "lines.txt"
    target.write_text("ab\ncd", encoding="utf-8")
    adapter = _adapter(tmp_path, argv_file=target)

    with _reaped(adapter):
        started = adapter.start("drei-kill-yank", _configuration())
        assert type(started) is Started, started
        initial_observation = started.observation
        assert initial_observation is not None
        initial_lines = _frame_lines(initial_observation)
        assert any(line.startswith("ab") for line in initial_lines), initial_lines

        # Point starts at 0: first C-k kills "ab", second kills the newline
        # (append chain) — the frame shows the joined remainder.
        killed_lines = initial_lines
        for _ in range(2):
            killed = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "k")))
            assert type(killed) is EpochCompleted, killed
            killed_observation = killed.observation
            assert killed_observation is not None
            killed_lines = _frame_lines(killed_observation)
        assert any(line.startswith("cd") for line in killed_lines), killed_lines

        # C-y yanks "ab\n" back at point 0.
        yanked = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "y")))
        assert type(yanked) is EpochCompleted, yanked
        yanked_observation = yanked.observation
        assert yanked_observation is not None
        yanked_lines = _frame_lines(yanked_observation)
        assert any(line.startswith("ab") for line in yanked_lines), yanked_lines
        assert any(line.startswith("cd") for line in yanked_lines), yanked_lines

        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "g")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_editor_region_kill_scenario(tmp_path: Path) -> None:
    """Region kill is NOT drivable through ConPTY on Windows.

    `getwch` treats NUL (C-@) as an extended-key prefix and swallows the
    following byte as a scan code (verified live: NUL + 'Z' consumes 'Z'
    with no frame change) — the same console-API constraint a real
    Windows Emacs works around with different input plumbing. The
    scenario is kept as a skip marker; region commands are proven
    in-process through the same run_editor byte loop and via the
    symbolic harness (tests/test_terminal.py), and the constraint is
    recorded in docs/knowledge/emacs-parity.md.
    """
    pytest.skip(
        "C-@ (NUL) is an msvcrt extended-key prefix on Windows; "
        "undeliverable through ConPTY"
    )


def test_shipped_editor_undo_scenario(tmp_path: Path) -> None:
    """Type 'ab' via keys; C-x u removes 'b'; C-x u again removes 'a'.

    Undo through ConPTY: C-x u is an ordinary C-x prefix plus a printable
    key — no delivery risk. The \\x1f (C-/) byte arm is probed live:
    unlike NUL it is an ordinary control byte, so it should pass through;
    if it ever regresses the scenario still proves undo via C-x u.
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-undo", _configuration())
        assert type(started) is Started, started

        typed_lines: tuple[str, ...] = ()
        for char in "ab":
            typed = adapter.dispatch(TextInput(ManualTime(0), char))
            assert type(typed) is EpochCompleted, typed
            typed_observation = typed.observation
            assert typed_observation is not None
            typed_lines = _frame_lines(typed_observation)
        assert any(line.startswith("ab") for line in typed_lines), typed_lines

        undone = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        assert type(undone) is EpochCompleted, undone
        undone = adapter.dispatch(TextInput(ManualTime(0), "u"))
        assert type(undone) is EpochCompleted, undone
        undone_observation = undone.observation
        assert undone_observation is not None
        undone_lines = _frame_lines(undone_observation)
        assert any(
            line.startswith("a") and not line.startswith("ab") for line in undone_lines
        ), undone_lines

        # Live probe of the \x1f (C-/) byte: undo the remaining 'a'.
        probed = adapter.dispatch(TextInput(ManualTime(0), "\x1f"))
        assert type(probed) is EpochCompleted, probed
        probed_observation = probed.observation
        assert probed_observation is not None
        probed_lines = _frame_lines(probed_observation)
        assert not any(line.startswith("a") for line in probed_lines), probed_lines

        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "g")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_editor_yank_pop_scenario(tmp_path: Path) -> None:
    """C-k C-k (chain broken) C-y through ConPTY; M-y pop proven in-process.

    ConPTY swallows a bare ESC written to the input stream, so the Alt+y
    chord cannot be delivered to the child (termverify issue #169). This
    scenario proves the kill/yank prefix end-to-end; the M-y byte assembly
    and the pop's frame evidence are covered by the in-process run_editor
    tests (same byte loop, scripted FakePort) in tests/test_terminal.py.
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = sandbox / "pop.txt"
    target.write_text("one\ntwo\nthree", encoding="utf-8")
    adapter = _adapter(tmp_path, argv_file=target)

    with _reaped(adapter):
        started = adapter.start("drei-yank-pop", _configuration())
        assert type(started) is Started, started

        # Kill "one", move, kill "two" -> ring ("two", "one"), text "\n\nthree".
        for chord in (("Control", "k"), ("Control", "f"), ("Control", "k")):
            stepped = adapter.dispatch(KeyInput(ManualTime(0), chord))
            assert type(stepped) is EpochCompleted, stepped
        killed_observation = stepped.observation
        assert killed_observation is not None
        killed_lines = _frame_lines(killed_observation)
        assert not any(line.startswith("one") for line in killed_lines)

        # C-y yanks the newest entry ("two") at point.
        yanked = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "y")))
        assert type(yanked) is EpochCompleted, yanked
        yanked_observation = yanked.observation
        assert yanked_observation is not None
        yanked_lines = _frame_lines(yanked_observation)
        assert any(line.startswith("two") for line in yanked_lines), yanked_lines

        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "g")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_editor_find_file_scenario(tmp_path: Path) -> None:
    """C-x C-f opens the minibuffer; typed path echoes; RET loads the file.

    The fixture lives under the delivered sandbox root; the typed path is
    relative and the adapter's cwd is the sandbox. RET (\\x0d) delivery is
    probed live through ConPTY — unlike NUL it is an ordinary byte. The
    abort arm (second scenario below) proves C-g closes the prompt without
    quitting and never touches the buffer.
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = sandbox / "found.txt"
    target.write_text("found me", encoding="utf-8")
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-find-file", _configuration())
        assert type(started) is Started, started
        initial_lines = _frame_lines(started.observation)
        assert any("Drei: scratch" in line for line in initial_lines), initial_lines

        # C-x C-f: the minibuffer prompt occupies the echo row.
        adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        prompted = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "f")))
        assert type(prompted) is EpochCompleted, prompted
        prompted_lines = _frame_lines(prompted.observation)
        assert any("Find file: " in line for line in prompted_lines), prompted_lines

        # Typed path echoes in the prompt, with one DEL correction.
        typed_lines = prompted_lines
        for char in "found.tx":
            typed = adapter.dispatch(TextInput(ManualTime(0), char))
            assert type(typed) is EpochCompleted, typed
            typed_lines = _frame_lines(typed.observation)
        assert any("Find file: found.tx" in line for line in typed_lines), typed_lines
        corrected = adapter.dispatch(TextInput(ManualTime(0), "\x7f"))
        assert type(corrected) is EpochCompleted, corrected
        final_char = adapter.dispatch(TextInput(ManualTime(0), "t"))
        assert type(final_char) is EpochCompleted, final_char
        final_lines = _frame_lines(final_char.observation)
        assert any("Find file: found.txt" in line for line in final_lines), final_lines

        # RET (probed live: \x0d through ConPTY) opens the file.
        accepted = adapter.dispatch(TextInput(ManualTime(0), "\x0d"))
        assert type(accepted) is EpochCompleted, accepted
        accepted_lines = _frame_lines(accepted.observation)
        assert any(line.startswith("found me") for line in accepted_lines), (
            accepted_lines
        )
        # Modeline shows the buffer id (single fixed buffer; wholesale
        # replace keeps it) — the minibuffer prompt is gone.
        assert not any("Find file:" in line for line in accepted_lines), accepted_lines

        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "g")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_editor_find_file_abort_scenario(tmp_path: Path) -> None:
    """C-x C-f C-g aborts the minibuffer: prompt gone, buffer unchanged,
    no quit — a second C-g exits cleanly (abort must not consume the quit
    or emit one itself)."""
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-find-file-abort", _configuration())
        assert type(started) is Started, started

        for char in "keep":
            typed = adapter.dispatch(TextInput(ManualTime(0), char))
            assert type(typed) is EpochCompleted, typed

        adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        prompted = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "f")))
        assert type(prompted) is EpochCompleted, prompted
        assert any("Find file: " in line for line in _frame_lines(prompted.observation))

        aborted = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "g")))
        assert type(aborted) is EpochCompleted, aborted
        aborted_lines = _frame_lines(aborted.observation)
        assert not any("Find file:" in line for line in aborted_lines), aborted_lines
        assert any(line.startswith("keep") for line in aborted_lines), aborted_lines

        # The abort was NOT a quit: the editor is still alive; a second
        # C-g (minibuffer closed) is the real keyboard quit.
        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "g")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


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
