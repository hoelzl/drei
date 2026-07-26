"""`C-x C-c` offers to save before ending the run (plan 0018, TD-11).

Two stages, and the split is the slice's whole shape: stage 1 offers to save
each *file-visiting* modified buffer one at a time, and stage 2 — reached
however stage 1 ended — asks once more if **any** buffer is still modified,
pathless ones included. Only a `y` at stage 2 (or an empty stage 2) ends the
run.

The session is the subject here rather than the terminal loop: `EditorExited`
is the loop's only exit condition, so "did the run end" is "did this outcome
carry `EditorExited`", assertable without a terminal. The end-to-end frame
evidence lives in `test_terminal.py`.
"""

from __future__ import annotations

from conftest import FakeFilePort

from drei.acp.machine import PermissionRequested
from drei.commands import (
    AbortPendingPermissions,
    BufferSaved,
    CommandOutcome,
    CreateAgentBuffer,
    EditorExited,
    ExitEditor,
    ExitRefused,
    FindFile,
    InsertAgentText,
    InsertText,
    KeyboardQuitEvent,
    Message,
    MinibufferAbort,
    MinibufferAborted,
    MinibufferAccept,
    MinibufferBackspace,
    MinibufferInput,
    MinibufferOpened,
    PromptPermission,
    SaveBuffer,
    SaveDeclined,
    SaveFailed,
    SetMark,
)
from drei.harness import EditorHarness
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession

SAVE_PROMPT = "Save file {}? (y or n) "
EXIT_PROMPT = "Modified buffers exist; exit anyway? (y or n) "


def _harness(files: FakeFilePort, *, width: int = 80) -> EditorHarness:
    """A harness over one file-visiting buffer (/tmp/notes.txt, unmodified
    until the test types) — the note lives on the frame, so the frame is
    where these tests read it (plan 0019 D3)."""
    return EditorHarness(
        width=width,
        height=6,
        file_port=files,
        file_path="/tmp/notes.txt",
        initial_text="saved",
    )


def _session(
    files: FakeFilePort | None = None,
    *,
    path: str | None = "/tmp/notes.txt",
    text: str = "saved",
) -> EditorSession:
    """A session over one buffer, file-visiting unless ``path`` is None."""
    name = path.rsplit("/", 1)[-1] if path else "scratch"
    return EditorSession(
        Buffer(
            BufferId(name),
            BufferValue(text=text, point=len(text), file_path=path),
        ),
        file_port=files if files is not None else FakeFilePort(),
    )


def _visit(session: EditorSession, path: str, edit: str = "x") -> None:
    """Open ``path`` through `C-x C-f` and leave the new buffer modified.

    Through the real find-file path rather than by reaching into `_buffers`:
    the exit sequence walks the buffers the editor actually has, so the test
    should build them the way the editor does.
    """
    session.dispatch(FindFile())
    for char in path:
        session.dispatch(MinibufferInput(char))
    session.dispatch(MinibufferAccept())
    session.dispatch(InsertText(edit))


def _exited(outcome: CommandOutcome) -> bool:
    """Whether this outcome ends the run — `EditorExited` is the loop's only
    exit condition, so the terminal is not needed to ask the question."""
    return any(isinstance(e, EditorExited) for e in outcome.events)


def _prompts(outcome: CommandOutcome) -> list[str]:
    return [e.prompt for e in outcome.events if isinstance(e, MinibufferOpened)]


# ----------------------------------------------------------------------
# Stage 1: the per-buffer offer
# ----------------------------------------------------------------------


def test_nothing_modified_exits_without_asking() -> None:
    """The unchanged path, and the reason the whole existing suite still
    exits in two keystrokes: no question is asked when nothing is at risk."""
    session = _session()
    outcome = session.dispatch(ExitEditor())
    assert _exited(outcome)
    assert session.minibuffer is None


def test_modified_file_buffer_is_offered_before_the_run_ends() -> None:
    session = _session()
    session.dispatch(InsertText("x"))
    outcome = session.dispatch(ExitEditor())
    assert not _exited(outcome)
    assert MinibufferOpened(SAVE_PROMPT.format("/tmp/notes.txt")) in outcome.events
    assert session.minibuffer_prompt == SAVE_PROMPT.format("/tmp/notes.txt")


