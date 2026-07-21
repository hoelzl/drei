"""A.2 step 6: property tests over multi-buffer/multi-window histories
(plan 0012, 'Property tests' section).

Oracles:
- undo isolation: undoing a buffer's edits in interleaved order replays
  that buffer's solo history;
- kill-chain breaks: kills separated by a buffer switch never append;
- window-point independence: two windows over one buffer keep their own
  points across focus round-trips;
- transcript replay: folds derived from the event stream equal the live
  session's state.
"""

from __future__ import annotations

from typing import cast

import hypothesis.strategies as st
from conftest import FakeFilePort
from hypothesis import HealthCheck, given, settings

from drei.commands import (
    BackwardChar,
    BufferCreated,
    BufferSelected,
    FindFile,
    ForwardChar,
    InsertText,
    KillLine,
    KillRegion,
    MinibufferAccept,
    MinibufferInput,
    OtherWindow,
    SetMark,
    SplitWindow,
    Undo,
    WindowFocusChanged,
    Yank,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import Command, EditorSession, Event

_MAX_HISTORY = 15

_BUFFER_EDIT_COMMANDS = st.sampled_from(
    [
        InsertText("x"),
        InsertText("yy"),
        ForwardChar(),
        BackwardChar(),
        SetMark(),
        KillLine(),
        KillRegion(),
        Yank(),
        Undo(),
    ]
)


def _buffer_index(session: EditorSession, name: str) -> int:
    return list(session.buffers).index(name)


@st.composite
def _histories(draw: st.DrawFn) -> list[tuple[str, Command]]:
    """Interleaved (buffer, edit-command) histories over two buffers, with
    buffer switches driven through the public C-x b accept path shape."""
    buffers = ["alpha", "beta"]
    history: list[tuple[str, Command]] = []
    current = "alpha"
    for _ in range(draw(st.integers(min_value=1, max_value=_MAX_HISTORY))):
        if draw(st.booleans()):
            current = buffers[1 - buffers.index(current)]
        command = cast(Command, draw(_BUFFER_EDIT_COMMANDS))
        history.append((current, command))
    return history


def _session_two_buffers() -> EditorSession:
    session = EditorSession(
        Buffer(BufferId("alpha"), BufferValue(text="", point=0)),
        file_port=FakeFilePort(),
        frame_size=(80, 24),
    )
    session._create_buffer("beta", BufferValue(text="", point=0), [])
    session._select_buffer(BufferId("alpha"), [])
    return session


def _apply_history(session: EditorSession, history: list[tuple[str, Command]]) -> None:
    """Drive the interleaved history through real buffer switches."""
    for buffer_name, command in history:
        if session.buffer.buffer_id.value != buffer_name:
            events: list[Event] = []
            session._select_buffer(BufferId(buffer_name), events)
        session.dispatch(command)


def _solo_replay(buffer_name: str, history: list[tuple[str, Command]]) -> BufferValue:
    return _solo_session(buffer_name, history).buffer.current


def _solo_session(
    buffer_name: str, history: list[tuple[str, Command]]
) -> EditorSession:
    """The isolation oracle: replay the interleaved history on a real
    session, redirecting the OTHER buffer's commands to a scratch buffer.

    Buffer isolation means the other buffer's commands cannot change THIS
    buffer's text/point/mark — but the kill ring is deliberately global
    (kill in one buffer, yank in the other is pinned Emacs parity), so the
    other buffer's commands may still change the shared ring that this
    buffer's Yank/KillLine consult. The oracle therefore replays both
    buffers' commands (identical ring evolution) but routes the other
    buffer's edits to scratch storage — exactly the isolation guarantee,
    nothing more."""
    session = EditorSession(
        Buffer(BufferId(buffer_name), BufferValue(text="", point=0)),
        file_port=FakeFilePort(),
    )
    session._create_buffer("scratch", BufferValue(text="", point=0), [])
    session._select_buffer(BufferId(buffer_name), [])
    current = buffer_name
    for target, command in history:
        destination = buffer_name if target == buffer_name else "scratch"
        if destination != current:
            session._select_buffer(BufferId(destination), [])
            current = destination
        session.dispatch(command)
    session._select_buffer(BufferId(buffer_name), [])
    return session


class TestUndoIsolationAcrossBuffers:
    """Plan 0012 evidence 2, property form: per-buffer undo means each
    buffer's final state equals replaying its own command sub-history
    solo — interleaving and switching must not perturb it."""

    @given(history=_histories())
    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
    def test_interleaved_buffers_match_solo_replays(
        self, history: list[tuple[str, Command]]
    ) -> None:
        session = _session_two_buffers()
        _apply_history(session, history)
        for name in ("alpha", "beta"):
            solo = _solo_replay(name, history)
            live = session._buffers[BufferId(name)].current
            assert live.text == solo.text
            assert live.point == solo.point
            assert live.mark == solo.mark
            assert live.modified == solo.modified

    @given(history=_histories())
    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
    def test_per_buffer_undo_depth_matches_solo(
        self, history: list[tuple[str, Command]]
    ) -> None:
        session = _session_two_buffers()
        _apply_history(session, history)
        for name in ("alpha", "beta"):
            solo_state = _solo_session(name, history)._states[BufferId(name)]
            live_state = session._states[BufferId(name)]
            assert len(live_state.undo_history) == len(solo_state.undo_history)
            assert len(live_state.undo_redo) == len(solo_state.undo_redo)


class TestKillChainBreaksAcrossSwitches:
    """Plan 0012 evidence 3, property form: any kill→switch→kill sequence
    produces a fresh ring entry; only back-to-back kills in ONE buffer
    append."""

    @given(
        first_text=st.text(alphabet="abc ", min_size=1, max_size=8),
        second_text=st.text(alphabet="xyz ", min_size=1, max_size=8),
        switches=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=60)
    def test_switch_between_kills_never_appends(
        self, first_text: str, second_text: str, switches: int
    ) -> None:
        session = EditorSession(
            Buffer(BufferId("alpha"), BufferValue(text=first_text, point=0)),
            file_port=FakeFilePort(),
            frame_size=(80, 24),
        )
        session._create_buffer("beta", BufferValue(text=second_text, point=0), [])
        session.dispatch(KillLine())  # ring head: first_text
        for _ in range(switches):
            session._select_buffer(BufferId("beta"), [])
            session._select_buffer(BufferId("alpha"), [])
        session._select_buffer(BufferId("beta"), [])
        session.dispatch(KillLine())  # fresh entry: second_text
        assert session.kill_ring[0] == second_text
        assert session.kill_ring[1] == first_text
        # The head did NOT absorb the first kill (no appending across the
        # switch — the two kills are distinct ring entries).
        assert session.kill_ring[0] != second_text + first_text

    @given(repeat=st.integers(min_value=2, max_value=4))
    @settings(max_examples=20)
    def test_back_to_back_kills_in_one_buffer_append(self, repeat: int) -> None:
        session = EditorSession(
            Buffer(BufferId("alpha"), BufferValue(text="ab\ncd\nef\ngh\n", point=0)),
            file_port=FakeFilePort(),
        )
        for _ in range(repeat):
            session.dispatch(KillLine())
        # Chain semantics (observed): the point never moves between kills
        # (slice-3 registered deviation), and consecutive kill-lines keep
        # APPENDING into one ring entry — Emacs chain semantics, verified
        # against the live session: kill 1 chains "ab", kill 2 chains the
        # newline, kill 3 chains "cd", and so on; the ring is a tuple.
        parts = ["ab", "\n", "cd", "\n", "ef", "\n", "gh", "\n"][:repeat]
        assert session.kill_ring[0] == "".join(parts)
        assert len(session.kill_ring) == 1
        # The buffer loses exactly the killed prefix.
        assert session.buffer.current.text == "ab\ncd\nef\ngh\n"[len("".join(parts)) :]


class TestWindowPointIndependence:
    """Design 0002's stress case as a property: two windows over one buffer
    hold independent points across arbitrary focus round-trips."""

    @given(
        moves_a=st.lists(st.integers(min_value=1, max_value=2), max_size=5),
        moves_b=st.lists(st.integers(min_value=1, max_value=2), max_size=5),
    )
    @settings(max_examples=60)
    def test_each_window_keeps_its_own_point(
        self, moves_a: list[int], moves_b: list[int]
    ) -> None:
        session = EditorSession(
            Buffer(BufferId("alpha"), BufferValue(text="0123456789", point=0)),
            file_port=FakeFilePort(),
            frame_size=(80, 24),
        )
        session.dispatch(SplitWindow())
        for n in moves_a:
            for _ in range(n):
                session.dispatch(ForwardChar())
        expected_a = session.buffer.current.point
        session.dispatch(OtherWindow())
        for n in moves_b:
            for _ in range(n):
                session.dispatch(ForwardChar())
        expected_b = session.buffer.current.point
        session.dispatch(OtherWindow())  # back to window 0
        assert session.buffer.current.point == expected_a
        assert session.windows[0].point == expected_a
        assert session.windows[1].point == expected_b


class TestTranscriptReplay:
    """The transcript is the sole durable fact (0002): folds over the event
    stream must equal the live session's derived state."""

    @given(history=_histories())
    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
    def test_buffer_set_fold_matches_live_buffers(
        self, history: list[tuple[str, Command]]
    ) -> None:
        session = _session_two_buffers()
        _apply_history(session, history)
        # The seeded buffers (alpha + beta) predate any events; creations
        # during the history are transcript facts.
        created = {"alpha", "beta"}
        for event in session.transcript:
            if isinstance(event, BufferCreated):
                created.add(event.buffer_id)
        assert set(session.buffers) == created

    @given(history=_histories())
    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
    def test_current_buffer_fold_matches_live(
        self, history: list[tuple[str, Command]]
    ) -> None:
        session = _session_two_buffers()
        # Track the switches the driver performs (its _select_buffer calls
        # pass a throwaway event list, so the transcript only sees events
        # from dispatched commands — the fold must model the driver's
        # switches itself).
        current = "alpha"
        for buffer_name, command in history:
            if session.buffer.buffer_id.value != buffer_name:
                events: list[Event] = []
                session._select_buffer(BufferId(buffer_name), events)
                current = buffer_name
            outcome = session.dispatch(command)
            for event in outcome.events:
                if isinstance(event, (BufferSelected, WindowFocusChanged)):
                    current = event.buffer_id
        assert session.buffer.buffer_id.value == current

    @given(
        opens=st.lists(
            st.sampled_from(["/tmp/one.txt", "/tmp/two.txt", "/tmp/one.txt"]),
            min_size=1,
            max_size=4,
        )
    )
    @settings(max_examples=40)
    def test_file_open_fold_matches_live(self, opens: list[str]) -> None:
        files = {"/tmp/one.txt": "first", "/tmp/two.txt": "second"}
        session = EditorSession(
            Buffer(BufferId("scratch"), BufferValue(text="", point=0)),
            file_port=FakeFilePort(files),
            frame_size=(80, 24),
        )
        for path in opens:
            session.dispatch(FindFile())
            for char in path:
                session.dispatch(MinibufferInput(char))
            session.dispatch(MinibufferAccept())
        # Re-opening an already-open path creates nothing: the BufferCreated
        # fold matches the live buffer set exactly.
        created = [
            e.buffer_id for e in session.transcript if isinstance(e, BufferCreated)
        ]
        assert len(created) == len(set(created))  # no duplicate creations
        assert set(session.buffers) == {"scratch", *created}

    @given(
        splits=st.integers(min_value=0, max_value=2),
        cycles=st.integers(min_value=0, max_value=4),
    )
    @settings(max_examples=40)
    def test_window_count_fold_matches_live(self, splits: int, cycles: int) -> None:
        session = EditorSession(
            Buffer(BufferId("alpha"), BufferValue(text="abc", point=0)),
            file_port=FakeFilePort(),
            frame_size=(80, 24),
        )
        for _ in range(splits):
            session.dispatch(SplitWindow())
        for _ in range(cycles):
            session.dispatch(OtherWindow())
        from drei.commands import WindowsCollapsed, WindowSplit

        count = 1
        for event in session.transcript:
            if isinstance(event, WindowSplit):
                count = event.count
            elif isinstance(event, WindowsCollapsed):
                count = 1
        assert len(session.windows) == count
        focus = 0
        for event in session.transcript:
            if isinstance(event, WindowFocusChanged):
                focus = event.index
            elif isinstance(event, WindowsCollapsed):
                focus = 0
        assert session.focused == focus
