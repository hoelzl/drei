from __future__ import annotations

from drei.commands import (
    BufferObservation,
    BufferSaved,
    CommandOutcome,
    FrameResized,
    KeyboardQuitEvent,
    Message,
    MinibufferAbort,
    MinibufferAborted,
    MinibufferAccept,
    MinibufferBackspace,
    MinibufferInput,
    OpenFailed,
    ResizeFrame,
    SaveFailed,
)
from drei.files import FilePort
from drei.keys import PendingKey, UnresolvedKey, resolve
from drei.model import Buffer, BufferId, BufferValue
from drei.render import Frame, render_session
from drei.session import Command, EditorSession

# The one token→text table (plan 0019 D1): the session emits Drei-owned
# tokens, this adapter owns the English. Seeded with the vocabulary the
# slice's parity-registry rows fix; each entry earns its behavioral test as
# its emitter lands (V2–V5).
_MESSAGE_TEXT = {
    "answer-y-or-n": "Please answer y or n",
    "end-of-buffer": "End of buffer",
    "mark-not-set": "The mark is not set now, or there is no region",
    "no-further-undo": "No further undo information",
    "previous-command-not-a-yank": "Previous command was not a yank",
    "too-small-for-splitting": "Too small for splitting",
}


def _message_text(token: str, subject: str | None = None) -> str:
    """Format one message token for the echo row.

    An unknown token fails visible — rendered as itself — rather than
    raising mid-frame or going blank: a missing table entry is a programming
    error the suite should catch, never a reason the user sees nothing.
    """
    text = _MESSAGE_TEXT.get(token, token)
    return f"{subject}: {text}" if subject is not None else text


