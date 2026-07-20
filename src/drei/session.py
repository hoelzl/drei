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
    PointMoved,
    SaveBuffer,
    SaveFailed,
    TextInserted,
)
from drei.files import FilePort, normalize_os_error
from drei.model import Buffer, BufferValue

Command = InsertText | ForwardChar | BackwardChar | SaveBuffer | KeyboardQuit
Event = TextInserted | PointMoved | BufferSaved | SaveFailed | KeyboardQuitEvent


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

    @property
    def transcript(self) -> tuple[Event, ...]:
        return tuple(self._transcript)

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
            case KeyboardQuit():
                new_value = current
                events.append(KeyboardQuitEvent())
            case _:
                raise TypeError(f"unsupported command: {type(command)}")

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
