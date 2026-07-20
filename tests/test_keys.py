from drei.commands import (
    BackwardChar,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KillLine,
    SaveBuffer,
    Yank,
)
from drei.keys import PendingKey, UnresolvedKey, resolve


def test_printable_text_resolves_to_insert() -> None:
    assert resolve(None, "a") == InsertText("a")
    assert resolve(None, "λ") == InsertText("λ")
    assert resolve(None, " ") == InsertText(" ")


def test_control_keys_resolve_to_commands() -> None:
    assert resolve(None, "C-f") == ForwardChar()
    assert resolve(None, "C-b") == BackwardChar()
    assert resolve(None, "C-g") == KeyboardQuit()
    assert resolve(None, "C-k") == KillLine()
    assert resolve(None, "C-y") == Yank()


def test_cx_enters_pending_state() -> None:
    result = resolve(None, "C-x")
    assert result == PendingKey("C-x")


def test_cx_cs_resolves_to_save() -> None:
    assert resolve("C-x", "C-s") == SaveBuffer()


def test_cx_then_other_key_is_unresolved_and_clears_pending() -> None:
    result = resolve("C-x", "a")
    assert isinstance(result, UnresolvedKey)
    assert result.key == "C-x a"


def test_pending_second_key_cx_again_is_unresolved() -> None:
    result = resolve("C-x", "C-x")
    assert isinstance(result, UnresolvedKey)
    assert result.key == "C-x C-x"


def test_unsupported_key_is_explicitly_unresolved() -> None:
    result = resolve(None, "C-z")
    assert isinstance(result, UnresolvedKey)
    assert result.key == "C-z"


def test_pending_does_not_leak_into_normal_resolution() -> None:
    # A resolved pending sequence leaves no residue: resolving after a
    # completed prefix behaves like a fresh resolve.
    resolve("C-x", "C-s")
    assert resolve(None, "a") == InsertText("a")
