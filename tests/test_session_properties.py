from conftest import FakeFilePort, FakeProcessPort
from hypothesis import given, settings
from hypothesis import strategies as st

from drei.commands import (
    BackwardChar,
    CopyRegionAsKill,
    DeliverProcessOutput,
    ExchangePointAndMark,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KeyboardQuitEvent,
    KillLine,
    KillRegion,
    MarkSet,
    ProcessOutputRecorded,
    RegionCopied,
    RegionKilled,
    SaveBuffer,
    SetMark,
    TextKilled,
    TextRedone,
    TextUndone,
    TextYanked,
    TextYankPopped,
    Undo,
    Yank,
    YankPop,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.process import ProcessResult
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


def _process_session(port: FakeProcessPort | None = None) -> EditorSession:
    return EditorSession(
        Buffer(
            BufferId("scratch"),
            BufferValue(text="", point=0, file_path="/tmp/prop.txt"),
        ),
        file_port=FakeFilePort(),
        process_port=port if port is not None else FakeProcessPort(),
    )


@st.composite
def _deliveries(draw: st.DrawFn) -> DeliverProcessOutput:
    """A process delivery: a captured result or a normalized launch failure."""
    if draw(st.booleans()):
        result = ProcessResult(
            argv=("cmd",),
            exit_code=draw(st.integers(min_value=0, max_value=3)),
            stdout=draw(st.text(min_size=0, max_size=6)),
            stderr=draw(st.text(min_size=0, max_size=6)),
        )
        return DeliverProcessOutput(("cmd",), result, None)
    return DeliverProcessOutput(
        ("cmd",),
        None,
        draw(
            st.sampled_from(["not-found", "permission-denied", "io-error", "timeout"])
        ),
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
                st.just(Undo()),
                _deliveries(),
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
    TextUndone/TextRedone set mark-setness from their mark_after field
    (undo can resurrect a mark with no MarkSet event — the fields are why
    the events carry both marks). We check the simpler structural rule:
    mark is set iff the latest of these six event types says so.
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
        elif isinstance(event, (TextUndone, TextRedone)):
            mark_set = event.mark_after is not None
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
    """Text changes set modified; save clears it; undo/redo restore it."""
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
        elif isinstance(command, Undo):
            # Undo/redo restore the flag from the group, they don't set it.
            expect_modified = session.buffer.current.modified
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


def _text_change_history(draw: st.DrawFn, max_size: int = 30) -> list[object]:
    """Only text-changing commands — the clean round-trip history."""
    size = draw(st.integers(min_value=0, max_value=max_size))
    return [
        draw(
            st.one_of(
                st.builds(InsertText, st.text(min_size=1, max_size=5)),
                st.just(KillLine()),
                st.just(Yank()),
                st.just(SetMark()),
                st.just(KillRegion()),
            )
        )
        for _ in range(size)
    ]


@st.composite
def text_change_history(draw: st.DrawFn) -> list[object]:
    return _text_change_history(draw)


@given(text_change_history())
def test_undo_all_restores_initial_state(history: list[object]) -> None:
    """Undoing every text-changing group restores the initial buffer."""
    session = _session()
    groups = 0
    for command in history:
        before = session.buffer.current
        session.dispatch(command)  # type: ignore[arg-type]
        after = session.buffer.current
        if after.text != before.text:
            groups += 1
    for _ in range(groups):
        session.dispatch(Undo())
    current = session.buffer.current
    assert current.text == ""
    assert current.point == 0
    assert not current.modified


@given(command_history())
def test_process_deliveries_never_perturb_editor_folds(history: list[object]) -> None:
    """Process deliveries are external inputs: they never change buffer, undo,
    or kill-ring folds, and the process log derives from the transcript."""
    session = _process_session()
    expected_log_len = 0
    for command in history:
        before = session.buffer.current
        undo_before = (len(session._undo_history), len(session._undo_redo))
        ring_before = session.kill_ring
        outcome = session.dispatch(command)  # type: ignore[arg-type]
        if isinstance(command, DeliverProcessOutput):
            # Buffer, undo stacks, and kill ring are all untouched.
            assert session.buffer.current == before
            assert (len(session._undo_history), len(session._undo_redo)) == undo_before
            assert session.kill_ring == ring_before
            # Exactly one delivery event per command; only successes log.
            recorded = [
                e for e in outcome.events if isinstance(e, ProcessOutputRecorded)
            ]
            assert len(recorded) == 1
            if command.result is not None:
                expected_log_len += 1
        assert len(session.process_log) == expected_log_len
    # The transcript carries exactly one ProcessOutputRecorded per delivery.
    deliveries = sum(1 for c in history if isinstance(c, DeliverProcessOutput))
    recorded_total = sum(
        1 for e in session.transcript if isinstance(e, ProcessOutputRecorded)
    )
    assert recorded_total == deliveries


@given(command_history())
def test_undo_stack_bounded(history: list[object]) -> None:
    """At most 100 groups; exhaustion degrades to silent no-ops."""
    from drei.session import UNDO_CAPACITY

    session = _session()
    for command in history:
        session.dispatch(command)  # type: ignore[arg-type]
    undone = 0
    while session.dispatch(Undo()).events:
        undone += 1
    assert undone <= UNDO_CAPACITY
