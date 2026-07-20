from __future__ import annotations

from dataclasses import replace

from drei.commands import (
    BackwardChar,
    BufferObservation,
    BufferSaved,
    CommandOutcome,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KeyboardQuitEvent,
    KillLine,
    PointMoved,
    SaveBuffer,
    SaveFailed,
    TextInserted,
    TextKilled,
    TextYanked,
    TextYankPopped,
    Yank,
    YankPop,
)
from drei.files import FilePort, normalize_os_error
from drei.model import Buffer, BufferValue

Command = (
    InsertText
    | ForwardChar
    | BackwardChar
    | SaveBuffer
    | KillLine
    | Yank
    | YankPop
    | KeyboardQuit
)
Event = (
    TextInserted
    | PointMoved
    | TextKilled
    | TextYanked
    | TextYankPopped
    | BufferSaved
    | SaveFailed
    | KeyboardQuitEvent
)

KILL_RING_CAPACITY = 60


class _NullFilePort:
    """Default port: every save fails with a normalized token."""

    def read(self, path: str) -> str:
        raise FileNotFoundError(path)

    def write(self, path: str, text: str) -> None:
        raise OSError("no file port configured")


class EditorSession:
    def __init__(self, buffer: Buffer, file_port: FilePort | None = None) -> None:
        self.buffer = buffer
        self._files: FilePort = file_port if file_port is not None else _NullFilePort()
        self._transcript: list[Event] = []
        self._kill_ring: list[str] = []
        self._last_was_kill = False
        self._yank_active = False
        self._yank_cursor = 0
        self._yank_bounds = (0, 0)

    @property
    def transcript(self) -> tuple[Event, ...]:
        return tuple(self._transcript)

    @property
    def kill_ring(self) -> tuple[str, ...]:
        """Newest-first view of the kill ring (derived cache, not the oracle)."""
        return tuple(self._kill_ring)

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
                        current, text=new_text, point=after, modified=True
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
            case KeyboardQuit():
                new_value = current
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
        )
        return CommandOutcome(tuple(events), observation)

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
        return replace(current, text=new_text, modified=True)

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
        return replace(current, text=new_text, point=after, modified=True)

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
        return replace(current, text=new_text, point=after, modified=True)