def test_y_at_the_offer_writes_the_file_and_ends_the_run() -> None:
    files = FakeFilePort({"/tmp/notes.txt": "saved"})
    session = _session(files)
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferInput("y"))
    assert BufferSaved("/tmp/notes.txt") in outcome.events
    assert _exited(outcome)
    assert files.files["/tmp/notes.txt"] == "savedx"
    # One outcome carries both events, which is what puts `Wrote …` on the
    # final frame instead of on a frame nobody sees.
    assert [type(e) for e in outcome.events] == [BufferSaved, EditorExited]


def test_saving_the_focused_buffer_is_not_undone_by_the_write_back() -> None:
    """D6's hazard, pinned.

    `dispatch` writes the arm's `new_value` into the *focused* buffer at the
    end, and for a `MinibufferInput` that is the buffer being asked about. A
    stale `current` captured before the arm ran would put `modified=True` back
    on a buffer that had just been saved.
    """
    files = FakeFilePort({"/tmp/notes.txt": "saved"})
    session = _session(files)
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    session.dispatch(MinibufferInput("y"))
    assert session.buffer.current.modified is False
    assert session.buffer.current.text == "savedx"


# ----------------------------------------------------------------------
# Stage 2: the gate
# ----------------------------------------------------------------------


def test_n_at_the_offer_leads_to_the_exit_gate() -> None:
    files = FakeFilePort({"/tmp/notes.txt": "saved"})
    session = _session(files)
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferInput("n"))
    assert not _exited(outcome)
    assert MinibufferOpened(EXIT_PROMPT) in outcome.events
    # `n` means "do not save", and nothing was written.
    assert files.files["/tmp/notes.txt"] == "saved"
    # ...but it is something the user *did*, so the transcript records it
    # (plan 0019 D4, issue #51): replay can now tell a declined save from a
    # save that was never offered.
    assert SaveDeclined("notes.txt") in outcome.events


def test_c_g_at_the_gate_says_quit() -> None:
    """Plan 0019 acceptance scenario 3 (row 92): `C-g` at the gate abandons
    the exit, and the echo says `Quit` — because that is what happened. The
    refusal (`n`) is silent, which is now *honest* silence: the transcript
    carries `ExitRefused` for it."""
    files = FakeFilePort({"/tmp/notes.txt": "saved"})
    harness = _harness(files)
    harness.send("x")
    harness.send("C-x")
    harness.send("C-c")
    harness.send("n")  # offer declined → the gate
    harness.send("C-g")
    assert harness.observation.minibuffer is None
    assert harness.frame.rows[-1].startswith("Quit")


def test_y_at_the_gate_ends_the_run_and_the_edit_is_gone() -> None:
    files = FakeFilePort({"/tmp/notes.txt": "saved"})
    session = _session(files)
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    session.dispatch(MinibufferInput("n"))
    outcome = session.dispatch(MinibufferInput("y"))
    assert _exited(outcome)
    assert files.files["/tmp/notes.txt"] == "saved"


def test_n_at_the_gate_leaves_the_editor_running_with_the_text_intact() -> None:
    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    session.dispatch(MinibufferInput("n"))
    outcome = session.dispatch(MinibufferInput("n"))
    assert not _exited(outcome)
    # A refusal is a decision, not an escape — the transcript distinguishes
    # it from `C-g`'s MinibufferAborted now (plan 0019 D4, issue #51).
    assert ExitRefused() in outcome.events
    assert MinibufferAborted() not in outcome.events
    assert session.minibuffer is None
    assert session.buffer.current.text == "savedx"
    assert session.buffer.current.modified is True


def test_the_refused_exit_can_be_asked_again() -> None:
    """A refusal abandons this exit, not the ability to exit."""
    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    session.dispatch(MinibufferInput("n"))
    session.dispatch(MinibufferInput("n"))
    outcome = session.dispatch(ExitEditor())
    assert MinibufferOpened(SAVE_PROMPT.format("/tmp/notes.txt")) in outcome.events


