from hypothesis import given, settings
from hypothesis import strategies as st

from drei.commands import BackwardChar, ForwardChar, InsertText
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession

settings.register_profile("ci", max_examples=50, derandomize=True, deadline=None)
settings.load_profile("ci")


@st.composite
def command_history(draw: st.DrawFn) -> list[object]:
    size = draw(st.integers(min_value=0, max_value=20))
    return [
        draw(
            st.one_of(
                st.builds(InsertText, st.text(min_size=0, max_size=5)),
                st.just(ForwardChar()),
                st.just(BackwardChar()),
            )
        )
        for _ in range(size)
    ]


@given(command_history())
def test_point_always_in_bounds(history: list[object]) -> None:
    session = EditorSession(Buffer(BufferId("scratch"), BufferValue(text="", point=0)))
    for command in history:
        session.dispatch(command)  # type: ignore[arg-type]
        current = session.buffer.current
        assert 0 <= current.point <= len(current.text)


@given(command_history())
def test_replay_produces_identical_evidence(history: list[object]) -> None:
    def run() -> tuple[tuple[object, ...], str, int]:
        session = EditorSession(
            Buffer(BufferId("scratch"), BufferValue(text="", point=0))
        )
        outcomes = tuple(session.dispatch(c) for c in history)  # type: ignore[arg-type]
        current = session.buffer.current
        return outcomes, current.text, current.point

    first, text1, point1 = run()
    second, text2, point2 = run()
    assert first == second
    assert text1 == text2
    assert point1 == point2


@given(command_history())
def test_insertion_preserves_existing_text(history: list[object]) -> None:
    session = EditorSession(Buffer(BufferId("scratch"), BufferValue(text="", point=0)))
    for command in history:
        if isinstance(command, InsertText) and command.text:
            before = session.buffer.current
            session.dispatch(command)
            after = session.buffer.current
            assert after.text[: before.point] == before.text[: before.point]
            assert after.text[after.point :] == before.text[before.point :]
