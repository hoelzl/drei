from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import drei.terminal
from drei.files import SystemFilePort, VisitRejected
from drei.genesis import KnownFrame, SessionGenesisV1
from drei.input import (
    AgentBytes,
    AgentExited,
    AgentStderr,
    EndOfInput,
    EventQueue,
    InputEvent,
    Key,
    Resize,
)
from drei.terminal import (
    _CLEAR_SCREEN,
    READINESS_MARKER_PREFIX,
    TerminalPort,
    TerminalReaders,
    run_editor,
)


@pytest.fixture(autouse=True)
def _production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ambient TermVerify cooperation out of direct terminal tests."""
    monkeypatch.delenv("TERMVERIFY_SEED", raising=False)


class FakePort(TerminalPort):
    def __init__(self, inputs: list[str]) -> None:
        self.inputs = list(inputs)
        self.outputs: list[str] = []
        self.journal: list[tuple[str, str | None]] = []
        self.restored = False
        self.raw_entered = False

    def enter_raw(self) -> None:
        self.raw_entered = True
        self.journal.append(("raw", None))

    def read_key(self) -> str:
        return self.inputs.pop(0)

    def write(self, text: str) -> None:
        self.outputs.append(text)
        self.journal.append(("write", text))

    def flush(self) -> None:
        self.journal.append(("flush", None))

    def get_size(self) -> tuple[int, int]:
        self.journal.append(("size", None))
        return (10, 3)

    def restore(self) -> None:
        self.restored = True


class _WidePort(FakePort):
    """A frame wide enough for a full exit prompt to survive `_clip`.

    `FakePort`'s 10 columns truncate `Save file /tmp/notes.txt? (y or n) ` to
    `Save file `, which would let an assertion on the prompt text pass while
    asserting almost nothing.
    """

    def get_size(self) -> tuple[int, int]:
        return (60, 4)


def scripted(events: list[InputEvent]) -> EventQueue:
    """A queue holding exactly these events, closed behind them.

    Design 0005's verification layer 1: no thread, no clock, no port. Closing
    it means a run that forgets to quit ends in `EndOfInput` rather than
    blocking the suite forever — the safety the old scripted source got from
    popping an empty list.
    """
    stream = EventQueue()
    for event in events:
        stream.put(event)
    stream.close()
    return stream


def run_with_keys(port: FakePort, **kwargs: object) -> None:
    """Drive the loop over the port's scripted characters.

    The port's `inputs` are turned into `Key` events rather than read through
    `read_key`, because production keys arrive the same way: a reader thread
    puts them on the queue. These tests pin the *loop*, so they skip the
    thread and script the queue directly.
    """
    run_editor(port, events=scripted(keys(*port.inputs)), **kwargs)  # type: ignore[arg-type]


def readiness_markers(port: FakePort) -> list[str]:
    return re.findall(
        rf"{re.escape(READINESS_MARKER_PREFIX)}[0-9]+>>", "".join(port.outputs)
    )


def test_editor_writes_readiness_and_exits_on_c_x_c_c() -> None:
    port = FakePort(["\x18", "\x03"])
    run_with_keys(port)
    assert port.outputs[0] == "DREI:READY\n"
    assert port.restored
    assert port.raw_entered


def test_startup_rejection_precedes_terminal_lifecycle() -> None:
    port = FakePort([])

    result = run_editor(port, events=scripted([]), file_path="notes/")

    assert result == VisitRejected("notes/", "empty-basename")
    assert port.journal == []
    assert port.outputs == []
    assert not port.raw_entered
    assert not port.restored


def test_successful_startup_preserves_lifecycle_and_installs_opened_genesis() -> None:
    from conftest import FakeFilePort

    files = FakeFilePort({"notes.txt": "a\r\nb\r\n"})
    port = FakePort([])

    result = run_editor(
        port,
        events=scripted([Key("\x18"), Key("\x03")]),
        file_port=files,
        file_path="notes.txt",
    )

    assert isinstance(result, SessionGenesisV1)
    assert result.initial_buffer.origin == "existing_file"
    assert result.initial_buffer.text == "a\nb\n"
    assert result.initial_buffer.line_ending == "\r\n"
    assert result.frame == KnownFrame(10, 3)
    assert port.journal[:4] == [
        ("write", "DREI:READY\n"),
        ("flush", None),
        ("raw", None),
        ("size", None),
    ]


def test_missing_startup_origin_survives_into_genesis() -> None:
    from conftest import FakeFilePort

    result = run_editor(
        FakePort([]),
        events=scripted([Key("\x18"), Key("\x03")]),
        file_port=FakeFilePort(),
        file_path="new.txt",
    )

    assert isinstance(result, SessionGenesisV1)
    assert result.initial_buffer.origin == "missing_file"


def test_scratch_startup_never_reads_the_file_port() -> None:
    class NoReadPort:
        def read(self, path: str) -> str:
            raise AssertionError("scratch startup must not read")

        def write(self, path: str, text: str) -> None:
            raise AssertionError("not used")

    result = run_editor(
        FakePort([]),
        events=scripted([Key("\x18"), Key("\x03")]),
        file_port=NoReadPort(),
    )

    assert isinstance(result, SessionGenesisV1)
    assert result.initial_buffer.origin == "scratch"


def test_production_run_uses_full_height_and_emits_no_cooperation_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TERMVERIFY_SEED", raising=False)
    port = FakePort(["\x18", "\x03"])

    run_with_keys(port)

    written = "".join(port.outputs)
    assert "termverify.ready" not in written
    assert "\x1b]7791;" not in written
    assert len(_frame_rows(port)[0]) == 3


def test_verification_run_reserves_bottom_row_and_restores_cursor_after_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Presence, not truthiness, enables cooperation.
    monkeypatch.setenv("TERMVERIFY_SEED", "")
    port = FakePort([])
    port.get_size = lambda: (40, 3)  # type: ignore[method-assign]

    with pytest.raises(EndOfInput):
        run_editor(port, events=scripted([]))

    marker_write = "\x1b[3;1H<<termverify.ready:0>>\x1b[1;1H"
    assert len(_frame_rows(port)[0]) == 2
    marker_index = port.journal.index(("write", marker_write))
    assert port.journal[marker_index + 1] == ("flush", None)


def test_verification_tokens_are_monotonic_across_completed_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERMVERIFY_SEED", "42")
    port = FakePort([])

    with pytest.raises(EndOfInput):
        run_editor(port, events=scripted(keys("a", "\x07")))

    assert readiness_markers(port) == [
        "<<termverify.ready:0>>",
        "<<termverify.ready:1>>",
        "<<termverify.ready:2>>",
    ]


def test_verification_tokens_restart_for_each_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERMVERIFY_SEED", "42")
    ports = [FakePort([]), FakePort([])]

    for port in ports:
        with pytest.raises(EndOfInput):
            run_editor(port, events=scripted([]))

    assert [readiness_markers(port) for port in ports] == [
        ["<<termverify.ready:0>>"],
        ["<<termverify.ready:0>>"],
    ]


def test_unresolved_key_marks_without_rewriting_and_restores_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERMVERIFY_SEED", "42")
    port = FakePort([])
    port.get_size = lambda: (40, 3)  # type: ignore[method-assign]

    with pytest.raises(EndOfInput):
        run_editor(port, events=scripted([Key("<up>")]))

    assert readiness_markers(port) == [
        "<<termverify.ready:0>>",
        "<<termverify.ready:1>>",
    ]
    assert "".join(port.outputs).count(_CLEAR_SCREEN) == 1
    marker_write = "\x1b[3;1H<<termverify.ready:1>>\x1b[1;1H"
    marker_index = port.journal.index(("write", marker_write))
    assert port.journal[marker_index + 1] == ("flush", None)


def test_held_input_prefix_stays_unmarked_until_it_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERMVERIFY_SEED", "42")
    port = FakePort([])

    with pytest.raises(EndOfInput):
        run_editor(port, events=scripted([Key("\x1b")]))

    assert readiness_markers(port) == ["<<termverify.ready:0>>"]


def test_quit_frame_emits_no_fresh_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMVERIFY_SEED", "42")
    port = FakePort(["\x18", "\x03"])

    run_with_keys(port)

    # Startup and the completed C-x prefix mark; the exiting C-c does not.
    assert readiness_markers(port) == [
        "<<termverify.ready:0>>",
        "<<termverify.ready:1>>",
    ]


def test_one_row_verification_terminal_leaves_zero_editor_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin subject geometry only; ConPTY cannot observe this marker (TV #287)."""
    monkeypatch.setenv("TERMVERIFY_SEED", "42")
    port = FakePort([])
    port.get_size = lambda: (10, 1)  # type: ignore[method-assign]

    with pytest.raises(EndOfInput):
        run_editor(port, events=scripted([]))

    assert (
        "write",
        "\x1b[1;1H<<termverify.ready:0>>\x1b[1;1H",
    ) in port.journal
    assert readiness_markers(port) == ["<<termverify.ready:0>>"]


