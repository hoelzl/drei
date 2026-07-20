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
class SetMark:
    pass


@dataclass(frozen=True, slots=True)
class KillRegion:
    pass


@dataclass(frozen=True, slots=True)
class CopyRegionAsKill:
    pass


@dataclass(frozen=True, slots=True)
class ExchangePointAndMark:
    pass


@dataclass(frozen=True, slots=True)
class Undo:
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
class MarkSet:
    position: int


@dataclass(frozen=True, slots=True)
class RegionKilled:
    text: str
    before: int
    after: int
    direction: str


@dataclass(frozen=True, slots=True)
class RegionCopied:
    text: str


@dataclass(frozen=True, slots=True)
class MarkExchanged:
    point_before: int
    mark_before: int


@dataclass(frozen=True, slots=True)
class TextUndone:
    start: int
    removed_text: str
    inserted_text: str
    point_before: int
    point_after: int
    mark_before: int | None
    mark_after: int | None


@dataclass(frozen=True, slots=True)
class TextRedone:
    start: int
    removed_text: str
    inserted_text: str
    point_before: int
    point_after: int
    mark_before: int | None
    mark_after: int | None


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
    mark: int | None = None


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    events: tuple[
        TextInserted
        | PointMoved
        | TextKilled
        | TextYanked
        | TextYankPopped
        | MarkSet
        | RegionKilled
        | RegionCopied
        | MarkExchanged
        | TextUndone
        | TextRedone
        | BufferSaved
        | SaveFailed
        | KeyboardQuitEvent,
        ...,
    ]
    observation: BufferObservation
