"""A.2 step 5: SessionObservation + multi-pane render (plan 0012 D5).

render(BufferObservation) is untouched (the existing render tests are the
byte-identical oracle); render_session draws one pane per window with a
modeline each, one shared echo row, and the cursor in the focused pane.
"""

from __future__ import annotations

from conftest import FakeFilePort

from drei.commands import (
    ForwardChar,
    ResizeFrame,
    SessionObservation,
    SplitWindow,
    WindowObservation,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.render import Frame, render_session
from drei.session import EditorSession


def _session(text: str = "hello world") -> EditorSession:
    return EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text=text, point=0)),
        file_port=FakeFilePort(),
        frame_size=(10, 8),
    )


def test_session_observation_single_window_matches_buffer_observation() -> None:
    session = _session()
    obs = session.session_observation()
    assert isinstance(obs, SessionObservation)
    assert obs.buffers == ("scratch",)
    assert obs.focused == 0
    assert len(obs.windows) == 1
    window = obs.windows[0]
    assert isinstance(window, WindowObservation)
    assert window.buffer.buffer_id == "scratch"
    assert window.buffer.text == "hello world"
    assert window.point == 0


def test_session_observation_two_windows_carry_per_window_points() -> None:
    session = _session()
    session.dispatch(SplitWindow())
    session.dispatch(ForwardChar())
    session.dispatch(ForwardChar())  # focused window: point 2
    obs = session.session_observation()
    assert len(obs.windows) == 2
    assert obs.windows[0].point == 2
    assert obs.windows[1].point == 0
    assert obs.focused == 0


def test_single_window_render_is_identical_to_buffer_render_shape() -> None:
    """One window: body + one modeline + echo — same shape as the legacy
    single-buffer frame (rows identical to render() of the observation)."""
    session = _session()
    frame = render_session(session.session_observation(), width=10, height=4)
    assert frame.rows == (
        "hello worl",
        "          ",
        "Drei: scra",
        "          ",
    )
    assert frame.cursor == (0, 0)


def test_two_windows_draw_two_panes_with_a_modeline_each() -> None:
    session = _session()
    session.dispatch(SplitWindow())
    frame = render_session(session.session_observation(), width=10, height=8)
    assert len(frame.rows) == 8
    assert frame.rows[-1] == "          "  # shared echo row, empty
    # Exactly two modelines (one per window).
    modeline_rows = [i for i, row in enumerate(frame.rows) if row.startswith("Drei:")]
    assert len(modeline_rows) == 2
    # Both panes show the same buffer's text at the top.
    assert frame.rows[0].startswith("hello")
    assert frame.rows[modeline_rows[0] + 1].startswith("hello")


def test_cursor_lands_in_the_focused_pane() -> None:
    session = _session()
    session.dispatch(SplitWindow())
    session.dispatch(ForwardChar())  # focused (top) point 1
    frame = render_session(session.session_observation(), width=10, height=8)
    assert frame.cursor == (0, 1)  # top pane, first body row, col 1


def test_render_session_minibuffer_uses_the_shared_echo_row() -> None:
    from drei.commands import FindFile

    session = _session()
    session.dispatch(SplitWindow())
    session.dispatch(FindFile())
    obs = session.session_observation()
    frame = render_session(obs, width=10, height=8)
    assert frame.rows[-1].startswith("Find file:")
    # Cursor is at the end of the prompt on the echo row (clamped to the
    # frame width, as in the legacy render).
    assert frame.cursor[0] == len(frame.rows) - 1
    assert frame.cursor[1] == min(len("Find file: "), 10 - 1)


def test_render_session_minibuffer_without_prompt_uses_empty_prompt() -> None:
    """minibuffer_prompt=None (a minibuffer opened without a prompt string)
    falls back to an empty prompt in the session renderer."""
    session = _session()
    obs = session.session_observation()
    # Synthesize the prompt-less observation shape (the session always sets a
    # prompt today; the renderer's None fallback is a contract).
    from dataclasses import replace as dc_replace

    obs = dc_replace(obs, minibuffer="x", minibuffer_prompt=None)
    frame = render_session(obs, width=10, height=4)
    assert frame.rows[-1] == "x         "
    assert frame.cursor == (3, 1)


