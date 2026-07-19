from __future__ import annotations

from drei.commands import BufferObservation, CommandOutcome, KeyboardQuitEvent
from drei.keys import UnresolvedKey, resolve
from drei.model import Buffer, BufferId, BufferValue
from drei.render import Frame, render
from drei.session import EditorSession


class EditorHarness:
    """In-process adapter over the production session, resolver, and renderer.

    Contains no edit, movement, or render logic of its own.
    """

    def __init__(self, width: int = 80, height: int = 24) -> None:
        self._session = EditorSession(Buffer(BufferId("scratch"), BufferValue("", 0)))
        self._width = width
        self._height = height
        self._outcomes: list[CommandOutcome] = []
        self._unresolved: list[UnresolvedKey] = []
        self._echo = ""
        self._frame = self._render_frame()

    def send(self, key: str) -> CommandOutcome | None:
        """Dispatch one symbolic key; return its outcome, or None if unresolved."""
        resolved = resolve(key)
        if isinstance(resolved, UnresolvedKey):
            self._unresolved.append(resolved)
            return None
        outcome = self._session.dispatch(resolved)
        self._outcomes.append(outcome)
        if any(isinstance(e, KeyboardQuitEvent) for e in outcome.events):
            self._echo = "Quit"
        self._frame = self._render_frame()
        return outcome

    @property
    def observation(self) -> BufferObservation:
        current = self._session.buffer.current
        return BufferObservation(
            buffer_id=self._session.buffer.buffer_id.value,
            text=current.text,
            point=current.point,
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
