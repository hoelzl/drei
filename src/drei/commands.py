from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from drei.process import ProcessResult


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
class FindFile:
    pass


@dataclass(frozen=True, slots=True)
class MinibufferInput:
    char: str


@dataclass(frozen=True, slots=True)
class MinibufferBackspace:
    pass


@dataclass(frozen=True, slots=True)
class MinibufferAccept:
    pass


@dataclass(frozen=True, slots=True)
class MinibufferAbort:
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


@dataclass(frozen=True, slots=True)
class DeliverProcessOutput:
    """External delivery: an already-captured process result enters the session.

    Not a user edit. The session records it as one immutable event; buffer,
    undo, and kill-ring state are untouched. Exactly one of ``result`` /
    ``error`` is set: ``result`` is the captured run, ``error`` is a
    normalized token (``not-found``, ``permission-denied``, ``io-error``,
    ``timeout``) when the launch itself failed. Validated at construction so
    machine-generated deliveries (the ACP pump) cannot record corrupt
    provenance into the transcript.
    """

    argv: tuple[str, ...]
    result: ProcessResult | None = None  # None on launch failure
    error: str | None = None

    _ERROR_TOKENS: ClassVar[frozenset[str]] = frozenset(
        {"not-found", "permission-denied", "io-error", "timeout"}
    )

    def __post_init__(self) -> None:
        if (self.result is None) == (self.error is None):
            raise ValueError(
                "exactly one of result / error must be set on a process delivery"
            )
        if self.result is not None and self.result.argv != self.argv:
            raise ValueError(
                f"result argv {self.result.argv!r} != delivery argv {self.argv!r}"
            )
        if self.error is not None and self.error not in self._ERROR_TOKENS:
            raise ValueError(
                f"error must be one of {sorted(self._ERROR_TOKENS)}, got {self.error!r}"
            )


@dataclass(frozen=True, slots=True)
class ProcessOutputRecorded:
    """One process delivery, recorded for the transcript oracle.

    Carries lengths and status, not full output, so the fold stays cheap and
    goldens stay stable. ``status`` is ``ok`` / ``nonzero-exit`` / a
    normalized launch-error token.
    """

    argv: tuple[str, ...]
    exit_code: int
    stdout_len: int
    stderr_len: int
    status: str


@dataclass(frozen=True, slots=True)
class MinibufferOpened:
    prompt: str


@dataclass(frozen=True, slots=True)
class MinibufferAborted:
    pass


@dataclass(frozen=True, slots=True)
class BufferOpened:
    path: str
    text_len: int


@dataclass(frozen=True, slots=True)
class OpenFailed:
    """A find-file read that failed at the file port.

    ``error`` is a normalized, Drei-owned token (same vocabulary as
    ``SaveFailed``), never raw exception text.
    """

    path: str
    error: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BufferObservation:
    buffer_id: str
    text: str
    point: int
    file_path: str | None = None
    modified: bool = False
    mark: int | None = None
    minibuffer: str | None = None
    minibuffer_prompt: str | None = None


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
        | KeyboardQuitEvent
        | ProcessOutputRecorded
        | MinibufferOpened
        | MinibufferAborted
        | BufferOpened
        | OpenFailed,
        ...,
    ]
    observation: BufferObservation
