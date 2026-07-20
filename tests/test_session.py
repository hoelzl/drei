import pytest

from drei.commands import (
    BackwardChar,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KeyboardQuitEvent,
    PointMoved,
    TextInserted,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def make_session() -> EditorSession:
    return EditorSession(Buffer(BufferId("scratch"), BufferValue(text="", point=0)))


def test_insert_into_empty_buffer() -> None:
    session = make_session()
    outcome = session.dispatch(InsertText("hello"))
    assert outcome.observation.text == "hello"
    assert outcome.observation.point == 5
    assert outcome.events == (TextInserted("hello", 0, 5),)


def test_insert_in_middle() -> None:
    session = make_session()
    session.dispatch(InsertText("ac"))
    session.dispatch(BackwardChar())
    outcome = session.dispatch(InsertText("b"))
    assert outcome.observation.text == "abc"
    assert outcome.observation.point == 2


def test_move_within_bounds() -> None:
    session = make_session()
    session.dispatch(InsertText("ab"))
    outcome = session.dispatch(BackwardChar())
    assert outcome.observation.point == 1
    assert outcome.events == (PointMoved(-1, -1),)


def test_clamp_at_beginning_and_end() -> None:
    session = make_session()
    session.dispatch(InsertText("ab"))
    outcome = session.dispatch(ForwardChar())
    assert outcome.observation.point == 2
    assert outcome.events == (PointMoved(1, 0),)

    session.dispatch(BackwardChar())
    session.dispatch(BackwardChar())
    outcome = session.dispatch(BackwardChar())
    assert outcome.observation.point == 0
    assert outcome.events == (PointMoved(-1, 0),)


def test_quit_does_not_mutate() -> None:
    session = make_session()
    session.dispatch(InsertText("x"))
    outcome = session.dispatch(KeyboardQuit())
    assert outcome.observation.text == "x"
    assert outcome.observation.point == 1
    assert outcome.events == (KeyboardQuitEvent(),)


def test_retained_shell_reference_stays_current() -> None:
    shell = Buffer(BufferId("scratch"), BufferValue(text="", point=0))
    session = EditorSession(shell)
    session.dispatch(InsertText("x"))
    assert shell.current.text == "x"


def test_failure_is_atomic() -> None:
    session = make_session()
    session.dispatch(InsertText("ok"))
    before = session.buffer.current
    before_events = len(session.transcript)

    class BadCommand:
        pass

    with pytest.raises(TypeError, match="unsupported"):
        session.dispatch(BadCommand())  # type: ignore[arg-type]

    assert session.buffer.current is before
    assert len(session.transcript) == before_events