def test_c_g_does_not_end_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole slice, in one assertion.

    `C-g` is `keyboard-quit`: it aborts what is in progress and echoes `Quit`,
    and it never exits. Until slice 17 it ended the run and discarded every
    modified buffer — the reference editor's safest key wired to its most
    destructive outcome (TD-11).

    The script does not quit at all, so the run ends by running out of input.
    That is what makes `EndOfInput` the assertion: had `C-g` still exited, the
    loop would have returned before the queue ran dry and nothing would have
    been raised.
    """
    monkeypatch.setenv("TERMVERIFY_SEED", "42")
    port = FakePort([])
    port.get_size = lambda: (10, 4)  # type: ignore[method-assign]
    with pytest.raises(EndOfInput):
        run_editor(port, events=scripted(keys("a", "\x07")))

    written = "".join(port.outputs)
    # The buffer survived, and `Quit` is on the echo row of the final frame —
    # the first time that message has been readable rather than drawn once
    # into a dying process.
    assert _frame_rows(port)[-1][0].startswith("a")
    assert any(row.startswith("Quit") for row in _frame_rows(port)[-1])
    assert written.count(READINESS_MARKER_PREFIX) == 3  # startup, "a", C-g
    assert port.restored


def test_c_x_c_c_offers_to_save_a_modified_buffer() -> None:
    """Plan 0018's acceptance scenario, end to end through the byte loop.

    Was `…_discards_a_modified_buffer_without_asking`: slice 17 wrote it as a
    pin on TD-11 *so that this slice would have something to turn red* on
    Windows, where the behaviour was otherwise covered only by a ConPTY
    scenario. Same fixture, one more key, inverted assertion.

    The last two assertions are the ones worth reading twice. `y` produces one
    outcome carrying **both** `BufferSaved` and `EditorExited`, so `Wrote …`
    lands on the very frame the loop writes before returning — unlike slice
    17's `Quit`, which the exit frame cleared.
    """
    from conftest import FakeFilePort

    files = FakeFilePort({"/tmp/notes.txt": "saved"})
    port = _WidePort(["x", "\x18", "\x03", "y"])
    run_with_keys(
        port, file_port=files, file_path="/tmp/notes.txt", initial_text="saved"
    )

    # The edit reached disk, and it was asked about first.
    assert files.files["/tmp/notes.txt"] == "xsaved"
    written = "".join(port.outputs)
    assert "Save file /tmp/notes.txt? (y or n) " in written
    assert any(row.startswith("Wrote /tmp/notes.txt") for row in _frame_rows(port)[-1])
    assert port.restored


def test_c_x_c_c_does_not_exit_while_the_save_prompt_is_open() -> None:
    """The prompt is a real pause, not a decoration drawn on the way out.

    The script ends at `C-c`, so the queue runs dry with the prompt open. Had
    the exit gone through anyway, the loop would have returned before the
    queue emptied and `EndOfInput` would never have been raised — the same
    shape slice 17 used to prove `C-g` stopped exiting.
    """
    from conftest import FakeFilePort

    files = FakeFilePort({"/tmp/notes.txt": "saved"})
    port = _WidePort([])
    with pytest.raises(EndOfInput):
        run_editor(
            port,
            events=scripted(keys("x", "\x18", "\x03")),
            file_port=files,
            file_path="/tmp/notes.txt",
            initial_text="saved",
        )

    # Nothing written while the question is still on screen.
    assert files.files["/tmp/notes.txt"] == "saved"
    assert any(
        row.startswith("Save file /tmp/notes.txt? (y or n) ")
        for row in _frame_rows(port)[-1]
    )


def test_a_failed_save_at_the_exit_prompt_is_readable_on_the_frame() -> None:
    """Review 0002 finding 1, at the level where it was invisible.

    The session emits `SaveFailed` and `_echo_for` renders it — but the same
    outcome opens the next exit prompt, and an open minibuffer owns the echo
    row, so the message was drawn over before it could be read. The user had
    asked to save, got no signal that the write failed, and was then asked a
    generic question that reads as being about some other buffer.

    Asserted on the *frame* rather than on the event, because the event was
    never the part that was broken.
    """
    from conftest import FakeFilePort

    files = FakeFilePort({"/tmp/notes.txt": "saved"}, fail="permission")
    port = _WidePort([])
    port.get_size = lambda: (100, 4)  # type: ignore[method-assign]
    with pytest.raises(EndOfInput):
        run_editor(
            port,
            events=scripted(keys("x", "\x18", "\x03", "y")),
            file_port=files,
            file_path="/tmp/notes.txt",
            initial_text="saved",
        )

    # The run did not end (the `y` was answered by the gate, not by an exit),
    # and the reason the write did not happen is on the prompt row.
    assert files.files["/tmp/notes.txt"] == "saved"
    assert any(
        row.startswith(
            "Modified buffers exist; exit anyway? (y or n) "
            "[/tmp/notes.txt: permission-denied]"
        )
        for row in _frame_rows(port)[-1]
    ), _frame_rows(port)[-1]


def test_the_exit_question_survives_a_narrow_frame_carrying_a_failure() -> None:
    """Finding 1 of the second review round, at the shipped width.

    The echo row is hard-clipped, and the ConPTY scenarios run at 40 columns.
    While the failure note was a *prefix* this row read
    `/tmp/notes.txt: permission-denied. Modif` — a truncated error with no
    visible question, on the row where `y` is the key that discards the
    buffer. The note is a suffix now, so the annotation is what gets cut.

    The width here is the one the shipped scenarios use, which is exactly the
    width no scenario exercises for this row (none of them fails a save).
    """
    from conftest import FakeFilePort

    files = FakeFilePort({"/tmp/notes.txt": "saved"}, fail="permission")
    port = FakePort([])  # 10 columns is narrower still; see the assert below
    port.get_size = lambda: (40, 4)  # type: ignore[method-assign]
    with pytest.raises(EndOfInput):
        run_editor(
            port,
            events=scripted(keys("x", "\x18", "\x03", "y")),
            file_port=files,
            file_path="/tmp/notes.txt",
            initial_text="saved",
        )

    echo = _frame_rows(port)[-1][-1]
    assert echo.startswith("Modified buffers exist; exit anyway?"), echo
    assert "permission-denied" not in echo  # the sacrificed half, deliberately


def test_editor_inserts_text_and_renders() -> None:
    # `y` answers the exit gate: the scratch buffer is modified and pathless,
    # so slice 18 asks before discarding it.
    port = FakePort(["a", "\x18", "\x03", "y"])
    run_with_keys(port)
    written = "".join(port.outputs)
    assert "a" in written


def test_editor_restores_on_exception() -> None:
    """A terminal that cannot be read hands the terminal back.

    Driven through the *production* path — real reader threads over a port
    whose `read_key` raises — because that is now the only way a port failure
    can reach the loop at all. Deterministic: the failure is immediate.
    """

    class BoomPort(FakePort):
        def read_key(self) -> str:
            raise RuntimeError("boom")

    port = BoomPort([])
    with pytest.raises(RuntimeError, match="boom"):
        run_editor(port)
    assert port.restored


def test_unresolved_key_marks_quiescence_without_frame_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DEL is not bound to any command; the loop must still emit the
    # readiness marker (quiescence) but must not rewrite the frame.
    monkeypatch.setenv("TERMVERIFY_SEED", "42")
    port = FakePort(["\x7f", "\x18", "\x03"])
    run_with_keys(port)
    written = "".join(port.outputs)
    # Three markers: the initial frame, the unresolved DEL, and the `C-x`
    # prefix — which is also an unresolved key as far as the loop is
    # concerned, so exiting costs one marker more than quitting used to.
    assert written.count(READINESS_MARKER_PREFIX) == 3
    # Two frame rewrites: the initial frame and the final exit frame. Neither
    # the DEL nor the prefix rewrites one.
    assert written.count("\x1b[2J\x1b[H") == 2


def test_editor_meta_chord_yank_pop_through_byte_loop() -> None:
    # ESC y assembles to M-y: with an empty ring it is a silent no-op, so the
    # loop treats it like any other no-state-change input and then quits.
    port = FakePort(["\x1b", "y", "\x18", "\x03"])
    run_with_keys(port)
    assert port.restored


def test_editor_yank_pop_frame_evidence_through_byte_loop() -> None:
    """End-to-end at the byte-loop level: kill, kill, yank, ESC y pops.

    This is the pop's frame evidence (ConPTY cannot deliver ESC; see
    termverify issue #169 and the termverify scenario docstring).
    """

    class TallPort(FakePort):
        def get_size(self) -> tuple[int, int]:
            return (40, 10)

    # C-k C-f C-k C-y ESC y, then C-x C-c, over "one\ntwo\nthree"
    port = TallPort(["\x0b", "\x06", "\x0b", "\x19", "\x1b", "y", "\x18", "\x03", "y"])
    run_with_keys(port, initial_text="one\ntwo\nthree")
    frames = "".join(port.outputs).split("\x1b[2J\x1b[H")
    pop_frame = frames[-2]  # last frame before the quit frame
    buffer_line = pop_frame.split("\r\n")[1]  # first buffer row (row 0 is blank)
    assert buffer_line.startswith("one")


def test_editor_region_commands_through_byte_loop() -> None:
    """C-@ C-f C-f C-w kills the region in-process; M-w copies; C-x C-x swaps.

    ConPTY cannot deliver C-@ on Windows (msvcrt extended-key prefix —
    see the skipped TermVerify scenario), so the byte-loop proof lives
    here, exercising the same decode path the POSIX terminal uses.
    """

    class TallPort(FakePort):
        def get_size(self) -> tuple[int, int]:
            return (40, 10)

    # C-@ C-f C-f C-w kills "he"; C-y restores it; C-@ C-b C-b M-w copies
    # "he" backward (copy clears the mark — the kill must come first);
    # C-x C-x without a mark is then a no-op; C-x C-c exits.
    port = TallPort(
        [
            "\x00",
            "\x06",
            "\x06",
            "\x17",  # mark 0 → point 2; kill "he"
            "\x19",  # yank "he" back at 0 → "hello world", point 2
            "\x00",
            "\x02",
            "\x02",
            "\x1b",
            "w",  # mark 2 → point 0; copy "he"
            "\x18",
            "\x18",  # C-x C-x: no mark (copy cleared it) → no-op
            "\x18",
            "\x03",
            "y",  # the buffer is modified: answer the exit gate
        ]
    )
    run_with_keys(port, initial_text="hello world")
    frames = "".join(port.outputs).split("\x1b[2J\x1b[H")
    rows = [f.split("\r\n")[0] for f in frames[1:]]  # row 0 = buffer line
    # After C-w: "llo world"; after C-y: "hello world" again; M-w and
    # C-x C-x leave the frame unchanged.
    assert any(r.startswith("llo world") for r in rows)
    assert rows[-2].startswith("hello world")  # last frame before quit
    assert rows[-1].startswith("hello world")  # quit frame


def test_editor_undo_through_byte_loop() -> None:
    """Type 'ab', undo twice via C-/ and C-x u: the frame reverts."""

    class TallPort(FakePort):
        def get_size(self) -> tuple[int, int]:
            return (40, 10)

    port = TallPort(
        [
            "a",
            "b",  # text "ab"
            "\x1f",  # C-/: undo "b" → "a"
            "\x18",
            "u",  # C-x u: undo "a" → ""
            "\x18",
            "\x03",
        ]
    )
    run_with_keys(port)
    frames = "".join(port.outputs).split("\x1b[2J\x1b[H")
    rows = [f.split("\r\n")[0] for f in frames[1:]]
    assert any(r.startswith("ab") for r in rows)
    assert any(r.startswith("a") and not r.startswith("ab") for r in rows)
    assert rows[-1].strip() == ""  # quit frame: both inserts undone


def test_editor_find_file_through_byte_loop(tmp_path: Path) -> None:
    """C-x C-f shows the prompt; typed path echoes; RET opens the file
    (host fixture); C-g aborts the prompt and C-x C-c exits. \x0d and \x7f are ordinary
    bytes — same delivery path the POSIX terminal uses."""

    fixture = tmp_path / "hello.txt"
    fixture.write_text("from disk", encoding="utf-8")

    class TallPort(FakePort):
        def get_size(self) -> tuple[int, int]:
            return (60, 10)

    path = str(fixture)
    port = TallPort(
        [
            "\x18",
            "\x06",  # C-x C-f: open the minibuffer
            *list(path[:3]),
            "\x7f",  # DEL: remove one char
            *list(path[2:]),  # retype from the corrected position
            "\x0d",  # RET: accept → opens the fixture
            "\x18",
            "\x06",  # C-x C-f again
            "\x07",  # C-g: abort (editor keeps running)
            "\x18",
            "\x03",  # C-x C-c: quit
        ]
    )
    run_with_keys(port, file_port=SystemFilePort(), initial_text="scratch")
    frames = "".join(port.outputs).split("\x1b[2J\x1b[H")
    rows = [f.split("\r\n") for f in frames[1:]]
    # Prompt visible with the typed path prefix echoed on the echo row.
    assert any(
        any(line.startswith("Find file: " + path[:2]) for line in frame)
        for frame in rows
    ), rows
    # After RET the buffer shows the file contents.
    assert any(frame and frame[0].startswith("from disk") for frame in rows), rows
    # The last frame: abort kept the buffer (no prompt), then quit.
    assert rows[-1][0].startswith("from disk")


def test_editor_arrow_keys_leave_the_buffer_untouched() -> None:
    """Finding 4: arrow keys used to insert "[", "A", … into the buffer.

    Command-level effect, not byte identity: every frame the loop writes
    still shows the original text, and no navigation byte reaches the
    buffer as printable input.
    """

    class TallPort(FakePort):
        def get_size(self) -> tuple[int, int]:
            return (40, 10)

    # Up, Down, Right, Left, Home (ESC [ H), Delete (ESC [ 3 ~), then quit.
    port = TallPort(list("\x1b[A\x1b[B\x1b[C\x1b[D\x1b[H\x1b[3~") + ["\x18", "\x03"])
    run_with_keys(port, initial_text="hi")
    frames = "".join(port.outputs).split("\x1b[2J\x1b[H")
    rows = [f.split("\r\n")[0] for f in frames[1:]]
    assert rows, frames
    assert all(row.startswith("hi") for row in rows), rows


def test_editor_arrow_key_does_not_rewrite_the_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An arrow is one unresolved key: one readiness marker, no frame."""
    monkeypatch.setenv("TERMVERIFY_SEED", "42")
    port = FakePort(list("\x1b[A") + ["\x18", "\x03"])
    run_with_keys(port)
    written = "".join(port.outputs)
    # Markers: initial frame, the arrow (unresolved), the `C-x` prefix. The
    # exit frame carries none.
    assert written.count(READINESS_MARKER_PREFIX) == 3
    # Frames: the initial one and the final exit frame only.
    assert written.count("\x1b[2J\x1b[H") == 2


