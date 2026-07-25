from __future__ import annotations

from drei.commands import (
    BufferObservation,
    BufferSaved,
    CommandOutcome,
    KeyboardQuitEvent,
    MinibufferAbort,
    MinibufferAccept,
    MinibufferBackspace,
    MinibufferInput,
    ResizeFrame,
    SaveFailed,
)
from drei.files import FilePort
from drei.keys import PendingKey, UnresolvedKey, resolve
from drei.model import Buffer, BufferId, BufferValue
from drei.render import Frame, render_session
from drei.session import Command, EditorSession


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
            self._frame = self._render_frame()
            return outcome
        resolved = resolve(self._pending, key)
        if isinstance(resolved, PendingKey):
            self._pending = resolved.prefix
            return None
        self._pending = None
        if isinstance(resolved, UnresolvedKey):
            self._unresolved.append(resolved)
            return None
        outcome = self._session.dispatch(resolved)
        self._outcomes.append(outcome)
        self._echo = self._echo_for(outcome)
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
        self._width = width
        self._height = height
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
        # TODO: [tech-debt] TD-4 — only these three events echo. Every other
        # failure (notably OpenFailed from a C-x C-f that hit a permission
        # error) closes the minibuffer with a blank echo row, which reads as
        # a successful no-op. Needs the echo-message slice; see
        # docs/technical-debt.md.
        for event in outcome.events:
            if isinstance(event, KeyboardQuitEvent):
                return "Quit"
            if isinstance(event, BufferSaved):
                return f"Wrote {event.path}"
            if isinstance(event, SaveFailed):
                return f"{event.path}: {event.error}"
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
        )
