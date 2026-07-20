from __future__ import annotations

from dataclasses import dataclass

from drei.commands import (
    BackwardChar,
    CopyRegionAsKill,
    ExchangePointAndMark,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KillLine,
    KillRegion,
    SaveBuffer,
    SetMark,
    Yank,
    YankPop,
)
from drei.session import Command

_CONTROL_KEYS: dict[str, Command] = {
    "C-@": SetMark(),
    "C-f": ForwardChar(),
    "C-b": BackwardChar(),
    "C-g": KeyboardQuit(),
    "C-k": KillLine(),
    "C-w": KillRegion(),
    "C-y": Yank(),
}

_META_KEYS: dict[str, Command] = {
    "M-w": CopyRegionAsKill(),
    "M-y": YankPop(),
}

_PREFIX_COMMANDS: dict[tuple[str, str], Command] = {
    ("C-x", "C-s"): SaveBuffer(),
    ("C-x", "C-x"): ExchangePointAndMark(),
}


@dataclass(frozen=True, slots=True)
class UnresolvedKey:
    key: str


@dataclass(frozen=True, slots=True)
class PendingKey:
    """A key that opened a prefix without completing a command."""

    prefix: str


def resolve(pending: str | None, key: str) -> Command | UnresolvedKey | PendingKey:
    """Resolve one symbolic key, given any pending prefix.

    Pure: the caller (harness) owns the pending value and passes it back in.
    A pending prefix plus a non-completing key records one ``UnresolvedKey``
    for the whole ``"<pending> <key>"`` sequence.
    """
    if pending is not None:
        completed = _PREFIX_COMMANDS.get((pending, key))
        if completed is not None:
            return completed
        return UnresolvedKey(f"{pending} {key}")
    if key == "C-x":
        return PendingKey("C-x")
    if key in _CONTROL_KEYS:
        return _CONTROL_KEYS[key]
    if key in _META_KEYS:
        return _META_KEYS[key]
    if len(key) == 1 and key.isprintable():
        return InsertText(key)
    return UnresolvedKey(key)
