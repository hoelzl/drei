from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InsertText:
    text: str


@dataclass(frozen=True, slots=True)
class ForwardChar:
    pass


@dataclass(frozen=True, slots=True)
class BackwardChar:
    pass


@dataclass(frozen=True, slots=True)
class KeyboardQuit:
    pass


@dataclass(frozen=True, slots=True)
class TextInserted:
    text: str
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class PointMoved:
    requested: int
    actual: int


@dataclass(frozen=True, slots=True)
class KeyboardQuitEvent:
    pass


@dataclass(frozen=True, slots=True)
class BufferObservation:
    buffer_id: str
    text: str
    point: int


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    events: tuple[TextInserted | PointMoved | KeyboardQuitEvent, ...]
    observation: BufferObservation
