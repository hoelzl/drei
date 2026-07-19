from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BufferId:
    value: str


@dataclass(frozen=True, slots=True)
class BufferValue:
    text: str
    point: int

    def __post_init__(self) -> None:
        if not 0 <= self.point <= len(self.text):
            raise ValueError(f"point {self.point} outside 0..{len(self.text)}")


class Buffer:
    def __init__(self, buffer_id: BufferId, initial: BufferValue) -> None:
        self.buffer_id = buffer_id
        self._current = initial

    @property
    def current(self) -> BufferValue:
        return self._current

    def replace(self, value: BufferValue) -> None:
        self._current = value
