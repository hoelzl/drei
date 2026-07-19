from __future__ import annotations

from dataclasses import dataclass

from drei.commands import BackwardChar, ForwardChar, InsertText, KeyboardQuit
from drei.session import Command

_CONTROL_KEYS: dict[str, Command] = {
    "C-f": ForwardChar(),
    "C-b": BackwardChar(),
    "C-g": KeyboardQuit(),
}


@dataclass(frozen=True, slots=True)
class UnresolvedKey:
    key: str


def resolve(key: str) -> Command | UnresolvedKey:
    if key in _CONTROL_KEYS:
        return _CONTROL_KEYS[key]
    if len(key) == 1 and key.isprintable():
        return InsertText(key)
    return UnresolvedKey(key)
