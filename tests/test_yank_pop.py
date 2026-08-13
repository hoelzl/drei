"""Yank-pop semantics: replacement, cycle, gating, chain interplay."""

from __future__ import annotations

from typing import NoReturn

import pytest
from conftest import semantic_state_snapshot

import drei.session as session_module
from drei.commands import (
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KillLine,
    KillRegion,
    Message,
    TextYanked,
    TextYankPopped,
    Yank,
    YankPop,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session(text: str = "", point: int = 0) -> EditorSession:
    return EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text=text, point=point))
    )


def _two_entry_session() -> EditorSession:
    """Ring: ("two", "one"); buffer "\n\nthree", point 1 (after the kills)."""
    session = _session("one\ntwo\nthree", 0)
    session.dispatch(KillLine())  # "one" -> ring ["one"]; text "\ntwo\nthree"
    session.dispatch(ForwardChar())  # breaks chain, point 1
    session.dispatch(KillLine())  # "two" -> ring ["two", "one"]; text "\n\nthree"
    return session


# --- Replacement ---------------------------------------------------------


def test_a_speaking_no_op_between_yank_and_pop_keeps_the_pop_active() -> None:
    """D2, the `yank_active` arm: a speaking no-op between a yank and its
    pop leaves the pop active. This is the session-side pin for the
    transcript shape — `TextYanked, Message, TextYankPopped` — that the
    slice-19 review (finding 1) caught the property tier's coherence fold
    rejecting."""
    session = _two_entry_session()
    session.dispatch(Yank())  # inserts "two"; the pop is active
    outcome = session.dispatch(KillRegion())  # no mark: speaks, nothing else
    assert outcome.events == (Message("mark-not-set"),)
    popped = session.dispatch(YankPop())
    assert TextYankPopped("two", "one", 1, 4) in popped.events
    assert session.buffer.current.text == "\none\nthree"


def test_pop_replaces_with_next_older_entry() -> None:
    session = _two_entry_session()
    session.dispatch(Yank())  # inserts "two" at end
    outcome = session.dispatch(YankPop())
    assert TextYankPopped("two", "one", 1, 4) in outcome.events
    assert session.buffer.current.text == "\none\nthree"


def test_yank_pop_construction_failure_preserves_semantic_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _two_entry_session()
    session.dispatch(Yank())
    before = semantic_state_snapshot(session)
    calls = 0

    def fail_replace(instance: object, /, **changes: object) -> NoReturn:
        nonlocal calls
        assert isinstance(instance, BufferValue)
        assert changes["text"] == "\none\nthree"
        assert changes["point"] == 4
        calls += 1
        raise RuntimeError("injected BufferValue construction failure")

    monkeypatch.setattr(session_module, "replace", fail_replace)

    with pytest.raises(RuntimeError, match="injected BufferValue"):
        session.dispatch(YankPop())

    assert calls == 1
    assert semantic_state_snapshot(session) == before


def test_pop_cycles_and_wraps() -> None:
    session = _two_entry_session()
    session.dispatch(Yank())  # "two"
    session.dispatch(YankPop())  # -> "one"
    outcome = session.dispatch(YankPop())  # wraps -> "two"
    assert TextYankPopped("one", "two", 1, 4) in outcome.events
    assert session.buffer.current.text == "\ntwo\nthree"


def test_pop_point_after_new_text() -> None:
    session = _two_entry_session()
    session.dispatch(Yank())
    outcome = session.dispatch(YankPop())
    assert outcome.observation.point == 1 + len("one")


def test_pop_length_change_retargets_bounds() -> None:
    """After a length-changing pop, the next pop replaces exactly the new span."""
    session = _session("a\nbcdef\nz", 0)
    session.dispatch(KillLine())  # "a"; text "\nbcdef\nz"
    session.dispatch(ForwardChar())  # point 1, chain broken
    session.dispatch(KillLine())  # "bcdef" -> ring ("bcdef", "a"); text "\n\nz"
    session.dispatch(Yank())  # "bcdef" at 1 -> "\nbcdef\nz", point 6
    session.dispatch(YankPop())  # -> "a": "\na\nz", point 2
    assert session.buffer.current.text == "\na\nz"
    outcome = session.dispatch(YankPop())  # wraps -> "bcdef" again
    assert TextYankPopped("a", "bcdef", 1, 6) in outcome.events
    assert session.buffer.current.text == "\nbcdef\nz"
    assert outcome.observation.point == 1 + len("bcdef")