# ----------------------------------------------------------------------
# The pathless buffer (D2 / parity row 4) — Drei cannot save it at all
# ----------------------------------------------------------------------


def test_a_modified_pathless_buffer_goes_straight_to_the_gate() -> None:
    """The startup buffer, where a new user's first typing lands.

    Emacs counts only file-visiting buffers here and would exit in silence.
    Drei has no `write-file` to rescue the text, so the only protection
    available is to ask.
    """
    session = _session(path=None, text="")
    session.dispatch(InsertText("hello"))
    outcome = session.dispatch(ExitEditor())
    assert not _exited(outcome)
    # Never *offered* — offering would offer something the editor cannot do.
    assert MinibufferOpened(EXIT_PROMPT) in outcome.events
    assert not any(isinstance(e, SaveFailed) for e in outcome.events)


def test_n_at_the_gate_keeps_the_pathless_text() -> None:
    session = _session(path=None, text="")
    session.dispatch(InsertText("hello"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferInput("n"))
    assert not _exited(outcome)
    assert session.buffer.current.text == "hello"
    assert session.buffer.current.modified is True


def test_y_at_the_gate_discards_the_pathless_text_deliberately() -> None:
    session = _session(path=None, text="")
    session.dispatch(InsertText("hello"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferInput("y"))
    assert _exited(outcome)


# ----------------------------------------------------------------------
# D3: the gate is recomputed from buffer state, not tallied
# ----------------------------------------------------------------------


def test_a_save_that_fails_still_blocks_the_exit() -> None:
    """`y` on a file Drei cannot write must not read as "saved, exiting".

    A tally of answers would let this through: the user *did* say `y`. The
    gate asks the buffers instead, and the buffer is still modified.
    """
    files = FakeFilePort({"/tmp/notes.txt": "saved"}, fail="permission")
    session = _session(files)
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferInput("y"))
    assert SaveFailed("/tmp/notes.txt", "permission-denied") in outcome.events
    assert not _exited(outcome)
    # The gate is reached; the failure rides it on the frame (D3), which the
    # note tests below pin — the prompt string itself stays bare.
    assert _prompts(outcome) == [EXIT_PROMPT]


def test_a_save_that_succeeds_stops_blocking() -> None:
    """The other half of D3: the gate is skipped entirely once the buffer is
    clean, so a successful `y` exits in one key rather than two."""
    files = FakeFilePort({"/tmp/notes.txt": "saved"})
    session = _session(files)
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferInput("y"))
    assert _exited(outcome)
    assert not any(
        isinstance(e, MinibufferOpened) and e.prompt == EXIT_PROMPT
        for e in outcome.events
    )


# ----------------------------------------------------------------------
# D5: the answer set is `y` / `n` / `C-g`, and nothing else
# ----------------------------------------------------------------------


def test_an_unrecognized_key_leaves_the_offer_standing() -> None:
    """Emacs echoes `Please answer y or n` here; since plan 0019, so does
    Drei (row 130) — as a Message, which is not a semantic event (D2).

    What matters either way is that the prompt does not resolve — and that the
    key is not appended as *text*, which is what a missed branch would do:
    `Save file …? z` on the echo row rather than a crash.
    """
    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    # `SPC` and `DEL` are *bound answers* in Emacs's `map-y-or-n-p`, and
    # `Y`/`N` are the obvious near-misses — the registry row names all of
    # them, so all of them are pinned rather than three of them assumed.
    for char in ("z", " ", "Y", "N", "!", "q", "."):
        outcome = session.dispatch(MinibufferInput(char))
        assert outcome.events == (Message("answer-y-or-n"),), char
        assert not _exited(outcome), char
        assert session.minibuffer == "", char
        assert session.minibuffer_prompt == SAVE_PROMPT.format("/tmp/notes.txt"), char


