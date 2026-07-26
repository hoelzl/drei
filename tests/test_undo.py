"""Undo semantics: C-//C-x u, descent, truncation, mark/modified restore."""

from __future__ import annotations

from conftest import FakeFilePort

from drei.commands import (
    BackwardChar,
    ForwardChar,
    InsertText,
    KillLine,
    KillRegion,
    Message,
    SaveBuffer,
    SetMark,
    TextUndone,
    Undo,
    Yank,
    YankPop,
)
from drei.files import FilePort
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session(text: str = "", point: int = 0) -> EditorSession:
    return EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text=text, point=point))
    )


def test_undo_empty_stack_is_noop() -> None:
    session = _session("hello", 5)
    outcome = session.dispatch(Undo())
    # Speaks (row 80) but changes nothing — a Message is not a semantic event.
    assert outcome.events == (Message("no-further-undo"),)
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
    # M-y now: no active yank → speaks (row 68), changes nothing
    from drei.commands import YankPop

    assert session.dispatch(YankPop()).events == (
        Message("previous-command-not-a-yank"),
    )


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
    # Nothing left: speaks (row 80), changes nothing.
    assert session.dispatch(Undo()).events == (Message("no-further-undo"),)


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


def test_exhausted_undo_does_not_flip_into_redo() -> None:
    """Review 0001 finding 2: an exhausted Undo intervenes in nothing and
    must not break the descent. Before the fix a held C-/ oscillated the
    buffer with period 3 (undo → no-op → redo → …) forever. Since plan 0019
    the no-op SPEAKS (row 80) — a Message, which describes rather than acts
    (D2), so the oscillation guard is unchanged."""
    session = _session()
    session.dispatch(InsertText("a"))
    session.dispatch(Undo())  # removes "a" — the only group
    assert session.buffer.current.text == ""
    for _ in range(6):
        outcome = session.dispatch(Undo())
        assert outcome.events == (Message("no-further-undo"),)
        assert session.buffer.current.text == ""


def test_a_speaking_no_op_does_not_break_the_undo_descent() -> None:
    """Plan 0019 D2's hazard, proven: undo three times (descending), press
    M-y on an empty ring — a no-op that now emits a Message — then C-/. If
    the Message counted as an intervening event the descent would be broken
    and the last C-/ would REDO; it must undo instead (registry row 82:
    only event-emitting commands intervene, and a message is not one)."""
    session = _session()
    for char in "abcd":
        session.dispatch(InsertText(char))
    session.dispatch(Undo())  # removes "d"
    session.dispatch(Undo())  # removes "c"
    session.dispatch(Undo())  # removes "b"
    assert session.buffer.current.text == "a"
    pop = session.dispatch(YankPop())  # speaks; must not intervene
    assert pop.events == (Message("previous-command-not-a-yank"),)
    outcome = session.dispatch(Undo())
    assert session.buffer.current.text == ""  # undid "a" — not redone to "ab"
    assert any(isinstance(e, TextUndone) for e in outcome.events)


def test_undo_stack_capacity() -> None:
    session = _session()
    for i in range(110):
        session.dispatch(InsertText(chr(97 + i % 26)))
    undone = 0
    # Exhaustion speaks now (row 80), so the loop keys on semantic events —
    # the same correction the property-suite twin of this loop needed.
    while any(not isinstance(e, Message) for e in session.dispatch(Undo()).events):
        undone += 1
    assert undone == 100  # oldest 10 groups were dropped
    assert len(session.buffer.current.text) == 10  # first 10 remain


def _file_session(text: str = "hello", port: FilePort | None = None) -> EditorSession:
    return EditorSession(
        Buffer(
            BufferId("u.txt"),
            BufferValue(text=text, point=len(text), file_path="/tmp/u.txt"),
        ),
        file_port=port if port is not None else FakeFilePort(),
    )


def _current(session: EditorSession) -> BufferValue:
    """Read the live value through a call: repeated asserts on the same
    attribute chain would otherwise narrow it to a literal for mypy."""
    return session.buffer.current


def test_undo_to_the_saved_text_clears_modified() -> None:
    session = _file_session()
    session.dispatch(InsertText("!"))
    assert _current(session).modified
    session.dispatch(SaveBuffer())
    assert not _current(session).modified
    session.dispatch(InsertText("?"))
    assert _current(session).modified
    session.dispatch(Undo())  # undo "?" → back to the SAVED state
    assert _current(session).text == "hello!"
    assert not _current(session).modified  # matches what was written


def test_undo_past_the_save_keeps_the_buffer_modified() -> None:
    """Review 0001 finding 3: undoing *past* a save leaves the buffer
    different from what is on disk, so the modeline must not report clean."""
    port = FakeFilePort()
    session = _file_session(port=port)
    session.dispatch(InsertText("!"))
    session.dispatch(SaveBuffer())
    assert port.files["/tmp/u.txt"] == "hello!"
    session.dispatch(Undo())  # back past the save point
    assert _current(session).text == "hello"  # disk still holds "hello!"
    assert _current(session).modified


def test_redo_forward_to_the_saved_text_clears_modified() -> None:
    session = _file_session()
    session.dispatch(InsertText("!"))
    session.dispatch(SaveBuffer())
    session.dispatch(Undo())  # "hello", modified
    session.dispatch(BackwardChar())  # breaks the descent → next Undo redoes
    session.dispatch(Undo())
    assert _current(session).text == "hello!"
    assert not _current(session).modified


def test_undo_in_a_never_saved_buffer_cannot_report_clean() -> None:
    """A buffer that arrived already modified has no known on-disk text, so
    no undo can prove it matches the file — it stays modified until a save."""
    session = EditorSession(
        Buffer(
            BufferId("u.txt"),
            BufferValue(text="hi", point=2, file_path="/tmp/u.txt", modified=True),
        )
    )
    session.dispatch(InsertText("!"))
    session.dispatch(Undo())
    assert _current(session).text == "hi"
    assert _current(session).modified
