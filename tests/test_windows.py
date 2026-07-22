"""A.2 step 4: windows — C-x 2 / C-x o / C-x 1 (plan 0012 D3–D5).

Windows are layout views over buffers with per-window points. Two windows
over one buffer hold independent points (design 0002's stress case, in the
product); splitting needs enough frame height; layout changes are events.
"""

from __future__ import annotations

from conftest import FakeFilePort

from drei.commands import (
    BackwardChar,
    BufferSelected,
    DeleteOtherWindows,
    ForwardChar,
    KillLine,
    OtherWindow,
    SetMark,
    SplitWindow,
    Undo,
    WindowFocusChanged,
    WindowsCollapsed,
    WindowSplit,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession, Event, WindowValue


def _session(height: int | None = 24) -> EditorSession:
    frame_size = (80, height) if height is not None else None
    return EditorSession(
        Buffer(BufferId("alpha"), BufferValue(text="aaa bbb", point=0)),
        file_port=FakeFilePort(),
        frame_size=frame_size,
    )


def test_session_starts_with_one_window_over_the_initial_buffer() -> None:
    session = _session()
    assert session.windows == (WindowValue(BufferId("alpha"), 0, None),)
    assert session.focused == 0


def test_split_creates_two_windows_over_the_same_buffer() -> None:
    session = _session()
    outcome = session.dispatch(SplitWindow())
    assert WindowSplit(2) in outcome.events
    assert len(session.windows) == 2
    assert all(w.buffer_id == BufferId("alpha") for w in session.windows)
    assert session.focused == 0  # focus stays on the original window


def test_window_points_are_independent() -> None:
    """Design 0002's stress case: shared buffer, independent window points."""
    session = _session()
    session.dispatch(SplitWindow())
    session.dispatch(ForwardChar())
    session.dispatch(ForwardChar())  # focused (top) window: point 2
    session.dispatch(OtherWindow())  # bottom: still at the buffer's old point
    assert session.windows[1].point == 0
    session.dispatch(ForwardChar())  # bottom: point 1
    session.dispatch(OtherWindow())  # back to top: point 2 preserved
    assert session.buffer.current.point == 2
    assert session.windows[0].point == 2
    assert session.windows[1].point == 1


def test_other_window_cycles_focus() -> None:
    session = _session()
    session.dispatch(SplitWindow())
    outcome = session.dispatch(OtherWindow())
    assert WindowFocusChanged(1, "alpha") in outcome.events
    assert session.focused == 1
    outcome = session.dispatch(OtherWindow())
    assert WindowFocusChanged(0, "alpha") in outcome.events
    assert session.focused == 0


def test_other_window_with_one_window_is_a_quiet_noop() -> None:
    session = _session()
    assert session.dispatch(OtherWindow()).events == ()
    assert session.focused == 0


def test_delete_other_windows_collapses_to_focused() -> None:
    session = _session()
    session.dispatch(SplitWindow())
    session.dispatch(OtherWindow())  # focus bottom
    outcome = session.dispatch(DeleteOtherWindows())
    assert WindowsCollapsed() in outcome.events
    assert len(session.windows) == 1
    assert session.focused == 0
    # The surviving window is the previously focused (bottom) one.
    assert session.windows[0].buffer_id == BufferId("alpha")


def test_delete_other_windows_with_one_window_is_a_quiet_noop() -> None:
    session = _session()
    assert session.dispatch(DeleteOtherWindows()).events == ()


def test_split_too_small_is_a_noop() -> None:
    """Deviation: Emacs errors 'Window too small for splitting'; Drei has no
    error-echo channel yet, so the split is a silent no-op (plan 0012
    evidence 6)."""
    session = _session(height=5)  # two windows need >= 2*3+1 = 7 rows
    assert session.dispatch(SplitWindow()).events == ()
    assert len(session.windows) == 1


def test_split_without_frame_size_is_unconstrained() -> None:
    session = _session(height=None)
    session.dispatch(SplitWindow())
    session.dispatch(SplitWindow())
    session.dispatch(SplitWindow())
    assert len(session.windows) == 4


def test_buffer_switch_retargets_the_focused_window() -> None:
    session = _session()
    session._create_buffer("beta", BufferValue(text="bbb", point=0), [])
    session.dispatch(SplitWindow())
    events: list[Event] = []
    session._select_buffer(BufferId("beta"), events)
    assert BufferSelected("beta") in events
    assert session.windows[session.focused].buffer_id == BufferId("beta")
    # The other window still shows alpha.
    others = {
        w.buffer_id for i, w in enumerate(session.windows) if i != session.focused
    }
    assert others == {BufferId("alpha")}


def test_focus_switch_to_window_over_another_buffer_selects_it() -> None:
    session = _session()
    session._create_buffer("beta", BufferValue(text="bbb", point=0), [])
    session._select_buffer(BufferId("beta"), [])  # focused window: beta
    session.dispatch(SplitWindow())  # top: beta, bottom: beta
    session._select_buffer(BufferId("alpha"), [])  # focused (top): alpha
    outcome = session.dispatch(OtherWindow())  # bottom: beta
    assert WindowFocusChanged(1, "beta") in outcome.events
    # Window-driven focus changes carry the buffer switch in
    # WindowFocusChanged; no separate BufferSelected event.
    assert not any(isinstance(e, BufferSelected) for e in outcome.events)
    assert session.buffer.buffer_id == BufferId("beta")


def test_focus_switch_to_window_over_a_buffer_missing_from_the_mru() -> None:
    """The window's buffer can be absent from the MRU (e.g. the session's
    buffer set was replaced while a window still shows an old name). Focus
    still lands on it; the MRU insert is unconditional."""
    session = _session()
    session._create_buffer("ghost", BufferValue(text="g", point=0), [])
    session._select_buffer(BufferId("ghost"), [])  # focused window: ghost
    session.dispatch(SplitWindow())
    session._select_buffer(BufferId("alpha"), [])  # focused (top): alpha
    # Replace the buffer set: 'ghost' is no longer a session buffer, so the
    # MRU entry 'ghost' is stale — but the bottom window still shows it.
    session._buffers = {
        BufferId("alpha"): session._buffers[BufferId("alpha")],
        BufferId("ghost"): Buffer(BufferId("ghost"), BufferValue(text="g", point=0)),
    }
    session._mru = ["alpha"]  # ghost dropped from the MRU
    outcome = session.dispatch(OtherWindow())  # bottom: ghost
    assert WindowFocusChanged(1, "ghost") in outcome.events
    assert session.buffer.buffer_id == BufferId("ghost")
    assert session._mru[0] == "ghost"  # inserted (was absent)


def test_window_value_is_frozen() -> None:
    import dataclasses

    window = WindowValue(BufferId("alpha"), 0, None)
    try:
        window.point = 5  # type: ignore[misc]
        raise AssertionError("WindowValue must be frozen")
    except dataclasses.FrozenInstanceError:
        pass


def test_focus_return_after_shrink_clamps_the_window_point() -> None:
    """The non-focused window's stored point is stale after the focused
    window shrinks the shared buffer; on focus return the point clamps to
    the buffer end (Emacs adjusts window-point markers — plan 0012 D3
    deviation note; found by adversarial review B1)."""
    session = _session()
    for _ in range(3):
        session.dispatch(ForwardChar())  # point 3 ("aaa| bbb")
    session.dispatch(SplitWindow())  # both windows at point 3
    session.dispatch(OtherWindow())  # bottom window focused
    session.dispatch(KillLine())  # kills " bbb" — buffer shrinks to "aaa"
    outcome = session.dispatch(OtherWindow())  # back to the top window
    assert WindowFocusChanged(0, "alpha") in outcome.events
    assert session.buffer.current.text == "aaa"
    assert session.buffer.current.point == 3  # in range: no clamp, no crash


def test_focus_return_after_shrink_below_point_clamps_to_the_end() -> None:
    session = _session()
    for _ in range(7):
        session.dispatch(ForwardChar())  # point 7 (end of "aaa bbb")
    session.dispatch(SplitWindow())  # both windows at point 7
    session.dispatch(OtherWindow())  # bottom window focused
    session.dispatch(BackwardChar())
    session.dispatch(BackwardChar())
    session.dispatch(BackwardChar())  # point 4
    session.dispatch(KillLine())  # kills "bbb" — buffer shrinks to "aaa "
    outcome = session.dispatch(OtherWindow())  # top window, stale point 7
    assert WindowFocusChanged(0, "alpha") in outcome.events
    assert session.buffer.current.point == 4  # clamped to the buffer end


def test_focus_return_after_shrink_clamps_a_stale_mark() -> None:
    session = _session()
    for _ in range(7):
        session.dispatch(ForwardChar())
    session.dispatch(SetMark())  # mark 7 in the top window
    session.dispatch(SplitWindow())
    session.dispatch(OtherWindow())
    for _ in range(3):
        session.dispatch(BackwardChar())
    session.dispatch(KillLine())  # buffer shrinks to "aaa "
    session.dispatch(OtherWindow())  # back to the top window
    assert session.buffer.current.mark == 4  # clamped


def test_focus_return_after_undo_shrink_clamps_the_window_point() -> None:
    session = _session()
    session.dispatch(SplitWindow())
    session.dispatch(OtherWindow())  # bottom window, point 0
    for _ in range(4):
        session.dispatch(ForwardChar())  # point 4
    session.dispatch(KillLine())  # kills "bbb" → "aaa " (undoable)
    session.dispatch(Undo())  # text back to "aaa bbb"
    # The bottom window's stored point is 4; shrink below it from the top.
    session.dispatch(OtherWindow())  # top window focused (stored point 0)
    for _ in range(2):
        session.dispatch(ForwardChar())  # point 2
    session.dispatch(KillLine())  # kills "a bbb" → "aa"
    outcome = session.dispatch(OtherWindow())  # bottom window, stale point 4
    assert WindowFocusChanged(1, "alpha") in outcome.events
    assert session.buffer.current.text == "aa"
    assert session.buffer.current.point == 2  # clamped to the buffer end