def test_ret_is_not_a_default_yes() -> None:
    """The exit prompts are the last guard before losing work, so a habitual
    `RET` must not answer them (B.8 finding 9's reasoning, D5). Like any
    other non-answer key it now says `Please answer y or n` (row 130)."""
    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferAccept())
    assert outcome.events == (Message("answer-y-or-n"),)
    assert session.minibuffer_prompt == SAVE_PROMPT.format("/tmp/notes.txt")


def test_ret_is_not_a_default_yes_at_the_gate() -> None:
    session = _session(path=None, text="")
    session.dispatch(InsertText("hello"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferAccept())
    assert outcome.events == (Message("answer-y-or-n"),)
    assert session.minibuffer_prompt == EXIT_PROMPT


# ----------------------------------------------------------------------
# C-g abandons the exit at either stage
# ----------------------------------------------------------------------


def test_c_g_at_the_offer_abandons_the_exit() -> None:
    files = FakeFilePort({"/tmp/notes.txt": "saved"})
    session = _session(files)
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferAbort())
    assert not _exited(outcome)
    assert MinibufferAborted() in outcome.events
    assert session.minibuffer is None
    assert session.buffer.current.text == "savedx"
    assert files.files["/tmp/notes.txt"] == "saved"


def test_c_g_at_the_gate_abandons_the_exit() -> None:
    session = _session(path=None, text="")
    session.dispatch(InsertText("hello"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferAbort())
    assert not _exited(outcome)
    # `C-g` is an escape, not a decision — the other half of the refusal /
    # abandonment distinction (plan 0019 D4, issue #51).
    assert MinibufferAborted() in outcome.events
    assert ExitRefused() not in outcome.events
    assert session.minibuffer is None
    assert session.buffer.current.text == "hello"


def test_c_g_does_not_deactivate_the_main_buffer_mark() -> None:
    """Aborting a prompt is not a top-level quit — the same rule the text
    prompts follow. `C-g` at an exit prompt must not reach into the buffer."""
    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(SetMark())
    session.dispatch(ExitEditor())
    session.dispatch(MinibufferAbort())
    assert session.buffer.current.mark == 6
    assert not any(isinstance(e, KeyboardQuitEvent) for e in session.transcript)


def test_c_g_leaves_already_saved_buffers_saved() -> None:
    """Abandoning is not undoing.

    Two buffers, both modified. `y` writes the first; `C-g` at the second
    abandons the exit — and the first file stays written, because a save is a
    completed effect on disk and nothing in the editor can take it back.
    """
    files = FakeFilePort({"/tmp/a.txt": "A", "/tmp/b.txt": "B"})
    session = _session(files, path="/tmp/a.txt", text="A")
    session.dispatch(InsertText("1"))
    _visit(session, "/tmp/b.txt", edit="2")
    session.dispatch(ExitEditor())

    session.dispatch(MinibufferInput("y"))  # a.txt: save
    outcome = session.dispatch(MinibufferAbort())  # b.txt: abandon

    assert not _exited(outcome)
    assert files.files["/tmp/a.txt"] == "A1"
    assert files.files["/tmp/b.txt"] == "B"
    assert session.minibuffer is None


# ----------------------------------------------------------------------
# The multi-buffer sequence (parity row 5: creation order, not recency)
# ----------------------------------------------------------------------


def test_each_modified_file_buffer_is_offered_in_creation_order() -> None:
    files = FakeFilePort({"/tmp/a.txt": "A", "/tmp/b.txt": "B"})
    session = _session(files, path="/tmp/a.txt", text="A")
    session.dispatch(InsertText("1"))
    _visit(session, "/tmp/b.txt", edit="2")

    first = session.dispatch(ExitEditor())
    assert _prompts(first) == [SAVE_PROMPT.format("/tmp/a.txt")]

    second = session.dispatch(MinibufferInput("n"))
    assert _prompts(second) == [SAVE_PROMPT.format("/tmp/b.txt")]
    assert not _exited(second)


def test_the_plans_two_buffer_scenario() -> None:
    """§1's third script, keystroke for keystroke.

    `n` on a.txt, `y` on b.txt: b is written, a is still modified, so the gate
    asks — and `C-g` there leaves the editor running with b.txt written.
    """
    files = FakeFilePort({"/tmp/a.txt": "A", "/tmp/b.txt": "B"})
    session = _session(files, path="/tmp/a.txt", text="A")
    session.dispatch(InsertText("1"))
    _visit(session, "/tmp/b.txt", edit="2")

    session.dispatch(ExitEditor())
    session.dispatch(MinibufferInput("n"))  # a.txt: skip
    gate = session.dispatch(MinibufferInput("y"))  # b.txt: save
    assert _prompts(gate) == [EXIT_PROMPT]
    assert files.files["/tmp/b.txt"] == "2B"

    outcome = session.dispatch(MinibufferAbort())
    assert not _exited(outcome)
    assert files.files["/tmp/b.txt"] == "2B"
    assert files.files["/tmp/a.txt"] == "A"


def test_saving_every_modified_buffer_skips_the_gate() -> None:
    files = FakeFilePort({"/tmp/a.txt": "A", "/tmp/b.txt": "B"})
    session = _session(files, path="/tmp/a.txt", text="A")
    session.dispatch(InsertText("1"))
    _visit(session, "/tmp/b.txt", edit="2")

    session.dispatch(ExitEditor())
    session.dispatch(MinibufferInput("y"))
    outcome = session.dispatch(MinibufferInput("y"))

    assert _exited(outcome)
    assert _prompts(outcome) == []
    assert files.files == {"/tmp/a.txt": "A1", "/tmp/b.txt": "2B"}


def test_a_buffer_saved_while_not_focused_is_written_and_marked_clean() -> None:
    """Stage 1 saves buffers the user is not looking at (D6).

    The focus is on b.txt throughout; a.txt is saved from under it, and the
    line ending and the clean point must come from a.txt's own state rather
    than from the focused buffer's.
    """
    files = FakeFilePort({"/tmp/a.txt": "A\r\nA", "/tmp/b.txt": "B"})
    session = _session(files, path="/tmp/a.txt", text="A\r\nA")
    session.dispatch(InsertText("1"))
    _visit(session, "/tmp/b.txt", edit="2")
    assert session.buffer.buffer_id.value == "b.txt"

    session.dispatch(ExitEditor())
    session.dispatch(MinibufferInput("y"))  # saves a.txt, which is not focused

    # a.txt's CRLF endings survived, and its own buffer reports clean.
    assert files.files["/tmp/a.txt"] == "A\r\nA1"
    assert session._buffers[BufferId("a.txt")].current.modified is False
    assert session.buffer.buffer_id.value == "b.txt"


# ----------------------------------------------------------------------
# Two facts §2 asserts rather than assumes
# ----------------------------------------------------------------------


def test_agent_output_never_triggers_an_exit_prompt() -> None:
    """An agent append leaves `modified` alone by design (0004), so a session
    full of agent output still exits in two keystrokes.

    Asserted rather than assumed: if an append ever set the flag, every
    `C-x C-c` after an agent turn would stop at a gate offering to save a
    buffer that visits no file.
    """
    session = _session()
    session.dispatch(CreateAgentBuffer("acp-1"))
    agent_id = session.agent_buffer_id("acp-1")
    assert agent_id is not None
    session.dispatch(InsertAgentText("agent says hi\n", buffer_id=agent_id))

    outcome = session.dispatch(ExitEditor())
    assert _exited(outcome)
    assert _prompts(outcome) == []


def test_a_saved_buffer_exits_without_a_prompt() -> None:
    """`C-x C-s` then `C-x C-c` is the ordinary path, and it stays quiet."""
    files = FakeFilePort({"/tmp/notes.txt": "saved"})
    session = _session(files)
    session.dispatch(InsertText("x"))
    session.dispatch(SaveBuffer())
    outcome = session.dispatch(ExitEditor())
    assert _exited(outcome)
    assert _prompts(outcome) == []


# ----------------------------------------------------------------------
# D7: an abandoned exit drains the permission queue; a completed one does not
# ----------------------------------------------------------------------


def _permission(request_id: int = 42) -> PermissionRequested:
    return PermissionRequested(
        request_id=request_id,
        params={
            "sessionId": "s1",
            "toolCall": {"toolCallId": "tc-1", "title": "run tests"},
            "options": [
                {"kind": "allow_once", "name": "Allow once", "optionId": "o-once"},
                {"kind": "reject_once", "name": "No", "optionId": "o-no"},
            ],
        },
    )


def test_a_request_arriving_behind_an_exit_prompt_queues() -> None:
    """Delivery-class, like every other permission request: the session's gate
    exempts `PromptPermission`, and a prompt is open, so it queues."""
    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(PromptPermission(_permission()))
    assert session.pending_permission_count() == 1
    assert _prompts(outcome) == []
    # The exit question still owns the echo row.
    assert session.minibuffer_prompt == SAVE_PROMPT.format("/tmp/notes.txt")


def test_c_g_abandoning_the_exit_presents_the_queued_request() -> None:
    """Otherwise the agent waits for an answer that is never coming — for the
    rest of the run, since the editor did not die after all."""
    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    session.dispatch(PromptPermission(_permission()))
    outcome = session.dispatch(MinibufferAbort())
    assert session.pending_permission_count() == 0
    assert len(_prompts(outcome)) == 1
    assert "run tests" in (session.minibuffer_prompt or "")


def test_refusing_the_gate_presents_the_queued_request() -> None:
    """`n` at the gate is the same abandonment as `C-g`."""
    session = _session(path=None, text="")
    session.dispatch(InsertText("hello"))
    session.dispatch(ExitEditor())
    session.dispatch(PromptPermission(_permission()))
    outcome = session.dispatch(MinibufferInput("n"))
    assert not _exited(outcome)
    assert session.pending_permission_count() == 0
    assert "run tests" in (session.minibuffer_prompt or "")


def test_a_completed_exit_does_not_present_the_queued_request() -> None:
    """The process is ending and `pump.close()` terminates the child, so a
    prompt about work that is about to stop existing would be asking the user
    to answer for nothing — and it would arrive on the frame after the run."""
    session = _session(path=None, text="")
    session.dispatch(InsertText("hello"))
    session.dispatch(ExitEditor())
    session.dispatch(PromptPermission(_permission()))
    outcome = session.dispatch(MinibufferInput("y"))
    assert _exited(outcome)
    assert _prompts(outcome) == []
    assert session.minibuffer is None


def test_advancing_the_sequence_does_not_present_the_queued_request() -> None:
    """Between two offers is not abandonment: the next question takes the echo
    row, and the request stays queued until the sequence resolves."""
    files = FakeFilePort({"/tmp/a.txt": "A", "/tmp/b.txt": "B"})
    session = _session(files, path="/tmp/a.txt", text="A")
    session.dispatch(InsertText("1"))
    _visit(session, "/tmp/b.txt", edit="2")
    session.dispatch(ExitEditor())
    session.dispatch(PromptPermission(_permission()))

    outcome = session.dispatch(MinibufferInput("n"))  # a.txt → offer b.txt
    assert _prompts(outcome) == [SAVE_PROMPT.format("/tmp/b.txt")]
    assert session.pending_permission_count() == 1


def test_abort_pending_permissions_leaves_the_exit_prompt_standing() -> None:
    """A cancelled turn clears the queue and closes a *choice* prompt; an exit
    prompt is not one, so the sequence survives it."""
    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    session.dispatch(PromptPermission(_permission()))
    outcome = session.dispatch(AbortPendingPermissions())

    assert session.pending_permission_count() == 0
    assert MinibufferAborted() not in outcome.events
    assert session.minibuffer_prompt == SAVE_PROMPT.format("/tmp/notes.txt")
    # And the sequence still resolves.
    assert _exited(session.dispatch(MinibufferInput("y")))


# ----------------------------------------------------------------------
# A save that fails inside the sequence says so (review 0002 finding 1)
# ----------------------------------------------------------------------


def test_a_failed_save_names_itself_in_the_prompt_that_follows() -> None:
    """The gate must not be the first thing the user reads after a failure.

    An open minibuffer owns the echo row, so a message left to `_echo_for`
    would be drawn over unread (review 0002 finding 1). Since plan 0019 D3
    the message *rides the prompt*: the harness appends `[<path>: <token>]`
    — `_message_text`'s own shape — to whatever prompt is open after the
    command. The `MinibufferOpened` event stays bare: the note is rendering,
    not prompt identity, and plan 0018's baked-in plumbing is gone. The
    rendered string is byte-identical to what that plumbing produced.
    """
    files = FakeFilePort({"/tmp/notes.txt": "saved"}, fail="permission")
    harness = _harness(files, width=100)
    harness.send("x")
    harness.send("C-x")
    harness.send("C-c")
    outcome = harness.send("y")

    assert outcome is not None
    assert SaveFailed("/tmp/notes.txt", "permission-denied") in outcome.events
    assert MinibufferOpened(EXIT_PROMPT) in outcome.events  # bare, not baked
    assert harness.frame.rows[-1].startswith(
        f"{EXIT_PROMPT}[/tmp/notes.txt: permission-denied]"
    )


def test_a_failed_save_names_itself_before_the_next_offer() -> None:
    """The multi-buffer case is the worse one: without the note the next
    frame is an ordinary `Save file …?` for the *other* buffer, with no trace
    at all that the previous one was not written."""
    files = FakeFilePort({"/tmp/a.txt": "A", "/tmp/b.txt": "B"}, fail="permission")
    harness = EditorHarness(
        width=100, height=6, file_port=files, file_path="/tmp/a.txt", initial_text="A"
    )
    harness.send("1")  # modify a.txt
    harness.send("C-x")
    harness.send("C-f")
    for char in "/tmp/b.txt":
        harness.send(char)
    harness.send("RET")  # visit b.txt
    harness.send("2")  # modify b.txt

    harness.send("C-x")
    harness.send("C-c")
    harness.send("y")  # a.txt offered first (creation order); write fails
    assert harness.frame.rows[-1].startswith(
        SAVE_PROMPT.format("/tmp/b.txt") + "[/tmp/a.txt: permission-denied]"
    )


def test_a_declined_save_is_not_reported_as_a_failure() -> None:
    """`n` is an answer, not an error — nothing to report, so the gate that
    follows carries no note."""
    files = FakeFilePort({"/tmp/notes.txt": "saved"}, fail="permission")
    harness = _harness(files, width=100)
    harness.send("x")
    harness.send("C-x")
    harness.send("C-c")
    harness.send("n")
    row = harness.frame.rows[-1]
    assert row.startswith(EXIT_PROMPT)
    assert "[" not in row


def test_an_unrecognized_key_rides_the_standing_prompt() -> None:
    """Plan 0019's second acceptance scenario (D3, row 130): a message
    raised while a prompt stays open rides that prompt as
    `<prompt> [<message>]`, the prompt stays up, and its answer set still
    works afterwards."""
    files = FakeFilePort({"/tmp/notes.txt": "saved"})
    harness = _harness(files, width=100)
    harness.send("x")
    harness.send("C-x")
    harness.send("C-c")
    harness.send("z")
    assert harness.frame.rows[-1].startswith(
        SAVE_PROMPT.format("/tmp/notes.txt") + "[Please answer y or n]"
    )
    assert harness.observation.minibuffer is not None  # the prompt is STILL UP
    harness.send("y")  # and its answer set still resolves the sequence
    assert files.files["/tmp/notes.txt"] == "xsaved"  # harness point starts at 0
    assert _exited(harness.outcomes[-1])


def test_del_at_an_exit_prompt_leaves_it_standing() -> None:
    """The fourth minibuffer arm (review 0002 finding 2).

    `MinibufferBackspace` has no exit branch: DEL falls through to text mode
    and is harmless only because an exit prompt's input is `""`, which the
    `elif self._minibuffer:` guard reads as falsy. That is a real property of
    the current design, but it was holding by accident — pinned here so a
    prompt that ever carries text cannot silently start eating it.
    """
    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferBackspace())
    assert outcome.events == (Message("answer-y-or-n"),)
    assert session.minibuffer == ""
    assert session.minibuffer_prompt == SAVE_PROMPT.format("/tmp/notes.txt")
    # And the sequence still resolves normally afterwards.
    assert _prompts(session.dispatch(MinibufferInput("n"))) == [EXIT_PROMPT]


