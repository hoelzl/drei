"""A.2 step 2: find-file create-or-select (plan 0012 D1).

The slice-7 single-buffer wholesale replacement is removed: find-file on a
new path creates a NEW buffer (old buffer + its undo history survive); on an
already-open path it SELECTS that buffer instead of re-reading.
"""

from __future__ import annotations

from conftest import FakeFilePort

from drei.commands import (
    BufferCreated,
    BufferOpened,
    BufferSelected,
    FindFile,
    InsertText,
    KillLine,
    MinibufferAccept,
    MinibufferInput,
    Undo,
    Yank,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session(files: dict[str, str] | None = None, text: str = "") -> EditorSession:
    return EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text=text, point=0)),
        file_port=FakeFilePort(files),
    )


def _find_file(session: EditorSession, path: str) -> tuple:
    session.dispatch(FindFile())
    for char in path:
        session.dispatch(MinibufferInput(char))
    return session.dispatch(MinibufferAccept()).events


def test_find_file_creates_a_new_buffer_keeping_the_old_one() -> None:
    session = _session({"/tmp/alpha.txt": "alpha body"}, text="scratch text")
    events = _find_file(session, "/tmp/alpha.txt")
    assert BufferOpened("/tmp/alpha.txt", 10) in events
    assert BufferCreated("alpha.txt", "/tmp/alpha.txt") in events
    assert BufferSelected("alpha.txt") in events
    # The new buffer is current, named by basename, contents loaded.
    current = session.buffer.current
    assert session.buffer.buffer_id == BufferId("alpha.txt")
    assert current.text == "alpha body"
    assert current.file_path == "/tmp/alpha.txt"
    assert not current.modified
    # The old buffer survives untouched.
    assert set(session.buffers) == {"scratch", "alpha.txt"}
    assert session._buffers[BufferId("scratch")].current.text == "scratch text"


def test_find_file_preserves_the_old_buffers_undo_history() -> None:
    """The slice-7 'undo history dropped on open' deviation is resolved."""
    session = _session({"/tmp/f.txt": "new file"})
    session.dispatch(InsertText("undoable"))
    _find_file(session, "/tmp/f.txt")
    assert session.buffer.buffer_id == BufferId("f.txt")
    # Switch back by re-finding the old buffer's path is impossible (scratch
    # has no path) — select it by name through the internal seam used by C-x b.
    session._select_buffer(BufferId("scratch"), [])
    outcome = session.dispatch(Undo())
    assert session.buffer.current.text == ""
    assert len(outcome.events) == 1


def test_find_file_on_already_open_path_selects_instead_of_rereading() -> None:
    files = {"/tmp/a.txt": "disk version", "/tmp/other.txt": "other"}
    session = _session(files)
    _find_file(session, "/tmp/a.txt")
    session.dispatch(InsertText("EDITED "))  # user edits the buffer
    _find_file(session, "/tmp/other.txt")  # leave
    files["/tmp/a.txt"] = "disk changed"  # disk diverges while away
    events = _find_file(session, "/tmp/a.txt")  # come back
    # Selection, not creation/re-read: no BufferCreated, no BufferOpened.
    assert BufferSelected("a.txt") in events
    assert not any(isinstance(e, BufferCreated) for e in events)
    assert not any(isinstance(e, BufferOpened) for e in events)
    current = session.buffer.current
    assert current.text == "EDITED disk version"  # edits NOT lost
    assert current.modified


def test_find_file_missing_file_creates_empty_named_buffer() -> None:
    session = _session()
    events = _find_file(session, "/tmp/brand-new.txt")
    assert BufferOpened("/tmp/brand-new.txt", 0) in events
    assert BufferCreated("brand-new.txt", "/tmp/brand-new.txt") in events
    current = session.buffer.current
    assert current.text == ""
    assert current.file_path == "/tmp/brand-new.txt"
    assert not current.modified


def test_basename_collision_gets_numeric_suffix() -> None:
    """Deviation from Emacs's <dirname> uniquify (plan 0012 evidence 1)."""
    session = _session({"/x/probe.txt": "one", "/y/probe.txt": "two"})
    _find_file(session, "/x/probe.txt")
    events = _find_file(session, "/y/probe.txt")
    assert BufferCreated("probe.txt<2>", "/y/probe.txt") in events
    assert session.buffer.buffer_id == BufferId("probe.txt<2>")
    assert session.buffer.current.text == "two"


def test_find_file_same_path_as_current_buffer_is_a_quiet_select() -> None:
    session = _session({"/tmp/a.txt": "body"})
    _find_file(session, "/tmp/a.txt")
    session.dispatch(InsertText("x"))
    events = _find_file(session, "/tmp/a.txt")
    assert events == ()


def test_kill_ring_is_global_across_buffers() -> None:
    """Slice-7 pin, re-asserted across REAL buffers: kill in one, yank in
    the other."""
    session = _session({"/tmp/b.txt": "target"}, text="killme")
    session.buffer.replace(BufferValue(text="killme", point=0))
    session.dispatch(KillLine())  # ring gets "killme"
    _find_file(session, "/tmp/b.txt")
    session.dispatch(Yank())
    assert session.buffer.current.text == "killmetarget"


def test_open_failed_still_leaves_everything_untouched() -> None:
    session = _session(text="keep")
    session.dispatch(FindFile())
    for char in "/nope":
        session.dispatch(MinibufferInput(char))
    from conftest import FakeFilePort as _F

    session._files = _F(fail_read="permission")
    outcome = session.dispatch(MinibufferAccept())
    assert len(outcome.events) == 1
    assert session.buffers == ("scratch",)
    assert session.buffer.current.text == "keep"