class EditorHarness:
    """In-process adapter over the production session, resolver, and renderer.

    Contains no edit, movement, or render logic of its own.
    """

    def __init__(
        self,
        width: int = 80,
        height: int = 24,
        *,
        file_port: FilePort | None = None,
        file_path: str | None = None,
        initial_text: str = "",
    ) -> None:
        buffer_id = BufferId(
            file_path.replace("\\", "/").rsplit("/", 1)[-1] if file_path else "scratch"
        )
        value = BufferValue(text=initial_text, point=0, file_path=file_path)
        self._session = EditorSession(
            Buffer(buffer_id, value),
            file_port=file_port,
            frame_size=(width, height),
        )
        self._width = width
        self._height = height
        self._pending: str | None = None
        self._outcomes: list[CommandOutcome] = []
        self._unresolved: list[UnresolvedKey] = []
        self._echo = ""
        self._note = ""
        self._frame = self._render_frame()

    def send(self, key: str) -> CommandOutcome | None:
        """Dispatch one key; return its outcome, or None if unresolved/pending.

        While the minibuffer is active, keys route directly to minibuffer
        commands (the single routing site — keys.resolve stays pure); any
        pending prefix is dropped (a C-x typed before activation dies).
        """
        if self._session.minibuffer is not None:
            self._pending = None
            command = self._minibuffer_command(key)
            if command is None:
                return None  # control/meta keys ignored while active
            outcome = self._session.dispatch(command)
            self._outcomes.append(outcome)
            self._echo = self._echo_for(outcome)
            self._note = self._note_for(outcome)
            self._frame = self._render_frame()
            return outcome
        resolved = resolve(self._pending, key)
        if isinstance(resolved, PendingKey):
            self._pending = resolved.prefix
            return None
        self._pending = None
        if isinstance(resolved, UnresolvedKey):
            self._unresolved.append(resolved)
            # Row 134 (plan 0019 D7): "<chord> is undefined" is composed here,
            # in the harness — no command reaches the session for it to speak
            # about. `_note` is deliberately NOT recomputed: it is non-empty
            # only while a minibuffer is open, and an open minibuffer routes
            # keys to `_minibuffer_command` before `resolve` ever runs — so
            # the note is always "" at this point (slice-19 review finding 5).
            self._echo = f"{resolved.key} is undefined"
            self._frame = self._render_frame()
            return None
        outcome = self._session.dispatch(resolved)
        self._outcomes.append(outcome)
        self._echo = self._echo_for(outcome)
        self._note = self._note_for(outcome)
        self._frame = self._render_frame()
        return outcome

    def resize(self, width: int, height: int) -> CommandOutcome:
        """Apply a new frame size (plan 0015 V3).

        Deliberately *not* routed through :meth:`send`: a resize is not a key,
        so the minibuffer gate never sees it. Frame size is a property of the
        terminal rather than of input focus — swallowing it while a prompt is
        open would leave the frame rendering against a stale size for as long
        as the prompt stayed open, and the prompt itself lives on the echo row
        whose position depends on the height.

        The echo message is left alone: a resize is not a user action and must
        not wipe an outstanding message the way a command does.
        """
        outcome = self._session.dispatch(ResizeFrame(width, height))
        self._outcomes.append(outcome)
        for event in outcome.events:
            if isinstance(event, FrameResized):
                self._width = event.width
                self._height = event.height
        self._frame = self._render_frame()
        return outcome

    def apply(self, command: Command) -> CommandOutcome:
        """Dispatch a command that did not come from a key (design 0005 D3).

        The pump's seam. Deliberately *not* routed through :meth:`send`, for
        the same reason :meth:`resize` is not: the minibuffer gate routes
        keys, and an agent delivery is not one — the session's own gate
        already exempts delivery-class commands, and a second gate here would
        swallow them before it saw them.

        The echo message is left alone. Agent output arriving is not a user
        action, so it must not wipe a message the user has not read yet; the
        same rule a resize follows.
        """
        outcome = self._session.dispatch(command)
        self._outcomes.append(outcome)
        self._frame = self._render_frame()
        return outcome

    def agent_buffer_id(self, acp_session_id: str) -> BufferId | None:
        """The buffer bound to an ACP session (design 0004 D1).

        Exposed rather than reached for: the binding is the session's, and
        nothing else may guess an agent buffer's identity.
        """
        return self._session.agent_buffer_id(acp_session_id)

    def generated_buffer_id(self, name: str) -> BufferId | None:
        """The buffer ``CreateGeneratedBuffer(name)`` minted (design 0005 D6)."""
        return self._session.generated_buffer_id(name)

    @staticmethod
    def _minibuffer_command(key: str) -> Command | None:
        """Map a symbolic key to a minibuffer command; None = ignored."""
        if key == "RET":
            return MinibufferAccept()
        if key == "DEL":
            return MinibufferBackspace()
        if key == "C-g":
            return MinibufferAbort()
        if len(key) == 1 and key.isprintable():
            return MinibufferInput(key)
        return None

    @staticmethod
    def _echo_for(outcome: CommandOutcome) -> str:
        # The one echo-derivation site (plan 0019 D1): every user-visible
        # message is derived from the outcome's events here — never inline in
        # the session, never a ninth ad-hoc branch.
        for event in outcome.events:
            if isinstance(event, KeyboardQuitEvent):
                return "Quit"
            if isinstance(event, MinibufferAborted):
                # `C-g` at a prompt (row 92). Reachable only from genuine
                # aborts: the gate refusal carries ExitRefused instead (D4),
                # so "Quit" here is the truth, not a euphemism.
                return "Quit"
            if isinstance(event, BufferSaved):
                return f"Wrote {event.path}"
            if isinstance(event, SaveFailed | OpenFailed):
                return _message_text(event.error, event.path)
            if isinstance(event, Message):
                return _message_text(event.token, event.subject)
        return ""

    def _note_for(self, outcome: CommandOutcome) -> str:
        """The message that rides an open prompt (plan 0019 D3), or "".

        Only the message class — a failure, or a refusal to act — belongs on
        a prompt: `Wrote …` is an echo, and `Quit` comes from a `C-g` that
        closes the prompt rather than riding one. Nothing rides a closed
        prompt: a `C-x C-f` failure leaves no prompt behind, and its text is
        the echo row's alone. Recomputed per command (D6); `resize` and
        `apply` leave it alone for the same reason they leave the echo.
        """
        if self._session.minibuffer is None:
            return ""
        for event in outcome.events:
            if isinstance(event, SaveFailed | OpenFailed):
                return _message_text(event.error, event.path)
            if isinstance(event, Message):
                return _message_text(event.token, event.subject)
        return ""

    @property
    def observation(self) -> BufferObservation:
        current = self._session.buffer.current
        return BufferObservation(
            buffer_id=self._session.buffer.buffer_id.value,
            text=current.text,
            point=current.point,
            file_path=current.file_path,
            modified=current.modified,
            mark=current.mark,
            minibuffer=self._session.minibuffer,
            minibuffer_prompt=self._session.minibuffer_prompt,
        )

    @property
    def frame(self) -> Frame:
        return self._frame

    @property
    def outcomes(self) -> tuple[CommandOutcome, ...]:
        return tuple(self._outcomes)

    @property
    def unresolved(self) -> tuple[UnresolvedKey, ...]:
        return tuple(self._unresolved)

    def _render_frame(self) -> Frame:
        return render_session(
            self._session.session_observation(),
            width=self._width,
            height=self._height,
            echo=self._echo,
            note=self._note,
        )
