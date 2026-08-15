"""SessionObservation rendering (plans 0012 D5 and 0027).

Ordinary one-window frames retain the ``render(BufferObservation)`` shape.
Constrained multi-window frames reserve the shared echo row, project a
focus-centered subset of complete panes, and keep the cursor with focus.
"""

from __future__ import annotations

from conftest import FakeFilePort

import drei.render as render_module
from drei.commands import (
    ForwardChar,
    SessionObservation,
    SplitWindow,
    WindowObservation,
)
from drei.harness import EditorHarness
from drei.model import Buffer, BufferId, BufferValue
from drei.render import Frame, render_session
from drei.session import EditorSession, WindowValue


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


def test_height_one_active_minibuffer_outranks_every_window() -> None:
    from drei.commands import FindFile

    session = _session()
    session.dispatch(SplitWindow())
    session.dispatch(FindFile())

    frame = render_session(session.session_observation(), width=12, height=1)

    assert frame.rows == ("Find file:  ",)
    assert frame.cursor == (0, 11)


def test_height_one_permission_prompt_owns_the_only_row() -> None:
    from drei.acp.machine import PermissionRequested
    from drei.commands import PromptPermission

    session = _session()
    session.dispatch(
        PromptPermission(
            PermissionRequested(
                request_id=1,
                params={
                    "sessionId": "s1",
                    "toolCall": {"toolCallId": "tc-1", "title": "run tests"},
                    "options": [
                        {
                            "kind": "allow_once",
                            "name": "Allow once",
                            "optionId": "yes",
                        }
                    ],
                },
            )
        )
    )

    frame = render_session(session.session_observation(), width=20, height=1)

    assert frame.rows == ("Allow run tests? [y]",)
    assert frame.cursor == (0, 19)


def test_height_one_exit_prompt_owns_the_only_row() -> None:
    from drei.commands import ExitEditor, InsertText

    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())

    frame = render_session(session.session_observation(), width=20, height=1)

    assert frame.rows == ("Modified buffers exi",)
    assert frame.cursor == (0, 19)


def test_height_one_width_zero_preserves_prompt_ownership() -> None:
    from drei.commands import FindFile

    session = _session()
    session.dispatch(FindFile())

    frame = render_session(session.session_observation(), width=0, height=1)

    assert frame.rows == ("",)
    assert frame.cursor == (0, 0)


def test_height_one_transient_message_outranks_the_focused_modeline() -> None:
    session = _session()
    session.dispatch(SplitWindow())

    frame = render_session(
        session.session_observation(), width=12, height=1, echo="Quit"
    )

    assert frame.rows == ("Quit        ",)
    assert frame.cursor == (0, 0)


def test_height_one_idle_frame_shows_the_focused_lower_modeline() -> None:
    from dataclasses import replace as dc_replace

    session = _session()
    session.dispatch(SplitWindow())
    obs = session.session_observation()
    lower = dc_replace(
        obs.windows[1],
        buffer=dc_replace(obs.windows[1].buffer, buffer_id="lower"),
    )
    obs = dc_replace(obs, windows=(obs.windows[0], lower), focused=1)

    frame = render_session(obs, width=14, height=1)

    assert frame.rows == ("Drei: lower --",)
    assert frame.cursor == (0, 0)


def test_height_two_reserves_echo_below_the_focused_modeline() -> None:
    from dataclasses import replace as dc_replace

    session = _session()
    session.dispatch(SplitWindow())
    obs = session.session_observation()
    lower = dc_replace(
        obs.windows[1],
        buffer=dc_replace(obs.windows[1].buffer, buffer_id="lower"),
    )
    obs = dc_replace(
        obs,
        windows=(obs.windows[0], lower),
        focused=1,
        minibuffer="",
        minibuffer_prompt="Find file: ",
    )

    frame = render_session(obs, width=14, height=2)

    assert frame.rows == ("Drei: lower --", "Find file:    ")
    assert frame.cursor == (1, 11)


def test_height_three_gives_the_focused_lower_pane_body_and_modeline() -> None:
    from dataclasses import replace as dc_replace

    session = _session()
    session.dispatch(SplitWindow())
    obs = session.session_observation()
    lower = dc_replace(
        obs.windows[1],
        buffer=dc_replace(obs.windows[1].buffer, buffer_id="lower"),
    )
    obs = dc_replace(obs, windows=(obs.windows[0], lower), focused=1)

    frame = render_session(obs, width=14, height=3)

    assert frame.rows == ("hello world   ", "Drei: lower --", "              ")
    assert frame.cursor == (0, 0)


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


def test_window_heights_distribute_admitted_panes_with_remainder_to_bottom() -> None:
    from drei.render import _window_heights

    assert _window_heights(4, 2) == (2, 2)
    assert _window_heights(5, 2) == (2, 3)
    assert _window_heights(7, 3) == (2, 2, 3)


def test_render_session_admits_only_complete_panes_within_the_frame_height() -> None:
    """A hand-built overcommitted observation still reserves the echo row and
    never admits a modeline-only non-focused pane."""
    session = _session()
    obs = session.session_observation()
    from dataclasses import replace as dc_replace

    window = obs.windows[0]
    obs = dc_replace(obs, windows=(window, window, window, window, window))
    frame = render_session(obs, width=10, height=4)
    assert len(frame.rows) == 4
    assert _modelines(frame) == 1
    assert frame.rows[-1] == "          "
    assert frame.cursor[0] < len(frame.rows)


