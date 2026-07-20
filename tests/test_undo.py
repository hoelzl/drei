"""Undo semantics: C-//C-x u, descent, truncation, mark/modified restore."""

from __future__ import annotations

from drei.commands import (
    BackwardChar,
    ForwardChar,
    InsertText,
    KillLine,
    KillRegion,
    SetMark,
    TextUndone,
    Undo,
    Yank,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session(text: str = "", point: int = 0) -> EditorSession:
    return EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text=text, point=point))
    )


def test_undo_empty_stack_is_noop() -> None:
    session = _session("hello", 5)
    outcome = session.dispatch(Undo())
    assert outcome.events == ()
    assert session.buffer.current.text == "hello"
    assert session.buffer.current.point == 5
    assert not session.buffer.current.modified


def test_undo_reverts_insert() -> None:
    session = _session()
    session.dispatch(InsertText("hello"))
    outcome = session.dispatch(Undo())
    assert TextUndone(0, "hello", "", 5, 0, None, None) in outcome.events
    assert session.buffer.current.text == ""
    assert session.buffer.current.point == 0
    # modified restored to the pre-insert state (False on a fresh buffer)
    assert not session.buffer.current.modified


def test_undo_twice_descends() -> None:
    session = _session()
    session.dispatch(InsertText("ab"))
    session.dispatch(InsertText("cd"))
    session.dispatch(Undo())  # removes "cd"
    assert session.buffer.current.text == "ab"
    session.dispatch(Undo())  # removes "ab"
    assert session.buffer.current.text == ""
    assert session.buffer.current.point == 0


def test_undo_restores_point() -> None:
    session = _session("ab", 2)
    session.dispatch(InsertText("X"))  # point 3
    session.dispatch(Undo())
    assert session.buffer.current.text == "ab"
    assert session.buffer.current.point == 2


def test_undo_of_kill_restores_text() -> None:
    session = _session("hello", 0)
    session.dispatch(KillLine())  # kills "hello"
    assert session.buffer.current.text == ""
    session.dispatch(Undo())
    assert session.buffer.current.text == "hello"
    assert session.buffer.current.point == 0


def test_undo_of_kill_region_restores_mark_and_text() -> None:
    session = _session("hello world", 0)
    session.dispatch(SetMark())  # mark 0
    for _ in range(5):
        session.dispatch(ForwardChar())
    session.dispatch(KillRegion())  # kills "hello", mark cleared
    assert session.buffer.current.mark is None
    session.dispatch(Undo())
    assert session.buffer.current.text == "hello world"
    assert session.buffer.current.point == 5  # point_before the kill
    assert session.buffer.current.mark == 0  # mark resurrected


def test_undo_of_yank_restores() -> None:
    session = _session("hello", 0)
    session.dispatch(KillLine())
    session.dispatch(Yank())  # "hello" back at 0
    assert session.buffer.current.text == "hello"
    session.dispatch(Undo())  # undo the yank
    assert session.buffer.current.text == ""
    assert session.buffer.current.point == 0
    # ring still holds "hello" — undo does not touch the ring
    assert session.kill_ring == ("hello",)


def test_undo_clears_yank_active() -> None:
    session = _session("a\nb", 0)
    session.dispatch(KillLine())  # "a"
    session.dispatch(KillLine())  # newline (appends)
    session.dispatch(Yank())  # "a\n" back
    session.dispatch(Undo())  # undo the yank
    # M-y now: no active yank → no-op (ring has one entry anyway)
    from drei.commands import YankPop

    assert session.dispatch(YankPop()).events == ()


def test_fresh_edit_after_undo_truncates_redo() -> None:
    session = _session()
    session.dispatch(InsertText("ab"))
    session.dispatch(InsertText("cd"))
    session.dispatch(Undo())  # removes "cd"
    session.dispatch(InsertText("X"))  # fresh edit truncates the redo tail
    session.dispatch(Undo())  # removes "X"
    assert session.buffer.current.text == "ab"
    session.dispatch(Undo())  # removes "ab" — "cd" is NOT resurrected
    assert session.buffer.current.text == ""
    assert session.dispatch(Undo()).events == ()  # nothing left


def test_motion_between_undos_breaks_descent() -> None:
    session = _session()
    session.dispatch(InsertText("ab"))
    session.dispatch(InsertText("cd"))
    session.dispatch(Undo())  # removes "cd"
    session.dispatch(BackwardChar())  # event-emitting → breaks descent
    outcome = session.dispatch(Undo())  # redoes "cd" (direction flip)
    assert session.buffer.current.text == "abcd"
    assert outcome.events  # a redo event, not a no-op


def test_noop_command_does_not_break_descent() -> None:
    session = _session()
    session.dispatch(InsertText("ab"))
    session.dispatch(InsertText("cd"))
    session.dispatch(Undo())  # removes "cd"
    session.dispatch(BackwardChar())  # at point 2 — emits PointMoved
    # BackwardChar DOES emit... use a true no-op instead: yank on empty ring
    session2 = _session()
    session2.dispatch(InsertText("ab"))
    session2.dispatch(InsertText("cd"))
    session2.dispatch(Undo())
    session2.dispatch(Yank())  # empty ring → no-op, no event
    session2.dispatch(Undo())  # continues descending: removes "ab"
    assert session2.buffer.current.text == ""


def test_undo_stack_capacity() -> None:
    session = _session()
    for i in range(110):
        session.dispatch(InsertText(chr(97 + i % 26)))
    undone = 0
    while session.dispatch(Undo()).events:
        undone += 1
    assert undone == 100  # oldest 10 groups were dropped
    assert len(session.buffer.current.text) == 10  # first 10 remain


def test_undo_restores_modified_from_group() -> None:
    from conftest import FakeFilePort

    from drei.commands import SaveBuffer

    session = EditorSession(
        Buffer(
            BufferId("scratch"),
            BufferValue(text="hello", point=5, file_path="/tmp/u.txt"),
        ),
        file_port=FakeFilePort(),
    )
    session.dispatch(InsertText("!"))
    assert session.buffer.current.modified
    session.dispatch(SaveBuffer())
    saved_state = session.buffer.current
    assert not saved_state.modified
    session.dispatch(InsertText("?"))
    assert session.buffer.current.modified
    session.dispatch(Undo())  # undo "?" → back to the SAVED state
    assert session.buffer.current.text == "hello!"
    assert not session.buffer.current.modified  # restored from the group