def test_editor_arrow_keys_do_not_reach_the_minibuffer() -> None:
    """C-x C-f then an arrow: the prompt text stays empty (no "[" echoed)."""

    class TallPort(FakePort):
        def get_size(self) -> tuple[int, int]:
            return (60, 10)

    port = TallPort(["\x18", "\x06", *list("\x1b[A"), "\x07", "\x18", "\x03"])
    run_with_keys(port)
    frames = "".join(port.outputs).split("\x1b[2J\x1b[H")
    prompts = [
        line.split("\x1b")[0]  # the echo row is last: trailing writes ride along
        for frame in frames
        for line in frame.split("\r\n")
        if line.startswith("Find file:")
    ]
    assert prompts, frames
    assert all(line.rstrip() == "Find file:" for line in prompts), prompts


def test_editor_esc_non_letter_reprocesses_byte() -> None:
    # ESC then "1": the bare ESC is unresolved; the "1" is reprocessed and
    # inserted as printable text.
    port = FakePort(["\x1b", "1", "\x18", "\x03", "y"])
    run_with_keys(port)
    written = "".join(port.outputs)
    assert "1" in written


def test_editor_esc_non_letter_emits_one_marker_per_physical_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ESC+non-letter yields one readiness marker per consumed physical input.

    The first ESC leaves the subject mid-chord and emits nothing. The next
    physical input resolves both the abandoned ESC and the reprocessed
    non-letter, but the adapter dispatched only that one input, so the pair
    must end in exactly one fresh marker.
    """
    monkeypatch.setenv("TERMVERIFY_SEED", "42")
    port = FakePort(["\x1b", "1", "\x18", "\x03", "y"])
    run_with_keys(port)
    written = "".join(port.outputs)
    # Markers: initial, the physical `1` resolving ESC+`1`, C-x, and C-c
    # opening the exit gate. The final `y` exits unmarked.
    assert written.count(READINESS_MARKER_PREFIX) == 4


def test_editor_esc_consumed_as_chord_start_then_keyboard_quit() -> None:
    """ESC then C-g: the bare ESC is reported unresolved and the C-g is
    reprocessed from the empty state as `keyboard-quit`.

    Before slice 17 the reprocessed C-g ended the run, which made this
    test pass for a reason that had nothing to do with the assembler. Now
    the editor survives it and C-x C-c is what exits.
    """
    port = FakePort(["\x1b", "\x07", "\x18", "\x03"])
    run_with_keys(port)
    assert port.restored
    frames = _frame_rows(port)
    # Exactly three: the initial one, the C-g frame, and the exit frame. The
    # ESC and the C-x prefix rewrite nothing. Counting them is what makes this
    # test notice if C-g exits again — an `any(... for frame in frames ...)`
    # scan would find `Quit` on the C-g frame either way and pass.
    assert len(frames) == 3, frames
    # `Quit` is on the C-g frame, not the last one: every command sets the
    # echo, and `C-x C-c` produces no message of its own, so the exit frame
    # clears it. Transient messages are Emacs's behaviour too.
    assert any(row.startswith("Quit") for row in frames[-2]), frames
    assert not any(row.startswith("Quit") for row in frames[-1]), frames


def keys(*chars: str) -> list[InputEvent]:
    return [Key(char) for char in chars]


def test_loop_consumes_events_and_never_reads_the_port() -> None:
    """The seam is real: with a source injected, `read_key` is dead code.

    The port's `read_key` raises, so any surviving call fails the test rather
    than silently falling back to the old path.
    """

    class UnreadablePort(FakePort):
        def read_key(self) -> str:
            raise AssertionError("run_editor read the port instead of the source")

    port = UnreadablePort([])
    run_editor(port, events=scripted(keys("a", "\x18", "\x03", "y")))
    assert "a" in "".join(port.outputs)


def test_a_producer_failure_leaves_the_terminal_restored() -> None:
    """The failure a reader thread reports is raised on the loop's thread, so
    it unwinds through the same `finally` a synchronous read failure did."""
    stream = EventQueue()
    stream.fail(RuntimeError("boom"))
    port = FakePort([])
    with pytest.raises(RuntimeError, match="boom"):
        run_editor(port, events=stream)
    assert port.restored


def test_a_stream_that_runs_dry_ends_the_run_rather_than_blocking() -> None:
    """A closed, drained queue is the end of input: the loop does not sit in
    `next_event` waiting for a producer that has already stopped."""
    port = FakePort([])
    with pytest.raises(EndOfInput):
        run_editor(port, events=scripted(keys("a")))
    assert port.restored


def test_cli_rejects_non_tty(capsys: pytest.CaptureFixture[str]) -> None:
    from drei.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert "TTY" in captured.err


def test_cli_version_preserved(capsys: pytest.CaptureFixture[str]) -> None:
    from drei.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("drei 0.1.0")


def test_cli_launches_editor_on_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    import drei.terminal
    from drei.cli import main

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    called: list[object] = []
    monkeypatch.setattr(
        drei.terminal, "run_editor", lambda port, **kw: called.append(port)
    )

    main([])  # must not raise
    assert len(called) == 1
    assert isinstance(called[0], drei.terminal.SystemTerminalPort)


def test_cli_agent_command_defaults_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One occurrence per argument, not one space-separated string: an agent
    path with a space in it is ordinary on both platforms, and shell-style
    quoting rules differ between them."""
    import sys

    import drei.terminal
    from drei.cli import main
    from drei.pump import DEFAULT_AGENT_ARGV

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        drei.terminal,
        "run_editor",
        lambda port, **kw: seen.append(kw["agent_argv"]),
    )

    main([])
    assert seen[-1] == DEFAULT_AGENT_ARGV

    main(["--agent-command", "C:/Program Files/py.exe", "--agent-command", "agent.py"])
    assert seen[-1] == ("C:/Program Files/py.exe", "agent.py")


