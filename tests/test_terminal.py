from __future__ import annotations

from pathlib import Path

import pytest

from drei.terminal import TerminalPort, run_editor


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


def test_editor_writes_readiness_and_exits_on_quit() -> None:
    port = FakePort(["\x07"])
    run_editor(port)
    assert port.outputs[0] == "DREI:READY\n"
    assert port.restored
    assert port.raw_entered


def test_editor_inserts_text_and_renders() -> None:
    port = FakePort(["a", "\x07"])
    run_editor(port)
    written = "".join(port.outputs)
    assert "a" in written


def test_editor_restores_on_exception() -> None:
    class BoomPort(FakePort):
        def read_key(self) -> str:
            raise RuntimeError("boom")

    port = BoomPort([])
    with pytest.raises(RuntimeError, match="boom"):
        run_editor(port)
    assert port.restored


def test_unresolved_key_marks_quiescence_without_frame_rewrite() -> None:
    # DEL is not bound to any command; the loop must still emit the
    # readiness marker (quiescence) but must not rewrite the frame.
    port = FakePort(["\x7f", "\x07"])
    run_editor(port)
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
    run_editor(port)
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
    run_editor(port, initial_text="one\ntwo\nthree")
    frames = "".join(port.outputs).split("\x1b[2J\x1b[H")
    pop_frame = frames[-2]  # last frame before the quit frame
    buffer_line = pop_frame.split("\r\n")[1]  # first buffer row (row 0 is blank)
    assert buffer_line.startswith("one")


def test_editor_esc_non_letter_reprocesses_byte() -> None:
    # ESC then "1": the bare ESC is unresolved; the "1" is reprocessed and
    # inserted as printable text.
    port = FakePort(["\x1b", "1", "\x07"])
    run_editor(port)
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
    run_editor(port)
    written = "".join(port.outputs)
    # Markers: initial frame, ESC (unresolved, no frame), "1" (frame), and
    # the final C-g quit frame carries none.
    assert written.count("\x1b]7791;ready\x1b\\") == 3


def test_editor_esc_consumed_as_chord_start_then_quit() -> None:
    # ESC followed by C-g: bare ESC reported (unresolved), C-g reprocessed
    # and quits the loop.
    port = FakePort(["\x1b", "\x07"])
    run_editor(port)
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


def test_decode_key_maps_kill_and_yank() -> None:
    from drei.terminal import decode_key

    assert decode_key("\x0b") == "C-k"
    assert decode_key("\x19") == "C-y"


def test_assemble_meta_esc_letter_yields_meta_chord() -> None:
    from drei.terminal import assemble_meta

    pending, key = assemble_meta(False, "\x1b")
    assert pending and key is None
    pending, key = assemble_meta(pending, "y")
    assert not pending
    assert key == "M-y"


def test_assemble_meta_esc_non_letter_reports_bare_esc() -> None:
    from drei.terminal import assemble_meta

    pending, key = assemble_meta(True, "1")
    assert not pending
    assert key == "\x1b"  # caller reprocesses the "1" with no pending state


def test_assemble_meta_esc_control_byte_reports_bare_esc() -> None:
    from drei.terminal import assemble_meta

    pending, key = assemble_meta(True, "\x07")  # ESC C-g: bare ESC, C-g reprocessed
    assert not pending
    assert key == "\x1b"


def test_assemble_meta_plain_byte_decodes_normally() -> None:
    from drei.terminal import assemble_meta

    pending, key = assemble_meta(False, "\x0b")
    assert not pending
    assert key == "C-k"


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
