from __future__ import annotations

from dataclasses import dataclass, replace

from drei.commands import (
    BackwardChar,
    BufferObservation,
    BufferSaved,
    CommandOutcome,
    CopyRegionAsKill,
    DeliverProcessOutput,
    ExchangePointAndMark,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KeyboardQuitEvent,
    KillLine,
    KillRegion,
    MarkExchanged,
    MarkSet,
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
from drei.model import Buffer, BufferValue
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


class EditorSession:
    def __init__(
        self,
        buffer: Buffer,
        file_port: FilePort | None = None,
        process_port: ProcessPort | None = None,
    ) -> None:
        self.buffer = buffer
        self._files: FilePort = file_port if file_port is not None else _NullFilePort()
        self._processes: ProcessPort = (
            process_port if process_port is not None else _NullProcessPort()
        )
        self._transcript: list[Event] = []
        self._process_log: list[ProcessResult] = []
        self._kill_ring: list[str] = []
        self._last_was_kill = False
        self._yank_active = False
        self._yank_cursor = 0
        self._yank_bounds = (0, 0)
        self._undo_history: list[_UndoGroup] = []  # applied groups, newest last
        self._undo_redo: list[_UndoGroup] = []  # undone groups, newest last
        self._undo_descending = False  # last command was an undo

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
            case KeyboardQuit():
                new_value = replace(current, mark=None)
                events.append(KeyboardQuitEvent())
            case _:
                raise TypeError(f"unsupported command: {type(command)}")

        if isinstance(command, KillLine):
            # A kill that emits an event starts/continues the append chain;
            # a no-op kill leaves the chain intact.
            if any(isinstance(e, TextKilled) for e in events):
                self._last_was_kill = True
        elif events:
            # Only event-emitting commands break the chain. A silent no-op
            # (empty insert) leaves no trace in the transcript, so it must
            # not intervene — keeping the chain derivable from the evidence
            # (modulo capacity eviction, which emits nothing).
            self._last_was_kill = False

        if isinstance(command, Yank):
            # Active only on an event-emitting yank; a no-op yank clears it.
            self._yank_active = any(isinstance(e, TextYanked) for e in events)
        elif isinstance(command, YankPop):
            # Active stays on for a successful pop (chains), off for a no-op.
            self._yank_active = any(isinstance(e, TextYankPopped) for e in events)
        elif events:
            # Same rule as the chain: only event-emitting commands intervene.
            self._yank_active = False

        # Undo bookkeeping: text-changing commands push a group and
        # truncate the redo tail (owned deviation — stock Emacs keeps redo
        # reachable via undo-more). Any event-emitting non-undo command
        # breaks the descent (matches Emacs's last-command gating); a
        # silent no-op intervenes in nothing.
        if isinstance(command, Undo):
            self._undo_descending = bool(events)
        else:
            group = _make_group(command, current, events)
            if group is not None:
                self._undo_history.append(group)
                del self._undo_history[
                    : max(0, len(self._undo_history) - UNDO_CAPACITY)
                ]
                self._undo_redo.clear()
            if events:
                self._undo_descending = False

        # Validation happens in BufferValue.__post_init__ before any
        # mutation, so command failure is atomic by construction.
        self.buffer.replace(new_value)

        self._transcript.extend(events)
        observation = BufferObservation(
            buffer_id=self.buffer.buffer_id.value,
            text=new_value.text,
            point=new_value.point,
            file_path=new_value.file_path,
            modified=new_value.modified,
            mark=new_value.mark,
        )
        return CommandOutcome(tuple(events), observation)

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
        if self._undo_descending or not self._undo_redo:
            if not self._undo_history:
                return current  # nothing to undo: silent no-op
            group = self._undo_history.pop()
            self._undo_redo.append(group)
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
        group = self._undo_redo.pop()
        self._undo_history.append(group)
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
        if self._last_was_kill and self._kill_ring:
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
        self._yank_cursor = 0
        self._yank_bounds = (before, after)
        return replace(
            current,
            text=new_text,
            point=after,
            modified=True,
            mark=_adjust_mark_insert(current.mark, before, len(text)),
        )

    def _yank_pop(self, current: BufferValue, events: list[Event]) -> BufferValue:
        if not self._yank_active or len(self._kill_ring) < 2:
            return current  # no active yank / empty or 1-entry ring: silent no-op
        start, end = self._yank_bounds
        old = current.text[start:end]
        cursor = (self._yank_cursor + 1) % len(self._kill_ring)
        new = self._kill_ring[cursor]
        after = start + len(new)
        new_text = current.text[:start] + new + current.text[end:]
        events.append(TextYankPopped(old, new, start, after))
        self._yank_cursor = cursor
        self._yank_bounds = (start, after)
        return replace(
            current,
            text=new_text,
            point=after,
            modified=True,
            mark=_adjust_mark_insert(
                _adjust_mark_delete(current.mark, start, end), start, len(new)
            ),
        )
