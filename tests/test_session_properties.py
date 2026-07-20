from conftest import FakeFilePort
from hypothesis import given, settings
from hypothesis import strategies as st

from drei.commands import (
    BackwardChar,
    CopyRegionAsKill,
    ExchangePointAndMark,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KeyboardQuitEvent,
    KillLine,
    KillRegion,
    MarkSet,
    RegionCopied,
    RegionKilled,
    SaveBuffer,
    SetMark,
    TextKilled,
    TextYanked,
    TextYankPopped,
    Yank,
    YankPop,
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
                st.just(YankPop()),
                st.just(SetMark()),
                st.just(KillRegion()),
                st.just(CopyRegionAsKill()),
                st.just(ExchangePointAndMark()),
                st.just(KeyboardQuit()),
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
def test_mark_always_in_bounds(history: list[object]) -> None:
    """The mark adjusts with every edit; it can never leave 0..len(text)."""
    session = _session()
    for command in history:
        session.dispatch(command)  # type: ignore[arg-type]
        current = session.buffer.current
        assert current.mark is None or 0 <= current.mark <= len(current.text)


@given(command_history())
def test_mark_fold_matches_transcript(history: list[object]) -> None:
    """Mark state is derivable from the transcript (the evidence oracle).

    Fold: MarkSet sets; RegionKilled/RegionCopied/KeyboardQuitEvent clear;
    RegionKilled adjusts prior to clearing — but the fold sees the same
    edit events as the session, so it can replay adjustment. Here we check
    the simpler structural rule: mark is set iff a MarkSet event is the
    most recent of (MarkSet, RegionKilled, RegionCopied, KeyboardQuitEvent)
    in the transcript.
    """
    session = _session()
    for command in history:
        session.dispatch(command)  # type: ignore[arg-type]
    mark_set = False
    for event in session.transcript:
        if isinstance(event, MarkSet):
            mark_set = True
        elif isinstance(event, (RegionKilled, RegionCopied, KeyboardQuitEvent)):
            mark_set = False
    assert (session.buffer.current.mark is not None) == mark_set


@given(command_history())
def test_replay_produces_identical_evidence(history: list[object]) -> None:
    def run() -> tuple[tuple[object, ...], str, int, bool, tuple[str, ...], int | None]:
        session = _session()
        outcomes = tuple(session.dispatch(c) for c in history)  # type: ignore[arg-type]
        current = session.buffer.current
        return (
            outcomes,
            current.text,
            current.point,
            current.modified,
            session.kill_ring,
            current.mark,
        )

    first, text1, point1, modified1, ring1, mark1 = run()
    second, text2, point2, modified2, ring2, mark2 = run()
    assert first == second
    assert text1 == text2
    assert point1 == point2
    assert modified1 == modified2
    assert ring1 == ring2
    assert mark1 == mark2


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
            or isinstance(command, (Yank, YankPop))
            and outcome.events
            or isinstance(command, KillRegion)
            and any(isinstance(e, RegionKilled) for e in outcome.events)
        ):
            expect_modified = True
        elif isinstance(command, SaveBuffer):
            expect_modified = False
        # SetMark / CopyRegionAsKill / ExchangePointAndMark / KeyboardQuit
        # never change text (verified vs Emacs: copy and set-mark leave
        # buffer-modified-p nil), so they get no arm here.
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
    """Narrowed round trip: a chain-opening kill followed by yank restores text.

    Only a kill that follows a non-kill command arms the check: chained
    kills append to the same ring entry, so yanking after a chain inserts
    the whole chain, not just the last kill's text.
    """
    session = _session()
    armed = False
    pre_kill_text = ""
    chain_open = False
    for command in history:
        if armed and isinstance(command, Yank):
            session.dispatch(command)
            assert session.buffer.current.text == pre_kill_text
            armed = False
            chain_open = False
            continue
        armed = False
        if isinstance(command, KillLine):
            before = session.buffer.current
            outcome = session.dispatch(command)
            if any(isinstance(e, TextKilled) and e.text for e in outcome.events):
                if not chain_open:
                    armed = True
                    pre_kill_text = before.text
                chain_open = True
            # no-op kill: chain state unchanged
        else:
            outcome = session.dispatch(command)  # type: ignore[arg-type]
            if outcome.events:
                # Only event-emitting commands break the session's chain.
                chain_open = False


def test_yank_with_empty_ring_changes_nothing() -> None:
    session = _session()
    outcome = session.dispatch(Yank())
    assert outcome.events == ()
    assert session.buffer.current.text == ""
    assert not session.buffer.current.modified


@given(command_history())
def test_yank_pop_transcript_coherence(history: list[object]) -> None:
    """Every TextYankPopped follows a TextYanked/TextYankPopped with no gap."""
    session = _session()
    for command in history:
        session.dispatch(command)  # type: ignore[arg-type]
    transcript = session.transcript
    for i, event in enumerate(transcript):
        if isinstance(event, TextYankPopped):
            assert i > 0
            assert isinstance(transcript[i - 1], (TextYanked, TextYankPopped))


@given(command_history())
def test_yank_pop_replaces_with_ring_entry(history: list[object]) -> None:
    """A pop after a yank replaces the yanked span with ring[cursor+1 % len]."""
    session = _session()
    last_yank: tuple[int, int] | None = None  # (start, end) of last yank/pop
    cursor = 0
    for command in history:
        before = session.buffer.current
        outcome = session.dispatch(command)  # type: ignore[arg-type]
        if isinstance(command, Yank) and outcome.events:
            last_yank = (before.point, before.point + len(session.kill_ring[0]))
            cursor = 0
        elif isinstance(command, YankPop):
            popped = [e for e in outcome.events if isinstance(e, TextYankPopped)]
            if popped:
                assert last_yank is not None
                ring = session.kill_ring
                assert len(ring) >= 2
                cursor = (cursor + 1) % len(ring)
                event = popped[0]
                assert event.new_text == ring[cursor]
                assert event.before == last_yank[0]
                assert event.after == event.before + len(event.new_text)
                last_yank = (event.before, event.after)
            # no-op pop: active state unchanged (still whatever it was)
        elif outcome.events:
            last_yank = None