def test_render_session_height_zero_is_an_empty_frame() -> None:
    session = _session()
    frame = render_session(session.session_observation(), width=10, height=0)
    assert frame.rows == ()
    assert frame.cursor == (0, 0)
    assert frame.height == 0


def test_visible_window_selection_is_contiguous_and_focus_centered() -> None:
    assert render_module._visible_window_indices(3, focused=0, visible_count=2) == (
        0,
        1,
    )
    assert render_module._visible_window_indices(3, focused=1, visible_count=2) == (
        1,
        2,
    )
    assert render_module._visible_window_indices(3, focused=2, visible_count=2) == (
        1,
        2,
    )


def test_three_window_render_uses_the_focus_centered_visible_subset() -> None:
    from dataclasses import replace as dc_replace

    session = _session()
    window = session.session_observation().windows[0]
    windows = tuple(
        dc_replace(
            window,
            buffer=dc_replace(window.buffer, buffer_id=name, text=name),
        )
        for name in ("A", "B", "C")
    )
    obs = dc_replace(session.session_observation(), windows=windows)

    for focused, expected in ((0, ("A", "B")), (1, ("B", "C")), (2, ("B", "C"))):
        frame = render_session(dc_replace(obs, focused=focused), width=12, height=5)
        modelines = tuple(
            row.removeprefix("Drei: ").split()[0]
            for row in frame.rows
            if row.startswith("Drei:")
        )
        assert modelines == expected
        assert frame.rows[-1] == "            "


def test_cursor_uses_the_selected_focused_pane_offset() -> None:
    from dataclasses import replace as dc_replace

    session = _session()
    window = session.session_observation().windows[0]
    windows = tuple(
        dc_replace(
            window,
            buffer=dc_replace(window.buffer, buffer_id=name, text=f"{name}text"),
            point=2,
        )
        for name in ("A", "B", "C")
    )
    obs = dc_replace(session.session_observation(), windows=windows, focused=1)

    frame = render_session(obs, width=12, height=5)

    assert frame.rows[0] == "Btext       "
    assert frame.cursor == (0, 2)


def test_surplus_row_goes_to_the_bottom_selected_pane() -> None:
    from dataclasses import replace as dc_replace

    session = _session()
    window = session.session_observation().windows[0]
    windows = tuple(
        dc_replace(
            window,
            buffer=dc_replace(window.buffer, buffer_id=name, text=f"{name}0\n{name}1"),
        )
        for name in ("A", "B", "C")
    )
    obs = dc_replace(session.session_observation(), windows=windows, focused=1)

    frame = render_session(obs, width=12, height=6)

    assert frame.rows == (
        "B0          ",
        "Drei: B --  ",
        "C0          ",
        "C1          ",
        "Drei: C --  ",
        "            ",
    )


def _modelines(frame: Frame) -> int:
    return sum(1 for row in frame.rows if row.startswith("Drei:"))


def _windows(harness: EditorHarness) -> tuple[WindowValue, ...]:
    return harness._session.windows  # noqa: SLF001 - layout state has no public reader


def _split_harness(height: int) -> EditorHarness:
    """A harness with two windows over one buffer, at the given height.

    The harness is what makes these *resize* tests rather than renderer
    tests: `render_session` takes width/height as arguments and never reads
    `session._frame_size`, so calling it directly with a smaller number
    proves nothing about `ResizeFrame`. `EditorHarness.resize` is the path
    that actually carries a resize into the rendered frame.
    """
    harness = EditorHarness(width=10, height=height, initial_text="alpha\nbeta")
    harness.send("C-x")
    harness.send("2")
    return harness


def test_shrink_below_the_split_minimum_degrades_in_stages() -> None:
    """Plan 0015 D7, render half: the shrink is absorbed by the renderer in
    stages, and the session keeps both windows through all of them.

    Below the *split* minimum, presentation sheds complete non-focused panes
    before sacrificing focused content or the shared echo row. No stage
    touches window state, which is what makes the resize reversible.
    """
    harness = _split_harness(height=8)
    assert _modelines(harness.frame) == 2

    harness.resize(10, 4)
    assert len(_windows(harness)) == 2  # nothing deleted to make it fit
    assert _modelines(harness.frame) == 1  # spare row enlarges the focused body

    harness.resize(10, 2)
    assert len(_windows(harness)) == 2  # still nothing deleted
    assert len(harness.frame.rows) == 2  # the Frame contract holds
    assert _modelines(harness.frame) == 1
    assert harness.frame.rows[-1] == "          "  # shared echo survives

    harness.resize(10, 1)
    assert len(_windows(harness)) == 2  # still nothing deleted
    assert _modelines(harness.frame) == 1  # the lower pane is now off-frame
    assert harness.frame.cursor[0] < len(harness.frame.rows)


def test_growing_the_frame_back_restores_both_panes() -> None:
    """The other half of D7: what a shrink hid, a grow shows again, with the
    window points it had. Emacs cannot do this — it deletes the window that
    no longer fits, and growing the frame does not bring it back."""
    harness = _split_harness(height=8)
    harness.send("C-x")
    harness.send("o")  # focus the lower window
    harness.send("C-f")  # and move its point off zero
    before = _windows(harness)

    harness.resize(10, 1)  # focused lower pane anchors the projection
    assert _modelines(harness.frame) == 1

    harness.resize(10, 8)
    assert _modelines(harness.frame) == 2
    assert _windows(harness) == before  # points and marks came back untouched
