"""Line-ending fidelity: the port is byte-faithful, the buffer holds LF.

Review 0001 finding 1: ``SystemFilePort`` used text mode with default
newline handling, so every read collapsed CRLF to LF and every write
translated LF to ``os.linesep`` — saving an unedited file rewrote every line
ending on disk. The port now translates nothing; the session owns the
policy: a uniformly-CRLF file is remembered as such and normalized to LF in
the buffer, and the save translates back.
"""

from __future__ import annotations

from pathlib import Path

from drei.commands import InsertText, SaveBuffer
from drei.files import SystemFilePort, detect_line_ending
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session(path: Path, text: str) -> EditorSession:
    """A session visiting ``path`` with the given raw file text, as the CLI
    builds it at startup (read through the port, then handed to the buffer)."""
    return EditorSession(
        Buffer(
            BufferId(path.name),
            BufferValue(text=text, point=0, file_path=str(path)),
        ),
        file_port=SystemFilePort(),
    )


# --- port: no translation in either direction ------------------------------


def test_port_read_does_not_collapse_crlf(tmp_path: Path) -> None:
    target = tmp_path / "crlf.txt"
    target.write_bytes(b"line1\r\nline2\r\n")
    assert SystemFilePort().read(str(target)) == "line1\r\nline2\r\n"


def test_port_write_does_not_translate_lf(tmp_path: Path) -> None:
    target = tmp_path / "lf.txt"
    SystemFilePort().write(str(target), "line1\nline2\n")
    assert target.read_bytes() == b"line1\nline2\n"


def test_port_round_trip_is_byte_identical(tmp_path: Path) -> None:
    target = tmp_path / "rt.txt"
    original = b"line1\r\nline2\r\n"
    target.write_bytes(original)
    port = SystemFilePort()
    port.write(str(target), port.read(str(target)))
    assert target.read_bytes() == original


# --- detection: uniform CRLF only ------------------------------------------


def test_detect_line_ending() -> None:
    assert detect_line_ending("") == "\n"
    assert detect_line_ending("a\nb\n") == "\n"
    assert detect_line_ending("a\r\nb\r\n") == "\r\n"
    assert detect_line_ending("a\r\nb") == "\r\n"  # no final newline
    assert detect_line_ending("a\r\nb\nc") == "\n"  # mixed: no uniform ending
    assert detect_line_ending("a\rb") == "\n"  # lone CR is not a line ending


# --- session: buffer holds LF, disk keeps its endings ----------------------


def test_buffer_holds_lf_for_a_crlf_file(tmp_path: Path) -> None:
    target = tmp_path / "crlf.txt"
    target.write_bytes(b"one\r\ntwo\r\n")
    session = _session(target, SystemFilePort().read(str(target)))
    assert session.buffer.current.text == "one\ntwo\n"


def test_save_restores_crlf_endings(tmp_path: Path) -> None:
    target = tmp_path / "crlf.txt"
    target.write_bytes(b"one\r\ntwo\r\n")
    session = _session(target, SystemFilePort().read(str(target)))
    session.dispatch(InsertText("x"))
    session.dispatch(SaveBuffer())
    assert target.read_bytes() == b"xone\r\ntwo\r\n"


def test_save_of_an_unedited_crlf_file_is_byte_identical(tmp_path: Path) -> None:
    target = tmp_path / "crlf.txt"
    original = b"one\r\ntwo\r\n"
    target.write_bytes(original)
    session = _session(target, SystemFilePort().read(str(target)))
    session.dispatch(SaveBuffer())
    assert target.read_bytes() == original


def test_save_keeps_lf_file_lf(tmp_path: Path) -> None:
    target = tmp_path / "lf.txt"
    target.write_bytes(b"one\ntwo\n")
    session = _session(target, SystemFilePort().read(str(target)))
    session.dispatch(InsertText("x"))
    session.dispatch(SaveBuffer())
    assert target.read_bytes() == b"xone\ntwo\n"


def test_mixed_endings_pass_through_untouched(tmp_path: Path) -> None:
    """No uniform ending to preserve: the CRs stay literal buffer characters
    (Emacs shows them as ``^M``) and the save writes them back verbatim."""
    target = tmp_path / "mixed.txt"
    original = b"one\r\ntwo\nthree\r\n"
    target.write_bytes(original)
    session = _session(target, SystemFilePort().read(str(target)))
    assert session.buffer.current.text == "one\r\ntwo\nthree\r\n"
    session.dispatch(SaveBuffer())
    assert target.read_bytes() == original


def test_a_stray_cr_renders_as_caret_m(tmp_path: Path) -> None:
    """Byte-faithful reads make a lone CR a real buffer character; the frame
    shows it as ``^M`` (Emacs) instead of emitting a raw CR to the terminal."""
    from drei.render import render_session

    target = tmp_path / "mixed.txt"
    target.write_bytes(b"one\r\ntwo\n")
    session = _session(target, SystemFilePort().read(str(target)))
    frame = render_session(session.session_observation(), width=10, height=4)
    assert frame.rows[0] == "one^M     "


def test_new_file_saves_with_lf_on_every_platform(tmp_path: Path) -> None:
    """A buffer with no file contents to imitate uses LF everywhere: editor
    semantics stay independent of the host platform (AGENTS.md)."""
    target = tmp_path / "new.txt"
    session = _session(target, "")
    session.dispatch(InsertText("a\nb\n"))
    session.dispatch(SaveBuffer())
    assert target.read_bytes() == b"a\nb\n"


def test_find_file_remembers_the_visited_file_endings(tmp_path: Path) -> None:
    """The find-file path detects independently of the startup path."""
    from drei.commands import FindFile, MinibufferAccept, MinibufferInput

    target = tmp_path / "opened.txt"
    target.write_bytes(b"one\r\ntwo\r\n")
    session = EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text="", point=0)),
        file_port=SystemFilePort(),
    )
    session.dispatch(FindFile())
    for char in str(target):
        session.dispatch(MinibufferInput(char))
    session.dispatch(MinibufferAccept())
    assert session.buffer.current.text == "one\ntwo\n"
    session.dispatch(InsertText("x"))
    session.dispatch(SaveBuffer())
    assert target.read_bytes() == b"xone\r\ntwo\r\n"


def test_line_endings_are_per_buffer(tmp_path: Path) -> None:
    """Two buffers, two conventions: saving one must not touch the other's."""
    from drei.commands import FindFile, MinibufferAccept, MinibufferInput

    crlf = tmp_path / "crlf.txt"
    crlf.write_bytes(b"one\r\n")
    lf = tmp_path / "lf.txt"
    lf.write_bytes(b"two\n")
    session = _session(crlf, SystemFilePort().read(str(crlf)))
    session.dispatch(FindFile())
    for char in str(lf):
        session.dispatch(MinibufferInput(char))
    session.dispatch(MinibufferAccept())
    session.dispatch(InsertText("b"))
    session.dispatch(SaveBuffer())
    assert lf.read_bytes() == b"btwo\n"
    session.dispatch(FindFile())  # back to the CRLF buffer (create-or-select)
    for char in str(crlf):
        session.dispatch(MinibufferInput(char))
    session.dispatch(MinibufferAccept())
    session.dispatch(InsertText("a"))
    session.dispatch(SaveBuffer())
    assert crlf.read_bytes() == b"aone\r\n"
