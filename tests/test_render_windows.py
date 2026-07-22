"""A.2 step 5: SessionObservation + multi-pane render (plan 0012 D5).

render(BufferObservation) is untouched (the existing render tests are the
byte-identical oracle); render_session draws one pane per window with a
modeline each, one shared echo row, and the cursor in the focused pane.
"""

from __future__ import annotations

from conftest import FakeFilePort

from drei.commands import (
    ForwardChar,
    SessionObservation,
    SplitWindow,
    WindowObservation,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.render import render_session
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


def test_render_session_height_zero_is_an_empty_frame() -> None:
    session = _session()
    frame = render_session(session.session_observation(), width=10, height=0)
    assert frame.rows == ()
    assert frame.cursor == (0, 0)
    assert frame.height == 0
