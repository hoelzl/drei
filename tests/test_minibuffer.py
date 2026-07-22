"""Minibuffer: C-x C-f find-file — prompt, input, accept, abort, open."""

from __future__ import annotations

from pathlib import Path

from conftest import FakeFilePort

from drei.commands import (
    BufferOpened,
    FindFile,
    InsertText,
    KeyboardQuit,
    MinibufferAbort,
    MinibufferAborted,
    MinibufferAccept,
    MinibufferBackspace,
    MinibufferInput,
    MinibufferOpened,
    OpenFailed,
    SetMark,
)
from drei.files import SystemFilePort
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session(
    files: FakeFilePort | None = None,
    text: str = "",
) -> EditorSession:
    """Default port is the real filesystem (tmp_path fixtures); pass a
    FakeFilePort for read-failure arms."""
    return EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text=text, point=len(text))),
        file_port=files if files is not None else SystemFilePort(),
    )


def test_find_file_opens_minibuffer_with_prompt() -> None:
    session = _session()
    outcome = session.dispatch(FindFile())
    assert MinibufferOpened("Find file: ") in outcome.events
    assert session.minibuffer == ""
    assert session.minibuffer_prompt == "Find file: "


def test_minibuffer_input_and_backspace() -> None:
    session = _session()
    session.dispatch(FindFile())
    session.dispatch(MinibufferInput("a"))
    session.dispatch(MinibufferInput("b"))
    assert session.minibuffer == "ab"
    session.dispatch(MinibufferBackspace())
    assert session.minibuffer == "a"
    session.dispatch(MinibufferBackspace())
    session.dispatch(MinibufferBackspace())  # no-op at empty
    assert session.minibuffer == ""


def test_abort_closes_prompt_and_preserves_buffer_and_mark() -> None:
    session = _session(text="hello")
    session.dispatch(SetMark())  # mark at 5
    session.dispatch(FindFile())
    session.dispatch(MinibufferInput("x"))
    outcome = session.dispatch(MinibufferAbort())
    assert MinibufferAborted() in outcome.events
    # Abort must NOT emit KeyboardQuitEvent (terminal exits on that event)
    assert all(type(event).__name__ != "KeyboardQuitEvent" for event in outcome.events)
    assert session.minibuffer is None
    assert session.buffer.current.text == "hello"
    assert session.buffer.current.mark == 5  # mark survives abort


def test_non_minibuffer_commands_are_noops_while_active() -> None:
    session = _session(text="hello")
    session.dispatch(FindFile())
    outcome = session.dispatch(InsertText("z"))
    assert outcome.events == ()
    assert session.buffer.current.text == "hello"
    assert session.dispatch(KeyboardQuit()).events == ()  # no quit while open


def test_nested_find_file_ignored() -> None:
    session = _session()
    session.dispatch(FindFile())
    session.dispatch(MinibufferInput("a"))
    assert session.dispatch(FindFile()).events == ()
    assert session.minibuffer == "a"  # input preserved


def test_accept_existing_file_replaces_buffer(tmp_path: Path) -> None:
    """A.2: find-file creates a new buffer and selects it (no more
    single-buffer wholesale replacement — the old buffer survives)."""
    target = tmp_path / "fixture.txt"
    target.write_text("line one\nline two", encoding="utf-8")
    session = _session(text="old dirty text")
    session.dispatch(InsertText("!"))  # modified=True
    session.dispatch(FindFile())
    for char in str(target):
        session.dispatch(MinibufferInput(char))
    outcome = session.dispatch(MinibufferAccept())
    opened = [e for e in outcome.events if isinstance(e, BufferOpened)]
    assert len(opened) == 1
    assert opened[0].path == str(target)
    current = session.buffer.current
    assert current.text == "line one\nline two"
    assert current.point == 0
    assert current.file_path == str(target)
    assert not current.modified
    assert current.mark is None
    assert session.minibuffer is None
    # The old buffer survives with its dirty text (A.2 create-or-select).
    assert session._buffers[BufferId("scratch")].current.text == "old dirty text!"


