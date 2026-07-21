"""Multi-buffer session: buffer set, per-buffer editing state (design 0003 §A.2).

Step-1 vertical: the session owns a dict of buffers plus per-buffer
undo/yank/kill-chain state. Single-buffer behavior is invariant — these
tests pin both the invariance and the new internal seams.
"""

from __future__ import annotations

from conftest import FakeFilePort

from drei.commands import InsertText, KeyboardQuit, KillLine, Undo, Yank, YankPop
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session(text: str = "") -> EditorSession:
    return EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text=text, point=0)),
        file_port=FakeFilePort(),
    )


def test_session_tracks_a_buffer_set() -> None:
    session = _session()
    assert isinstance(session.buffers, tuple)
    assert session.buffers == ("scratch",)


def test_buffer_property_resolves_the_current_buffer() -> None:
    session = _session("hello")
    assert session.buffer.buffer_id == BufferId("scratch")
    assert session.buffer.current.text == "hello"


def test_per_buffer_state_record_exists_for_the_initial_buffer() -> None:
    session = _session()
    state = session._states[BufferId("scratch")]
    assert state.undo_history == []
    assert state.undo_redo == []
    assert state.undo_descending is False
    assert state.yank_active is False
    assert state.last_was_kill is False


def test_undo_still_works_through_the_per_buffer_record() -> None:
    session = _session()
    session.dispatch(InsertText("abc"))
    outcome = session.dispatch(Undo())
    assert session.buffer.current.text == ""
    assert len(outcome.events) == 1


def test_kill_chain_and_yank_state_unchanged_in_one_buffer() -> None:
    session = _session("one two\nthree four\n")
    session.dispatch(KillLine())
    session.dispatch(KillLine())  # appends the newline to the chain
    assert session.kill_ring == ("one two\n",)
    session.dispatch(Yank())
    session.dispatch(KeyboardQuit())  # intervenes: breaks yank-pop
    assert session.dispatch(YankPop()).events == ()
    assert session.buffer.current.text == "one two\nthree four\n"


def test_buffers_tuple_is_a_derived_view_not_mutable_state() -> None:
    session = _session()
    first = session.buffers
    assert first == session.buffers
    assert isinstance(first, tuple)