def test_window_heights_clamp_the_bottom_pane_when_windows_exceed_rows() -> None:
    """More windows than body rows: each pane keeps its modeline row and the
    bottom pane clamps to 1 (M2 — the clamp IS reachable, e.g. 4 windows in
    2 body rows)."""
    from drei.render import _window_heights

    assert _window_heights(2, 4) == (1, 1, 1, 1)
    assert _window_heights(3, 6) == (1, 1, 1, 1, 1, 1)


def test_render_session_row_count_clamps_to_the_frame_height() -> None:
    """A hand-built observation with more windows than rows (possible when
    frame_size=None removed the split gate) must not overflow the Frame
    contract: at most `height` rows, cursor inside (M1)."""
    session = _session()
    obs = session.session_observation()
    from dataclasses import replace as dc_replace

    window = obs.windows[0]
    obs = dc_replace(obs, windows=(window, window, window, window, window))
    frame = render_session(obs, width=10, height=4)
    assert len(frame.rows) <= 4
    assert frame.cursor[0] < len(frame.rows)


def test_render_session_height_zero_is_an_empty_frame() -> None:
    session = _session()
    frame = render_session(session.session_observation(), width=10, height=0)
    assert frame.rows == ()
    assert frame.cursor == (0, 0)
    assert frame.height == 0


def _modelines(frame: Frame) -> int:
    return sum(1 for row in frame.rows if row.startswith("Drei:"))


def test_shrink_below_the_split_minimum_degrades_in_stages() -> None:
    """Plan 0015 D7, render half: the shrink is absorbed by the renderer in
    stages, and the session keeps both windows through all of them.

    Below the *split* minimum (4 < (2+1)*3+1 = 10) both panes still render;
    the top one loses its body rows and keeps its modeline. Squeezed further,
    the frame cap starts cutting from the bottom — first the shared echo row
    (see TD-10), then the lower panes. No stage touches session state, which
    is what makes the resize reversible.
    """
    session = _session(text="alpha\nbeta")
    session.dispatch(SplitWindow())

    session.dispatch(ResizeFrame(10, 4))
    degraded = render_session(session.session_observation(), width=10, height=4)
    assert len(session.windows) == 2  # nothing deleted to make it fit
    assert _modelines(degraded) == 2  # both panes present, the top bodyless

    session.dispatch(ResizeFrame(10, 2))
    squeezed = render_session(session.session_observation(), width=10, height=2)
    assert len(session.windows) == 2  # still nothing deleted
    assert len(squeezed.rows) == 2  # the Frame contract holds
    # TD-10: the echo row is what the cap takes first, so both modelines
    # survive and the echo area does not. Pinned as the current truth, not
    # endorsed — see docs/technical-debt.md.
    assert _modelines(squeezed) == 2

    session.dispatch(ResizeFrame(10, 1))
    dropped = render_session(session.session_observation(), width=10, height=1)
    assert len(session.windows) == 2  # still nothing deleted
    assert _modelines(dropped) == 1  # the lower pane is now off-frame
    assert dropped.cursor[0] < len(dropped.rows)


def test_growing_the_frame_back_restores_both_panes() -> None:
    """The other half of D7: what a shrink hid, a grow shows again. Emacs
    could not do this — it deletes the window that no longer fits."""
    session = _session(text="alpha\nbeta")
    session.dispatch(SplitWindow())
    session.dispatch(ResizeFrame(10, 2))  # a pane goes off-frame
    session.dispatch(ResizeFrame(10, 8))
    grown = render_session(session.session_observation(), width=10, height=8)
    assert _modelines(grown) == 2