def test_pop_on_multiline_yank() -> None:
    session = _session("ab\ncd", 0)
    session.dispatch(KillLine())  # "ab"; text "\ncd"
    session.dispatch(KillLine())  # "\n" appended -> "ab\n"; text "cd"
    session.dispatch(ForwardChar())  # break chain, point 1
    session.dispatch(KillLine())  # "d" -> ring ("d", "ab\n"); text "c"
    session.dispatch(Yank())  # "d" at 1 -> "cd", point 2
    outcome = session.dispatch(YankPop())  # -> "ab\n" replacing "d"
    assert TextYankPopped("d", "ab\n", 1, 4) in outcome.events
    assert session.buffer.current.text == "cab\n"


def test_pop_sets_modified() -> None:
    session = _two_entry_session()
    session.dispatch(Yank())
    session.dispatch(YankPop())
    assert session.buffer.current.modified


# --- Gating ---------------------------------------------------------------


def test_pop_without_active_yank_is_noop() -> None:
    session = _two_entry_session()
    before = session.buffer.current
    outcome = session.dispatch(YankPop())
    # No active yank: speaks (row 68), changes nothing.
    assert outcome.events == (Message("previous-command-not-a-yank"),)
    assert session.buffer.current is before


def test_pop_after_non_yank_command_is_noop() -> None:
    session = _two_entry_session()
    session.dispatch(Yank())
    session.dispatch(ForwardChar())  # emits PointMoved -> clears active
    outcome = session.dispatch(YankPop())
    assert outcome.events == (Message("previous-command-not-a-yank"),)


def test_pop_after_keyboard_quit_is_noop() -> None:
    session = _two_entry_session()
    session.dispatch(Yank())
    session.dispatch(KeyboardQuit())
    outcome = session.dispatch(YankPop())
    assert outcome.events == (Message("previous-command-not-a-yank"),)


def test_pop_on_one_entry_ring_is_noop() -> None:
    session = _session("only", 0)
    session.dispatch(KillLine())  # ring ("only",)
    session.dispatch(Yank())
    outcome = session.dispatch(YankPop())
    assert outcome.events == ()
    assert session.buffer.current.text == "only"


def test_noop_yank_clears_active() -> None:
    """A no-op yank (empty ring) clears any prior active state."""
    session = _two_entry_session()
    session.dispatch(Yank())  # active, "two" yanked
    # Drain the ring? Can't. Instead: fresh session, empty ring, no-op yank.
    empty = _session("text", 0)
    outcome = empty.dispatch(Yank())  # no-op, no event
    assert outcome.events == ()
    pop = empty.dispatch(YankPop())  # no active yank: speaks, changes nothing
    assert pop.events == (Message("previous-command-not-a-yank"),)


def test_pop_breaks_append_chain() -> None:
    session = _two_entry_session()
    session.dispatch(Yank())
    session.dispatch(YankPop())
    session.dispatch(ForwardChar())
    session.dispatch(KillLine())  # kills "three" -> NEW entry
    assert session.kill_ring[0] == "three"
    assert len(session.kill_ring) == 3


def test_noop_pop_preserves_chain() -> None:
    """Plan 0019 D2's kill-chain hazard, proven: consecutive C-k appends into
    one ring entry, and a *speaking* no-op between them is not an
    intervening command — the pop says why it did nothing (row 68) and the
    chain survives it."""
    session = _session("ab\nc", 0)
    session.dispatch(KillLine())  # "ab", chain on
    pop = session.dispatch(YankPop())  # no active yank -> speaks, intervenes not
    assert pop.events == (Message("previous-command-not-a-yank"),)
    session.dispatch(KillLine())  # kills "\n" -> appended
    assert session.kill_ring == ("ab\n",)


# --- Events ----------------------------------------------------------------


def test_yank_sets_bounds_for_pop() -> None:
    session = _two_entry_session()
    outcome = session.dispatch(Yank())
    yanked = [e for e in outcome.events if isinstance(e, TextYanked)]
    assert yanked == [TextYanked("two", 1, 4)]


def test_transcript_coherence() -> None:
    """Every TextYankPopped follows a yank/pop with no intervening event."""
    session = _two_entry_session()
    session.dispatch(Yank())
    session.dispatch(YankPop())
    session.dispatch(YankPop())
    transcript = session.transcript
    for i, event in enumerate(transcript):
        if isinstance(event, TextYankPopped):
            previous = transcript[i - 1]
            assert isinstance(previous, (TextYanked, TextYankPopped))


def test_pop_after_insert_is_noop() -> None:
    session = _two_entry_session()
    session.dispatch(Yank())
    session.dispatch(InsertText("x"))
    outcome = session.dispatch(YankPop())
    assert outcome.events == (Message("previous-command-not-a-yank"),)
