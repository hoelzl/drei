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
