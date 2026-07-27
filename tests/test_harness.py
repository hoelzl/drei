from drei.commands import (
    FrameResized,
    KeyboardQuitEvent,
    PointMoved,
    TextInserted,
    WindowFocusChanged,
    WindowSplit,
)
from drei.harness import EditorHarness
from drei.keys import UnresolvedKey


def test_harness_produces_exact_evidence() -> None:
    harness = EditorHarness(width=20, height=5)
    harness.send("h")
    harness.send("e")
    harness.send("l")
    harness.send("l")
    harness.send("o")
    harness.send("C-b")
    harness.send("!")
    harness.send("C-f")
    harness.send("C-g")

    assert harness.observation.text == "hell!o"
    assert harness.observation.point == 6
    assert harness.frame.rows[0] == "hell!o              "
    assert harness.frame.cursor == (0, 6)
    assert harness.outcomes[-1].events == (KeyboardQuitEvent(),)


def test_c_g_after_a_prefix_cancels_it_and_quits() -> None:
    """TD-5, through the routing the user actually goes through.

    The mark is the observable that used to survive: `C-x C-g` produced one
    silent `UnresolvedKey` and nothing else happened at all.
    """
    harness = EditorHarness(width=20, height=5, initial_text="hello")
    harness.send("C-@")  # set the mark at 0
    harness.send("C-f")
    assert harness.observation.mark == 0

    harness.send("C-x")  # prefix pending
    outcome = harness.send("C-g")

    assert outcome is not None
    assert outcome.events == (KeyboardQuitEvent(),)
    assert harness.frame.rows[-1].startswith("Quit")
    assert harness.unresolved == ()  # not recorded as an unresolved chord
    # The prefix is gone: the next key is an ordinary self-insert, not the
    # second half of a chord.
    harness.send("z")
    assert harness.observation.text == "hzello"
    # Last, because `is None` narrows the observation for everything after it
    # under mypy's reachability analysis (the same bleed the minibuffer test
    # below documents); the runtime state is unaffected.
    assert harness.observation.mark is None


def test_harness_records_unresolved_keys() -> None:
    # Wide enough that the echo row isn't clipped: the undefined-chord text
    # is asserted in full below.
    harness = EditorHarness(width=30, height=3)
    # Bare C-x opens a prefix: nothing recorded yet.
    harness.send("C-x")
    unresolved_before = harness.unresolved
    assert len(unresolved_before) == 0
    assert len(harness.outcomes) == 0
    # A non-completing second key records the whole sequence as unresolved —
    # and says so (row 134, D7): the harness composes "<chord> is undefined"
    # itself, since no command ever reaches the session.
    harness.send("C-z")
    assert harness.observation.text == ""
    assert harness.unresolved == (UnresolvedKey("C-x C-z"),)
    assert len(harness.outcomes) == 0
    assert harness.frame.rows[-1].startswith("C-x C-z is undefined")


def test_harness_save_via_prefix() -> None:
    from conftest import FakeFilePort

    port = FakeFilePort()
    harness = EditorHarness(
        width=20, height=4, file_port=port, file_path="/tmp/notes.txt"
    )
    harness.send("x")
    harness.send("C-x")
    outcome = harness.send("C-s")
    assert port.files["/tmp/notes.txt"] == "x"
    assert outcome is not None
    assert harness.observation.modified is False
    assert harness.frame.rows[-1].startswith("Wrote /tmp/notes.txt")


def test_harness_save_failure_echoes_token() -> None:
    from conftest import FakeFilePort

    port = FakeFilePort(fail="permission")
    harness = EditorHarness(width=30, height=4, file_port=port, file_path="/root/x.txt")
    harness.send("x")
    harness.send("C-x")
    outcome = harness.send("C-s")
    assert outcome is not None
    assert harness.observation.modified is True
    assert harness.frame.rows[-1].startswith("/root/x.txt: permission-denied")


def test_harness_find_file_failure_echoes_token_then_clears() -> None:
    """Plan 0019 V1's acceptance scenario — the case TD-4 called invisible.

    A `C-x C-f` the port refuses closes the prompt and says so on the echo
    row — `<path>: <token>`, the `SaveFailed` shape — where it used to close
    on a blank row indistinguishable from a successful no-op. The buffer is
    untouched, and the message lives exactly until the next command (D6).
    """
    from conftest import FakeFilePort

    port = FakeFilePort(fail_read="permission")
    harness = EditorHarness(width=40, height=6, file_port=port)
    harness.send("C-x")
    harness.send("C-f")
    for char in "/etc/shadow":
        harness.send(char)
    outcome = harness.send("RET")

    assert outcome is not None
    assert harness.observation.minibuffer is None  # the prompt CLOSES
    assert harness.observation.text == ""  # the buffer is untouched
    assert harness.frame.rows[-1].startswith("/etc/shadow: permission-denied")

    harness.send("a")
    assert harness.frame.rows[-1].strip() == ""
    assert harness.observation.text == "a"