def test_cli_opens_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    import drei.terminal
    from drei.cli import main

    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        drei.terminal,
        "run_editor",
        lambda port, **kw: captured.update(kw),
    )

    main([str(target)])
    assert captured["file_path"] == str(target)
    assert "initial_text" not in captured  # run_editor owns shared visit resolution


def test_cli_missing_file_opens_empty_visiting_buffer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    import drei.terminal
    from drei.cli import main

    target = tmp_path / "new.txt"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        drei.terminal,
        "run_editor",
        lambda port, **kw: captured.update(kw),
    )

    main([str(target)])  # must not raise or exit
    assert captured["file_path"] == str(target)
    assert "initial_text" not in captured  # missing-file origin is resolved downstream


def test_cli_unreadable_file_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    from drei.cli import main

    target = tmp_path / "dir.txt"
    target.mkdir()  # reading a directory raises IsADirectoryError
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    with pytest.raises(SystemExit) as excinfo:
        main([str(target)])
    assert excinfo.value.code == 2


def test_cli_undecodable_file_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    from drei.cli import main

    target = tmp_path / "binary.txt"
    target.write_bytes(b"\xff\xfe\x00invalid utf-8 \x80\x81")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    with pytest.raises(SystemExit) as excinfo:
        main([str(target)])
    assert excinfo.value.code == 2


