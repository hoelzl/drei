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
    FindFile,
    InsertAgentText,
    InsertText,
    KeyboardQuitEvent,
    MinibufferAbort,
    MinibufferAborted,
    MinibufferAccept,
    MinibufferInput,
    MinibufferOpened,
    PromptPermission,
    SaveBuffer,
    SaveFailed,
    SetMark,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession

SAVE_PROMPT = "Save file {}? (y or n) "
EXIT_PROMPT = "Modified buffers exist; exit anyway? (y or n) "


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
    assert MinibufferAborted() in outcome.events
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
    assert MinibufferOpened(EXIT_PROMPT) in outcome.events


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
    """Emacs echoes `Please answer y or n` here; Drei is silent (TD-4).

    What matters either way is that the prompt does not resolve — and that the
    key is not appended as *text*, which is what a missed branch would do:
    `Save file …? z` on the echo row rather than a crash.
    """
    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferInput("z"))
    assert outcome.events == ()
    assert not _exited(outcome)
    assert session.minibuffer == ""
    assert session.minibuffer_prompt == SAVE_PROMPT.format("/tmp/notes.txt")


def test_ret_is_not_a_default_yes() -> None:
    """The exit prompts are the last guard before losing work, so a habitual
    `RET` must not answer them (B.8 finding 9's reasoning, D5)."""
    session = _session()
    session.dispatch(InsertText("x"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferAccept())
    assert outcome.events == ()
    assert session.minibuffer_prompt == SAVE_PROMPT.format("/tmp/notes.txt")


def test_ret_is_not_a_default_yes_at_the_gate() -> None:
    session = _session(path=None, text="")
    session.dispatch(InsertText("hello"))
    session.dispatch(ExitEditor())
    outcome = session.dispatch(MinibufferAccept())
    assert outcome.events == ()
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
