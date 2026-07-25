from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import drei.terminal
from drei.files import SystemFilePort
from drei.input import InputEvent, InputSource, Key, Resize
from drei.terminal import (
    _CLEAR_SCREEN,
    READINESS_MARKER,
    SynchronousTerminalSource,
    TerminalPort,
    ThreadedTerminalSource,
    run_editor,
)


class FakePort(TerminalPort):
    def __init__(self, inputs: list[str]) -> None:
        self.inputs = list(inputs)
        self.outputs: list[str] = []
        self.restored = False
        self.raw_entered = False

    def enter_raw(self) -> None:
        self.raw_entered = True

    def read_key(self) -> str:
        return self.inputs.pop(0)

    def write(self, text: str) -> None:
        self.outputs.append(text)

    def flush(self) -> None:
        pass

    def get_size(self) -> tuple[int, int]:
        return (10, 3)

    def restore(self) -> None:
        self.restored = True


def run_with_keys(port: FakePort, **kwargs: object) -> None:
    """Drive the loop over the port's scripted bytes, synchronously.

    The production default is the threaded source (plan 0015 V4). These tests
    pin the *loop*, not the threads, so they inject the synchronous source —
    which is exactly the `read_key()` the loop used to call inline, making
    them the unchanged regression gate they were before the seam existed.
    """
    run_editor(port, source=SynchronousTerminalSource(port), **kwargs)  # type: ignore[arg-type]


def test_editor_writes_readiness_and_exits_on_quit() -> None:
    port = FakePort(["\x07"])
    run_with_keys(port)
    assert port.outputs[0] == "DREI:READY\n"
    assert port.restored
    assert port.raw_entered


def test_editor_inserts_text_and_renders() -> None:
    port = FakePort(["a", "\x07"])
    run_with_keys(port)
    written = "".join(port.outputs)
    assert "a" in written


def test_editor_restores_on_exception() -> None:
    class BoomPort(FakePort):
        def read_key(self) -> str:
            raise RuntimeError("boom")

    port = BoomPort([])
    with pytest.raises(RuntimeError, match="boom"):
        run_with_keys(port)
    assert port.restored


def test_unresolved_key_marks_quiescence_without_frame_rewrite() -> None:
    # DEL is not bound to any command; the loop must still emit the
    # readiness marker (quiescence) but must not rewrite the frame.
    port = FakePort(["\x7f", "\x07"])
    run_with_keys(port)
    written = "".join(port.outputs)
    # Two markers: one after the initial frame, one after the unresolved key.
    assert written.count("\x1b]7791;ready\x1b\\") == 2
    # Two frame rewrites: the initial frame and the final C-g quit frame.
    # The unresolved key in between triggers no rewrite of its own.
    assert written.count("\x1b[2J\x1b[H") == 2


def test_editor_meta_chord_yank_pop_through_byte_loop() -> None:
    # ESC y assembles to M-y: with an empty ring it is a silent no-op, so the
    # loop treats it like any other no-state-change input and then quits.
    port = FakePort(["\x1b", "y", "\x07"])
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

    # C-k C-f C-k C-y ESC y C-g over "one\ntwo\nthree"
    port = TallPort(["\x0b", "\x06", "\x0b", "\x19", "\x1b", "y", "\x07"])
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
    # C-x C-x without a mark is then a no-op; C-g quits.
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
            "\x07",
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
            "\x07",
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
    (host fixture); C-g C-g aborts then quits. \x0d and \x7f are ordinary
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
            "\x07",  # C-g: quit
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
    port = TallPort(list("\x1b[A\x1b[B\x1b[C\x1b[D\x1b[H\x1b[3~") + ["\x07"])
    run_with_keys(port, initial_text="hi")
    frames = "".join(port.outputs).split("\x1b[2J\x1b[H")
    rows = [f.split("\r\n")[0] for f in frames[1:]]
    assert rows, frames
    assert all(row.startswith("hi") for row in rows), rows


def test_editor_arrow_key_does_not_rewrite_the_frame() -> None:
    """An arrow is one unresolved key: one readiness marker, no frame."""
    port = FakePort(list("\x1b[A") + ["\x07"])
    run_with_keys(port)
    written = "".join(port.outputs)
    # Markers: initial frame + the arrow (unresolved). The quit frame has none.
    assert written.count("\x1b]7791;ready\x1b\\") == 2
    # Frames: the initial one and the final quit frame only.
    assert written.count("\x1b[2J\x1b[H") == 2


