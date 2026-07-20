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
