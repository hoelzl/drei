"""Mark and region semantics: set-mark, kill/copy region, exchange."""

from __future__ import annotations

from drei.commands import (
    CopyRegionAsKill,
    ExchangePointAndMark,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KillLine,
    KillRegion,
    MarkExchanged,
    MarkSet,
    RegionCopied,
    RegionKilled,
    SetMark,
    Yank,
    YankPop,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session(text: str = "", point: int = 0) -> EditorSession:
    return EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text=text, point=point))
    )


# --- SetMark ---------------------------------------------------------------


def test_set_mark_records_point() -> None:
    session = _session("hello", 2)
    outcome = session.dispatch(SetMark())
    assert MarkSet(2) in outcome.events
    assert session.buffer.current.mark == 2


def test_set_mark_replaces_existing_mark() -> None:
    session = _session("hello", 1)
    session.dispatch(SetMark())
    session.dispatch(ForwardChar())
    session.dispatch(ForwardChar())
    outcome = session.dispatch(SetMark())
    assert MarkSet(3) in outcome.events
    assert session.buffer.current.mark == 3


def test_set_mark_does_not_set_modified() -> None:
    session = _session("hello", 0)
    session.dispatch(SetMark())
    assert not session.buffer.current.modified


# --- Mark adjustment on edits (probed Emacs marker semantics) --------------


def test_insert_before_mark_shifts_it() -> None:
    session = _session("abc def", 0)
    session.buffer.replace(BufferValue(text="abc def", point=0, mark=3))
    session.dispatch(InsertText("XY"))
    assert session.buffer.current.mark == 5


def test_insert_at_mark_keeps_it_before() -> None:
    session = _session("abcdef", 3)
    session.buffer.replace(BufferValue(text="abcdef", point=3, mark=3))
    session.dispatch(InsertText("XY"))
    assert session.buffer.current.mark == 3
    assert session.buffer.current.text == "abcXYdef"


def test_kill_before_mark_shifts_it_left() -> None:
    session = _session("ab\ncd", 0)
    session.buffer.replace(BufferValue(text="ab\ncd", point=0, mark=4))
    session.dispatch(KillLine())  # kills "ab" [0,2); mark >= end → -2
    assert session.buffer.current.mark == 2


def test_kill_spanning_mark_clamps_to_start() -> None:
    session = _session("ab\ncd", 0)
    session.buffer.replace(BufferValue(text="ab\ncd", point=0, mark=1))
    session.dispatch(KillLine())  # kills "ab" [0,2), mark 1 inside → 0
    assert session.buffer.current.mark == 0


def test_kill_after_mark_leaves_it() -> None:
    session = _session("ab\ncd", 3)
    session.buffer.replace(BufferValue(text="ab\ncd", point=3, mark=0))
    session.dispatch(KillLine())  # kills "cd" [3,5)
    assert session.buffer.current.mark == 0


def test_yank_before_mark_shifts_it() -> None:
    session = _session("ab", 0)
    session.dispatch(KillLine())  # ring ("ab",), text ""
    session.buffer.replace(BufferValue(text="", point=0, mark=0))
    session.dispatch(Yank())  # insert "ab" at 0 == mark: stays
    assert session.buffer.current.mark == 0


# --- KillRegion -------------------------------------------------------------


def test_kill_region_forward() -> None:
    session = _session("hello world", 0)
    session.buffer.replace(BufferValue(text="hello world", point=6, mark=1))
    outcome = session.dispatch(KillRegion())
    assert RegionKilled("ello ", 1, 6, "forward") in outcome.events
    assert session.buffer.current.text == "hworld"
    assert session.buffer.current.point == 1
    assert session.buffer.current.mark is None
    assert session.kill_ring == ("ello ",)
    assert session.buffer.current.modified


def test_kill_region_backward() -> None:
    session = _session("hello world", 6)
    session.buffer.replace(BufferValue(text="hello world", point=1, mark=6))
    outcome = session.dispatch(KillRegion())
    assert RegionKilled("ello ", 1, 6, "backward") in outcome.events
    assert session.buffer.current.text == "hworld"
    assert session.buffer.current.point == 1
    assert session.kill_ring == ("ello ",)


def test_kill_region_without_mark_is_noop() -> None:
    session = _session("hello", 2)
    outcome = session.dispatch(KillRegion())
    assert outcome.events == ()
    assert session.buffer.current.text == "hello"


