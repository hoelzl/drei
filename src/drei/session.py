from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from drei.acp.machine import (
        AcpMachine,
        PermissionDecision,
        PermissionRequested,
        SessionEffect,
    )
    from drei.acp.messages import JsonValue, Message, RequestId

from drei.commands import (
    AbortPendingPermissions,
    AgentPromptSubmitted,
    AgentTextInserted,
    AgentTranscriptUpdated,
    BackwardChar,
    BufferCreated,
    BufferObservation,
    BufferOpened,
    BufferSaved,
    BufferSelected,
    CommandOutcome,
    CopyRegionAsKill,
    CreateAgentBuffer,
    CreateGeneratedBuffer,
    DeleteOtherWindows,
    DeliverProcessOutput,
    DeliverSessionEffects,
    ExchangePointAndMark,
    FindFile,
    ForwardChar,
    FrameResized,
    InsertAgentText,
    InsertText,
    KeyboardQuit,
    KeyboardQuitEvent,
    KillLine,
    KillRegion,
    MarkExchanged,
    MarkSet,
    MinibufferAbort,
    MinibufferAborted,
    MinibufferAccept,
    MinibufferBackspace,
    MinibufferInput,
    MinibufferOpened,
    OpenFailed,
    OtherWindow,
    PermissionDecided,
    PointMoved,
    ProcessOutputRecorded,
    PromptAgent,
    PromptPermission,
    RegionCopied,
    RegionKilled,
    ResizeFrame,
    SaveBuffer,
    SaveFailed,
    SessionObservation,
    SetMark,
    SplitWindow,
    SwitchBuffer,
    TextInserted,
    TextKilled,
    TextRedone,
    TextUndone,
    TextYanked,
    TextYankPopped,
    Undo,
    WindowFocusChanged,
    WindowObservation,
    WindowsCollapsed,
    WindowSplit,
    Yank,
    YankPop,
)
from drei.files import (
    CRLF,
    LF,
    FilePort,
    detect_line_ending,
    normalize_os_error,
    to_buffer_text,
    to_file_text,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.process import (
    ProcessPort,
    ProcessResult,
    ProcessTimedOut,
    normalize_process_error,
)

Command = (
    InsertText
    | ForwardChar
    | BackwardChar
    | SaveBuffer
    | KillLine
    | Yank
    | YankPop
    | SetMark
    | KillRegion
    | CopyRegionAsKill
    | ExchangePointAndMark
    | Undo
    | KeyboardQuit
    | DeliverProcessOutput
    | DeliverSessionEffects
    | InsertAgentText
    | CreateAgentBuffer
    | CreateGeneratedBuffer
    | PromptAgent
    | PromptPermission
    | AbortPendingPermissions
    | FindFile
    | SwitchBuffer
    | SplitWindow
    | OtherWindow
    | DeleteOtherWindows
    | ResizeFrame
    | MinibufferInput
    | MinibufferBackspace
    | MinibufferAccept
    | MinibufferAbort
)
Event = (
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
    | FrameResized
    | OpenFailed
    | AgentPromptSubmitted
    | AgentTranscriptUpdated
    | AgentTextInserted
)


def _make_group(
    command: Command, current: BufferValue, events: list[Event]
) -> _UndoGroup | None:
    """Build the inverse patch for a text-changing command; None otherwise.

    The event stream carries every span position, so the group is derived
    from the same evidence the transcript records.
    """
    for event in events:
        match event:
            case TextInserted(text=text, before=before):
                return _UndoGroup(
                    before,
                    "",
                    text,
                    current.point,
                    before + len(text),
                    current.mark,
                    _adjust_mark_insert(current.mark, before, len(text)),
                )
            case TextKilled(text=killed, before=before, after=after):
                return _UndoGroup(
                    before,
                    killed,
                    "",
                    current.point,
                    current.point,
                    current.mark,
                    _adjust_mark_delete(current.mark, before, after),
                )
            case RegionKilled(text=killed, before=lo):
                return _UndoGroup(
                    lo,
                    killed,
                    "",
                    current.point,
                    lo,
                    current.mark,
                    None,
                )
            case TextYanked(text=text, before=before):
                return _UndoGroup(
                    before,
                    "",
                    text,
                    current.point,
                    before + len(text),
                    current.mark,
                    _adjust_mark_insert(current.mark, before, len(text)),
                )
            case TextYankPopped(old_text=old, new_text=new, before=start, after=after):
                return _UndoGroup(
                    start,
                    old,
                    new,
                    current.point,
                    after,
                    current.mark,
                    _adjust_mark_insert(
                        _adjust_mark_delete(current.mark, start, start + len(old)),
                        start,
                        len(new),
                    ),
                )
            case _:
                continue
    return None


def _adjust_mark_insert(mark: int | None, at: int, count: int) -> int | None:
    """Emacs marker semantics for inserting `count` chars at `at`.

    Insertion before the mark shifts it right; insertion exactly at the
    mark keeps it before the inserted text (default insertion type).
    """
    if mark is None:
        return None
    if at < mark:
        return mark + count
    return mark


def _adjust_mark_delete(mark: int | None, start: int, end: int) -> int | None:
    """Emacs marker semantics for deleting [start, end).

    A mark after the deleted span shifts left; a mark inside clamps to the
    deletion start; a mark before it is untouched.
    """
    if mark is None:
        return None
    if mark >= end:
        return mark - (end - start)
    if mark > start:
        return start
    return mark


def _shift_index(file_text: str, index: int) -> int:
    """Map an index in CRLF file text onto the LF-normalized buffer text."""
    return index - file_text.count(CRLF, 0, index)


BufferKind = Literal["ordinary", "generated"]


def _visit(value: BufferValue) -> tuple[BufferValue, _BufferState]:
    """Prepare a buffer value that carries file text, and its state.

    One place decides what visiting a file means, for both entry points (the
    startup buffer and find-file): remember the file's line ending, hold the
    text LF-separated in the buffer, and record the text as the clean point
    unless the value arrived already modified — nothing then proves what the
    file holds (review 0001 findings 1 and 3).
    """
    eol = detect_line_ending(value.text)
    text = to_buffer_text(value.text, eol)
    if text != value.text:
        value = replace(
            value,
            text=text,
            point=_shift_index(value.text, value.point),
            mark=None if value.mark is None else _shift_index(value.text, value.mark),
        )
    return value, _BufferState(None if value.modified else value.text, eol)


KILL_RING_CAPACITY = 60
UNDO_CAPACITY = 100
# A window needs a modeline row plus at least two text rows (plan 0012 D4).
MIN_WINDOW_ROWS = 3


@dataclass(frozen=True, slots=True)
class _UndoGroup:
    """Inverse patch for one text-changing command (the redo patch is the
    same record read forward)."""

    start: int
    removed_text: str  # what the command deleted (re-inserted by undo)
    inserted_text: str  # what the command inserted (removed by undo)
    point_before: int
    point_after: int
    mark_before: int | None
    mark_after: int | None


@dataclass(frozen=True, slots=True)
class WindowValue:
    """One window: a layout view over a buffer with its own point/mark
    (design 0003 §A.2, plan 0012 D3). Window-point is NOT BufferValue.point:
    the buffer's point is the focused window's; each window stores its own
    so focus changes restore where that window was (Emacs window-point
    semantics, probed vs pinned 29.3 — plan 0012 evidence 4)."""

    buffer_id: BufferId
    point: int
    mark: int | None


class _NullFilePort:
    """Default port: every save fails with a normalized token."""

    def read(self, path: str) -> str:
        raise FileNotFoundError(path)

    def write(self, path: str, text: str) -> None:
        raise OSError("no file port configured")


class _NullProcessPort:
    """Default port: every launch fails with a normalized token."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        raise FileNotFoundError(argv[0] if argv else "")


class _BufferState:
    """Per-buffer editing state (design 0003 §A.2, plan 0012 D2).

    Everything Emacs scopes per buffer lives here: undo history/redo/descent,
    yank-pop chaining, the kill-append chain flag, the buffer's kind, and the
    file-facing facts (last-saved text, line ending). Session-global state
    (kill ring, transcript, process log, minibuffer, ports) stays on the
    session — the ring is global in Emacs (kill in one buffer, yank in
    another), pinned since slice 7.
    """

    __slots__ = (
        "undo_history",
        "undo_redo",
        "undo_descending",
        "yank_active",
        "yank_cursor",
        "yank_bounds",
        "last_was_kill",
        "saved_text",
        "eol",
        "kind",
    )

    def __init__(
        self,
        saved_text: str | None = None,
        eol: str = LF,
        kind: BufferKind = "ordinary",
    ) -> None:
        # What produced this buffer (design 0004 D3). "generated" means it was
        # produced by an effect and visits no file — today only agent buffers,
        # and only they accept a delivery. It lives here rather than on
        # BufferValue because it is a per-buffer session fact that never
        # varies per edit, the same category as saved_text and eol. §A.3's
        # read-only enforcement is the hook's other user.
        self.kind: BufferKind = kind
        # Buffer text as last read from or written to the file, or None when
        # that is unknown (a buffer handed to the session already modified).
        # The undo path derives the modified flag from it (finding 3).
        self.saved_text = saved_text
        # Line ending this buffer's file uses; the save writes it back
        # (finding 1). LF for buffers with no file text to imitate.
        self.eol = eol
        self.undo_history: list[_UndoGroup] = []  # applied groups, newest last
        self.undo_redo: list[_UndoGroup] = []  # undone groups, newest last
        self.undo_descending = False  # last command was an undo
        self.yank_active = False
        self.yank_cursor = 0
        self.yank_bounds = (0, 0)
        self.last_was_kill = False

    def break_chains(self) -> None:
        """Switching buffers intervenes like any other command in Emacs:
        kill-append and yank-pop chaining (last-command state) do not
        survive the switch. Undo history is per-buffer and is NOT touched —
        returning to a buffer resumes its own undo stack (probed vs pinned
        29.3, plan 0012 evidence 2/3)."""
        self.last_was_kill = False
        self.yank_active = False
        self.undo_descending = False


class EditorSession:
    def __init__(
        self,
        buffer: Buffer,
        file_port: FilePort | None = None,
        process_port: ProcessPort | None = None,
        frame_size: tuple[int, int] | None = None,
    ) -> None:
        # The startup buffer arrives holding raw file text (the CLI reads it
        # through the port); visiting it is the same operation as find-file.
        initial, initial_state = _visit(buffer.current)
        buffer.replace(initial)
        self._buffers: dict[BufferId, Buffer] = {buffer.buffer_id: buffer}
        self._current_id: BufferId = buffer.buffer_id
        self._states: dict[BufferId, _BufferState] = {buffer.buffer_id: initial_state}
        self._frame_size = frame_size
        self._files: FilePort = file_port if file_port is not None else _NullFilePort()
        self._processes: ProcessPort = (
            process_port if process_port is not None else _NullProcessPort()
        )
        self._transcript: list[Event] = []
        self._process_log: list[ProcessResult] = []
        self._kill_ring: list[str] = []
        self._minibuffer: str | None = None  # None = inactive
        self._minibuffer_prompt: str = ""
        # What MinibufferAccept does with the input: find-file opens a path,
        # switch-buffer selects/creates a buffer by name (plan 0012 D7).
        self._minibuffer_kind: str | None = None
        # Most-recently-used buffer names, most recent first (index 0 is the
        # current buffer). Maintained on every BufferSelected; the C-x b
        # empty-input default is index 1 (plan 0012 D7, Emacs other-buffer).
        self._mru: list[str] = [buffer.buffer_id.value]
        self._windows: tuple[WindowValue, ...] = (
            WindowValue(buffer.buffer_id, buffer.current.point, buffer.current.mark),
        )
        self._focused = 0
        # Choice-minibuffer state (B.8): when a permission prompt is open,
        # _choice holds the in-flight PermissionRequested and the minibuffer
        # is in choice mode (MinibufferInput maps a key to an option rather
        # than appending text). None in text mode / when inactive.
        self._choice: PermissionRequested | None = None
        # Permission requests that arrived while a prompt was open, in order.
        # Grows only while a prompt is open and drains on resolution; in
        # practice bounded by the agent's concurrent in-flight requests (a
        # hostile agent can flood it — accepted: each request is already in the
        # machine's in_flight_incoming, so this adds no new resource class).
        self._permission_queue: list[PermissionRequested] = []
        # Agent-transcript fold cache, per agent buffer (design 0003 §B.7,
        # keyed by target since design 0004 D5 — the fold is rendering state,
        # and one shared fold would let two ACP sessions close each other's
        # turns): a derived, reconstructible cache of the
        # AgentTranscriptUpdated event stream —
        # the same discipline as _process_log. The transcript remains
        # authoritative. The fold advances *inside* the dispatch that records
        # the delivery event, not after it (review 0001 finding 28 — this
        # comment used to claim otherwise): _render_effects runs a few lines
        # before the AgentTranscriptUpdated append. That is safe because the
        # two are one command — a caller never observes a state between them,
        # and replaying the event stream reconstructs the same fold. Since
        # design 0005 D4 the *whole* delivery is that one command: the append
        # happens in the same dispatch, so apply_session_effects is atomic in
        # the same sense rather than in a weaker one.
        from drei.acp.transcript import TranscriptFold

        self._agent_folds: dict[BufferId, TranscriptFold] = {}
        # ACP session id → its agent buffer (design 0004 D1). One buffer per
        # ACP session: the binding must be as fine-grained as the transcript
        # being folded, or a second session would append into the first's.
        self._agent_buffers: dict[str, BufferId] = {}
        # Generated buffers that no ACP session owns, by requested name
        # (design 0005 D6 — the agent's diagnostics). Recorded rather than
        # assumed, because the collision rule may have renamed the buffer:
        # a user with an ordinary buffer of the same name would otherwise
        # have agent output appended into their own text.
        self._generated_buffers: dict[str, BufferId] = {}

    @property
    def buffer(self) -> Buffer:
        """The current buffer (identity shell; plan 0012 D1)."""
        return self._buffers[self._current_id]

    @property
    def buffers(self) -> tuple[str, ...]:
        """Derived view: the session's buffer names, in creation order."""
        return tuple(buffer_id.value for buffer_id in self._buffers)

    @property
    def windows(self) -> tuple[WindowValue, ...]:
        """Derived view: the layout, top-to-bottom (plan 0012 D5)."""
        return self._windows

    @property
    def focused(self) -> int:
        """Index of the focused window in ``windows``."""
        return self._focused

    @property
    def _state(self) -> _BufferState:
        return self._states[self._current_id]

    @property
    def minibuffer(self) -> str | None:
        """Minibuffer input-so-far; None when inactive."""
        return self._minibuffer

    @property
    def minibuffer_prompt(self) -> str | None:
        """Prompt label while the minibuffer is active; None otherwise."""
        return self._minibuffer_prompt if self._minibuffer is not None else None

    def pending_permission_count(self) -> int:
        """Permission requests queued behind an open prompt (B.8)."""
        return len(self._permission_queue)

    @property
    def transcript(self) -> tuple[Event, ...]:
        return tuple(self._transcript)

    @property
    def kill_ring(self) -> tuple[str, ...]:
        """Newest-first view of the kill ring (derived cache, not the oracle)."""
        return tuple(self._kill_ring)

    @property
    def process_log(self) -> tuple[ProcessResult, ...]:
        """Captured process results, oldest-first.

        An independent cache, not a transcript fold: the ``ProcessOutputRecorded``
        events carry lengths/status only, so the full ``stdout``/``stderr`` here
        is richer than the transcript and not reconstructible from it. Only
        successful launches are logged (a launch failure records an event but
        appends nothing), and construction-time validation on
        ``DeliverProcessOutput`` keeps ``log[i]`` consistent with its event.
        """
        return tuple(self._process_log)

    def agent_buffer_id(self, acp_session_id: str) -> BufferId | None:
        """The agent buffer bound to an ACP session, or None if unbound
        (design 0004 D1). A caller reads it to build deliveries; nothing else
        may guess an agent buffer's identity."""
        return self._agent_buffers.get(acp_session_id)

    def generated_buffer_id(self, name: str) -> BufferId | None:
        """The buffer ``CreateGeneratedBuffer(name)`` minted, or None.

        The requested name is not the answer: the collision rule may have
        appended a ``<N>`` suffix. A caller that guessed would append agent
        output into whatever buffer happened to own the name.
        """
        return self._generated_buffers.get(name)

    def _pinned_target(self, command: Command, events: list[Event]) -> BufferId | None:
        """The buffer a command pins as its target, when it names or mints one.

        ``None`` means "the focused buffer" — the answer for every command
        except an agent delivery and the creation of an agent buffer.
        Deliveries name their target because a transcript binds to an agent
        buffer, not to whatever the human happens to be looking at (review
        0001 finding 5); ``CreateAgentBuffer`` mints one here, so it too edits
        no buffer the user is looking at.

        A delivery naming a buffer that does not exist, or one that is not
        generated, raises ``ValueError`` before anything mutates (design 0004
        D3): dropping it silently would desync the fold, and appending into a
        file buffer is the hazard this slice exists to remove. The pump can
        only pass ids the session itself minted, so a violation is a
        programming error, not peer input.
        """
        match command:
            case (
                DeliverSessionEffects(buffer_id=buffer_id)
                | InsertAgentText(buffer_id=buffer_id)
            ):
                state = self._states.get(buffer_id)
                if state is None:
                    raise ValueError(
                        f"delivery target: no such buffer {buffer_id.value!r}"
                    )
                if state.kind != "generated":
                    raise ValueError(
                        f"delivery target {buffer_id.value!r} is not a generated buffer"
                    )
                return buffer_id
            case CreateGeneratedBuffer(name=name):
                existing = self._generated_buffers.get(name)
                if existing is not None:
                    return existing  # idempotent: no second buffer, no event
                buffer_id = self._create_buffer(
                    name,
                    BufferValue(text="", point=0),
                    events,
                    kind="generated",
                )
                self._generated_buffers[name] = buffer_id
                return buffer_id
            case CreateAgentBuffer(acp_session_id=acp_session_id):
                existing = self._agent_buffers.get(acp_session_id)
                if existing is not None:
                    return existing  # idempotent: no second buffer, no event
                buffer_id = self._create_buffer(
                    "*agent*",
                    BufferValue(text="", point=0),
                    events,
                    kind="generated",
                )
                self._agent_buffers[acp_session_id] = buffer_id
                return buffer_id
            case _:
                return None

    def dispatch(self, command: Command) -> CommandOutcome:
        events: list[Event] = []
        new_value: BufferValue

        # While the minibuffer is active, only minibuffer commands act —
        # plus external deliveries, which are not user input and must not be
        # swallowed while a prompt is open (a dropped delivery would desync
        # the agent-buffer fold from the transcript; parity registry row).
        # PromptPermission is delivery-class too: a swallowed permission
        # request would hang the agent (the same row, extended in B.8).
        # The gate runs before target resolution because resolution has
        # effects — CreateAgentBuffer mints a buffer — and a gated command
        # must leave no trace at all.
        if self._minibuffer is not None and not isinstance(
            command,
            MinibufferInput
            | MinibufferBackspace
            | MinibufferAccept
            | MinibufferAbort
            | DeliverProcessOutput
            | DeliverSessionEffects
            | InsertAgentText
            | CreateAgentBuffer
            | CreateGeneratedBuffer
            | PromptPermission
            | AbortPendingPermissions,
        ):
            return CommandOutcome((), self._observation(self.buffer.current))

        # A delivery **pins** its target buffer (design 0004 D1); every other
        # command follows focus. The distinction is not cosmetic: `find-file`
        # and `C-x b` change the focused buffer *inside* their match arm, and
        # their new value belongs to the buffer they switched TO. So a pinned
        # target is resolved once here, while an unpinned one is resolved
        # again after the arm runs — `commit_id` below.
        pinned_id = self._pinned_target(command, events)
        current = self._buffers[pinned_id].current if pinned_id else self.buffer.current

        match command:
            case InsertText(text=text):
                if text:
                    before = current.point
                    after = before + len(text)
                    new_text = current.text[:before] + text + current.text[before:]
                    new_value = replace(
                        current,
                        text=new_text,
                        point=after,
                        modified=True,
                        mark=_adjust_mark_insert(current.mark, before, len(text)),
                    )
                    events.append(TextInserted(text, before, after))
                else:
                    new_value = current
            case ForwardChar():
                new_point = min(current.point + 1, len(current.text))
                actual = new_point - current.point
                new_value = replace(current, point=new_point)
                events.append(PointMoved(1, actual))
            case BackwardChar():
                new_point = max(current.point - 1, 0)
                actual = new_point - current.point
                new_value = replace(current, point=new_point)
                events.append(PointMoved(-1, actual))
            case SaveBuffer():
                new_value = self._save(current, events)
            case KillLine():
                new_value = self._kill_line(current, events)
            case Yank():
                new_value = self._yank(current, events)
            case YankPop():
                new_value = self._yank_pop(current, events)
            case SetMark():
                new_value = replace(current, mark=current.point)
                events.append(MarkSet(current.point))
            case KillRegion():
                new_value = self._kill_region(current, events)
            case CopyRegionAsKill():
                new_value = self._copy_region(current, events)
            case ExchangePointAndMark():
                if current.mark is None:
                    new_value = current  # no mark: silent no-op
                else:
                    new_value = replace(current, point=current.mark, mark=current.point)
                    events.append(MarkExchanged(current.point, current.mark))
            case Undo():
                new_value = self._undo(current, events)
            case DeliverProcessOutput(argv=argv, result=result, error=error):
                # External delivery, not a user edit: buffer value untouched.
                new_value = current
                if result is not None:
                    self._process_log.append(result)
                    events.append(
                        ProcessOutputRecorded(
                            argv,
                            result.exit_code,
                            len(result.stdout),
                            len(result.stderr),
                            "ok" if result.exit_code == 0 else "nonzero-exit",
                        )
                    )
                else:
                    # Construction validation guarantees error is a token here.
                    assert error is not None
                    events.append(ProcessOutputRecorded(argv, -1, 0, 0, error))
            case DeliverSessionEffects(effects=effects, buffer_id=target):
                # One dispatch, both events (design 0005 D4, plan 0015 D5).
                # The fold advances and the rendered suffix lands in the same
                # command, so no code path can observe a state between them —
                # which is what design 0003 consequence 2 always claimed.
                rendered = self._render_effects(effects, target)
                events.append(AgentTranscriptUpdated(effects, rendered, target.value))
                new_value = self._append_agent_text(current, rendered, target, events)
            case InsertAgentText(text=text, buffer_id=target):
                # Still a command in its own right: appending agent text
                # without advancing a transcript fold is a real thing to want.
                new_value = self._append_agent_text(current, text, target, events)
            case CreateAgentBuffer() | CreateGeneratedBuffer():
                # The buffer (and its BufferCreated event) came out of target
                # resolution above; creation edits nothing. `current` is the
                # new buffer's own value, so the write-back below is a no-op
                # and the bookkeeping lands on the new buffer's fresh state
                # rather than intervening in the focused buffer's chains.
                new_value = current
            case SplitWindow():
                new_value = self._split_window(events)
            case OtherWindow():
                new_value = self._other_window(events)
            case DeleteOtherWindows():
                new_value = self._delete_other_windows(events)
            case ResizeFrame(width, height):
                # Plan 0015 D7: the size changes and nothing else. Windows are
                # never deleted to make them fit — the renderer drops the
                # panes that do not, so growing the frame back restores the
                # layout with its points intact. `C-x 2` is gated at the new
                # size from here on, which is why this is a command.
                self._frame_size = (width, height)
                events.append(FrameResized(width, height))
                new_value = current
            case KeyboardQuit():
                new_value = replace(current, mark=None)
                events.append(KeyboardQuitEvent())
            case FindFile():
                self._minibuffer = ""
                self._minibuffer_prompt = "Find file: "
                self._minibuffer_kind = "find-file"
                events.append(MinibufferOpened(self._minibuffer_prompt))
                new_value = current
            case PromptAgent():
                self._minibuffer = ""
                self._minibuffer_prompt = "Agent: "
                self._minibuffer_kind = "agent-prompt"
                events.append(MinibufferOpened(self._minibuffer_prompt))
                new_value = current
            case SwitchBuffer():
                self._minibuffer = ""
                self._minibuffer_prompt = "Switch to buffer: "
                self._minibuffer_kind = "switch-buffer"
                events.append(MinibufferOpened(self._minibuffer_prompt))
                new_value = current
            case MinibufferInput(char=char):
                if self._choice is not None:
                    # Choice mode: map a key to an option kind, resolve, close.
                    decision = self._choice_key_to_decision(self._choice, char)
                    if decision is not None:
                        request_id = self._choice.request_id
                        events.append(PermissionDecided(request_id, decision))
                        self._close_choice(events)
                elif self._minibuffer is not None:
                    self._minibuffer += char
                new_value = current
            case MinibufferBackspace():
                if self._choice is not None:
                    pass  # no text to delete in choice mode
                elif self._minibuffer:
                    self._minibuffer = self._minibuffer[:-1]
                new_value = current
            case MinibufferAbort():
                # Never emits KeyboardQuitEvent (the terminal exits on that
                # event); the main buffer's mark survives the abort.
                if self._choice is not None:
                    # Aborting a permission prompt denies the request.
                    request_id = self._choice.request_id
                    from drei.acp.machine import Cancelled

                    events.append(PermissionDecided(request_id, Cancelled()))
                    self._close_choice(events)
                elif self._minibuffer is not None:
                    self._minibuffer = None
                    self._minibuffer_prompt = ""
                    self._minibuffer_kind = None
                    events.append(MinibufferAborted())
                    # Draining here too: a queued permission request must not
                    # wait forever behind an aborted text prompt.
                    if self._permission_queue:
                        nxt = self._permission_queue.pop(0)
                        self._open_choice(nxt, events)
                new_value = current
            case MinibufferAccept():
                if self._choice is not None:
                    # Accept in choice mode resolves the highlighted (first)
                    # allow option; without one, denies.
                    decision = self._choice_accept_decision(self._choice)
                    request_id = self._choice.request_id
                    events.append(PermissionDecided(request_id, decision))
                    self._close_choice(events)
                    new_value = current
                elif self._minibuffer is not None:
                    text = self._minibuffer
                    kind = self._minibuffer_kind
                    self._minibuffer = None
                    self._minibuffer_prompt = ""
                    self._minibuffer_kind = None
                    if kind == "agent-prompt":
                        # The session says what the user asked for and stops
                        # there; the pump owns everything after. Empty input
                        # closes the prompt silently, like the other arms.
                        if text:
                            events.append(AgentPromptSubmitted(text))
                        new_value = current
                    elif kind == "switch-buffer":
                        # C-x b: empty input takes the MRU default (Emacs
                        # other-buffer); an unknown name creates a new empty
                        # buffer (probed vs pinned 29.3, plan 0012
                        # evidence 5). Selection consumes the old value.
                        name = text or (self._mru[1] if len(self._mru) > 1 else "")
                        if name:
                            buffer_id = BufferId(name)
                            if buffer_id not in self._buffers:
                                buffer_id = self._create_buffer(
                                    name,
                                    BufferValue(text="", point=0),
                                    events,
                                )
                            self._select_buffer(buffer_id, events)
                        new_value = self.buffer.current
                    elif text:
                        # Create-or-select CONSUMES the old buffer's value:
                        # a successful open switches identity (the new buffer
                        # carries its own value); a failed open keeps the old
                        # buffer as-is. The trailing buffer.replace must not
                        # write the old value into the new buffer.
                        self._open_file(current, text, events)
                        new_value = self.buffer.current
                    else:
                        # empty input: silent no-op close
                        new_value = current
                    # A permission request queued behind this text prompt is
                    # presented next — otherwise it would wait forever and
                    # hang the agent.
                    if self._permission_queue:
                        nxt = self._permission_queue.pop(0)
                        self._open_choice(nxt, events)
                else:
                    new_value = current
            case PromptPermission(request=request):
                if self._minibuffer is not None:
                    # A prompt is already open: queue, never swallow.
                    self._permission_queue.append(request)
                    new_value = current
                else:
                    self._open_choice(request, events)
                    new_value = current
            case AbortPendingPermissions():
                # Turn cancelled: the machine's cancel() already answered
                # every pending request, so no PermissionDecided here — only
                # presentation state is cleared. A text prompt (find-file,
                # switch-buffer) is user state and stays open.
                self._permission_queue.clear()
                if self._choice is not None:
                    self._choice = None
                    self._minibuffer = None
                    self._minibuffer_prompt = ""
                    events.append(MinibufferAborted())
                new_value = current
            case _:
                raise TypeError(f"unsupported command: {type(command)}")

        # Resolved only now: an unpinned command may have switched buffers in
        # its arm, and the bookkeeping belongs to the buffer it ended on (the
        # pre-refactor behavior, which read `self._state` at exactly this
        # point). A pinned delivery keeps its own target's state, so it can
        # never break the focused buffer's kill/yank chains or undo descent.
        commit_id = pinned_id or self._current_id
        state = self._states[commit_id]

        if isinstance(command, KillLine):
            # A kill that emits an event starts/continues the append chain;
            # a no-op kill leaves the chain intact.
            if any(isinstance(e, TextKilled) for e in events):
                state.last_was_kill = True
        elif events:
            # Only event-emitting commands break the chain. A silent no-op
            # (empty insert) leaves no trace in the transcript, so it must
            # not intervene — keeping the chain derivable from the evidence
            # (modulo capacity eviction, which emits nothing).
            state.last_was_kill = False

        if isinstance(command, Yank):
            # Active only on an event-emitting yank; a no-op yank clears it.
            state.yank_active = any(isinstance(e, TextYanked) for e in events)
        elif isinstance(command, YankPop):
            # Active stays on for a successful pop (chains), off for a no-op.
            state.yank_active = any(isinstance(e, TextYankPopped) for e in events)
        elif events:
            # Same rule as the chain: only event-emitting commands intervene.
            state.yank_active = False

        # Undo bookkeeping: text-changing commands push a group and
        # truncate the redo tail (owned deviation — stock Emacs keeps redo
        # reachable via undo-more). Any event-emitting non-undo command
        # breaks the descent (matches Emacs's last-command gating); a
        # silent no-op intervenes in nothing.
        if isinstance(command, Undo):
            if events:
                # Only an Undo that actually moved the buffer sets the
                # direction. An exhausted Undo emits nothing, and a silent
                # no-op intervenes in nothing — clearing the flag here would
                # send the *next* Undo down the redo branch, so a held C-/
                # oscillated the buffer forever (review 0001 finding 2).
                state.undo_descending = True
        else:
            group = _make_group(command, current, events)
            if group is not None:
                state.undo_history.append(group)
                del state.undo_history[
                    : max(0, len(state.undo_history) - UNDO_CAPACITY)
                ]
                state.undo_redo.clear()
            if events:
                state.undo_descending = False

        # Validation happens in BufferValue.__post_init__ before any
        # mutation, so command failure is atomic by construction.
        self._buffers[commit_id].replace(new_value)

        # The focused window tracks the buffer it displays: every command
        # that moved point/mark updates its WindowValue (plan 0012 D3). Only
        # when the command actually edited the focused buffer — a delivery
        # into another buffer must not overwrite the focused window's point
        # with a value read from a buffer it does not show.
        if commit_id == self._current_id:
            self._windows = tuple(
                WindowValue(w.buffer_id, new_value.point, new_value.mark)
                if i == self._focused
                else w
                for i, w in enumerate(self._windows)
            )

        self._transcript.extend(events)
        # The observation is the read model for what the user is looking at,
        # whichever buffer the command edited.
        return CommandOutcome(tuple(events), self._observation(self.buffer.current))

    # ------------------------------------------------------------------
    # B.8 choice-minibuffer helpers
    # ------------------------------------------------------------------

    def _open_choice(self, request: PermissionRequested, events: list[Event]) -> None:
        self._choice = request
        self._minibuffer = ""  # choice mode: no text, but minibuffer active
        self._minibuffer_prompt = self._choice_prompt(request)
        events.append(MinibufferOpened(self._minibuffer_prompt))

    def _close_choice(self, events: list[Event]) -> None:
        """Close the resolved choice prompt and present the next queued
        permission request, if any (queue drains FIFO)."""
        self._choice = None
        self._minibuffer = None
        self._minibuffer_prompt = ""
        if self._permission_queue:
            nxt = self._permission_queue.pop(0)
            self._open_choice(nxt, events)

    @staticmethod
    def _choice_options(request: PermissionRequested) -> list[dict[str, JsonValue]]:
        params = request.params
        if isinstance(params, dict):
            options = params.get("options")
            if isinstance(options, list):
                return [o for o in options if isinstance(o, dict)]
        return []

    @classmethod
    def _choice_prompt(cls, request: PermissionRequested) -> str:
        title = "permission"
        params = request.params
        if isinstance(params, dict):
            tool_call = params.get("toolCall")
            if isinstance(tool_call, dict):
                t = tool_call.get("title") or tool_call.get("toolCallId")
                if isinstance(t, str) and t:
                    title = t
        keys = {"allow_once": "y", "allow_session": "s", "allow_always": "a"}
        parts = []
        for option in cls._choice_options(request):
            kind = option.get("kind")
            name = option.get("name")
            key = keys.get(kind, "n") if isinstance(kind, str) else "n"
            label = name if isinstance(name, str) and name else str(kind)
            parts.append(f"[{key}]{label}")
        return f"Allow {title}? " + " ".join(parts) + " [n]o "

    @classmethod
    def _choice_key_to_decision(
        cls, request: PermissionRequested, char: str
    ) -> PermissionDecision | None:
        from drei.acp.machine import _REJECT_KINDS, Cancelled, Selected

        key_to_kind = {
            "y": "allow_once",
            "s": "allow_session",
            "a": "allow_always",
            "n": "reject",
        }
        kind = key_to_kind.get(char)
        if kind is None:
            return None
        options = cls._choice_options(request)
        if kind == "reject":
            # First enum reject option; absent any, a deny is a cancel. Match
            # by membership, not startswith ("reject_evil" is not a deny).
            # optionIds are strings per ACP; a non-string id is unusable,
            # never str()-coerced into a value the agent never sent.
            for option in options:
                if option.get("kind") in _REJECT_KINDS:
                    oid = option.get("optionId")
                    if isinstance(oid, str):
                        return Selected(oid)
            return Cancelled()
        for option in options:
            if option.get("kind") == kind:
                oid = option.get("optionId")
                if isinstance(oid, str):
                    return Selected(oid)
        return None

    @classmethod
    def _choice_accept_decision(
        cls, request: PermissionRequested
    ) -> PermissionDecision:
        from drei.acp.machine import Cancelled, Selected

        # Accept maps to allow_once only (review 0001 finding 9): the option
        # order is agent-controlled, so "first allow option" would let an
        # agent turn a habitual RET into a session/always grant. Broader
        # scopes require their explicit keys (s / a). Absent a usable
        # allow_once, fail-closed to a cancel (never auto-approve an invented
        # "allow_*" kind).
        for option in cls._choice_options(request):
            if option.get("kind") == "allow_once":
                oid = option.get("optionId")
                if isinstance(oid, str):
                    return Selected(oid)
        return Cancelled()

    def apply_permission_decision(
        self,
        machine: AcpMachine,
        request_id: RequestId,
        decision: PermissionDecision,
    ) -> tuple[AcpMachine, list[Message], list[SessionEffect]]:
        """Feed a human decision back to the ACP machine (B.8 seam), mirroring
        ``apply_session_effects``. The session owns no machine (the §C pump
        does); this takes and returns it so the pure ``resolve_permission``
        maps the decision onto the exact 0.9.0 response. The returned
        ``Response`` is what the pump sends; nothing is sent here.

        TODO: [tech-debt] TD-2 — no pump exists, so no caller ever sends it:
        a real agent asking permission would still block forever. See
        docs/technical-debt.md.
        """
        from drei.acp.machine import resolve_permission

        return resolve_permission(machine, request_id, decision)

    def _observation(self, value: BufferValue) -> BufferObservation:
        return BufferObservation(
            buffer_id=self.buffer.buffer_id.value,
            text=value.text,
            point=value.point,
            file_path=value.file_path,
            modified=value.modified,
            mark=value.mark,
            minibuffer=self._minibuffer,
            minibuffer_prompt=self.minibuffer_prompt,
        )

    def session_observation(self) -> SessionObservation:
        """Derived read model over the whole session (plan 0012 D5): one
        WindowObservation per window (each with its buffer snapshot and its
        own point/mark), the buffer names, and the focused index. Rebuilt on
        demand — windows are layout state, not session facts; the events
        already record every layout change."""
        windows = tuple(
            WindowObservation(
                buffer=self._buffer_observation(w.buffer_id),
                point=w.point,
                mark=w.mark,
            )
            for w in self._windows
        )
        return SessionObservation(
            buffers=self.buffers,
            windows=windows,
            focused=self._focused,
            minibuffer=self.minibuffer,
            minibuffer_prompt=self.minibuffer_prompt,
        )

    def _buffer_observation(self, buffer_id: BufferId) -> BufferObservation:
        current = self._buffers[buffer_id].current
        return BufferObservation(
            buffer_id=buffer_id.value,
            text=current.text,
            point=current.point,
            file_path=current.file_path,
            modified=current.modified,
            mark=current.mark,
            minibuffer=self._minibuffer,
            minibuffer_prompt=self.minibuffer_prompt,
        )

    def _tail_follow_windows(
        self, buffer_id: BufferId, before: int, after: int
    ) -> None:
        """Apply D6's tail-follow rule to the **non-focused** windows showing
        an appended-to buffer.

        0004 states the rule over windows rather than over the buffer because
        A.2 made window point distinct from ``BufferValue.point``: a user
        scrolled back through the transcript in one window is not dragged to
        the end because output arrived in another window on the same buffer.
        The focused window is excluded because its live point *is*
        ``BufferValue.point``, which the append itself already moved (or did
        not); refreshing it from the new value happens at the end of dispatch.

        Window marks need no adjustment: an append lands at end-of-buffer, and
        the existing insert rule never moves a mark at or before the insertion
        point.
        """
        self._windows = tuple(
            WindowValue(w.buffer_id, after, w.mark)
            if i != self._focused and w.buffer_id == buffer_id and w.point == before
            else w
            for i, w in enumerate(self._windows)
        )

    def _append_agent_text(
        self,
        current: BufferValue,
        text: str,
        target: BufferId,
        events: list[Event],
    ) -> BufferValue:
        """Append agent text to ``current``, recording ``AgentTextInserted``.

        Shared by `DeliverSessionEffects` and `InsertAgentText` so the two
        cannot drift apart — before design 0005 D4 the append existed only on
        the second, which is what made the "atomic" delivery two dispatches.
        Empty text is a no-op that records nothing: a delivery of silent
        effects leaves no trace in the buffer.
        """
        if not text:
            return current
        before = len(current.text)
        after = before + len(text)
        new_value = replace(
            current,
            text=current.text + text,
            # Tail-follow, not a cursor grab (design 0004 D6): a point that
            # sat at end-of-buffer tracks the stream; a point anywhere else
            # is where a human put it.
            point=after if current.point == before else current.point,
            mark=_adjust_mark_insert(current.mark, before, len(text)),
        )
        self._tail_follow_windows(target, before, after)
        events.append(AgentTextInserted(text, before, after, target.value))
        return new_value

    def _split_window(self, events: list[Event]) -> BufferValue:
        """C-x 2 (plan 0012 D3): split the focused window into two stacked
        halves over the same buffer; both inherit the buffer's current
        point/mark (probed vs pinned 29.3, evidence 4). Needs at least
        MIN_WINDOW_ROWS per window plus the shared echo row when the frame
        size is known; otherwise a silent no-op (deviation: Emacs errors
        'too small for splitting', evidence 6)."""
        if self._frame_size is not None:
            _, height = self._frame_size
            if height < (len(self._windows) + 1) * MIN_WINDOW_ROWS + 1:
                return self.buffer.current
        focused = self._windows[self._focused]
        # The new window copies the FOCUSED WINDOW's point/mark (not the
        # buffer's): from here on the two windows hold independent points
        # (design 0002's stress case), and the focused window's stored
        # value is the authoritative copy of the split position.
        duplicate = WindowValue(focused.buffer_id, focused.point, focused.mark)
        windows = list(self._windows)
        windows.insert(self._focused + 1, duplicate)
        self._windows = tuple(windows)
        events.append(WindowSplit(len(self._windows)))
        return self.buffer.current

    def _other_window(self, events: list[Event]) -> BufferValue:
        """C-x o (plan 0012 D3): cycle focus. The departing window keeps its
        WindowValue (already synced); the arriving window's buffer becomes
        current at the window's stored point/mark."""
        if len(self._windows) < 2:
            return self.buffer.current
        self._focused = (self._focused + 1) % len(self._windows)
        window = self._windows[self._focused]
        events.append(WindowFocusChanged(self._focused, window.buffer_id.value))
        if window.buffer_id != self._current_id:
            self._select_window_buffer(window)
        else:
            self.buffer.replace(
                replace(
                    self.buffer.current,
                    point=self._clamped_point(window.point),
                    mark=self._clamped_mark(window.mark),
                )
            )
        return self.buffer.current

    def _clamped_point(self, point: int) -> int:
        """A non-focused window's stored point is stale when the focused
        window shrank the shared buffer; restore clamps to the buffer bounds
        (Emacs adjusts window-point markers on edit — plan 0012 D3 note)."""
        return min(max(point, 0), len(self.buffer.current.text))

    def _clamped_mark(self, mark: int | None) -> int | None:
        if mark is None:
            return None
        return self._clamped_point(mark)

    def _select_window_buffer(self, window: WindowValue) -> None:
        """Focus landed on a window over another buffer: switch current at
        the window's stored point/mark (no events — OtherWindow already
        emitted WindowFocusChanged; BufferSelected is for user switches)."""
        self._state.break_chains()
        self._current_id = window.buffer_id
        name = window.buffer_id.value
        if name in self._mru:
            self._mru.remove(name)
        self._mru.insert(0, name)
        self.buffer.replace(
            replace(
                self.buffer.current,
                point=self._clamped_point(window.point),
                mark=self._clamped_mark(window.mark),
            )
        )

    def _delete_other_windows(self, events: list[Event]) -> BufferValue:
        """C-x 1 (plan 0012 D3): collapse to the focused window."""
        if len(self._windows) < 2:
            return self.buffer.current
        self._windows = (self._windows[self._focused],)
        self._focused = 0
        events.append(WindowsCollapsed())
        return self.buffer.current

    def _select_buffer(self, buffer_id: BufferId, events: list[Event]) -> None:
        """Make ``buffer_id`` the current buffer (plan 0012 D1/D2).

        Switching intervenes like any other command in Emacs: the departing
        buffer's kill-append and yank-pop chains break (last-command state);
        its undo history is per-buffer and survives (probed vs pinned 29.3,
        plan 0012 evidence 2/3). Selecting the already-current buffer is a
        quiet no-op — no event, no chain break.
        """
        if buffer_id == self._current_id:
            return
        self._state.break_chains()
        self._current_id = buffer_id
        name = buffer_id.value
        if name in self._mru:
            self._mru.remove(name)
        self._mru.insert(0, name)
        # The focused window follows the switch (Emacs set-window-buffer),
        # displaying the target at the target's own point/mark.
        target = self._buffers[buffer_id].current
        self._windows = tuple(
            WindowValue(buffer_id, target.point, target.mark)
            if i == self._focused
            else w
            for i, w in enumerate(self._windows)
        )
        events.append(BufferSelected(buffer_id.value))

    def _create_buffer(
        self,
        name: str,
        value: BufferValue,
        events: list[Event],
        kind: BufferKind = "ordinary",
    ) -> BufferId:
        """Add a new buffer to the set with a unique name (plan 0012 D1).

        Same-basename collisions get numeric ``<N>`` suffixes — a recorded
        deviation from Emacs 29.3's ``<dirname>`` uniquify suffixes (plan
        0012 evidence 1; deterministic without directory context). The same
        rule names the second and later agent buffers (design 0004 D1).

        ``kind`` is the buffer's provenance (design 0004 D3); only a
        ``"generated"`` buffer accepts an agent delivery.
        """
        candidate = name
        suffix = 2
        while BufferId(candidate) in self._buffers:
            candidate = f"{name}<{suffix}>"
            suffix += 1
        buffer_id = BufferId(candidate)
        visited, state = _visit(value)
        state.kind = kind
        self._buffers[buffer_id] = Buffer(buffer_id, visited)
        self._states[buffer_id] = state
        events.append(BufferCreated(buffer_id.value, visited.file_path))
        return buffer_id

    def _open_file(
        self, current: BufferValue, path: str, events: list[Event]
    ) -> BufferValue:
        """Find-file accept (plan 0012 D1): create-or-select.

        An already-open path (string equality on ``file_path``) SELECTS its
        buffer — re-reading a file the user may have edited would be data
        loss. A new path reads through the port: success or missing-file
        creates a new buffer named by basename (with ``<N>`` collision
        suffixes); the old buffer, its undo history, and the global kill
        ring all survive. Other read errors report and leave everything
        untouched.
        """
        for buffer_id, buffer in self._buffers.items():
            if buffer.current.file_path == path:
                self._select_buffer(buffer_id, events)
                return current
        try:
            text = self._files.read(path)
        except FileNotFoundError:
            text = ""  # missing file (or missing directory): new empty buffer
        except OSError as error:
            events.append(OpenFailed(path, normalize_os_error(error)))
            return current
        except UnicodeDecodeError:
            events.append(OpenFailed(path, "io-error"))
            return current
        # TODO: [tech-debt] TD-3 — a trailing slash ("notes/") makes this
        # basename "", and a buffer named "" is unreachable afterwards: C-x b
        # with empty input takes the MRU default and no typed name matches
        # it, so edits made there are stranded. See docs/technical-debt.md.
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        buffer_id = self._create_buffer(
            name,
            BufferValue(text=text, point=0, file_path=path, modified=False, mark=None),
            events,
        )
        # The length is the buffer's, not the file's: CRLF pairs are one
        # newline in the buffer, and the transcript describes buffer state.
        events.append(BufferOpened(path, len(self._buffers[buffer_id].current.text)))
        self._select_buffer(buffer_id, events)
        return current

    def _render_effects(
        self, effects: tuple[SessionEffect, ...], buffer_id: BufferId
    ) -> str:
        """Fold effects through the target buffer's cached ``TranscriptFold``;
        return the newly rendered suffix. The fold is interpreter state only —
        it never touches the buffer. One fold per agent buffer (design 0004
        D5): a buffer with no fold yet starts from a fresh one."""
        from drei.acp.transcript import TranscriptFold, advance

        parts: list[str] = []
        fold = self._agent_folds.get(buffer_id, TranscriptFold())
        for effect in effects:
            fold, text = advance(fold, effect)
            parts.append(text)
        self._agent_folds[buffer_id] = fold
        return "".join(parts)

    def apply_session_effects(
        self, effects: tuple[SessionEffect, ...], buffer_id: BufferId
    ) -> CommandOutcome:
        """The agent-delivery entry point (design 0003 §B.7), mirroring
        ``run_process``: validate, record the fold as one immutable delivery
        event, and append the newly rendered text as one buffer edit. One
        ``handle()`` call's effects land as one ``AgentTranscriptUpdated``
        plus at most one ``AgentTextInserted``.

        ``buffer_id`` is the agent buffer this transcript belongs to — the one
        ``CreateAgentBuffer`` minted for the ACP session these effects came
        from, readable via ``agent_buffer_id``. It is required and must name a
        generated buffer; anything else raises (design 0004 D3).

        The delivery is **one dispatch** (design 0005 D4), so it is atomic in
        the sense design 0003 consequence 2 always asserted: no code path can
        observe the fold advanced without the text appended. This is now a
        thin wrapper — the work is the `DeliverSessionEffects` arm — and it
        stays because it is the named entry point the ACP side calls.
        """
        return self.dispatch(DeliverSessionEffects(tuple(effects), buffer_id))

    def run_process(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> CommandOutcome:
        """Run a child process via the injected port and record the delivery.

        The port does the blocking run-to-completion; this wraps the captured
        result (or a normalized launch failure) in ``DeliverProcessOutput``
        and dispatches it, so the run enters the transcript as one immutable
        external-delivery event. Never raises for a launch failure — the
        outcome carries a normalized ``ProcessOutputRecorded`` status token.
        """
        try:
            result = self._processes.run(argv, input_text=input_text, timeout=timeout)
        except (ProcessTimedOut, OSError) as error:
            token = normalize_process_error(error)
            return self.dispatch(DeliverProcessOutput(argv, None, token))
        return self.dispatch(DeliverProcessOutput(argv, result, None))

    def _modified_after_undo(self, text: str) -> bool:
        """Whether the buffer differs from its file after an undo or redo.

        The flag is a fact about the text, not a replay of the flag the
        undone command happened to carry: undoing *past* a save leaves the
        buffer different from disk and must report modified, and undoing or
        redoing back *to* the saved text reports clean (review 0001 finding
        3; Emacs tracks the same boundary through the undo list). A buffer
        whose file contents were never observed stays modified.

        Registered deviation from Emacs (`docs/knowledge/emacs-parity.md`,
        "Undo restoring mark/modified"): Drei compares text, so a buffer
        edited back to the saved text by ordinary commands and then undone
        reads clean where Emacs — which counts undo-list position — would
        still report modified.
        """
        saved = self._state.saved_text
        return saved is None or text != saved

    def _undo(self, current: BufferValue, events: list[Event]) -> BufferValue:
        """Apply the newest group's inverse (descending) or, after any
        intervening event-emitting command, redo the newest undone group
        (Emacs's direction flip on last-command != undo).

        TODO: [tech-debt] TD-8 — the undo stacks are mutated below *before*
        the replacement BufferValue is constructed. Unreachable today (the
        value's invariants cannot be violated from here), but if
        __post_init__ ever raised the stacks would be mutated with no event
        recorded. See docs/technical-debt.md.
        """
        if self._state.undo_descending or not self._state.undo_redo:
            if not self._state.undo_history:
                return current  # nothing to undo: silent no-op
            group = self._state.undo_history.pop()
            self._state.undo_redo.append(group)
            events.append(
                TextUndone(
                    group.start,
                    group.inserted_text,
                    group.removed_text,
                    group.point_after,
                    group.point_before,
                    group.mark_after,
                    group.mark_before,
                )
            )
            undone_text = (
                current.text[: group.start]
                + group.removed_text
                + current.text[group.start + len(group.inserted_text) :]
            )
            return replace(
                current,
                text=undone_text,
                point=group.point_before,
                mark=group.mark_before,
                modified=self._modified_after_undo(undone_text),
            )
        group = self._state.undo_redo.pop()
        self._state.undo_history.append(group)
        events.append(
            TextRedone(
                group.start,
                group.removed_text,
                group.inserted_text,
                group.point_before,
                group.point_after,
                group.mark_before,
                group.mark_after,
            )
        )
        redone_text = (
            current.text[: group.start]
            + group.inserted_text
            + current.text[group.start + len(group.removed_text) :]
        )
        return replace(
            current,
            text=redone_text,
            point=group.point_after,
            mark=group.mark_after,
            modified=self._modified_after_undo(redone_text),
        )

    def _save(self, current: BufferValue, events: list[Event]) -> BufferValue:
        path = current.file_path
        if path is None:
            # No file to write: name the buffer and say so honestly — a fake
            # path with "not-found" would read as a missing file (review 0001
            # finding 26). Echoes as "<buffer>: no-file" until a write-file
            # (path-prompting) slice exists.
            events.append(SaveFailed(self._current_id.value, "no-file"))
            return current
        try:
            self._files.write(path, to_file_text(current.text, self._state.eol))
        except OSError as error:
            events.append(SaveFailed(path, normalize_os_error(error)))
            return current
        events.append(BufferSaved(path))
        # The save moves the clean point: undoing past it must now report the
        # buffer modified (review 0001 finding 3).
        self._state.saved_text = current.text
        return replace(current, modified=False)

    def _kill_line(self, current: BufferValue, events: list[Event]) -> BufferValue:
        point = current.point
        text = current.text
        if point == len(text):
            return current  # no-op at buffer end: no event, ring untouched
        if text[point] == "\n":
            killed, end = "\n", point + 1
        else:
            end = text.find("\n", point)
            if end == -1:
                end = len(text)
            killed = text[point:end]
        new_text = text[:point] + text[end:]
        # TODO: [tech-debt] TD-8 — the ring is mutated before the replacement
        # BufferValue is constructed; same unreachable-today ordering as
        # _undo. See docs/technical-debt.md.
        if self._state.last_was_kill and self._kill_ring:
            self._kill_ring[0] += killed
        else:
            self._kill_ring.insert(0, killed)
            del self._kill_ring[KILL_RING_CAPACITY:]
        events.append(TextKilled(killed, point, end, "forward"))
        return replace(
            current,
            text=new_text,
            modified=True,
            mark=_adjust_mark_delete(current.mark, point, end),
        )

    def _kill_region(self, current: BufferValue, events: list[Event]) -> BufferValue:
        if current.mark is None or current.mark == current.point:
            return current  # no mark / empty region: silent no-op
        lo = min(current.point, current.mark)
        hi = max(current.point, current.mark)
        killed = current.text[lo:hi]
        direction = "forward" if current.point > current.mark else "backward"
        self._kill_ring.insert(0, killed)
        del self._kill_ring[KILL_RING_CAPACITY:]
        events.append(RegionKilled(killed, lo, hi, direction))
        return replace(
            current,
            text=current.text[:lo] + current.text[hi:],
            point=lo,
            modified=True,
            mark=None,
        )

    def _copy_region(self, current: BufferValue, events: list[Event]) -> BufferValue:
        if current.mark is None or current.mark == current.point:
            return current  # no mark / empty region: silent no-op
        lo = min(current.point, current.mark)
        hi = max(current.point, current.mark)
        self._kill_ring.insert(0, current.text[lo:hi])
        del self._kill_ring[KILL_RING_CAPACITY:]
        events.append(RegionCopied(current.text[lo:hi]))
        return replace(current, mark=None)

    def _yank(self, current: BufferValue, events: list[Event]) -> BufferValue:
        if not self._kill_ring:
            return current
        text = self._kill_ring[0]
        before = current.point
        after = before + len(text)
        new_text = current.text[:before] + text + current.text[before:]
        events.append(TextYanked(text, before, after))
        self._state.yank_cursor = 0
        self._state.yank_bounds = (before, after)
        return replace(
            current,
            text=new_text,
            point=after,
            modified=True,
            mark=_adjust_mark_insert(current.mark, before, len(text)),
        )

    def _yank_pop(self, current: BufferValue, events: list[Event]) -> BufferValue:
        if not self._state.yank_active or len(self._kill_ring) < 2:
            return current  # no active yank / empty or 1-entry ring: silent no-op
        start, end = self._state.yank_bounds
        old = current.text[start:end]
        cursor = (self._state.yank_cursor + 1) % len(self._kill_ring)
        new = self._kill_ring[cursor]
        after = start + len(new)
        new_text = current.text[:start] + new + current.text[end:]
        events.append(TextYankPopped(old, new, start, after))
        self._state.yank_cursor = cursor
        self._state.yank_bounds = (start, after)
        return replace(
            current,
            text=new_text,
            point=after,
            modified=True,
            mark=_adjust_mark_insert(
                _adjust_mark_delete(current.mark, start, end), start, len(new)
            ),
        )