# ----------------------------------------------------------------------
# The note is a SUFFIX, because the row is clipped (review 0002r2 finding 1)
# ----------------------------------------------------------------------


class _RefusingPort(FakeFilePort):
    """A port that refuses writes to named paths only.

    `FakeFilePort.fail` is global to the port, so a sequence where one save
    fails and the next succeeds — the case that distinguishes a per-prompt
    note from sticky session state — cannot be built with it.
    """

    def __init__(self, files: dict[str, str], refuse: set[str]) -> None:
        super().__init__(files)
        self.refuse = refuse

    def write(self, path: str, text: str) -> None:
        if path in self.refuse:
            raise PermissionError(path)
        super().write(path, text)


def test_the_question_survives_clipping_when_a_note_is_present() -> None:
    """The fix must not make the question unreadable to make the failure so.

    The echo row is hard-clipped (`render._clip`: no wrap, no scroll), and the
    shipped ConPTY scenarios run at 40 columns. A *prefixed* note pushes the
    question off the end — at 40 the gate read
    `/tmp/notes.txt: permission-denied. Modif`, a truncated error with no
    visible question, on the row where `y` is the key that discards the
    buffer. A suffix guarantees the opposite sacrifice: the annotation is what
    gets cut, and the question and its answer set always survive.
    """
    from drei.render import _clip

    files = _RefusingPort({"/tmp/notes.txt": "saved"}, refuse={"/tmp/notes.txt"})
    harness = _harness(files, width=40)
    harness.send("x")
    harness.send("C-x")
    harness.send("C-c")
    harness.send("y")

    # At the width the shipped scenarios use, the question is intact and it
    # is the note that is sacrificed.
    assert harness.frame.rows[-1] == _clip(EXIT_PROMPT, 40)