def test_kill_region_empty_region_is_noop() -> None:
    session = _session("hello", 2)
    session.buffer.replace(BufferValue(text="hello", point=2, mark=2))
    outcome = session.dispatch(KillRegion())
    assert outcome.events == ()
    assert session.buffer.current.mark == 2  # mark survives a no-op


def test_kill_region_breaks_chain_and_opens_new_entry() -> None:
    session = _session("ab\ncd", 0)
    session.dispatch(KillLine())  # "ab", chain on
    session.dispatch(KillLine())  # "\n" appended -> "ab\n"
    session.buffer.replace(
        BufferValue(text=session.buffer.current.text, point=0, mark=2)
    )
    session.dispatch(KillRegion())  # kills "cd" -> NEW entry
    assert session.kill_ring == ("cd", "ab\n")


def test_kill_line_after_region_kill_opens_new_entry() -> None:
    session = _session("ab\ncd\nef", 0)
    session.buffer.replace(BufferValue(text="ab\ncd\nef", point=1, mark=0))
    session.dispatch(KillRegion())  # "a"
    session.dispatch(KillLine())  # at point 0: "b" — must NOT append
    assert session.kill_ring[0] == "b"
    assert len(session.kill_ring) == 2


# --- CopyRegionAsKill --------------------------------------------------------


def test_copy_region_pushes_ring_without_changing_text() -> None:
    session = _session("hello world", 0)
    session.buffer.replace(BufferValue(text="hello world", point=5, mark=0))
    outcome = session.dispatch(CopyRegionAsKill())
    assert RegionCopied("hello") in outcome.events
    assert session.buffer.current.text == "hello world"
    assert session.buffer.current.point == 5
    assert session.buffer.current.mark is None
    assert session.kill_ring == ("hello",)
    assert not session.buffer.current.modified


def test_copy_region_without_mark_is_noop() -> None:
    session = _session("hello", 2)
    outcome = session.dispatch(CopyRegionAsKill())
    assert outcome.events == ()
    assert session.kill_ring == ()


def test_copy_region_empty_is_noop() -> None:
    session = _session("hello", 2)
    session.buffer.replace(BufferValue(text="hello", point=2, mark=2))
    outcome = session.dispatch(CopyRegionAsKill())
    assert outcome.events == ()
    assert session.kill_ring == ()


# --- ExchangePointAndMark ----------------------------------------------------


def test_exchange_point_and_mark_swaps() -> None:
    session = _session("hello", 0)
    session.buffer.replace(BufferValue(text="hello", point=0, mark=4))
    outcome = session.dispatch(ExchangePointAndMark())
    assert MarkExchanged(0, 4) in outcome.events
    assert session.buffer.current.point == 4
    assert session.buffer.current.mark == 0


def test_exchange_without_mark_is_noop() -> None:
    session = _session("hello", 2)
    outcome = session.dispatch(ExchangePointAndMark())
    assert outcome.events == ()
    assert session.buffer.current.point == 2


# --- C-g clears the mark -----------------------------------------------------


def test_keyboard_quit_clears_mark() -> None:
    session = _session("hello", 2)
    session.buffer.replace(BufferValue(text="hello", point=2, mark=0))
    session.dispatch(KeyboardQuit())
    assert session.buffer.current.mark is None


# --- Yank-pop with mark present ----------------------------------------------


def test_yank_pop_adjusts_mark() -> None:
    """Pop replacing a longer entry with a shorter one before the mark shifts it."""
    session = _session("one\nthree!", 0)
    session.dispatch(KillLine())  # "one"
    session.dispatch(ForwardChar())  # break chain
    session.dispatch(KillLine())  # "three!"
    # text "\n", point 1; yank "three!" at 1, set mark at end, pop to "one"
    session.dispatch(Yank())  # "\nthree!", point 7
    session.buffer.replace(BufferValue(text="\nthree!", point=7, mark=7))
    session.dispatch(YankPop())  # replaces [1,7) with "one"
    assert session.buffer.current.text == "\none"
    # mark 7 = end of the deleted span → shifts to the deletion start 1;
    # the "one" insertion is AT the mark → stays before it (probed rule).
    assert session.buffer.current.mark == 1
