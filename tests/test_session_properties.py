from conftest import FakeFilePort
from hypothesis import given, settings
from hypothesis import strategies as st

from drei.commands import (
    BackwardChar,
    ForwardChar,
    InsertText,
    KillLine,
    SaveBuffer,
    TextKilled,
    Yank,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession

settings.register_profile("ci", max_examples=50, derandomize=True, deadline=None)
settings.load_profile("ci")


def _session(port: FakeFilePort | None = None) -> EditorSession:
    return EditorSession(
        Buffer(
            BufferId("scratch"),
            BufferValue(text="", point=0, file_path="/tmp/prop.txt"),
        ),
        file_port=port if port is not None else FakeFilePort(),
    )


@st.composite
def command_history(draw: st.DrawFn) -> list[object]:
    size = draw(st.integers(min_value=0, max_value=20))
    return [
        draw(
            st.one_of(
                st.builds(InsertText, st.text(min_size=0, max_size=5)),
                st.just(ForwardChar()),
                st.just(BackwardChar()),
                st.just(SaveBuffer()),
                st.just(KillLine()),
                st.just(Yank()),
            )
        )
        for _ in range(size)
    ]


@given(command_history())
def test_point_always_in_bounds(history: list[object]) -> None:
    session = _session()
    for command in history:
        session.dispatch(command)  # type: ignore[arg-type]
        current = session.buffer.current
        assert 0 <= current.point <= len(current.text)


@given(command_history())
def test_replay_produces_identical_evidence(history: list[object]) -> None:
    def run() -> tuple[tuple[object, ...], str, int, bool, tuple[str, ...]]:
        session = _session()
        outcomes = tuple(session.dispatch(c) for c in history)  # type: ignore[arg-type]
        current = session.buffer.current
        return (
            outcomes,
            current.text,
            current.point,
            current.modified,
            session.kill_ring,
        )

    first, text1, point1, modified1, ring1 = run()
    second, text2, point2, modified2, ring2 = run()
    assert first == second
    assert text1 == text2
    assert point1 == point2
    assert modified1 == modified2
    assert ring1 == ring2


@given(command_history())
def test_insertion_preserves_existing_text(history: list[object]) -> None:
    session = _session()
    for command in history:
        if isinstance(command, InsertText) and command.text:
            before = session.buffer.current
            session.dispatch(command)
            after = session.buffer.current
            assert after.text[: before.point] == before.text[: before.point]
            assert after.text[after.point :] == before.text[before.point :]


@given(command_history())
def test_modified_flag_consistent_with_history(history: list[object]) -> None:
    """Modified is true iff a text-changing event postdates the last save."""
    session = _session()
    expect_modified = False
    for command in history:
        outcome = session.dispatch(command)  # type: ignore[arg-type]
        if (
            isinstance(command, InsertText)
            and command.text
            or isinstance(command, KillLine)
            and any(isinstance(e, TextKilled) for e in outcome.events)
            or isinstance(command, Yank)
            and outcome.events
        ):
            expect_modified = True
        elif isinstance(command, SaveBuffer):
            expect_modified = False
        assert session.buffer.current.modified is expect_modified


@given(command_history())
def test_save_writes_current_text(history: list[object]) -> None:
    port = FakeFilePort()
    session = _session(port)
    for command in history:
        session.dispatch(command)  # type: ignore[arg-type]
        if isinstance(command, SaveBuffer):
            assert port.files["/tmp/prop.txt"] == session.buffer.current.text


@given(command_history())
def test_successful_kill_then_yank_restores_text(history: list[object]) -> None:
    """Narrowed round trip: a non-empty kill followed by yank restores text."""
    session = _session()
    previous_was_nonempty_kill = False
    pre_kill_text = ""
    for command in history:
        if previous_was_nonempty_kill and isinstance(command, Yank):
            session.dispatch(command)
            assert session.buffer.current.text == pre_kill_text
            previous_was_nonempty_kill = False
            continue
        previous_was_nonempty_kill = False
        if isinstance(command, KillLine):
            before = session.buffer.current
            outcome = session.dispatch(command)
            if any(isinstance(e, TextKilled) and e.text for e in outcome.events):
                previous_was_nonempty_kill = True
                pre_kill_text = before.text
        else:
            session.dispatch(command)  # type: ignore[arg-type]


def test_yank_with_empty_ring_changes_nothing() -> None:
    session = _session()
    outcome = session.dispatch(Yank())
    assert outcome.events == ()
    assert session.buffer.current.text == ""
    assert not session.buffer.current.modified
