"""TermVerify terminal evidence for the shipped `drei` executable.

Drives the real `drei` process through TermVerify's ConPTY adapter on
Windows: wait for the cooperation readiness marker, insert text, move
backward/forward, exit with C-x C-c, and assert clean exit plus frame evidence.

The semantic oracle remains the direct tests; this scenario proves the
shipped terminal integration (raw mode, key decoding, frame writes,
readiness cooperation, clean exit) end to end.

Platform support: ConPTY is Windows-only in TermVerify 0.1.1, so the
scenario skips on other platforms. CI runs it on the Windows leg of the
matrix via the default `pytest --cov` invocation.
"""

from __future__ import annotations

import re
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
    Resize,
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


def _configuration(*, columns: int = _COLUMNS) -> RunConfiguration:
    return RunConfiguration(
        seed=42,
        clock=ClockConfiguration(initial_ms=0),
        locale="en-US",
        timezone="UTC",
        terminal=TerminalConfiguration(
            columns=columns, rows=_ROWS + 1, capabilities=()
        ),
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


def _physical_frame_lines(observation: Observation) -> tuple[str, ...]:
    assert observation.frame is not None, observation
    return tuple(observation.frame.lines)


def _frame_lines(observation: Observation) -> tuple[str, ...]:
    """Drei's editor rows; TermVerify cooperation owns the physical bottom row."""
    return _physical_frame_lines(observation)[:-1]


def _modeline_row(lines: tuple[str, ...]) -> int:
    """Index of Drei's modeline in the screen.

    Sensitive to the height *Drei* is rendering at, unlike `len(lines)`,
    which is the screen model's height and reflects the adapter's resize
    whatever the editor did with it.
    """
    rows = [i for i, line in enumerate(lines) if line.startswith("Drei:")]
    assert len(rows) == 1, lines
    return rows[0]


def _exit_through_the_gate(adapter: ConptyAdapter) -> TerminalResult:
    """`C-x C-c` then `y` — the exit of a scenario that leaves work unsaved.

    Since slice 18 `C-x C-c` stops to ask. These scenarios end on a modified
    `scratch` buffer, which visits no file and so is never *offered* a save;
    it reaches the stage-2 gate instead (plan 0018 D2, parity row 4). The
    `C-c` is therefore an ordinary quiescent epoch and the `y` is what ends
    the run.

    Asserting the gate text here rather than only the exit is what keeps this
    from degrading into "press one more key until it exits": an epoch that
    completed for some other reason would satisfy the type check alone.
    """
    prefix = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
    assert type(prefix) is EpochCompleted, prefix
    gate = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "c")))
    assert type(gate) is EpochCompleted, gate
    assert gate.observation is not None
    gate_lines = _frame_lines(gate.observation)
    assert any("exit anyway?" in line for line in gate_lines), gate_lines
    final = adapter.dispatch(TextInput(ManualTime(0), "y"))
    assert isinstance(final, TerminalResult), final
    return final


def _exit_saving_the_file(adapter: ConptyAdapter, path: Path) -> TerminalResult:
    """`C-x C-c` then `y` on a *file-visiting* modified buffer: it is written.

    Stage 1's offer through the shipped executable, with disk evidence — plan
    0018's acceptance criterion that at least one ConPTY scenario exits
    *through* the prompt rather than around it. `Wrote …` reaches the final
    frame because the `y` produces one outcome carrying both `BufferSaved` and
    `EditorExited`.
    """
    prefix = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
    assert type(prefix) is EpochCompleted, prefix
    offer = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "c")))
    assert type(offer) is EpochCompleted, offer
    assert offer.observation is not None
    offer_lines = _frame_lines(offer.observation)
    assert any("Save file" in line for line in offer_lines), offer_lines

    # Removed host-side so the write is *observable*: the caller's buffer may
    # hold exactly the bytes already on disk (a kill/yank round trip does),
    # in which case "the file changed" proves nothing and "the file exists
    # again" proves the save ran. The child holds no handle to it.
    expected = path.read_text(encoding="utf-8")
    path.unlink()

    final = adapter.dispatch(TextInput(ManualTime(0), "y"))
    assert isinstance(final, TerminalResult), final
    assert final.observation is not None
    assert any("Wrote" in line for line in _frame_lines(final.observation)), final
    assert path.exists(), "the offer answered `y` and wrote nothing"
    assert path.read_text(encoding="utf-8") == expected
    return final


