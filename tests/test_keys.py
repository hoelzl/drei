from drei.commands import BackwardChar, ForwardChar, InsertText, KeyboardQuit
from drei.keys import UnresolvedKey, resolve


def test_printable_text_resolves_to_insert() -> None:
    assert resolve("a") == InsertText("a")
    assert resolve("λ") == InsertText("λ")
    assert resolve(" ") == InsertText(" ")


def test_control_keys_resolve_to_commands() -> None:
    assert resolve("C-f") == ForwardChar()
    assert resolve("C-b") == BackwardChar()
    assert resolve("C-g") == KeyboardQuit()


def test_unsupported_key_is_explicitly_unresolved() -> None:
    result = resolve("C-x")
    assert isinstance(result, UnresolvedKey)
    assert result.key == "C-x"
