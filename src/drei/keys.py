from __future__ import annotations

from dataclasses import dataclass

from drei.commands import (
    BackwardChar,
    CopyRegionAsKill,
    DeleteOtherWindows,
    ExchangePointAndMark,
    FindFile,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KillLine,
    KillRegion,
    OtherWindow,
    PromptAgent,
    SaveBuffer,
    SetMark,
    SplitWindow,
    SwitchBuffer,
    Undo,
    Yank,
    YankPop,
)
from drei.session import Command

_CONTROL_KEYS: dict[str, Command] = {
    "C-@": SetMark(),
    "C-/": Undo(),  # \x1f — same byte as C-_
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

# Emacs reserves C-c for the user and the major mode, which is exactly where
# an editor-specific command like "talk to the agent" belongs. C-x is the
# global prefix and stays that.
_PREFIXES = frozenset({"C-x", "C-c"})

_PREFIX_COMMANDS: dict[tuple[str, str], Command] = {
    ("C-c", "a"): PromptAgent(),
    ("C-x", "C-s"): SaveBuffer(),
    ("C-x", "C-x"): ExchangePointAndMark(),
    ("C-x", "u"): Undo(),
    ("C-x", "C-f"): FindFile(),
    ("C-x", "b"): SwitchBuffer(),
    ("C-x", "2"): SplitWindow(),
    ("C-x", "o"): OtherWindow(),
    ("C-x", "1"): DeleteOtherWindows(),
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
        # TODO: [tech-debt] TD-5 — C-g lands here too, so "C-x C-g" is one
        # silent unresolved key: no quit, no echo, and the mark survives.
        # Emacs cancels the prefix and quits. See docs/technical-debt.md.
        return UnresolvedKey(f"{pending} {key}")
    if key in _PREFIXES:
        return PendingKey(key)
    if key in _CONTROL_KEYS:
        return _CONTROL_KEYS[key]
    if key in _META_KEYS:
        return _META_KEYS[key]
    if len(key) == 1 and key.isprintable():
        return InsertText(key)
    return UnresolvedKey(key)