def _exit_declining_the_save(adapter: ConptyAdapter, path: Path) -> TerminalResult:
    """`C-x C-c`, `n`, `y`: decline the offer, then confirm the loss.

    Both stages in one ConPTY scenario — `n` at stage 1 does not end the run,
    it hands over to the gate, and the file on disk is untouched at the end.
    """
    before = path.read_text(encoding="utf-8")
    prefix = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
    assert type(prefix) is EpochCompleted, prefix
    offer = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "c")))
    assert type(offer) is EpochCompleted, offer
    assert offer.observation is not None
    assert any("Save file" in line for line in _frame_lines(offer.observation)), offer

    gate = adapter.dispatch(TextInput(ManualTime(0), "n"))
    assert type(gate) is EpochCompleted, gate
    assert gate.observation is not None
    gate_lines = _frame_lines(gate.observation)
    assert any("exit anyway?" in line for line in gate_lines), gate_lines

    final = adapter.dispatch(TextInput(ManualTime(0), "y"))
    assert isinstance(final, TerminalResult), final
    assert path.read_text(encoding="utf-8") == before, "declining wrote anyway"
    return final


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
        physical_lines = _physical_frame_lines(started.observation)
        assert re.fullmatch(r"<<termverify\.ready:[0-9]+>> *", physical_lines[-1])
        initial_lines = physical_lines[:-1]
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

        # C-x C-c exits the editor cleanly (native end-of-stream).
        final = _exit_through_the_gate(adapter)
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final
        assert final.observation is not None
        process = final.observation.process
        assert process is not None
        assert process.state == "exited", process


def test_shipped_editor_marker_survives_wrap_and_token_growth(tmp_path: Path) -> None:
    """Cursor restoration cannot disturb a marker that wraps on its own row."""
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-wrapped-marker", _configuration(columns=10))
        assert type(started) is Started, started

        # Startup is token 0; crossing token 10 grows the decimal marker by a
        # cell while it already spans three physical lines at this width.
        for _ in range(11):
            completed = adapter.dispatch(TextInput(ManualTime(0), "a"))
            assert type(completed) is EpochCompleted, completed

        stopped = adapter.stop(Stop(ManualTime(0)))
        assert isinstance(stopped.outcome, RunFinished), stopped


