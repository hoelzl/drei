from drei.commands import (
    KeyboardQuitEvent,
    PointMoved,
    TextInserted,
)
from drei.harness import EditorHarness
from drei.keys import UnresolvedKey


def test_harness_produces_exact_evidence() -> None:
    harness = EditorHarness(width=20, height=5)
    harness.send("h")
    harness.send("e")
    harness.send("l")
    harness.send("l")
    harness.send("o")
    harness.send("C-b")
    harness.send("!")
    harness.send("C-f")
    harness.send("C-g")

    assert harness.observation.text == "hell!o"
    assert harness.observation.point == 6
    assert harness.frame.rows[0] == "hell!o              "
    assert harness.frame.cursor == (0, 6)
    assert harness.outcomes[-1].events == (KeyboardQuitEvent(),)


def test_harness_records_unresolved_keys() -> None:
    harness = EditorHarness(width=10, height=3)
    # Bare C-x opens a prefix: nothing recorded yet.
    harness.send("C-x")
    unresolved_before = harness.unresolved
    assert len(unresolved_before) == 0
    assert len(harness.outcomes) == 0
    # A non-completing second key records the whole sequence as unresolved.
    harness.send("C-z")
    assert harness.observation.text == ""
    assert harness.unresolved == (UnresolvedKey("C-x C-z"),)
    assert len(harness.outcomes) == 0


def test_harness_save_via_prefix() -> None:
    from conftest import FakeFilePort

    port = FakeFilePort()
    harness = EditorHarness(
        width=20, height=4, file_port=port, file_path="/tmp/notes.txt"
    )
    harness.send("x")
    harness.send("C-x")
    outcome = harness.send("C-s")
    assert port.files["/tmp/notes.txt"] == "x"
    assert outcome is not None
    assert harness.observation.modified is False
    assert harness.frame.rows[-1].startswith("Wrote /tmp/notes.txt")


def test_harness_save_failure_echoes_token() -> None:
    from conftest import FakeFilePort

    port = FakeFilePort(fail="permission")
    harness = EditorHarness(width=30, height=4, file_port=port, file_path="/root/x.txt")
    harness.send("x")
    harness.send("C-x")
    outcome = harness.send("C-s")
    assert outcome is not None
    assert harness.observation.modified is True
    assert harness.frame.rows[-1].startswith("/root/x.txt: permission-denied")


def test_harness_outcome_sequence() -> None:
    harness = EditorHarness(width=10, height=3)
    harness.send("a")
    harness.send("C-b")
    harness.send("C-f")

    assert harness.outcomes[0].events == (TextInserted("a", 0, 1),)
    assert harness.outcomes[1].events == (PointMoved(-1, -1),)
    assert harness.outcomes[2].events == (PointMoved(1, 1),)
    assert harness.outcomes[2].observation.point == 1


def test_harness_routes_minibuffer_keys() -> None:
    """C-x C-f opens the prompt; keys route to the minibuffer; a pending
    prefix typed before activation is dropped; RET accepts (missing file
    through the null port → empty buffer); C-g aborts and a second C-g
    quits."""
    harness = EditorHarness(width=40, height=6)
    harness.send("z")  # dirty the buffer: text "z"
    harness.send("C-x")  # pending prefix...
    outcome = harness.send("C-f")  # ...completes as C-x C-f → FindFile
    assert outcome is not None
    assert any(type(e).__name__ == "MinibufferOpened" for e in outcome.events)
    assert harness.observation.minibuffer == ""
    assert harness.frame.rows[-1].startswith("Find file: ")

    harness.send("a")
    harness.send("b")
    assert harness.observation.minibuffer == "ab"
    assert harness.frame.rows[-1].startswith("Find file: ab")
    assert harness.frame.cursor[0] == len(harness.frame.rows) - 1  # echo row
    harness.send("DEL")
    assert harness.observation.minibuffer == "a"

    # Pending prefix is dead; control keys ignored while active.
    assert harness.send("C-f") is None  # ForwardChar does NOT run
    assert harness.observation.minibuffer == "a"

    # Abort: prompt closes, buffer and mark untouched, no quit.
    outcome = harness.send("C-g")
    assert outcome is not None
    assert any(type(e).__name__ == "MinibufferAborted" for e in outcome.events)
    assert all(type(e).__name__ != "KeyboardQuitEvent" for e in outcome.events)
    closed = harness.observation.minibuffer
    assert closed is None
    # mypy's property narrowing survives the send() call above; the buffer
    # really is still "z" at runtime.
    assert harness.observation.text == "z"  # type: ignore[unreachable]

    # Accept path: open again, type a path, RET → null port read fails
    # not-found → empty buffer at that path.
    harness.send("C-x")
    harness.send("C-f")
    for char in "/tmp/nope.txt":
        harness.send(char)
    outcome = harness.send("RET")
    assert outcome is not None
    assert any(type(e).__name__ == "BufferOpened" for e in outcome.events)
    assert harness.observation.text == ""
    assert harness.observation.file_path == "/tmp/nope.txt"
    assert harness.observation.minibuffer is None
