from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from drei.acp.machine import SessionEffect

from drei.commands import (
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
    DeliverProcessOutput,
    DeliverSessionEffects,
    ExchangePointAndMark,
    FindFile,
    ForwardChar,
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
    PointMoved,
    ProcessOutputRecorded,
    RegionCopied,
    RegionKilled,
    SaveBuffer,
    SaveFailed,
    SetMark,
    TextInserted,
    TextKilled,
    TextRedone,
    TextUndone,
    TextYanked,
    TextYankPopped,
    Undo,
    Yank,
    YankPop,
)
from drei.files import FilePort, normalize_os_error
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
    | FindFile
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
    | BufferOpened
    | BufferCreated
    | BufferSelected
    | OpenFailed
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
                    current.modified,
                    True,
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
                    current.modified,
                    True,
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
                    current.modified,
                    True,
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
                    current.modified,
                    True,
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
                    current.modified,
                    True,
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


KILL_RING_CAPACITY = 60
UNDO_CAPACITY = 100


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
    modified_before: bool
    modified_after: bool


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
    yank-pop chaining, and the kill-append chain flag. Session-global state
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
    )

    def __init__(self) -> None:
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
        self._buffers: dict[BufferId, Buffer] = {buffer.buffer_id: buffer}
        self._current_id: BufferId = buffer.buffer_id
        self._states: dict[BufferId, _BufferState] = {buffer.buffer_id: _BufferState()}
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
        # Agent-transcript fold cache (design 0003 §B.7): a derived,
        # reconstructible cache of the AgentTranscriptUpdated event stream —
        # the same discipline as _process_log. The transcript remains
        # authoritative; the fold only advances after the delivery event is
        # recorded, so cache and transcript cannot desync mid-dispatch.
        from drei.acp.transcript import TranscriptFold

        self._agent_fold = TranscriptFold()

    @property
    def buffer(self) -> Buffer:
        """The current buffer (identity shell; plan 0012 D1)."""
        return self._buffers[self._current_id]

    @property
    def buffers(self) -> tuple[str, ...]:
        """Derived view: the session's buffer names, in creation order."""
        return tuple(buffer_id.value for buffer_id in self._buffers)

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

    def dispatch(self, command: Command) -> CommandOutcome:
        current = self.buffer.current
        events: list[Event] = []
        new_value: BufferValue

        # While the minibuffer is active, only minibuffer commands act —
        # plus external deliveries, which are not user input and must not be
        # swallowed while a prompt is open (a dropped delivery would desync
        # the agent-buffer fold from the transcript; parity registry row).
        if self._minibuffer is not None and not isinstance(
            command,
            MinibufferInput
            | MinibufferBackspace
            | MinibufferAccept
            | MinibufferAbort
            | DeliverProcessOutput
            | DeliverSessionEffects
            | InsertAgentText,
        ):
            return CommandOutcome((), self._observation(current))

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
            case DeliverSessionEffects(effects=effects):
                # External delivery, not a user edit: buffer value untouched.
                # The fold→append step lives in apply_session_effects (the
                # atomic delivery seam); a raw dispatch only records the fold.
                new_value = current
                rendered = self._render_effects(effects)
                events.append(AgentTranscriptUpdated(effects, rendered))
            case InsertAgentText(text=text):
                if text:
                    before = len(current.text)
                    after = before + len(text)
                    new_value = replace(
                        current,
                        text=current.text + text,
                        point=after,
                        mark=_adjust_mark_insert(current.mark, before, len(text)),
                    )
                    events.append(AgentTextInserted(text, before, after))
                else:
                    new_value = current
            case KeyboardQuit():
                new_value = replace(current, mark=None)
                events.append(KeyboardQuitEvent())
            case FindFile():
                self._minibuffer = ""
                self._minibuffer_prompt = "Find file: "
                events.append(MinibufferOpened(self._minibuffer_prompt))
                new_value = current
            case MinibufferInput(char=char):
                if self._minibuffer is not None:
                    self._minibuffer += char
                new_value = current
            case MinibufferBackspace():
                if self._minibuffer:
                    self._minibuffer = self._minibuffer[:-1]
                new_value = current
            case MinibufferAbort():
                # Never emits KeyboardQuitEvent (the terminal exits on that
                # event); the main buffer's mark survives the abort.
                if self._minibuffer is not None:
                    self._minibuffer = None
                    self._minibuffer_prompt = ""
                    events.append(MinibufferAborted())
                new_value = current
            case MinibufferAccept():
                if self._minibuffer is not None:
                    path = self._minibuffer
                    self._minibuffer = None
                    self._minibuffer_prompt = ""
                    if path:
                        # Create-or-select CONSUMES the old buffer's value:
                        # a successful open switches identity (the new buffer
                        # carries its own value); a failed open keeps the old
                        # buffer as-is. The trailing buffer.replace must not
                        # write the old value into the new buffer.
                        self._open_file(current, path, events)
                        new_value = self.buffer.current
                    else:
                        # empty input: silent no-op close
                        new_value = current
                else:
                    new_value = current
            case _:
                raise TypeError(f"unsupported command: {type(command)}")

        if isinstance(command, KillLine):
            # A kill that emits an event starts/continues the append chain;
            # a no-op kill leaves the chain intact.
            if any(isinstance(e, TextKilled) for e in events):
                self._state.last_was_kill = True
        elif events:
            # Only event-emitting commands break the chain. A silent no-op
            # (empty insert) leaves no trace in the transcript, so it must
            # not intervene — keeping the chain derivable from the evidence
            # (modulo capacity eviction, which emits nothing).
            self._state.last_was_kill = False

        if isinstance(command, Yank):
            # Active only on an event-emitting yank; a no-op yank clears it.
            self._state.yank_active = any(isinstance(e, TextYanked) for e in events)
        elif isinstance(command, YankPop):
            # Active stays on for a successful pop (chains), off for a no-op.
            self._state.yank_active = any(isinstance(e, TextYankPopped) for e in events)
        elif events:
            # Same rule as the chain: only event-emitting commands intervene.
            self._state.yank_active = False

        # Undo bookkeeping: text-changing commands push a group and
        # truncate the redo tail (owned deviation — stock Emacs keeps redo
        # reachable via undo-more). Any event-emitting non-undo command
        # breaks the descent (matches Emacs's last-command gating); a
        # silent no-op intervenes in nothing.
        if isinstance(command, Undo):
            self._state.undo_descending = bool(events)
        else:
            group = _make_group(command, current, events)
            if group is not None:
                self._state.undo_history.append(group)
                del self._state.undo_history[
                    : max(0, len(self._state.undo_history) - UNDO_CAPACITY)
                ]
                self._state.undo_redo.clear()
            if events:
                self._state.undo_descending = False

        # Validation happens in BufferValue.__post_init__ before any
        # mutation, so command failure is atomic by construction.
        self.buffer.replace(new_value)

        self._transcript.extend(events)
        return CommandOutcome(tuple(events), self._observation(new_value))

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
        events.append(BufferSelected(buffer_id.value))

    def _create_buffer(
        self, name: str, value: BufferValue, events: list[Event]
    ) -> BufferId:
        """Add a new buffer to the set with a unique name (plan 0012 D1).

        Same-basename collisions get numeric ``<N>`` suffixes — a recorded
        deviation from Emacs 29.3's ``<dirname>`` uniquify suffixes (plan
        0012 evidence 1; deterministic without directory context).
        """
        candidate = name
        suffix = 2
        while BufferId(candidate) in self._buffers:
            candidate = f"{name}<{suffix}>"
            suffix += 1
        buffer_id = BufferId(candidate)
        self._buffers[buffer_id] = Buffer(buffer_id, value)
        self._states[buffer_id] = _BufferState()
        events.append(BufferCreated(buffer_id.value, value.file_path))
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
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        buffer_id = self._create_buffer(
            name,
            BufferValue(text=text, point=0, file_path=path, modified=False, mark=None),
            events,
        )
        events.append(BufferOpened(path, len(text)))
        self._select_buffer(buffer_id, events)
        return current

    def _render_effects(self, effects: tuple[SessionEffect, ...]) -> str:
        """Fold effects through the cached ``TranscriptFold``; return the
        newly rendered suffix. The fold is interpreter state only — it never
        touches the buffer."""
        from drei.acp.transcript import advance

        parts: list[str] = []
        fold = self._agent_fold
        for effect in effects:
            fold, text = advance(fold, effect)
            parts.append(text)
        self._agent_fold = fold
        return "".join(parts)

    def apply_session_effects(
        self, effects: tuple[SessionEffect, ...]
    ) -> CommandOutcome:
        """The agent-delivery entry point (design 0003 §B.7), mirroring
        ``run_process``: validate, record the fold as one immutable delivery
        event, then append the newly rendered text as one buffer edit. One
        ``handle()`` call's effects land as one ``AgentTranscriptUpdated``
        plus at most one ``AgentTextInserted`` — atomic per design 0003
        §consequence-2.
        """
        delivery = DeliverSessionEffects(tuple(effects))
        outcome = self.dispatch(delivery)
        rendered = next(
            e.rendered for e in outcome.events if isinstance(e, AgentTranscriptUpdated)
        )
        if not rendered:
            return outcome
        append = self.dispatch(InsertAgentText(rendered))
        return CommandOutcome(outcome.events + append.events, append.observation)

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

    def _undo(self, current: BufferValue, events: list[Event]) -> BufferValue:
        """Apply the newest group's inverse (descending) or, after any
        intervening event-emitting command, redo the newest undone group
        (Emacs's direction flip on last-command != undo)."""
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
            return replace(
                current,
                text=(
                    current.text[: group.start]
                    + group.removed_text
                    + current.text[group.start + len(group.inserted_text) :]
                ),
                point=group.point_before,
                mark=group.mark_before,
                modified=group.modified_before,
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
        return replace(
            current,
            text=(
                current.text[: group.start]
                + group.inserted_text
                + current.text[group.start + len(group.removed_text) :]
            ),
            point=group.point_after,
            mark=group.mark_after,
            modified=group.modified_after,
        )

    def _save(self, current: BufferValue, events: list[Event]) -> BufferValue:
        path = current.file_path
        if path is None:
            events.append(SaveFailed("scratch", "not-found"))
            return current
        try:
            self._files.write(path, current.text)
        except OSError as error:
            events.append(SaveFailed(path, normalize_os_error(error)))
            return current
        events.append(BufferSaved(path))
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
