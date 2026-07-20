"""Kill ring semantics: KillLine, Yank, append chain, modified flag."""

from __future__ import annotations

from conftest import FakeFilePort

from drei.commands import (
    BackwardChar,
    ForwardChar,
    InsertText,
    KillLine,
    TextKilled,
    TextYanked,
    Yank,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session(text: str = "", point: int = 0) -> EditorSession:
    return EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text=text, point=point)),
        file_port=FakeFilePort(),
    )


# --- KillLine decision table ---------------------------------------------


def test_kill_line_text_to_eol() -> None:
    session = _session("ab\ncd", 0)
    outcome = session.dispatch(KillLine())
    assert session.buffer.current.text == "\ncd"
    assert session.buffer.current.point == 0
    assert TextKilled("ab", 0, 2, "forward") in outcome.events


def test_kill_line_at_eol_kills_newline() -> None:
    session = _session("ab\ncd", 2)
    outcome = session.dispatch(KillLine())
    assert session.buffer.current.text == "abcd"
    assert session.buffer.current.point == 2
    assert TextKilled("\n", 2, 3, "forward") in outcome.events


def test_kill_line_at_buffer_end_is_noop() -> None:
    session = _session("ab\ncd", 5)
    outcome = session.dispatch(KillLine())
    assert session.buffer.current.text == "ab\ncd"
    assert outcome.events == ()
    assert session.kill_ring == ()


def test_kill_line_empty_buffer_is_noop() -> None:
    session = _session("", 0)
    outcome = session.dispatch(KillLine())
    assert outcome.events == ()


def test_kill_line_on_lone_newline_kills_it() -> None:
    session = _session("\n", 0)
    session.dispatch(KillLine())
    assert session.buffer.current.text == ""
    assert session.kill_ring == ("\n",)


def test_kill_sets_modified() -> None:
    session = _session("ab", 0)
    before = session.buffer.current
    assert not before.modified
    outcome = session.dispatch(KillLine())
    after = session.buffer.current
    assert after.modified
    assert outcome.observation.modified


# --- Append chain ----------------------------------------------------------


def test_consecutive_kills_append_into_one_entry() -> None:
    session = _session("ab\ncd", 0)
    session.dispatch(KillLine())  # kills "ab"
    session.dispatch(KillLine())  # kills "\n"
    assert session.kill_ring == ("ab\n",)


def test_non_kill_command_breaks_chain() -> None:
    session = _session("ab\ncd", 0)
    session.dispatch(KillLine())
    session.dispatch(ForwardChar())
    session.dispatch(KillLine())
    assert len(session.kill_ring) == 2


def test_noop_kill_does_not_break_chain() -> None:
    session = _session("ab", 0)
    session.dispatch(KillLine())  # kills "ab", chain starts
    session.dispatch(KillLine())  # no-op at eob; chain intact
    session.dispatch(InsertText("x"))  # breaks chain
    session.dispatch(BackwardChar())
    session.dispatch(KillLine())  # kills "x"
    assert len(session.kill_ring) == 2


# --- Yank -------------------------------------------------------------------


def test_yank_inserts_newest_and_moves_point_past() -> None:
    session = _session("ab\ncd", 0)
    session.dispatch(KillLine())  # ring: ("ab",), text "\ncd"
    outcome = session.dispatch(Yank())
    assert session.buffer.current.text == "ab\ncd"
    assert session.buffer.current.point == 2
    assert TextYanked("ab", 0, 2) in outcome.events
    assert outcome.observation.point == 2


def test_yank_empty_ring_is_noop() -> None:
    session = _session("xy", 1)
    outcome = session.dispatch(Yank())
    assert session.buffer.current.text == "xy"
    assert outcome.events == ()


def test_yank_multiline() -> None:
    session = _session("ab\ncd", 0)
    session.dispatch(KillLine())  # "ab"
    session.dispatch(KillLine())  # "\n" appended -> "ab\n"
    outcome = session.dispatch(Yank())
    assert session.buffer.current.text == "ab\ncd"
    current = session.buffer.current
    assert current.point == 3
    assert outcome.observation.point == 3


def test_yank_sets_modified() -> None:
    session = _session("ab", 0)
    session.dispatch(KillLine())
    outcome = session.dispatch(Yank())
    assert outcome.observation.modified


def test_noop_kill_then_yank_inserts_prior_ring_head() -> None:
    # Not a round trip: no-op kill leaves ring intact, yank inserts stale head.
    session = _session("ab", 0)
    session.dispatch(KillLine())  # ring ("ab",), text ""
    session.dispatch(KillLine())  # no-op
    session.dispatch(Yank())
    assert session.buffer.current.text == "ab"
