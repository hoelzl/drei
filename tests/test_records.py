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
    records = [
        (InsertText("x"), "text"),
        (ForwardChar(), None),
        (BackwardChar(), None),
        (KeyboardQuit(), None),
        (TextInserted("x", 0, 1), "text"),
        (PointMoved(1, 1), "requested"),
        (KeyboardQuitEvent(), None),
        (BufferObservation(buffer_id="scratch", text="x", point=1), "text"),
        (
            CommandOutcome(
                (), BufferObservation(buffer_id="scratch", text="", point=0)
            ),
            "events",
        ),
    ]
    for record, field in records:
        if field is not None:
            with pytest.raises(AttributeError):
                setattr(record, field, None)
        # Frozen dataclasses with slots reject new attributes too (the exact
        # exception type varies by CPython version).
        with pytest.raises((AttributeError, TypeError)):
            record.other = None  # type: ignore[attr-defined]


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