def test_accept_missing_file_creates_empty_buffer(tmp_path: Path) -> None:
    missing = str(tmp_path / "new.txt")
    session = _session(text="old")
    session.dispatch(FindFile())
    for char in missing:
        session.dispatch(MinibufferInput(char))
    outcome = session.dispatch(MinibufferAccept())
    assert any(isinstance(e, BufferOpened) for e in outcome.events)
    current = session.buffer.current
    assert current.text == ""
    assert current.point == 0
    assert current.file_path == missing
    assert not current.modified


def test_accept_empty_input_is_noop_close() -> None:
    session = _session(text="keep")
    session.dispatch(FindFile())
    outcome = session.dispatch(MinibufferAccept())
    assert outcome.events == ()
    assert session.minibuffer is None
    assert session.buffer.current.text == "keep"


def test_open_keeps_old_buffer_undo_history(tmp_path: Path) -> None:
    """A.2 resolves the slice-7 deviation: the old buffer's undo history is
    no longer dropped — switching back to it resumes its own undo stack."""
    target = tmp_path / "f.txt"
    target.write_text("new", encoding="utf-8")
    session = _session()
    session.dispatch(InsertText("undoable"))
    session.dispatch(FindFile())
    for char in str(target):
        session.dispatch(MinibufferInput(char))
    session.dispatch(MinibufferAccept())
    # New buffer selected; the old buffer's undo history survived the open.
    assert session.buffer.current.text == "new"
    session._select_buffer(BufferId("scratch"), [])
    from drei.commands import Undo

    assert len(session.dispatch(Undo()).events) == 1
    assert session.buffer.current.text == ""


def test_open_no_longer_discards_unsaved_edits(tmp_path: Path) -> None:
    """A.2 resolves the registry row 'find-file replacing the buffer': a
    successful find-file on a MODIFIED buffer creates a new buffer instead
    of wholesale-replacing — the dirty buffer and its undo history survive
    (Emacs per-file buffer behavior)."""
    target = tmp_path / "f.txt"
    target.write_text("new", encoding="utf-8")
    session = _session()
    session.dispatch(InsertText("dirty"))
    assert session.buffer.current.modified
    session.dispatch(FindFile())
    for char in str(target):
        session.dispatch(MinibufferInput(char))
    outcome = session.dispatch(MinibufferAccept())
    assert BufferOpened(str(target), 3) in outcome.events
    assert session.buffer.current.text == "new"
    # The dirty buffer survives, still modified, edits intact.
    old = session._buffers[BufferId("scratch")].current
    assert old.text == "dirty"
    assert old.modified


def test_open_preserves_kill_ring_and_clears_yank_state(tmp_path: Path) -> None:
    from drei.commands import KillLine, YankPop

    target = tmp_path / "f.txt"
    target.write_text("new", encoding="utf-8")
    session = _session(text="killme")
    session.buffer.replace(
        BufferValue(text="killme", point=0)
    )  # point 0 so C-k kills the line
    session.dispatch(KillLine())  # ring gets "killme"
    session.dispatch(FindFile())
    for char in str(target):
        session.dispatch(MinibufferInput(char))
    session.dispatch(MinibufferAccept())
    assert session.kill_ring == ("killme",)  # ring preserved
    # Yank state cleared by the event-emitting open: M-y is a no-op.
    assert session.dispatch(YankPop()).events == ()


def test_open_failed_on_read_error_leaves_buffer_untouched() -> None:
    files = FakeFilePort(fail_read="permission")
    session = _session(files=files, text="untouched")
    session.dispatch(FindFile())
    for char in "/some/path":
        session.dispatch(MinibufferInput(char))
    outcome = session.dispatch(MinibufferAccept())
    failed = [e for e in outcome.events if isinstance(e, OpenFailed)]
    assert len(failed) == 1
    assert failed[0].path == "/some/path"
    assert session.minibuffer is None
    assert session.buffer.current.text == "untouched"


def test_binary_file_open_fails_without_crash() -> None:
    files = FakeFilePort(fail_read="binary")
    session = _session(files=files, text="keep")
    session.dispatch(FindFile())
    for char in "/x/blob.bin":
        session.dispatch(MinibufferInput(char))
    outcome = session.dispatch(MinibufferAccept())
    assert any(isinstance(e, OpenFailed) for e in outcome.events)
    assert session.buffer.current.text == "keep"