def test_harness_trailing_slash_find_file_echoes_the_refusal() -> None:
    """Plan 0020 §1's second acceptance scenario (TD-3).

    `C-x C-f notes/ RET` closes the prompt and says `notes/:
    empty-basename` on the echo row, where it used to open a buffer named
    `""` that unsaved edits could never be reached from again. The buffer
    is untouched — and, being a name judgment, the refusal needs no file
    port at all (a read would answer `permission-denied`).
    """
    from conftest import FakeFilePort

    port = FakeFilePort(fail_read="permission")
    harness = EditorHarness(width=40, height=6, file_port=port)
    harness.send("C-x")
    harness.send("C-f")
    for char in "notes/":
        harness.send(char)
    outcome = harness.send("RET")

    assert outcome is not None
    assert harness.observation.minibuffer is None  # the prompt CLOSES
    assert harness.observation.text == ""  # the buffer is untouched
    assert harness.frame.rows[-1].startswith("notes/: empty-basename")


def test_message_text_formats_through_the_token_table() -> None:
    """The one formatting seam (plan 0019 D1).

    The whole table is pinned — a typo'd or unmapped token fails here rather
    than rendering as itself in production. An unknown token still fails
    visible as itself rather than raising mid-frame; a subject prefixes as
    `<subject>: <text>` — the shape `SaveFailed` has used since review 0001
    finding 26.
    """
    from drei.harness import _message_text

    assert _message_text("answer-y-or-n") == "Please answer y or n"
    assert _message_text("end-of-buffer") == "End of buffer"
    assert (
        _message_text("mark-not-set")
        == "The mark is not set now, or there is no region"
    )
    assert _message_text("no-further-undo") == "No further undo information"
    assert (
        _message_text("previous-command-not-a-yank")
        == "Previous command was not a yank"
    )
    assert _message_text("too-small-for-splitting") == "Too small for splitting"
    assert _message_text("some-future-token") == "some-future-token"
    assert (
        _message_text("permission-denied", "/etc/shadow")
        == "/etc/shadow: permission-denied"
    )


def test_an_exhausted_undo_speaks_on_the_echo_row_then_clears() -> None:
    """The end-to-end pin for `_echo_for`'s Message branch (plan 0019 D1/D6).

    Registry rows 66/68/72/80/98 each pin their token at the session and the
    table in `test_message_text_formats_through_the_token_table`; this is
    the one that drives a plain message — no prompt involved — from a key to
    the echo row, so the "echoed as …" half of those rows is not pinned
    unit-wise only (the row-92 lesson). D6: it clears on the next command.
    """
    harness = EditorHarness(width=40, height=6)
    harness.send("C-/")  # nothing to undo
    assert harness.frame.rows[-1].startswith("No further undo information")
    harness.send("a")
    assert harness.frame.rows[-1].strip() == ""
    assert harness.observation.text == "a"


def test_harness_outcome_sequence() -> None:
    harness = EditorHarness(width=10, height=3)
    harness.send("a")
    harness.send("C-b")
    harness.send("C-f")

    assert harness.outcomes[0].events == (TextInserted("a", 0, 1),)
    assert harness.outcomes[1].events == (PointMoved(-1, -1),)
    assert harness.outcomes[2].events == (PointMoved(1, 1),)
    assert harness.outcomes[2].observation.point == 1


def test_harness_routes_minibuffer_keys() -> None:
    """C-x C-f opens the prompt; keys route to the minibuffer; a pending
    prefix typed before activation is dropped; RET accepts (missing file
    through the null port → empty buffer); C-g aborts the prompt without
    touching the buffer or the mark."""
    harness = EditorHarness(width=40, height=6)
    harness.send("z")  # dirty the buffer: text "z"
    harness.send("C-x")  # pending prefix...
    outcome = harness.send("C-f")  # ...completes as C-x C-f → FindFile
    assert outcome is not None
    assert any(type(e).__name__ == "MinibufferOpened" for e in outcome.events)
    assert harness.observation.minibuffer == ""
    assert harness.frame.rows[-1].startswith("Find file: ")

    harness.send("a")
    harness.send("b")
    assert harness.observation.minibuffer == "ab"
    assert harness.frame.rows[-1].startswith("Find file: ab")
    assert harness.frame.cursor[0] == len(harness.frame.rows) - 1  # echo row
    harness.send("DEL")
    assert harness.observation.minibuffer == "a"

    # Pending prefix is dead; control keys ignored while active.
    assert harness.send("C-f") is None  # ForwardChar does NOT run
    assert harness.observation.minibuffer == "a"

    # Abort: prompt closes, buffer and mark untouched, no quit — and the
    # echo says what happened (row 92: this assertion is the one this test
    # was cited for without ever having had).
    outcome = harness.send("C-g")
    assert outcome is not None
    assert any(type(e).__name__ == "MinibufferAborted" for e in outcome.events)
    assert all(type(e).__name__ != "KeyboardQuitEvent" for e in outcome.events)
    assert harness.frame.rows[-1].startswith("Quit")
    closed = harness.observation.minibuffer
    assert closed is None
    # The `closed is None` narrowing bleeds into the next expression under
    # mypy's reachability analysis; the runtime state is unaffected.
    assert harness.observation.text == "z"  # type: ignore[unreachable]

    # Accept path: open again, type a path, RET → null port read fails
    # not-found → empty buffer at that path.
    harness.send("C-x")
    harness.send("C-f")
    for char in "/tmp/nope.txt":
        harness.send(char)
    outcome = harness.send("RET")
    assert outcome is not None
    assert any(type(e).__name__ == "BufferOpened" for e in outcome.events)
    assert harness.observation.text == ""
    assert harness.observation.file_path == "/tmp/nope.txt"
    assert harness.observation.minibuffer is None