def test_shipped_editor_marker_survives_resize_to_narrow_width(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-narrow-resize", _configuration())
        assert type(started) is Started, started

        resized = adapter.dispatch(Resize(ManualTime(0), 10, _ROWS + 1))
        assert type(resized) is EpochCompleted, resized
        completed = adapter.dispatch(TextInput(ManualTime(0), "a"))
        assert type(completed) is EpochCompleted, completed

        stopped = adapter.stop(Stop(ManualTime(0)))
        assert isinstance(stopped.outcome, RunFinished), stopped


def test_shipped_editor_resize_scenario(tmp_path: Path) -> None:
    """A real terminal resize reaches the shipped editor and reflows the frame.

    This is the terminal-level proof of plan 0015: the threaded source's size
    watcher notices the new size, the loop turns it into a `ResizeFrame`
    command, and the next frame is drawn at the new width. Nothing here stubs
    a port — the child is the real `drei` process on a real ConPTY.

    It is also the evidence that settled deviation 2. TermVerify dispatches a
    `Resize` on the same ordered input stream as a key and then reads until
    exactly one readiness marker, so a resize *is* an input epoch under the
    cooperation protocol: the editor marks it like any other consumed input.
    Had it stayed unmarked, this epoch would have hung until the abort
    deadline rather than merely being "harder to observe".

    The one thing that is genuinely weaker here: the watcher polls, so the
    resize is observed within a poll interval rather than instantly. The
    epoch's own marker wait absorbs that — no sleep, no fixed deadline in the
    test (parity registry: presentation-only lag).
    """
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-resize-scenario", _configuration())
        assert type(started) is Started, started
        initial_lines = _frame_lines(started.observation)
        assert _modeline_row(initial_lines) == _ROWS - 2, initial_lines

        # Type something so the reflow has content to carry across the resize.
        for char in "hi":
            inserted = adapter.dispatch(TextInput(ManualTime(0), char))
            assert type(inserted) is EpochCompleted, inserted

        # Now resize the terminal itself. Wider and taller than the start.
        wider, taller = _COLUMNS + 20, _ROWS + 4
        resized = adapter.dispatch(Resize(ManualTime(0), wider, taller + 1))
        assert type(resized) is EpochCompleted, resized
        resized_lines = _frame_lines(resized.observation)

        # DREI's OWN geometry, not the screen model's. `len(resized_lines)`
        # would be `taller` even if the editor ignored the resize entirely —
        # it is the ConPTY screen, which the adapter resized. What only Drei
        # controls is where it puts its modeline: it draws `height` rows as
        # body + modeline + echo, so the modeline sits at `height - 2`. An
        # editor still rendering at the old height would leave it at
        # `_ROWS - 2`, four rows higher.
        assert _modeline_row(resized_lines) == taller - 2, resized_lines
        assert any(line.startswith("hi") for line in resized_lines), resized_lines

        # The editor is still live and still consuming input after the resize.
        moved = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "b")))
        assert type(moved) is EpochCompleted, moved

        final = _exit_through_the_gate(adapter)
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_editor_resizes_while_find_file_prompt_is_open(
    tmp_path: Path,
) -> None:
    """A real ConPTY resize crosses Drei's open-prompt session gate.

    The resized screen model is not the oracle: TermVerify changes that before
    Drei handles the input. The modeline and `Find file:` echo row are both
    Drei-owned landmarks, so their positions prove the editor accepted the new
    geometry while preserving the prompt.
    """
    adapter = _adapter(tmp_path)
    target = tmp_path / "sandbox" / "resized.txt"

    with _reaped(adapter):
        started = adapter.start("drei-prompt-resize", _configuration())
        assert type(started) is Started, started

        prefix = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        assert type(prefix) is EpochCompleted, prefix
        prompted = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "f")))
        assert type(prompted) is EpochCompleted, prompted
        prompted_lines = _frame_lines(prompted.observation)
        assert prompted_lines[-1].startswith("Find file:"), prompted_lines

        wider, taller = _COLUMNS + 20, _ROWS + 4
        resized = adapter.dispatch(Resize(ManualTime(0), wider, taller + 1))
        assert type(resized) is EpochCompleted, resized
        resized_lines = _frame_lines(resized.observation)
        assert _modeline_row(resized_lines) == taller - 2, resized_lines
        assert resized_lines[taller - 1].startswith("Find file:"), resized_lines

        accepted: EpochCompleted | TerminalResult
        for char in str(target):
            accepted = adapter.dispatch(TextInput(ManualTime(0), char))
            assert type(accepted) is EpochCompleted, accepted
        accepted = adapter.dispatch(KeyInput(ManualTime(0), ("Enter",)))
        assert type(accepted) is EpochCompleted, accepted
        accepted_lines = _frame_lines(accepted.observation)
        assert _modeline_row(accepted_lines) == taller - 2, accepted_lines
        assert any("Drei: resized.txt --" in line for line in accepted_lines)
        assert not any("Find file:" in line for line in accepted_lines)

        prefix = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        assert type(prefix) is EpochCompleted, prefix
        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "c")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_split_editor_keeps_prompt_at_two_editor_rows(tmp_path: Path) -> None:
    """Constrained shipped rendering reserves prompt space before extra panes."""
    adapter = _adapter(tmp_path)
    focused_path = "focused.txt"

    with _reaped(adapter):
        started = adapter.start("drei-constrained-prompt-resize", _configuration())
        assert type(started) is Started, started

        # Split and focus the lower pane, then give it a distinct buffer so
        # the constrained modeline proves focus ownership rather than merely
        # proving that some pane survived.
        inputs = (
            KeyInput(ManualTime(0), ("Control", "x")),
            TextInput(ManualTime(0), "2"),
            KeyInput(ManualTime(0), ("Control", "x")),
            TextInput(ManualTime(0), "o"),
            KeyInput(ManualTime(0), ("Control", "x")),
            KeyInput(ManualTime(0), ("Control", "f")),
        )
        for input_event in inputs:
            completed = adapter.dispatch(input_event)
            assert type(completed) is EpochCompleted, completed
        for char in focused_path:
            completed = adapter.dispatch(TextInput(ManualTime(0), char))
            assert type(completed) is EpochCompleted, completed
        visited = adapter.dispatch(KeyInput(ManualTime(0), ("Enter",)))
        assert type(visited) is EpochCompleted, visited

        prefix = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        assert type(prefix) is EpochCompleted, prefix
        prompted = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "f")))
        assert type(prompted) is EpochCompleted, prompted

        resized = adapter.dispatch(Resize(ManualTime(0), _COLUMNS, 3))
        assert type(resized) is EpochCompleted, resized
        resized_lines = _frame_lines(resized.observation)
        assert len(resized_lines) == 2
        assert resized_lines[0].startswith("Drei: focused.txt --"), resized_lines
        assert resized_lines[1].startswith("Find file:"), resized_lines

        stopped = adapter.stop(Stop(ManualTime(0)))
        assert isinstance(stopped.outcome, RunFinished), stopped


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

        prefix = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        assert type(prefix) is EpochCompleted, prefix
        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "c")))
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

        # This buffer visits a file, so the exit offers to save it — and `y`
        # writes it. The one ConPTY scenario that exits *through* stage 1.
        final = _exit_saving_the_file(adapter, target)
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

        prefix = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        assert type(prefix) is EpochCompleted, prefix
        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "c")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_editor_exhausted_undo_speaks(tmp_path: Path) -> None:
    """Plan 0019's ConPTY acceptance: a message read off the shipped frame.

    C-x u on a fresh buffer has nothing to undo; the echo row must say
    "No further undo information" — the whole slice-19 path (session
    `Message` event -> harness token table -> `_echo_for` -> frame) proven
    through the real executable, not the in-process harness. D6: the next
    command clears it.
    """
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-undo-message", _configuration())
        assert type(started) is Started, started

        adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        spoken = adapter.dispatch(TextInput(ManualTime(0), "u"))
        assert type(spoken) is EpochCompleted, spoken
        spoken_observation = spoken.observation
        assert spoken_observation is not None
        spoken_lines = _frame_lines(spoken_observation)
        assert any(
            line.startswith("No further undo information") for line in spoken_lines
        ), spoken_lines

        # D6: the message lives exactly until the next command.
        cleared = adapter.dispatch(TextInput(ManualTime(0), "a"))
        assert type(cleared) is EpochCompleted, cleared
        cleared_observation = cleared.observation
        assert cleared_observation is not None
        cleared_lines = _frame_lines(cleared_observation)
        assert not any(
            line.startswith("No further undo information") for line in cleared_lines
        ), cleared_lines

        final = _exit_through_the_gate(adapter)
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

        # The other half of the exit: `n` declines the offer and hands over to
        # the gate, and the file on disk is untouched when the run ends.
        final = _exit_declining_the_save(adapter, target)
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

        # Composition proof: the opened file_path propagated — C-x C-s after
        # an edit writes through the real SystemFilePort to the typed path.
        # (Point is 0 after open, so "!" prepends.)
        appended = adapter.dispatch(TextInput(ManualTime(0), "!"))
        assert type(appended) is EpochCompleted, appended
        adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        saved = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "s")))
        assert type(saved) is EpochCompleted, saved
        assert any("Wrote" in line for line in _frame_lines(saved.observation))
        assert target.read_text(encoding="utf-8") == "!found me"

        prefix = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        assert type(prefix) is EpochCompleted, prefix
        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "c")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_editor_find_file_abort_scenario(tmp_path: Path) -> None:
    """C-x C-f C-g aborts the minibuffer: prompt gone, buffer unchanged, and
    no quit event — the abort must not emit one.

    Since slice 17 the second half is a weaker claim than it used to be:
    `C-g` no longer exits at all, so "the abort did not quit" is true of
    every `C-g`. What still matters is that the abort leaves the *main*
    buffer's mark alone, which `MinibufferAbort` guarantees by not emitting
    `KeyboardQuitEvent`.
    """
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
        # Slice 19 (row 92): the abort says so — Quit on the echo row, read
        # off the shipped frame. It derives from MinibufferAborted, not from
        # a quit event.
        assert any(line.startswith("Quit") for line in aborted_lines), aborted_lines

        # The editor is still alive, and C-x C-c is what ends it.
        final = _exit_through_the_gate(adapter)
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_editor_navigation_keys_are_inert(tmp_path: Path) -> None:
    """Arrow keys must not touch the buffer in the shipped editor.

    Regression evidence for adversarial-review finding 4: the arrow keys
    used to reach the editor as their raw bytes ("[", "A", ... on POSIX;
    NUL -> C-@ -> set-mark on the Windows console) and typed garbage into
    the buffer. They are deliberately unbound for now (registry
    deviation), so the frame must be identical before and after.
    """
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-navigation-keys", _configuration())
        assert type(started) is Started, started

        typed_lines: tuple[str, ...] = ()
        for char in "hi":
            typed = adapter.dispatch(TextInput(ManualTime(0), char))
            assert type(typed) is EpochCompleted, typed
            assert typed.observation is not None
            typed_lines = _frame_lines(typed.observation)
        assert any(line.startswith("hi") for line in typed_lines), typed_lines

        for base in ("ArrowUp", "ArrowDown", "ArrowRight", "ArrowLeft", "Home"):
            pressed = adapter.dispatch(KeyInput(ManualTime(0), (base,)))
            assert type(pressed) is EpochCompleted, pressed
            assert pressed.observation is not None
            assert _frame_lines(pressed.observation) == typed_lines, base

        # The mark is untouched too — the Windows console arm is invisible in
        # the frame otherwise (a stale mark renders nothing). Pre-fix the
        # arrows ran set-mark at point 2, so C-b C-b C-w killed "hi"; with no
        # mark, kill-region is a silent no-op and the text survives.
        for chord in (("Control", "b"), ("Control", "b"), ("Control", "w")):
            stepped = adapter.dispatch(KeyInput(ManualTime(0), chord))
            assert type(stepped) is EpochCompleted, stepped
        assert stepped.observation is not None
        survived_lines = _frame_lines(stepped.observation)
        assert any(line.startswith("hi") for line in survived_lines), survived_lines

        final = _exit_through_the_gate(adapter)
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
