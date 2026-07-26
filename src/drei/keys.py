from __future__ import annotations

from dataclasses import dataclass

from drei.commands import (
    BackwardChar,
    CopyRegionAsKill,
    DeleteOtherWindows,
    ExchangePointAndMark,
    ExitEditor,
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
    # `C-c` is also a prefix in its own right, and this pair wins: while a
    # prefix is pending, `resolve` returns from the pending branch before the
    # prefix SET is ever consulted, so `C-x C-c` completes rather than opening
    # a nested prefix.
    ("C-x", "C-c"): ExitEditor(),
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
        if key == "C-g":
            # Cancel the prefix and quit, as Emacs does. Dropping the pending
            # prefix is implicit: the caller clears it for anything that is
            # not a `PendingKey`. A user who has half-typed a chord and wants
            # out presses this key, so something has to happen — it used to
            # become one silent `UnresolvedKey("C-x C-g")`.
            return KeyboardQuit()
        # Every *other* unbound key after a prefix resolves to one
        # UnresolvedKey for the whole sequence; the harness owns the echo —
        # "C-x <key> is undefined" (row 134, plan 0019 D7) — because no
        # command exists for the session to speak about.
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