class TestHarnessResize:
    """V3 of plan 0015: a resize reaches the session as a command and the
    harness re-renders at the new size."""

    def test_resize_changes_the_rendered_width(self) -> None:
        harness = EditorHarness(width=20, height=5)
        harness.send("h")
        assert len(harness.frame.rows[0]) == 20
        harness.resize(40, 8)
        assert len(harness.frame.rows[0]) == 40
        assert len(harness.frame.rows) == 8
        assert harness.frame.rows[0].startswith("h")

    def test_resize_records_the_command_in_the_outcome_sequence(self) -> None:
        harness = EditorHarness(width=20, height=5)
        harness.resize(40, 8)
        assert FrameResized(40, 8) in harness.outcomes[-1].events

    def test_a_grown_frame_permits_a_split_the_old_size_refused(self) -> None:
        harness = EditorHarness(width=20, height=6)  # < (1+1)*3+1 = 7
        harness.send("C-x")
        harness.send("2")
        assert not any(
            isinstance(e, WindowSplit) for o in harness.outcomes for e in o.events
        )
        harness.resize(20, 24)
        harness.send("C-x")
        harness.send("2")
        assert WindowSplit(2) in harness.outcomes[-1].events

    def test_a_shrunk_frame_refuses_a_further_split(self) -> None:
        harness = EditorHarness(width=20, height=24)
        harness.send("C-x")
        harness.send("2")
        harness.resize(20, 6)
        harness.send("C-x")
        harness.send("2")
        assert WindowSplit(3) not in harness.outcomes[-1].events

    def test_focus_still_cycles_into_a_window_the_frame_cannot_show(self) -> None:
        """The hazard D7 owns: while shrunk, a window exists that the frame
        does not render. It is not lost — C-x o reaches it and typing lands
        in it — which is exactly why keeping it is safe rather than confusing
        state with nothing behind it.
        """
        harness = EditorHarness(width=20, height=24, initial_text="ab")
        harness.send("C-x")
        harness.send("2")
        harness.resize(20, 1)  # only the top pane's modeline fits
        harness.send("C-x")
        harness.send("o")  # focus the pane that is off-frame
        assert WindowFocusChanged(1, "scratch") in harness.outcomes[-1].events

        # The invisible window is a real window, not a bookkeeping entry: it
        # carries its own point, and editing moves *its* point rather than
        # the visible window's. Asserting on the buffer text alone would not
        # show this — both windows share one buffer, so the text is the same
        # whichever window has focus.
        harness.send("C-f")
        session = harness._session  # noqa: SLF001 - layout has no public reader
        assert session.windows[1].point == 1
        assert session.windows[0].point == 0

    def test_resize_does_not_clear_a_pending_echo_message(self) -> None:
        """A resize is not a user action and must not wipe the echo area:
        `Quit` survives the terminal changing shape underneath it."""
        harness = EditorHarness(width=20, height=5)
        harness.send("C-g")
        assert harness.frame.rows[-1].startswith("Quit")
        harness.resize(40, 8)
        assert harness.frame.rows[-1].startswith("Quit")

    def test_resize_while_the_minibuffer_is_open_is_not_swallowed(self) -> None:
        """Pinned answer (plan 0015 V3): the minibuffer gate routes *keys*,
        and a resize is not one. The frame size is a property of the
        terminal, not of input focus — swallowing it would render the prompt
        against a stale width for as long as the prompt stayed open.
        """
        harness = EditorHarness(width=20, height=5)
        harness.send("C-x")
        harness.send("C-f")  # find-file prompt open
        assert harness.observation.minibuffer == ""
        harness.resize(40, 8)
        # The prompt survived the resize and re-rendered at the new size...
        assert harness.observation.minibuffer == ""
        assert len(harness.frame.rows[-1]) == 40
        assert harness.frame.rows[-1].startswith("Find file:")
        # ...and typing continues into the same prompt, so the resize was
        # not read as minibuffer input.
        harness.send("x")
        assert harness.observation.minibuffer == "x"