def test_editor_arrow_keys_do_not_reach_the_minibuffer() -> None:
    """C-x C-f then an arrow: the prompt text stays empty (no "[" echoed)."""

    class TallPort(FakePort):
        def get_size(self) -> tuple[int, int]:
            return (60, 10)

    port = TallPort(["\x18", "\x06", *list("\x1b[A"), "\x07", "\x07"])
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
    port = FakePort(["\x1b", "1", "\x07"])
    run_with_keys(port)
    written = "".join(port.outputs)
    assert "1" in written


def test_editor_esc_non_letter_marks_quiescence_for_both_inputs() -> None:
    """ESC+non-letter yields one readiness marker per consumed physical input.

    The bare ESC is unresolved (no state change, no frame rewrite) but the
    subject IS quiescent after it — the verifier needs one marker for the
    ESC and one for the reprocessed byte, symmetric with the C-x prefix
    path. A bare ESC as chord START is different: the subject is mid-chord
    and correctly emits no marker until the chord resolves.
    """
    port = FakePort(["\x1b", "1", "\x07"])
    run_with_keys(port)
    written = "".join(port.outputs)
    # Markers: initial frame, ESC (unresolved, no frame), "1" (frame), and
    # the final C-g quit frame carries none.
    assert written.count("\x1b]7791;ready\x1b\\") == 3


def test_editor_esc_consumed_as_chord_start_then_quit() -> None:
    # ESC followed by C-g: bare ESC reported (unresolved), C-g reprocessed
    # and quits the loop.
    port = FakePort(["\x1b", "\x07"])
    run_with_keys(port)
    assert port.restored


class ScriptedSource(InputSource):
    """A list of input events, in order (plan 0015 D2, verification layer 1).

    Every test but V4's touches the loop through one of these: no thread, no
    port, no clock. Exhaustion raises like a closed stream would, which is how
    the existing suite already ends a run that forgets to quit.
    """

    def __init__(self, events: list[InputEvent]) -> None:
        self.events = list(events)
        self.closed = False

    def next_event(self) -> InputEvent:
        return self.events.pop(0)

    def close(self) -> None:
        self.closed = True


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
    source = ScriptedSource(keys("a", "\x07"))
    run_editor(port, source=source)
    assert "a" in "".join(port.outputs)


def test_loop_closes_the_source_even_when_the_body_raises() -> None:
    class BoomSource(ScriptedSource):
        def next_event(self) -> InputEvent:
            raise RuntimeError("boom")

    source = BoomSource([])
    with pytest.raises(RuntimeError, match="boom"):
        run_editor(FakePort([]), source=source)
    assert source.closed


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
    assert captured["initial_text"] == "hello"


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
    assert captured["initial_text"] == ""


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
    assert _feed("\x1b\x07") == ("\x1b", "C-g")  # ESC C-g: C-g still quits


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
    run_editor(port, source=ScriptedSource([Key("a"), Resize(30, 6), Key("\x07")]))
    frames = _frame_rows(port)
    # Frames before the resize are 10 wide, after it 30 — and the buffer
    # content survived the resize.
    assert len(frames[0][0]) == 10
    assert len(frames[-1][0]) == 30
    assert frames[-1][0].startswith("a")
    assert len(frames[-1]) == 6


def test_resize_redraw_carries_a_readiness_marker() -> None:
    """One marker per consumed input, and a resize is one.

    Plan 0015 originally deviated here — deviation 2 said a resize redraw was
    "spontaneous" and so unmarked. Reading TermVerify's epoch model showed
    that is wrong: `dispatch` accepts a `Resize` on the same ordered input
    stream as a key and then reads until it sees exactly one marker. An
    unmarked resize would not be a quieter epoch, it would be an epoch that
    swallows the *next* input's marker and shifts every epoch after it.
    """
    keyed = FakePort([])
    run_editor(keyed, source=ScriptedSource(keys("a", "\x07")))

    resized = FakePort([])
    run_editor(resized, source=ScriptedSource([Key("a"), Resize(30, 6), Key("\x07")]))

    written = "".join(resized.outputs)
    baseline = "".join(keyed.outputs)
    # The resize added exactly one frame and exactly one marker with it.
    assert written.count(_CLEAR_SCREEN) == baseline.count(_CLEAR_SCREEN) + 1
    assert written.count(READINESS_MARKER) == baseline.count(READINESS_MARKER) + 1


