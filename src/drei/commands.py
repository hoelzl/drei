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
class SaveBuffer:
    pass


@dataclass(frozen=True, slots=True)
class KillLine:
    pass


@dataclass(frozen=True, slots=True)
class Yank:
    pass


@dataclass(frozen=True, slots=True)
class YankPop:
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
class TextKilled:
    text: str
    before: int
    after: int
    direction: str


@dataclass(frozen=True, slots=True)
class TextYanked:
    text: str
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class TextYankPopped:
    old_text: str
    new_text: str
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class BufferSaved:
    path: str


@dataclass(frozen=True, slots=True)
class SaveFailed:
    """A save that failed at the file port.

    ``error`` is a normalized, Drei-owned token (``not-found``,
    ``permission-denied``, ``io-error``), never raw exception text, so
    replay outcomes and echo text are platform-independent.
    """

    path: str
    error: str


@dataclass(frozen=True, slots=True)
class KeyboardQuitEvent:
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class BufferObservation:
    buffer_id: str
    text: str
    point: int
    file_path: str | None = None
    modified: bool = False


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    events: tuple[
        TextInserted
        | PointMoved
        | TextKilled
        | TextYanked
        | TextYankPopped
        | BufferSaved
        | SaveFailed
        | KeyboardQuitEvent,
        ...,
    ]
    observation: BufferObservation
