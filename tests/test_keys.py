from drei.commands import (
    BackwardChar,
    CopyRegionAsKill,
    ExchangePointAndMark,
    ExitEditor,
    FindFile,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KillLine,
    KillRegion,
    SaveBuffer,
    SetMark,
    Undo,
    Yank,
    YankPop,
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


def test_meta_y_resolves_to_yank_pop() -> None:
    assert resolve(None, "M-y") == YankPop()


def test_unknown_meta_chord_is_unresolved() -> None:
    assert resolve(None, "M-x") == UnresolvedKey("M-x")


def test_cx_enters_pending_state() -> None:
    result = resolve(None, "C-x")
    assert result == PendingKey("C-x")


def test_cx_cs_resolves_to_save() -> None:
    assert resolve("C-x", "C-s") == SaveBuffer()


def test_cx_cc_resolves_to_exit() -> None:
    """`C-c` is a prefix in its own right, and the pair still wins.

    While a prefix is pending, `resolve` never consults the prefix SET at all
    — the pending branch returns before reaching it. Hoisting that check above
    the pending branch would turn `C-x C-c` into a nested pending prefix and
    make exiting unreachable. (It would also break `C-x C-x`, which
    `test_pending_second_key_cx_again_is_unresolved` catches; this test is the
    one that names the consequence that matters.)
    """
    assert resolve("C-x", "C-c") == ExitEditor()
    assert resolve(None, "C-c") == PendingKey("C-c")


def test_cx_then_other_key_is_unresolved_and_clears_pending() -> None:
    result = resolve("C-x", "a")
    assert isinstance(result, UnresolvedKey)
    assert result.key == "C-x a"


def test_c_g_cancels_a_pending_prefix_and_quits() -> None:
    """TD-5: `C-g` after a prefix used to become one silent
    `UnresolvedKey("C-x C-g")` — no quit, no echo, and the mark survived.

    Emacs cancels the prefix *and* quits, which is the whole point of the
    key: a user who has half-typed a chord and wants out presses `C-g`, and
    something must happen.
    """
    assert resolve("C-x", "C-g") == KeyboardQuit()
    assert resolve("C-c", "C-g") == KeyboardQuit()


def test_a_prefix_still_swallows_every_other_unbound_key() -> None:
    """Only `C-g` is special-cased. Whether an unbound chord should echo
    `C-x <key> is undefined` the way Emacs does is a question about prefix
    semantics generally, and it needs the echo mechanism TD-4 tracks."""
    for key in ("a", "z", "C-z", "RET"):
        result = resolve("C-x", key)
        assert isinstance(result, UnresolvedKey), key


def test_pending_second_key_cx_again_is_unresolved() -> None:
    # C-x C-x is bound (exchange-point-and-mark), so no longer unresolved.
    assert resolve("C-x", "C-x") == ExchangePointAndMark()


def test_mark_region_keys_resolve_to_commands() -> None:
    assert resolve(None, "C-@") == SetMark()
    assert resolve(None, "C-w") == KillRegion()
    assert resolve(None, "M-w") == CopyRegionAsKill()


def test_undo_keys_resolve_to_commands() -> None:
    assert resolve(None, "C-/") == Undo()
    assert resolve("C-x", "u") == Undo()


def test_cx_cf_resolves_to_find_file() -> None:
    assert resolve("C-x", "C-f") == FindFile()


def test_ret_and_del_are_unresolved_in_main_map() -> None:
    # Both decode from \x0d / \x7f (minibuffer-only); inactive = unresolved.
    assert isinstance(resolve(None, "RET"), UnresolvedKey)
    assert isinstance(resolve(None, "DEL"), UnresolvedKey)


def test_unsupported_key_is_explicitly_unresolved() -> None:
    result = resolve(None, "C-z")
    assert isinstance(result, UnresolvedKey)
    assert result.key == "C-z"


def test_pending_does_not_leak_into_normal_resolution() -> None:
    # A resolved pending sequence leaves no residue: resolving after a
    # completed prefix behaves like a fresh resolve.
    resolve("C-x", "C-s")
    assert resolve(None, "a") == InsertText("a")


def test_navigation_keys_are_unresolved_not_self_inserting() -> None:
    """Arrows and other escape-sequence keys must never insert text.

    Registered deviation from Emacs (which binds them): Drei leaves them
    unbound for now — but they must resolve as unresolved keys rather than
    falling through to InsertText (finding 4).
    """
    for key in ("<up>", "<down>", "<right>", "<left>", "<csi:3~>", "<ext:S>"):
        assert resolve(None, key) == UnresolvedKey(key)
