from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from drei.process import ProcessResult

if TYPE_CHECKING:
    from drei.acp.machine import (
        PermissionDecision,
        PermissionRequested,
        SessionEffect,
    )
    from drei.acp.messages import RequestId


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
class SwitchBuffer:
    """``C-x b``: prompt for a buffer name and switch to it (design 0003
    §A.2). The minibuffer carries the most-recently-used other buffer as its
    default."""

    pass


@dataclass(frozen=True, slots=True)
class SplitWindow:
    """``C-x 2``: split the focused window into two stacked halves over the
    same buffer (design 0003 §A.2, plan 0012 D3)."""

    pass


@dataclass(frozen=True, slots=True)
class OtherWindow:
    """``C-x o``: move focus to the next window cyclically."""

    pass


@dataclass(frozen=True, slots=True)
class DeleteOtherWindows:
    """``C-x 1``: collapse the layout to the focused window."""

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
class DeliverSessionEffects:
    """External delivery: one ``AcpMachine.handle`` call's ``SessionEffect``
    list enters the session (design 0003 §B.7).

    Not a user edit. The session records it as one immutable event; buffer,
    undo, and kill-ring state are untouched. Validated at construction so a
    machine-generated delivery (the §C ACP pump) cannot record a corrupt
    transcript fold: the list must be non-empty and every member must be a
    ``SessionEffect``.
    """

    effects: tuple[SessionEffect, ...]

    def __post_init__(self) -> None:
        from drei.acp.machine import SessionEffect as _SessionEffect

        if not self.effects:
            raise ValueError("a session-effects delivery must be non-empty")
        for effect in self.effects:
            if not isinstance(effect, _SessionEffect):
                raise ValueError(
                    f"delivery members must be SessionEffect values, got {effect!r}"
                )


@dataclass(frozen=True, slots=True)
class InsertAgentText:
    """Append agent-streamed text to the agent buffer at end-of-buffer.

    Not a user edit: the buffer's ``modified`` flag is untouched and no undo
    group is created (undo of an external stream is incoherent with the
    fold-of-effects invariant — parity registry row). Point moves to the new
    end so a visible agent buffer tracks the stream.
    """

    text: str


@dataclass(frozen=True, slots=True)
class PromptPermission:
    """Open the choice minibuffer for a ``session/request_permission`` (B.8).

    Delivery-class (agent-initiated), exempt from the minibuffer gate: a
    request arriving while another prompt is open must queue rather than be
    swallowed — a dropped permission prompt would hang the agent (the B.7
    delivery-bypass parity row, extended). The prompt presents the request's
    ``PermissionOption``\\ s and resolves to one ``PermissionDecision`` (or
    ``Cancelled`` on abort), recorded as ``PermissionDecided``.
    """

    request: PermissionRequested


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
class PermissionDecided:
    """The human resolved a permission prompt (B.8). Carries the decision the
    machine's ``resolve_permission`` maps onto the 0.9.0 response."""

    request_id: RequestId
    decision: PermissionDecision


@dataclass(frozen=True, slots=True)
class AgentTranscriptUpdated:
    """One session-effects delivery, recorded for the transcript oracle.

    ``rendered`` is exactly the text this delivery appended to the agent
    buffer (the incremental suffix, not the whole transcript), so the
    buffer's agent text is reconstructible as the concatenation of every
    ``AgentTranscriptUpdated.rendered`` in the transcript — one of the two
    fold oracles (design 0003 §B.7 verify). ``effects`` carries the folded
    ``SessionEffect`` values for the second oracle (refolding through
    ``TranscriptFold.advance`` must reproduce the same text).
    """

    effects: tuple[SessionEffect, ...]
    rendered: str


@dataclass(frozen=True, slots=True)
class AgentTextInserted:
    """Agent text appended at end-of-buffer; ``before`` is the pre-insert
    buffer end, ``after`` the new end."""

    text: str
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class BufferOpened:
    path: str
    text_len: int


@dataclass(frozen=True, slots=True)
class BufferCreated:
    """A new buffer entered the session's buffer set (design 0003 §A.2).

    ``file_path`` is None for name-created buffers (``C-x b`` to an unknown
    name); file buffers carry their path. Buffer creation is recorded once,
    at creation — the buffer set is derivable from the transcript.
    """

    buffer_id: str
    file_path: str | None


@dataclass(frozen=True, slots=True)
class BufferSelected:
    """The current buffer changed (find-file reuse, ``C-x b``).

    Recorded on every switch whose target differs from the current buffer;
    the current-buffer fold of the transcript is the oracle for which buffer
    is live.
    """

    buffer_id: str


@dataclass(frozen=True, slots=True)
class WindowSplit:
    """The focused window was split in two (``C-x 2``); ``count`` is the new
    total window count."""

    count: int


@dataclass(frozen=True, slots=True)
class WindowFocusChanged:
    """Window focus moved (``C-x o`` or a buffer switch landing in another
    window); ``index`` is the new focused window, ``buffer_id`` what it
    shows."""

    index: int
    buffer_id: str


@dataclass(frozen=True, slots=True)
class WindowsCollapsed:
    """``C-x 1`` collapsed the layout to the focused window."""

    pass


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
class WindowObservation:
    """One pane (design 0003 §A.2): a buffer snapshot plus this window's own
    point/mark (window-point, plan 0012 D3/D5)."""

    buffer: BufferObservation
    point: int
    mark: int | None


@dataclass(frozen=True, slots=True)
class SessionObservation:
    """Derived read model over the whole session (plan 0012 D5): the buffer
    names, one WindowObservation per window top-to-bottom, the focused
    index, and the shared minibuffer state. CommandOutcome keeps returning
    the legacy BufferObservation — the focused window's view — so existing
    consumers are untouched."""

    buffers: tuple[str, ...]
    windows: tuple[WindowObservation, ...]
    focused: int
    minibuffer: str | None
    minibuffer_prompt: str | None


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
        | PermissionDecided
        | BufferOpened
        | BufferCreated
        | BufferSelected
        | WindowSplit
        | WindowFocusChanged
        | WindowsCollapsed
        | OpenFailed
        | AgentTranscriptUpdated
        | AgentTextInserted,
        ...,
    ]
    observation: BufferObservation
