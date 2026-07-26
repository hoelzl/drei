from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from drei.model import BufferId
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
    """`C-g`: abort what is in progress. Never ends the run.

    Emacs's `keyboard-quit` — the key a user presses when they do not know
    what is happening, and the one key guaranteed to destroy nothing. Until
    slice 17 this ended the editor and discarded every modified buffer, which
    inverted the reference editor's safest key into its most destructive one.
    Exiting is :class:`ExitEditor` — and since slice 18 that asks first, so
    `C-g` is also what abandons an exit half-way through its prompts.
    """


@dataclass(frozen=True, slots=True)
class ExitEditor:
    """`C-x C-c`: end the run.

    Separate from :class:`KeyboardQuit` because one event cannot mean both
    "the user aborted something" and "tear the process down" — conflating them
    is what made `C-g` destructive. The session records the request and the
    terminal loop acts on it; nothing here decides *how* the process ends.

    Since slice 18 the request is not automatically granted: the session
    offers to save each modified file-visiting buffer and confirms the exit
    if anything is still modified, so `EditorExited` follows this command by
    one keystroke or by several — or not at all (plan 0018 D1).
    """


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
class ResizeFrame:
    """The frame changed size (plan 0015 D3). Not a user command: it binds to
    no key and is dispatched by the loop when the input stream carries a
    resize.

    A command rather than a setter because frame size is *semantic*: it gates
    ``C-x 2`` (see ``_split_window``), so a transcript that omitted the resize
    would not reproduce a later split-or-no-op decision on replay.

    Shrinking below the size that would have permitted an existing split does
    **not** delete windows (D7): the layout survives and the frame renders
    what fits, so the operation is reversible.
    """

    width: int
    height: int


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

    Not a user edit. Since design 0005 D4 one delivery is **one dispatch**:
    it records the transcript fold as ``AgentTranscriptUpdated`` *and*
    appends the newly rendered suffix to the target buffer as
    ``AgentTextInserted``, so no code path can observe the fold advanced
    without the text having landed. Undo and kill-ring state stay untouched —
    an agent append creates no undo group (parity registry) and breaks no
    kill chain.

    Validated at construction so a machine-generated delivery (the §C ACP
    pump) cannot record a corrupt transcript fold: the list must be non-empty
    and every member must be a ``SessionEffect``.

    ``buffer_id`` names the **target** — the agent buffer this transcript
    belongs to (design 0004 D2). It is carried explicitly rather than resolved
    from focus at dispatch time: an implicit binding would make the transcript
    un-replayable across a rebinding, and appending to whatever happened to be
    focused is review 0001 finding 5.
    """

    effects: tuple[SessionEffect, ...]
    buffer_id: BufferId

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
    """Append agent-streamed text to ``buffer_id`` at end-of-buffer.

    Not a user edit: the buffer's ``modified`` flag is untouched and no undo
    group is created (undo of an external stream is incoherent with the
    fold-of-effects invariant — parity registry row). Point moves to the new
    end so a visible agent buffer tracks the stream.

    ``buffer_id`` names the target explicitly — see
    :class:`DeliverSessionEffects`.
    """

    text: str
    buffer_id: BufferId


@dataclass(frozen=True, slots=True)
class CreateAgentBuffer:
    """Bind an ACP session to a generated ``*agent*`` buffer (design 0004 D1).

    Idempotent: a session already bound keeps its buffer and the command is a
    silent no-op — a re-fold of ``SessionEstablished`` must not mint a second
    transcript. Otherwise a generated buffer named ``*agent*`` (``*agent*<2>``
    … via the existing collision rule) is created, visiting no file, and
    ``BufferCreated`` is recorded.

    Delivery-class (agent-initiated), exempt from the minibuffer gate: a
    swallowed creation would leave every later delivery for this session
    naming a buffer that does not exist, which is an error since design 0004
    D3. It does **not** switch focus — the agent buffer appearing must not
    yank the user out of their work.
    """

    acp_session_id: str


@dataclass(frozen=True, slots=True)
class DisplayBuffer:
    """Show a buffer in a window the user is not focused on (design 0005 D6).

    Emacs's ``display-buffer``, reduced to what one slice needs. A turn's
    transcript that exists but is nowhere on screen is a feature the user
    cannot use: `C-c a` would send a prompt and nothing visible would happen.

    The rule: split once if the frame holds a single window and the split gate
    permits, then put the buffer in the window after the focused one. **Focus
    never moves** — that is design 0004 D1's constraint, and it is why this is
    a separate command from ``CreateAgentBuffer`` rather than part of it: the
    buffer's *identity* is bound when the ACP session is established, and where
    it is *shown* is a presentation decision the caller makes.

    A frame too small to split is a silent no-op. The buffer still exists and
    `C-x b` still reaches it; what it does not do is destroy the user's only
    window to make room.

    Delivery-class (agent-initiated), exempt from the minibuffer gate: the
    session is established on the peer's schedule, and swallowing this because
    a prompt happened to be open would leave the transcript invisible for the
    rest of the run.
    """

    buffer_id: BufferId


@dataclass(frozen=True, slots=True)
class PromptAgent:
    """Open the minibuffer to compose a prompt for the agent (design 0005 D6).

    A user command like ``FindFile``, not a delivery: it opens a text prompt
    and nothing else. What the accepted text *means* is not the session's
    business — it emits ``AgentPromptSubmitted`` and the pump decides whether
    that requires spawning a child, waiting for a session, or holding the
    prompt behind a turn already in flight. The session holds no
    ``AcpMachine`` and learns nothing about ACP from this (0005 D7).
    """


@dataclass(frozen=True, slots=True)
class CreateGeneratedBuffer:
    """Mint a generated buffer that no ACP session owns (design 0005 D6).

    ``CreateAgentBuffer`` binds one buffer *per ACP session* because a
    transcript belongs to a session. The agent's diagnostics do not: stderr is
    a property of the child process, it arrives before any session exists, and
    it must survive the session ending. So it gets a plain generated buffer,
    named rather than bound.

    Delivery-class (agent-initiated), exempt from the minibuffer gate: the
    first thing a misconfigured agent writes to stderr is why it is about to
    die, and swallowing that because a prompt happened to be open would hide
    the one message that explains the failure.

    Idempotent by requested name, like ``CreateAgentBuffer`` is by session id:
    a second dispatch returns the existing buffer and records nothing. The
    *minted* id is not the requested name, though — the collision rule may
    have produced ``*agent-log*<2>`` — so a caller reads it back with
    ``generated_buffer_id`` rather than guessing.
    """

    name: str


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
class AbortPendingPermissions:
    """Clear pending permission presentation state on turn cancel (review
    0001 finding 10).

    Delivery-class (turn-initiated), exempt from the minibuffer gate. The
    machine's ``cancel()`` has already answered every pending request with
    the ``cancelled`` outcome, so this emits no ``PermissionDecided`` (that
    would double-answer a request the agent no longer awaits): it closes an
    open *choice* prompt (recorded as ``MinibufferAborted``) and drains the
    queue so no prompt is ever presented for a dead turn. An open *text*
    prompt is user state, not turn state, and is left untouched.
    """


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
    """A save that failed at the file port (or never reached it).

    ``error`` is a normalized, Drei-owned token (``not-found``,
    ``permission-denied``, ``io-error``, or ``no-file`` for a buffer with no
    file path — then ``path`` carries the buffer name), never raw exception
    text, so replay outcomes and echo text are platform-independent.
    """

    path: str
    error: str


@dataclass(frozen=True, slots=True)
class KeyboardQuitEvent:
    """The user aborted something with `C-g`. Renders as `Quit`.

    Not an exit signal. It was one until slice 17, which is why the editor
    used to die on the key that means "never mind".
    """


@dataclass(frozen=True, slots=True)
class EditorExited:
    """The user asked to end the run, and the session agreed.

    The terminal loop's only exit condition. An event rather than a return
    value because the transcript is the record of what the user did, and
    "they quit" belongs in it.
    """


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
class BufferDisplayed:
    """A buffer was shown in ``window`` without focus moving there."""

    buffer_id: str
    window: int


@dataclass(frozen=True, slots=True)
class AgentPromptSubmitted:
    """The human accepted an agent prompt (design 0005 D6).

    An event, not a call: the session records what the user asked for and the
    pump reads it out of the outcome, exactly as it reads
    ``PermissionDecided``. Both directions across that seam are events, which
    is what keeps the protocol out of the session and the transcript a
    complete record of what the user did.
    """

    text: str


@dataclass(frozen=True, slots=True)
class AgentTranscriptUpdated:
    """One session-effects delivery, recorded for the transcript oracle.

    ``rendered`` is exactly the text this delivery appended to the agent
    buffer (the incremental suffix, not the whole transcript), so the
    buffer's agent text is reconstructible as the concatenation of every
    ``AgentTranscriptUpdated.rendered`` **targeting that buffer** — one of the
    two fold oracles (design 0003 §B.7 verify, scoped per buffer by design
    0004 D2; without ``buffer_id`` the oracle described no buffer once more
    than one could receive deliveries). ``effects`` carries the folded
    ``SessionEffect`` values for the second oracle (refolding through
    ``TranscriptFold.advance`` must reproduce the same text).
    """

    effects: tuple[SessionEffect, ...]
    rendered: str
    buffer_id: str


@dataclass(frozen=True, slots=True)
class AgentTextInserted:
    """Agent text appended at end-of-buffer; ``before`` is the pre-insert
    buffer end, ``after`` the new end. ``buffer_id`` names the buffer that
    changed — deliveries do not target the focused buffer (design 0004 D2)."""

    text: str
    before: int
    after: int
    buffer_id: str


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
class FrameResized:
    """The frame's size changed to ``width`` x ``height`` (plan 0015 D3).

    Recorded on every resize, including one that changes nothing about the
    window layout, because the size is an input to a later command's outcome
    and replay must see it.
    """

    width: int
    height: int


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
        | EditorExited
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
        | FrameResized
        | OpenFailed
        | AgentPromptSubmitted
        | BufferDisplayed
        | AgentTranscriptUpdated
        | AgentTextInserted,
        ...,
    ]
    observation: BufferObservation
