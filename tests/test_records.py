import pytest

from drei.acp.codec import DecodedFrame, DecodeFailure
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
from drei.files import VisitOpened, VisitRejected
from drei.genesis import UNKNOWN_FRAME, KnownFrame, scratch_genesis


def test_records_are_frozen() -> None:
    genesis = scratch_genesis(UNKNOWN_FRAME)
    records = [
        (DecodedFrame({"ok": True}), "value"),
        (DecodeFailure(b"bad"), "line"),
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
        (
            VisitOpened("existing_file", "f", "f", "x", "x", "\n"),
            "text",
        ),
        (VisitRejected("f", "io-error"), "error"),
        (genesis.initial_buffer, "text"),
        (genesis.initial_windows[0], "point"),
        (KnownFrame(80, 24), "width"),
        (UNKNOWN_FRAME, None),
        (genesis, "focused_window"),
    ]
    for record, field in records:
        if field is not None:
            with pytest.raises(AttributeError):
                setattr(record, field, None)
        # Frozen dataclasses with slots reject new attributes too (the exact
        # exception type varies by CPython version).
        with pytest.raises((AttributeError, TypeError)):
            record.other = None  # type: ignore[attr-defined]


def test_decode_result_records_are_slotted() -> None:
    records = (
        (DecodedFrame({"ok": True}), ("value",)),
        (DecodeFailure(b"bad"), ("line",)),
    )
    for record, fields in records:
        assert type(record).__slots__ == fields
        assert not hasattr(record, "__dict__")


def test_genesis_and_visit_records_are_slotted() -> None:
    genesis = scratch_genesis(UNKNOWN_FRAME)
    records = (
        VisitOpened("existing_file", "f", "f", "x", "x", "\n"),
        VisitRejected("f", "io-error"),
        genesis.initial_buffer,
        genesis.initial_windows[0],
        KnownFrame(80, 24),
        UNKNOWN_FRAME,
        genesis,
    )
    for record in records:
        assert not hasattr(record, "__dict__")


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
