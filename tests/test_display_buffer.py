"""`DisplayBuffer`: show a buffer without taking the user out of their work.

Design 0005 D6 needs this and design 0004 D1 constrains it. A transcript that
exists but is nowhere on screen is a feature the user cannot use — `C-c a`
would send a prompt and nothing visible would happen — but an agent buffer
that *steals focus* is exactly what 0004 D1 forbids. So: another window, never
the focused one.

Kept as its own command rather than folded into `CreateAgentBuffer`, because
the buffer's identity is bound when the ACP session is established and where
it is shown is a presentation decision the caller makes.
"""

from __future__ import annotations

import pytest

from drei.commands import (
    BufferDisplayed,
    CreateGeneratedBuffer,
    DisplayBuffer,
    SplitWindow,
    WindowSplit,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession

FOCUSED = BufferId("work")
SHOWN = BufferId("*agent*")


def _session(*, frame: tuple[int, int] | None = (40, 24)) -> EditorSession:
    session = EditorSession(
        Buffer(FOCUSED, BufferValue(text="mine", point=2)), frame_size=frame
    )
    session.dispatch(CreateGeneratedBuffer("*agent*"))
    return session


def test_a_single_window_frame_splits_to_make_room() -> None:
    session = _session()

    outcome = session.dispatch(DisplayBuffer(SHOWN))

    assert WindowSplit(2) in outcome.events
    assert BufferDisplayed("*agent*", 1) in outcome.events
    assert session.windows[1].buffer_id == SHOWN


def test_focus_never_moves() -> None:
    """Design 0004 D1's constraint: the buffer appearing must not yank the
    user out of what they were doing."""
    session = _session()

    session.dispatch(DisplayBuffer(SHOWN))

    assert session.focused == 0
    assert session.windows[0].buffer_id == FOCUSED
    assert session.buffer.buffer_id == FOCUSED
    # Typing still goes where the user was typing.
    from drei.commands import InsertText

    session.dispatch(InsertText("!"))
    assert session.buffer.current.text == "mi!ne"


def test_an_existing_second_window_is_reused_rather_than_a_third_created() -> None:
    """Emacs's `display-buffer` reuses a window rather than subdividing the
    frame further, and so does this: a frame that grew a window every time an
    agent session started would shrink to nothing."""
    session = _session()
    session.dispatch(SplitWindow())

    outcome = session.dispatch(DisplayBuffer(SHOWN))

    assert not any(isinstance(event, WindowSplit) for event in outcome.events)
    assert len(session.windows) == 2
    assert session.windows[1].buffer_id == SHOWN


def test_the_displayed_window_starts_at_the_top_of_the_buffer() -> None:
    """The window is being repurposed, so carrying the previous buffer's point
    into it would be meaningless."""
    session = _session()
    session.dispatch(SplitWindow())

    session.dispatch(DisplayBuffer(SHOWN))

    assert session.windows[1].point == 0
    assert session.windows[1].mark is None


def test_a_frame_too_small_to_split_shows_nothing_and_breaks_nothing() -> None:
    """The buffer still exists and `C-x b` still reaches it. Destroying the
    user's only window to make room for agent output would be a worse answer
    than not showing it.

    Genuinely silent (plan 0021 D3, TD-13 paid): the user issued no command,
    so the transcript records no Message — the row-98 refusal sentence
    belongs to the user's own `C-x 2`
    (`test_windows.py::test_split_too_small_is_a_noop`, unchanged)."""
    session = _session(frame=(40, 4))  # below (1 + 1) * 3 + 1

    outcome = session.dispatch(DisplayBuffer(SHOWN))

    assert outcome.events == ()
    assert len(session.windows) == 1
    assert session.windows[0].buffer_id == FOCUSED


def test_displaying_a_buffer_that_does_not_exist_is_a_programming_error() -> None:
    """Same discipline as a delivery target (design 0004 D3): the caller can
    only pass ids the session itself minted, so a miss is a bug in Drei rather
    than peer input, and it must not half-apply."""
    session = _session()

    with pytest.raises(ValueError, match="no such buffer"):
        session.dispatch(DisplayBuffer(BufferId("nope")))

    assert len(session.windows) == 1


def test_the_focused_window_is_never_the_target_even_when_focus_moved() -> None:
    """`(focused + 1) % len(windows)` rather than "window 1": after `C-x o`
    the user is in the second window, and displaying into it would be exactly
    the focus theft this avoids."""
    from drei.commands import OtherWindow

    session = _session()
    session.dispatch(SplitWindow())
    session.dispatch(OtherWindow())
    assert session.focused == 1

    session.dispatch(DisplayBuffer(SHOWN))

    assert session.focused == 1
    assert session.windows[1].buffer_id == FOCUSED  # still the user's
    assert session.windows[0].buffer_id == SHOWN
