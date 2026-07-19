from __future__ import annotations

from drei.commands import (
    BackwardChar,
    BufferObservation,
    CommandOutcome,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KeyboardQuitEvent,
    PointMoved,
    TextInserted,
)
from drei.model import Buffer, BufferValue

Command = InsertText | ForwardChar | BackwardChar | KeyboardQuit
Event = TextInserted | PointMoved | KeyboardQuitEvent


class EditorSession:
    def __init__(self, buffer: Buffer) -> None:
        self.buffer = buffer
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
                    new_value = BufferValue(new_text, after)
                    events.append(TextInserted(text, before, after))
                else:
                    new_value = current
            case ForwardChar():
                new_point = min(current.point + 1, len(current.text))
                actual = new_point - current.point
                new_value = BufferValue(current.text, new_point)
                events.append(PointMoved(1, actual))
            case BackwardChar():
                new_point = max(current.point - 1, 0)
                actual = new_point - current.point
                new_value = BufferValue(current.text, new_point)
                events.append(PointMoved(-1, actual))
            case KeyboardQuit():
                new_value = current
                events.append(KeyboardQuitEvent())
            case _:
                raise TypeError(f"unsupported command: {type(command)}")

        try:
            self.buffer.replace(new_value)
        except ValueError:
            self.buffer.replace(current)
            raise

        self._transcript.extend(events)
        observation = BufferObservation(
            buffer_id=self.buffer.buffer_id.value,
            text=new_value.text,
            point=new_value.point,
        )
        return CommandOutcome(tuple(events), observation)
