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
    harness.send("C-x")
    assert harness.observation.text == ""
    assert harness.unresolved == (UnresolvedKey("C-x"),)
    assert harness.outcomes == ()


def test_harness_outcome_sequence() -> None:
    harness = EditorHarness(width=10, height=3)
    harness.send("a")
    harness.send("C-b")
    harness.send("C-f")

    assert harness.outcomes[0].events == (TextInserted("a", 0, 1),)
    assert harness.outcomes[1].events == (PointMoved(-1, -1),)
    assert harness.outcomes[2].events == (PointMoved(1, 1),)
    assert harness.outcomes[2].observation.point == 1