def test_decode_key_maps_control_bytes() -> None:
    from drei.terminal import decode_key

    assert decode_key("\x06") == "C-f"
    assert decode_key("\x02") == "C-b"
    assert decode_key("\x07") == "C-g"
    assert decode_key("a") == "a"


def test_decode_key_maps_prefix_and_save() -> None:
    from drei.terminal import decode_key

    assert decode_key("\x18") == "C-x"
    assert decode_key("\x13") == "C-s"


def test_decode_key_maps_region_bytes() -> None:
    from drei.terminal import decode_key

    assert decode_key("\x00") == "C-@"
    assert decode_key("\x17") == "C-w"
    assert decode_key("\x1f") == "C-/"  # C-_ is the same byte
    assert decode_key("\x0d") == "RET"  # Enter
    assert decode_key("\x7f") == "DEL"  # backspace


class _FakeMsvcrt:
    def __init__(self, chars: list[str]) -> None:
        self._chars = chars

    def getwch(self) -> str:
        return self._chars.pop(0)


def _windows_read(chars: list[str], count: int = 1) -> list[str]:
    """Drive ``_read_key_windows`` over a scripted getwch stream."""
    from drei.terminal import SystemTerminalPort

    # getattr: the method only exists on win32 (class-body platform guard);
    # direct attribute access fails mypy --platform linux in CI.
    read = getattr(SystemTerminalPort, "_read_key_windows")  # noqa: B009
    with patch.dict(sys.modules, {"msvcrt": _FakeMsvcrt(chars)}):
        return [read(None) for _ in range(count)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console input path")
def test_windows_extended_key_pair_yields_a_symbolic_key() -> None:
    """getwch NUL/E0 prefix + scan code → one symbolic navigation key.

    The pair used to collapse to ``"\\x00"``, which ``decode_key`` maps to
    C-@ → SetMark: every arrow press silently moved the mark (finding 4).
    Only runs where the class has the Windows method (win32).
    """
    assert _windows_read(["\x00", "H", "a"], 2) == ["<up>", "a"]
    assert _windows_read(["\xe0", "P"]) == ["<down>"]
    assert _windows_read(["\xe0", "M"]) == ["<right>"]
    assert _windows_read(["\xe0", "K"]) == ["<left>"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console input path")
def test_windows_unmapped_extended_key_yields_a_generic_key() -> None:
    assert _windows_read(["\xe0", "S"]) == ["<ext:S>"]  # Delete
    assert _windows_read(["\x00", ";"]) == ["<ext:;>"]  # F1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console input path")
def test_windows_plain_key_passes_through() -> None:
    assert _windows_read(["\x06"]) == ["\x06"]  # control byte untouched
    assert _windows_read(["z"]) == ["z"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console input path")
def test_windows_extended_key_resolves_to_no_command() -> None:
    """Command-level effect, not byte identity: an extended key is inert.

    Before the fix this exact chain ran SetMark, so a later C-w killed
    point↔stale-mark. The whole delivery path is exercised: msvcrt pair →
    assembler → keymap.
    """
    from drei.keys import UnresolvedKey, resolve
    from drei.terminal import KeyAssembler

    (key,) = _windows_read(["\x00", "H"])
    _, keys = KeyAssembler().feed(key)
    assert keys == ("<up>",)
    assert resolve(None, keys[0]) == UnresolvedKey("<up>")


def test_decode_key_maps_kill_and_yank() -> None:
    from drei.terminal import decode_key

    assert decode_key("\x0b") == "C-k"
    assert decode_key("\x19") == "C-y"


def _feed(chars: str) -> tuple[str, ...]:
    """Feed characters through a fresh assembler; return every key emitted."""
    from drei.terminal import KeyAssembler

    assembler = KeyAssembler()
    keys: list[str] = []
    for char in chars:
        assembler, emitted = assembler.feed(char)
        keys.extend(emitted)
    return tuple(keys)


def test_assembler_esc_letter_yields_meta_chord() -> None:
    from drei.terminal import KeyAssembler

    assembler, keys = KeyAssembler().feed("\x1b")
    assert keys == ()  # mid-chord: nothing to dispatch yet
    assembler, keys = assembler.feed("y")
    assert keys == ("M-y",)
    assert assembler == KeyAssembler()  # back to the empty state


def test_assembler_esc_non_letter_emits_bare_esc_then_the_key() -> None:
    # ESC then "1": the bare ESC is unresolved, the "1" resolves on its own.
    assert _feed("\x1b1") == ("\x1b", "1")


def test_assembler_esc_control_byte_emits_bare_esc_then_the_key() -> None:
    assert _feed("\x1b\x07") == (
        "\x1b",
        "C-g",
    )  # ESC C-g: bare ESC reported, then keyboard-quit


def test_assembler_esc_esc_emits_one_bare_esc_and_stays_pending() -> None:
    from drei.terminal import KeyAssembler

    assembler, keys = KeyAssembler().feed("\x1b")
    assembler, keys = assembler.feed("\x1b")
    assert keys == ("\x1b",)  # the first ESC; the second starts a new chord
    assembler, keys = assembler.feed("y")
    assert keys == ("M-y",)


def test_assembler_plain_byte_decodes_normally() -> None:
    assert _feed("\x0b") == ("C-k",)


def test_assembler_csi_arrows_yield_one_symbolic_key_each() -> None:
    # The whole ESC [ X sequence collapses to a single key: no "[" or letter
    # ever reaches the editor (finding 4 — arrows used to type garbage).
    assert _feed("\x1b[A") == ("<up>",)
    assert _feed("\x1b[B") == ("<down>",)
    assert _feed("\x1b[C") == ("<right>",)
    assert _feed("\x1b[D") == ("<left>",)


def test_assembler_ss3_arrows_yield_the_same_symbolic_keys() -> None:
    # Application-cursor mode sends ESC O A rather than ESC [ A.
    assert _feed("\x1bOA") == ("<up>",)
    assert _feed("\x1bOD") == ("<left>",)


def test_assembler_csi_with_parameters_yields_a_generic_key() -> None:
    assert _feed("\x1b[3~") == ("<csi:3~>",)  # Delete
    assert _feed("\x1b[1;5A") == ("<csi:1;5A>",)  # C-<up>
    assert _feed("\x1b[H") == ("<csi:H>",)  # Home (no parameters, not an arrow)


def test_assembler_ss3_non_arrow_yields_a_generic_key() -> None:
    assert _feed("\x1bOP") == ("<ss3:P>",)  # F1


def test_assembler_multiple_sequences_in_a_row() -> None:
    assert _feed("\x1b[A\x1b[Ba") == ("<up>", "<down>", "a")


def test_assembler_unterminated_sequence_emits_a_marker_then_restarts() -> None:
    # A byte that cannot appear in a CSI sequence abandons it: one unresolved
    # key for the partial sequence, then the byte resolves from scratch.
    assert _feed("\x1b[\x07") == ("<csi:unterminated>", "C-g")
    assert _feed("\x1bO\x07") == ("<ss3:unterminated>", "C-g")
    assert _feed("\x1b[1\x1b[A") == ("<csi:unterminated>", "<up>")


def test_system_port_write_and_flush(capsys: pytest.CaptureFixture[str]) -> None:
    from drei.terminal import SystemTerminalPort

    port = SystemTerminalPort()
    port.write("hello")
    port.flush()
    assert capsys.readouterr().out == "hello"


def test_system_port_get_size(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from drei.terminal import SystemTerminalPort

    monkeypatch.setattr(os, "get_terminal_size", lambda *a: os.terminal_size((80, 24)))
    port = SystemTerminalPort()
    assert port.get_size() == (80, 24)


def test_system_port_restore_without_raw_is_noop() -> None:
    from drei.terminal import SystemTerminalPort

    port = SystemTerminalPort()
    port.restore()  # must not raise


def test_system_port_restore_resets_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from drei.terminal import SystemTerminalPort

    port = SystemTerminalPort()
    port._saved = 42
    called: list[str] = []
    method = "_restore_windows" if sys.platform == "win32" else "_restore_posix"
    monkeypatch.setattr(port, method, lambda: called.append(method))

    port.restore()
    assert called == [method]


def _frame_rows(port: FakePort) -> list[list[str]]:
    """The rows of each frame written, oldest first."""
    frames = "".join(port.outputs).split(_CLEAR_SCREEN)[1:]
    return [frame.split("\r\n") for frame in frames]


def test_resize_event_redraws_at_the_new_size() -> None:
    """V3 wiring: a Resize on the stream reaches the session as a command and
    every later frame is drawn at the new size. FakePort starts at 10x3."""
    port = FakePort([])
    run_editor(
        port,
        events=scripted([Key("a"), Resize(30, 6), Key("\x18"), Key("\x03"), Key("y")]),
    )
    frames = _frame_rows(port)
    # Frames before the resize are 10 wide, after it 30 — and the buffer
    # content survived the resize.
    assert len(frames[0][0]) == 10
    assert len(frames[-1][0]) == 30
    assert frames[-1][0].startswith("a")
    assert len(frames[-1]) == 6


def test_resize_redraw_carries_one_fresh_marker_on_the_new_physical_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One marker per consumed input, and a resize is one.

    Plan 0015 originally deviated here — deviation 2 said a resize redraw was
    "spontaneous" and so unmarked. Reading TermVerify's epoch model showed
    that is wrong: `dispatch` accepts a `Resize` on the same ordered input
    stream as a key and then reads until it sees exactly one marker. An
    unmarked resize would not be a quieter epoch, it would be an epoch that
    swallows the *next* input's marker and shifts every epoch after it.
    """
    monkeypatch.setenv("TERMVERIFY_SEED", "42")
    resized = FakePort([])
    resized.get_size = lambda: (30, 3)  # type: ignore[method-assign]
    with pytest.raises(EndOfInput):
        run_editor(resized, events=scripted([Resize(30, 6)]))

    written = "".join(resized.outputs)
    assert written.count(_CLEAR_SCREEN) == 2
    assert readiness_markers(resized) == [
        "<<termverify.ready:0>>",
        "<<termverify.ready:1>>",
    ]
    assert len(_frame_rows(resized)[0]) == 2
    assert len(_frame_rows(resized)[1]) == 5
    marker_writes = [
        entry
        for entry in resized.journal
        if entry[0] == "write" and READINESS_MARKER_PREFIX in (entry[1] or "")
    ]
    assert marker_writes == [
        ("write", "\x1b[3;1H<<termverify.ready:0>>\x1b[1;1H"),
        ("write", "\x1b[6;1H<<termverify.ready:1>>\x1b[1;1H"),
    ]


def test_resize_while_the_minibuffer_is_open_reaches_the_session() -> None:
    """The pinned answer: the minibuffer gate routes keys, and a resize is
    not a key, so the prompt survives, re-renders at the new width, and keeps
    receiving input. C-g aborts the prompt; C-x C-c is what exits.
    """
    port = FakePort([])
    run_editor(
        port,
        events=scripted(
            [
                Key("\x18"),
                Key("\x06"),  # C-x C-f
                Resize(30, 6),
                Key("x"),
                Key("\x07"),  # abort the prompt
                Key("\x18"),
                Key("\x03"),  # C-x C-c: quit
            ]
        ),
    )
    # The echo row is a frame's last row, so it carries the trailing cursor
    # escape; measure the cell content in front of it.
    prompts = [
        row.split("\x1b")[0]
        for frame in _frame_rows(port)
        for row in frame
        if row.startswith("Find file:")
    ]
    # Drawn at the new width after the resize...
    assert any(len(row) == 30 for row in prompts), prompts
    # ...and the "x" went into that same prompt rather than the buffer.
    assert any(row.rstrip() == "Find file: x" for row in prompts), prompts


class TestLoopAgentArms:
    """The loop's half of §C.2: agent events become pump calls and redraws.

    The pump itself is proved in `test_pump.py`; what these pin is the wiring
    — that each event kind reaches the right entry point, and that an agent
    redraw carries no readiness marker.
    """

    @staticmethod
    def _recording(
        journal: list[tuple[str, object]] | None = None,
    ) -> tuple[list[tuple[str, object]], type]:
        """A stand-in pump that records what the loop asked of it.

        ``journal`` lets a caller share one ordered log with the port, so a
        test can assert *when* the pump was called relative to terminal I/O
        rather than only that it was.
        """
        calls: list[tuple[str, object]] = journal if journal is not None else []

        class RecordingPump:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def receive(self, data: bytes, harness: object) -> None:
                calls.append(("receive", data))

            def diagnostics(self, data: bytes, harness: object) -> None:
                calls.append(("diagnostics", data))

            def exited(self, status: int | None, harness: object) -> None:
                calls.append(("exited", status))

            def after_command(self, outcome: object, harness: object) -> None:
                calls.append(("after_command", None))

            def close(self) -> None:
                calls.append(("close", None))

        return calls, RecordingPump

    def test_each_agent_event_reaches_its_entry_point(self) -> None:
        """No quit key in the script: a `Key` sits in the priority lane and
        would preempt every agent event queued behind it (design 0005 D3), so
        this run ends by running out of input instead."""
        calls, recording = self._recording()
        port = FakePort([])
        with (
            patch.object(drei.terminal, "AgentPump", recording),
            pytest.raises(EndOfInput),
        ):
            run_editor(
                port,
                events=scripted(
                    [
                        AgentBytes(b"{}\n"),
                        AgentStderr(b"warning\n"),
                        AgentExited(3),
                    ]
                ),
            )
        assert calls[:3] == [
            ("receive", b"{}\n"),
            ("diagnostics", b"warning\n"),
            ("exited", 3),
        ]
        assert calls[-1] == ("close", None)

    def test_a_key_preempts_agent_output_the_loop_has_not_reached(self) -> None:
        """The fairness rule, visible from the loop: a `C-g` queued *after* a
        burst of agent bytes is still consumed first. A human's keystroke must not
        wait behind a paragraph of streamed text."""
        calls, recording = self._recording()
        port = FakePort([])
        with patch.object(drei.terminal, "AgentPump", recording):
            run_editor(
                port,
                events=scripted(
                    [AgentBytes(b"a lot of text\n"), Key("\x18"), Key("\x03")]
                ),
            )

        assert ("receive", b"a lot of text\n") not in calls

    def test_agent_redraws_carry_no_readiness_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one deliberate gap in the cooperation protocol. An agent
        delivery is a redraw the verifier did not dispatch, so it belongs to
        no input epoch — a marker here would be counted against the *next*
        keystroke and shift every epoch after it. Design 0005 records the cost:
        an end-to-end agent scenario waits on frame content, not quiescence.
        """
        monkeypatch.setenv("TERMVERIFY_SEED", "42")
        _, recording = self._recording()
        baseline = FakePort([])
        streamed = FakePort([])
        with patch.object(drei.terminal, "AgentPump", recording):
            with pytest.raises(EndOfInput):
                run_editor(baseline, events=scripted([]))
            with pytest.raises(EndOfInput):
                run_editor(
                    streamed,
                    events=scripted(
                        [
                            AgentBytes(b"{}\n"),
                            AgentStderr(b"warning\n"),
                            AgentExited(3),
                        ]
                    ),
                )

        written = "".join(streamed.outputs)
        quiet = "".join(baseline.outputs)
        # The three agent events added frames...
        assert written.count(_CLEAR_SCREEN) == quiet.count(_CLEAR_SCREEN) + 3
        # ...and no marker with them.
        assert written.count(READINESS_MARKER_PREFIX) == quiet.count(
            READINESS_MARKER_PREFIX
        )

    def test_every_key_outcome_is_offered_to_the_pump(self) -> None:
        """`after_command` is how a permission answer and a submitted prompt
        get out of the session, so it has to run after every key that produced
        an outcome — not only the ones that look agent-related."""
        calls, recording = self._recording()
        port = FakePort([])
        with patch.object(drei.terminal, "AgentPump", recording):
            run_editor(port, events=scripted(keys("a", "\x18", "\x03", "y")))

        # Three, not two: `C-c` opens the exit gate instead of ending the run,
        # and the `y` that answers it is a third outcome. Slice 17's "exiting
        # costs one marker more than quitting did", in a different currency.
        assert [name for name, _ in calls].count("after_command") == 3

    def test_the_child_is_terminated_before_the_terminal_is_restored(self) -> None:
        """A leaked `hermes acp` holding a pipe outlives a garbled terminal,
        and terminating it is what releases the agent reader threads.

        One shared journal, written by both the pump and the port, so the
        assertion is about *order* — an earlier version appended "close" to
        the front unconditionally and passed with the two calls reversed.
        """
        journal: list[tuple[str, object]] = []
        _, recording = self._recording(journal)

        class TrackingPort(FakePort):
            def restore(self) -> None:
                journal.append(("restore", None))
                super().restore()

        port = TrackingPort([])
        with patch.object(drei.terminal, "AgentPump", recording):
            run_editor(port, events=scripted(keys("\x18", "\x03")))

        names = [name for name, _ in journal]
        assert names[-2:] == ["close", "restore"]


class _GatedPort(FakePort):
    """A port whose two inputs are driven independently by the test.

    `read_key` serves scripted characters and then parks on an event, the way
    a real terminal parks waiting for a keystroke; `get_size` walks a scripted
    list and repeats its last value.
    """

    def __init__(self, chars: list[str], sizes: list[tuple[int, int]]) -> None:
        super().__init__([])
        self._chars = list(chars)
        self._sizes = list(sizes)
        self._size_index = 0
        self.parked = threading.Event()
        self.release = threading.Event()
        self.after_release = "z"

    def read_key(self) -> str:
        if self._chars:
            return self._chars.pop(0)
        self.parked.set()
        self.release.wait(timeout=5)
        return self.after_release

    def get_size(self) -> tuple[int, int]:
        size = self._sizes[min(self._size_index, len(self._sizes) - 1)]
        self._size_index += 1
        return size


def _next_within(stream: EventQueue, timeout: float = 5.0) -> InputEvent:
    """`next_event` once an event is known to be queued.

    The wait is a test-harness deadline, not a semantic one: it keeps a
    regression from hanging CI forever instead of failing.
    """
    deadline = time.monotonic() + timeout
    while _empty(stream) and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not _empty(stream), "no event arrived within the deadline"
    return stream.next_event()


def _empty(stream: EventQueue) -> bool:
    return not stream._priority and not stream._background


def test_threaded_source_merges_keys_and_sizes_onto_one_queue() -> None:
    """The only test in the suite that starts a thread (plan 0015 V4).

    Everything else feeds scripted events, which is design 0005's
    verification layer 1. This one proves the adapter itself: both readers
    reach the same queue, keys keep their relative order, a size change
    becomes a Resize, an unchanged size produces nothing, and close() stops
    the watcher.

    It asserts ordering and content, never timing — the poll interval is an
    adapter concern no editor semantics depend on.
    """
    port = _GatedPort(
        chars=["a", "b"],
        # Seed, then one unchanged poll, then the change.
        sizes=[(10, 3), (10, 3), (20, 5)],
    )
    stream = EventQueue()
    readers = TerminalReaders(port, stream, poll_interval=0.01)
    try:
        # Keys arrive in the order the port produced them...
        assert _next_within(stream) == Key("a")
        assert _next_within(stream) == Key("b")
        # ...and the size change arrives as a Resize on the same queue. The
        # unchanged poll in between queued nothing, or this would be (10, 3).
        assert _next_within(stream) == Resize(20, 5)
    finally:
        readers.close()

    # close() stopped the watcher.
    assert not readers._sizes.is_alive()
    # The key reader is parked in read_key and cannot be interrupted; it
    # notices the stop flag only when one more key arrives. Release it and
    # it delivers that key, then exits rather than looping again.
    assert port.parked.wait(timeout=5)
    port.release.set()
    readers._keys.join(timeout=5)
    assert not readers._keys.is_alive()
    assert _next_within(stream) == Key("z")
    assert _empty(stream)


def test_run_editor_starts_the_terminal_readers_by_default() -> None:
    """The shipped editor gets the reader threads without being asked: the
    default is production behavior and tests opt out, rather than production
    having to opt in.

    Substitutes the class rather than starting it, so this stays a test about
    wiring and the thread count of the suite stays where the failure tests
    below put it.
    """
    built: list[tuple[TerminalPort, EventQueue]] = []

    class RecordingReaders:
        def __init__(self, port: TerminalPort, events: EventQueue) -> None:
            built.append((port, events))
            events.put(Key("\x18"))
            events.put(Key("\x03"))
            # Closed behind them, like `scripted()` does. This is the only
            # loop test that builds its own queue, and without the close it
            # was the only one where a broken exit path *hangs* instead of
            # failing — which falsified the sweep's whole safety argument for
            # exactly one test. Found by the adversarial review, by hoisting
            # the prefix-set check above the prefix table so that `C-x C-c`
            # became a nested prefix: this test then ran forever.
            events.close()

        def close(self) -> None:
            pass

    port = FakePort([])
    with patch.object(drei.terminal, "TerminalReaders", RecordingReaders):
        run_editor(port)
    assert [pair[0] for pair in built] == [port]
    # The readers were handed the very queue the loop consumes — the property
    # that makes "one totally ordered input stream" true rather than a phrase,
    # and the one the agent's reader will rely on in §C.2.
    assert isinstance(built[0][1], EventQueue)


class TestReaderFailures:
    """A dead reader thread must not look like a quiet one.

    Found by adversarial review of slice 15. A thread cannot raise into the
    loop, so before this the loop blocked in `next_event` forever with the
    terminal still in raw mode — and `C-g` could not reach it, because the
    thread that would have delivered the `C-g` was the one that had died. On
    `main` the same failure propagated out of a synchronous `read_key()`
    straight into `port.restore()`, so this was a regression the seam
    introduced.

    These tests start threads, which the plan wanted confined to one test.
    Correctness wins: each failure is immediate and deterministic, and the
    alternative is an unquittable editor with no test naming it.
    """

    @staticmethod
    def _readers(port: TerminalPort) -> tuple[EventQueue, TerminalReaders]:
        stream = EventQueue()
        return stream, TerminalReaders(port, stream, poll_interval=0.01)

    def test_a_reader_exception_reaches_the_loop_instead_of_wedging_it(self) -> None:
        class BoomPort(FakePort):
            def read_key(self) -> str:
                raise OSError(5, "Input/output error")

        stream, readers = self._readers(BoomPort([]))
        try:
            with pytest.raises(OSError, match="Input/output error"):
                stream.next_event()
        finally:
            readers.close()

    def test_end_of_input_ends_the_run_instead_of_spinning(self) -> None:
        """`read(1)` returns "" at EOF and keeps returning it.

        Unguarded this is not an idle loop but an unbounded one: the reader
        queues empty keys as fast as the interpreter allows (measured at
        ~10^6/second, memory climbing) while the loop treats each as an
        unresolved key. It has to terminate the run.
        """

        class EofPort(FakePort):
            def read_key(self) -> str:
                return ""

        stream, readers = self._readers(EofPort([]))
        try:
            with pytest.raises(EOFError):
                stream.next_event()
        finally:
            readers.close()

    def test_a_size_watcher_exception_reaches_the_loop(self) -> None:
        """A dead watcher silently loses every later resize, which is
        indistinguishable from a terminal nobody resized."""

        parked = threading.Event()

        class UnsizeablePort(FakePort):
            def read_key(self) -> str:
                # Park like a real terminal waiting for a keystroke, so the
                # watcher's failure is the only one in play.
                parked.wait(timeout=5)
                return "\x07"

            def get_size(self) -> tuple[int, int]:
                raise OSError("no tty")

        stream, readers = self._readers(UnsizeablePort([]))
        try:
            with pytest.raises(OSError, match="no tty"):
                stream.next_event()
        finally:
            parked.set()
            readers.close()

    def test_a_half_started_reader_pair_stops_the_thread_that_did_start(
        self,
    ) -> None:
        """The threads start one after the other. If the second will not
        start, the first is running with no owner — `run_editor` never gets
        its `readers` back, so nothing closes it and it goes on filling a
        queue nobody reads."""
        stream = EventQueue()
        real_start = threading.Thread.start
        started: list[threading.Thread] = []

        def once(self: threading.Thread) -> None:
            if started:
                raise RuntimeError("can't start new thread")
            started.append(self)
            real_start(self)

        parked = threading.Event()

        class ParkedPort(FakePort):
            def read_key(self) -> str:
                parked.wait(timeout=5)
                return "\x07"

        with (
            patch.object(threading.Thread, "start", once),
            pytest.raises(RuntimeError, match="can't start new thread"),
        ):
            TerminalReaders(ParkedPort([]), stream, poll_interval=0.01)

        parked.set()
        started[0].join(timeout=5)
        assert not started[0].is_alive()

    def test_the_terminal_is_restored_when_the_readers_cannot_be_started(self) -> None:
        """`enter_raw()` has already happened by the time the readers are
        started, so a failure there (a thread that will not start) must still
        hand the terminal back."""

        def explode(port: TerminalPort, events: EventQueue) -> None:
            raise RuntimeError("can't start new thread")

        port = FakePort([])
        with (
            patch.object(drei.terminal, "TerminalReaders", explode),
            pytest.raises(RuntimeError, match="can't start new thread"),
        ):
            run_editor(port)
        assert port.restored