def test_the_offer_keeps_its_filename_when_a_note_is_present() -> None:
    """The stage-1 case is the one where truncation used to hide the *target*:
    at 40 columns the prefixed form read `Save file` with no filename, and `y`
    then wrote a file the user had never been shown."""
    files = _RefusingPort({"/tmp/a.txt": "A", "/tmp/b.txt": "B"}, refuse={"/tmp/a.txt"})
    harness = EditorHarness(
        width=40, height=6, file_port=files, file_path="/tmp/a.txt", initial_text="A"
    )
    harness.send("1")
    harness.send("C-x")
    harness.send("C-f")
    for char in "/tmp/b.txt":
        harness.send(char)
    harness.send("RET")
    harness.send("2")

    harness.send("C-x")
    harness.send("C-c")
    harness.send("y")  # a.txt refuses
    assert "/tmp/b.txt" in harness.frame.rows[-1]


def test_the_note_does_not_outlive_the_prompt_it_was_raised_on() -> None:
    """a.txt fails, b.txt saves cleanly — the gate must be clean.

    The note is recomputed per command (D6), exactly like the echo row's own
    lifetime: it belongs to the keystroke whose save failed, not to the
    sequence.
    """
    files = _RefusingPort({"/tmp/a.txt": "A", "/tmp/b.txt": "B"}, refuse={"/tmp/a.txt"})
    harness = EditorHarness(
        width=100, height=6, file_port=files, file_path="/tmp/a.txt", initial_text="A"
    )
    harness.send("1")
    harness.send("C-x")
    harness.send("C-f")
    for char in "/tmp/b.txt":
        harness.send(char)
    harness.send("RET")
    harness.send("2")

    harness.send("C-x")
    harness.send("C-c")
    harness.send("y")  # a.txt refuses
    assert "permission-denied" in harness.frame.rows[-1]

    harness.send("y")  # b.txt saves cleanly
    assert files.files["/tmp/b.txt"] == "2B"
    # a.txt is still modified, so the gate is reached — and it says nothing
    # about a failure, because none happened on this keystroke.
    row = harness.frame.rows[-1]
    assert row.startswith(EXIT_PROMPT)
    assert "permission-denied" not in row
