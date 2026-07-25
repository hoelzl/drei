"""The shared event mailbox (design 0005 D2, plan 0016 D4).

`EventQueue` is what makes "one totally ordered input stream" a thing rather
than a phrase: every producer — the terminal's two reader threads, the agent's
— pushes into it, and the loop takes one event at a time. It owns no thread and
no clock; the threads live in the producers.
"""

from __future__ import annotations

import threading

import pytest

from drei.input import EndOfInput, EventQueue, Key, Resize


def test_events_come_out_in_the_order_they_went_in() -> None:
    events = EventQueue()
    events.put(Key("a"))
    events.put(Resize(20, 5))
    events.put(Key("b"))
    assert events.next_event() == Key("a")
    assert events.next_event() == Resize(20, 5)
    assert events.next_event() == Key("b")


def test_a_producer_failure_is_raised_on_the_consumer_thread() -> None:
    """A thread cannot raise into the loop. Without this a dead producer looks
    exactly like a quiet one, and the loop blocks forever holding the terminal
    in raw mode (the regression slice 15's review found)."""
    events = EventQueue()
    events.fail(OSError(5, "Input/output error"))
    with pytest.raises(OSError, match="Input/output error"):
        events.next_event()


def test_a_failure_waits_its_turn_behind_the_events_already_queued() -> None:
    """The failure is ordered, not out-of-band: input the producer already
    delivered is still input, and dropping it would lose keystrokes the user
    really typed."""
    events = EventQueue()
    events.put(Key("a"))
    events.fail(RuntimeError("boom"))
    assert events.next_event() == Key("a")
    with pytest.raises(RuntimeError, match="boom"):
        events.next_event()


def test_a_closed_queue_drains_what_it_holds_and_then_ends_the_run() -> None:
    events = EventQueue()
    events.put(Key("a"))
    events.close()
    assert events.next_event() == Key("a")
    with pytest.raises(EndOfInput):
        events.next_event()


def test_a_closed_queue_stays_closed() -> None:
    """Every later `next_event` ends the run too — the end of input is not a
    one-shot signal that the next call forgets."""
    events = EventQueue()
    events.close()
    for _ in range(3):
        with pytest.raises(EndOfInput):
            events.next_event()


def test_close_is_idempotent() -> None:
    events = EventQueue()
    events.close()
    events.close()
    with pytest.raises(EndOfInput):
        events.next_event()


def test_a_producer_racing_the_shutdown_cannot_reopen_the_queue() -> None:
    """Producers are threads and they stop when they notice, not when asked, so
    one of them putting an event after close is normal. It is dropped: a run
    that has ended does not resume because a straggler arrived."""
    events = EventQueue()
    events.close()
    events.put(Key("a"))
    events.fail(RuntimeError("late"))
    with pytest.raises(EndOfInput):
        events.next_event()


def test_close_wakes_a_consumer_already_blocked_on_an_empty_queue() -> None:
    """The one concurrency property the mailbox itself owns: `close` from
    another thread has to release a `next_event` that is already waiting, or
    shutdown deadlocks against the very call it is trying to end."""
    events = EventQueue()
    raised = threading.Event()

    def consume() -> None:
        try:
            events.next_event()
        except EndOfInput:
            raised.set()

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    events.close()
    consumer.join(timeout=5)
    assert raised.is_set(), "close() did not release the blocked consumer"
