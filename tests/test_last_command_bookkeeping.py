"""Slice 21 (plan 0021, review 0002 cluster A): last-command bookkeeping keys
on user-issued commands only.

The fold's ``intervened`` used to key on event shapes, so any command
emitting a semantic event — including ones the user never issued — broke the
kill-append chain, yank-pop state, and the undo descent of the focused
buffer. The critical arm: a resize between two undos flipped the third into
a *redo* — the buffer moved forward when the user pressed undo (review 0002
finding 1; review-0001 finding 2's class, driven by terminal/peer timing).

Emacs's model: the command loop sets ``last-command`` only for commands; a
resize runs no command, and peer output is not a command.
"""

from conftest import FakeFilePort

from drei.acp.machine import PermissionRequested
from drei.commands import (
    AbortPendingPermissions,
    DisplayBuffer,
    InsertText,
    KillLine,
    MinibufferAbort,
    PromptPermission,
    ResizeFrame,
    Undo,
)
from drei.harness import EditorHarness
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session(text: str = "", point: int = 0) -> EditorSession:
    return EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text=text, point=point)),
        file_port=FakeFilePort(),
    )


def _type(session: EditorSession, text: str) -> None:
    for char in text:
        session.dispatch(InsertText(char))


class TestUserCommandBookkeeping:
    """Plan 0021's acceptance scenario (three arms) plus the permission
    presentation arm. Peer/housekeeping dispatches — a resize, an agent-side
    DisplayBuffer, a permission presentation — are not commands in Emacs's
    last-command sense and must not intervene."""

    def test_a_resize_between_undos_does_not_flip_the_descent(self) -> None:
        """Acceptance arm 1, harness level (where review 0002 repro'd it)."""
        harness = EditorHarness(width=80, height=24)
        for char in "abc":
            harness.send(char)
        harness.send("C-/")
        harness.send("C-/")
        assert harness.observation.text == "a"
        harness.resize(100, 30)
        harness.send("C-/")
        # Still descending: the last group ("a") is undone. Pre-slice the
        # resize's FrameResized event flipped the descent and this REDID "b".
        assert harness.observation.text == ""

    def test_a_resize_between_kills_keeps_the_append_chain(self) -> None:
        """Acceptance arm 2: consecutive kills append across a resize."""
        session = _session("one\ntwo\n")
        session.dispatch(KillLine())
        assert session.kill_ring == ("one",)
        session.dispatch(ResizeFrame(100, 30))
        session.dispatch(KillLine())
        # The newline kill appended to "one". Pre-slice the resize split the
        # chain: ("\n", "one").
        assert session.kill_ring == ("one\n",)

    def test_a_display_buffer_between_undos_does_not_flip_the_descent(
        self,
    ) -> None:
        """Acceptance arm 3: the agent handshake's DisplayBuffer is not a
        user command (the pump dispatches it on session bind)."""
        session = _session()
        _type(session, "abc")
        session.dispatch(Undo())
        session.dispatch(Undo())
        assert session.buffer.current.text == "a"
        session.dispatch(DisplayBuffer(BufferId("scratch")))
        session.dispatch(Undo())
        assert session.buffer.current.text == ""

    def test_a_display_buffer_through_the_pump_seam_does_not_flip_the_descent(
        self,
    ) -> None:
        """Pump-seam arm (plan 0021's acceptance criterion: "pinned at session
        and pump level"): harness.apply is exactly how the pump dispatches
        DisplayBuffer — review 0002's repro path. The split SUCCEEDS here, so
        the dispatch carries a WindowSplit semantic event, and the descent
        still survives."""
        harness = EditorHarness(width=80, height=24)
        for char in "abc":
            harness.send(char)
        harness.send("C-/")
        harness.send("C-/")
        harness.apply(DisplayBuffer(BufferId("scratch")))
        harness.send("C-/")
        assert harness.observation.text == ""

    def test_a_permission_presentation_and_its_abort_do_not_flip_the_descent(
        self,
    ) -> None:
        """The peer's presentation (PromptPermission) and the turn-cancel
        sweep that closes it (AbortPendingPermissions) are both peer-class:
        an undo after the whole exchange still descends. The user's own
        answer or C-g WOULD intervene (plan 0021 D4) — that is pinned in
        test_the_users_answer_still_intervenes below."""
        session = _session()
        _type(session, "abc")
        session.dispatch(Undo())
        session.dispatch(Undo())
        assert session.buffer.current.text == "a"
        session.dispatch(PromptPermission(PermissionRequested(7, {"options": []})))
        session.dispatch(AbortPendingPermissions())
        session.dispatch(Undo())
        assert session.buffer.current.text == ""

    def test_the_users_answer_still_intervenes(self) -> None:
        """Plan 0021 D4 / Q1 (owner-resolved: keep + registry row): the
        user's own commands keep breaking the descent, per Emacs's
        last-command model where C-g is a real command. At a choice prompt
        the user's C-g arrives as MinibufferAbort (the harness's routing),
        which is the command pinned here."""
        session = _session()
        _type(session, "abc")
        session.dispatch(Undo())
        session.dispatch(Undo())
        assert session.buffer.current.text == "a"
        session.dispatch(PromptPermission(PermissionRequested(7, {"options": []})))
        session.dispatch(MinibufferAbort())
        session.dispatch(Undo())
        # The user's C-g intervened, so this undo REDOES.
        assert session.buffer.current.text == "ab"
