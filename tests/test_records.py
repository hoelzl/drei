import pytest

from drei.commands import (
    BackwardChar,
    BufferObservation,
    CommandOutcome,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KeyboardQuitEvent,
    PointMoved,
    TextInserted,
)


def test_records_are_frozen() -> None:
    with pytest.raises(AttributeError):
        InsertText("x").text = "y"  # type: ignore[misc]


def test_structural_equality() -> None:
    assert InsertText("x") == InsertText("x")
    assert ForwardChar() == ForwardChar()
    assert BackwardChar() == BackwardChar()
    assert KeyboardQuit() == KeyboardQuit()


def test_event_records_carry_expected_fields() -> None:
    inserted = TextInserted(text="ab", before=0, after=2)
    assert inserted.text == "ab"
    assert inserted.before == 0
    assert inserted.after == 2

    moved = PointMoved(requested=1, actual=1)
    assert moved.requested == 1
    assert moved.actual == 1

    quit_event = KeyboardQuitEvent()
    assert quit_event == KeyboardQuitEvent()


def test_observation_and_outcome_are_values() -> None:
    obs = BufferObservation(buffer_id="scratch", text="x", point=1)
    outcome = CommandOutcome(events=(TextInserted("x", 0, 1),), observation=obs)
    assert outcome.observation is obs
