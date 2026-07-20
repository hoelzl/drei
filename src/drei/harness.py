from __future__ import annotations

from drei.commands import (
    BufferObservation,
    BufferSaved,
    CommandOutcome,
    KeyboardQuitEvent,
    SaveFailed,
)
from drei.files import FilePort
from drei.keys import PendingKey, UnresolvedKey, resolve
from drei.model import Buffer, BufferId, BufferValue
from drei.render import Frame, render
from drei.session import EditorSession


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
        self._session = EditorSession(Buffer(buffer_id, value), file_port=file_port)
        self._width = width
        self._height = height
        self._pending: str | None = None
        self._outcomes: list[CommandOutcome] = []
        self._unresolved: list[UnresolvedKey] = []
        self._echo = ""
        self._frame = self._render_frame()

    def send(self, key: str) -> CommandOutcome | None:
        """Dispatch one key; return its outcome, or None if unresolved/pending."""
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

    @staticmethod
    def _echo_for(outcome: CommandOutcome) -> str:
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
        return render(
            self.observation,
            width=self._width,
            height=self._height,
            echo=self._echo,
        )