def test_resize_while_the_minibuffer_is_open_reaches_the_session() -> None:
    """The pinned answer: the minibuffer gate routes keys, and a resize is
    not a key, so the prompt survives, re-renders at the new width, and keeps
    receiving input. Two C-g: the first aborts the prompt, the second quits.
    """
    port = FakePort([])
    run_editor(
        port,
        source=ScriptedSource(
            [
                Key("\x18"),
                Key("\x06"),  # C-x C-f
                Resize(30, 6),
                Key("x"),
                Key("\x07"),  # abort the prompt
                Key("\x07"),  # quit
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


def _next_within(source: ThreadedTerminalSource, timeout: float = 5.0) -> InputEvent:
    """`next_event` once an event is known to be queued.

    The wait is a test-harness deadline, not a semantic one: it keeps a
    regression from hanging CI forever instead of failing.
    """
    deadline = time.monotonic() + timeout
    while source._events.empty() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not source._events.empty(), "no event arrived within the deadline"
    return source.next_event()


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
    source = ThreadedTerminalSource(port, poll_interval=0.01)
    try:
        # Keys arrive in the order the port produced them...
        assert _next_within(source) == Key("a")
        assert _next_within(source) == Key("b")
        # ...and the size change arrives as a Resize on the same queue. The
        # unchanged poll in between queued nothing, or this would be (10, 3).
        assert _next_within(source) == Resize(20, 5)
    finally:
        source.close()

    # close() stopped the watcher.
    assert not source._sizes.is_alive()
    # The key reader is parked in read_key and cannot be interrupted; it
    # notices the stop flag only when one more key arrives. Release it and
    # it delivers that key, then exits rather than looping again.
    assert port.parked.wait(timeout=5)
    port.release.set()
    source._keys.join(timeout=5)
    assert not source._keys.is_alive()
    assert _next_within(source) == Key("z")
    assert source._events.empty()


def test_run_editor_defaults_to_the_threaded_source() -> None:
    """The shipped editor gets the threaded source without being asked: the
    default is production behavior and tests opt out, rather than production
    having to opt in.

    Substitutes the class rather than starting it, so this stays a test about
    wiring and the thread count of the suite stays at one.
    """
    built: list[TerminalPort] = []

    class RecordingSource(InputSource):
        def __init__(self, port: TerminalPort) -> None:
            built.append(port)
            self.closed = False
            self._events = [Key("\x07")]

        def next_event(self) -> InputEvent:
            return self._events.pop(0)

        def close(self) -> None:
            self.closed = True

    port = FakePort([])
    with patch.object(drei.terminal, "ThreadedTerminalSource", RecordingSource):
        run_editor(port)
    assert built == [port]


class TestThreadedSourceFailures:
    """A dead reader thread must not look like a quiet one.

    Found by adversarial review. A thread cannot raise into the loop, so
    before this the loop blocked in `next_event` forever with the terminal
    still in raw mode — and `C-g` could not reach it, because the thread that
    would have delivered the `C-g` was the one that had died. On `main` the
    same failure propagated out of a synchronous `read_key()` straight into
    `port.restore()`, so this was a regression the seam introduced.

    These tests start threads, which the plan wanted confined to one test.
    Correctness wins: each failure is immediate and deterministic, and the
    alternative is an unquittable editor with no test naming it.
    """

    def test_a_reader_exception_reaches_the_loop_instead_of_wedging_it(self) -> None:
        class BoomPort(FakePort):
            def read_key(self) -> str:
                raise OSError(5, "Input/output error")

        source = ThreadedTerminalSource(BoomPort([]), poll_interval=0.01)
        try:
            with pytest.raises(OSError, match="Input/output error"):
                source.next_event()
        finally:
            source.close()

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

        source = ThreadedTerminalSource(EofPort([]), poll_interval=0.01)
        try:
            with pytest.raises(EOFError):
                source.next_event()
        finally:
            source.close()

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

        source = ThreadedTerminalSource(UnsizeablePort([]), poll_interval=0.01)
        try:
            with pytest.raises(OSError, match="no tty"):
                source.next_event()
        finally:
            parked.set()
            source.close()

    def test_the_terminal_is_restored_when_the_source_cannot_be_built(self) -> None:
        """`enter_raw()` has already happened by the time the default source
        is constructed, so a failure there (a thread that will not start)
        must still hand the terminal back."""

        def explode(port: TerminalPort) -> InputSource:
            raise RuntimeError("can't start new thread")

        port = FakePort([])
        with (
            patch.object(drei.terminal, "ThreadedTerminalSource", explode),
            pytest.raises(RuntimeError, match="can't start new thread"),
        ):
            run_editor(port)
        assert port.restored
