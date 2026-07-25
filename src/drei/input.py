"""Input events and the source seam (design 0005 D2, plan 0015 D1/D2).

`run_editor` consumes one totally ordered stream of input events instead of
blocking in `TerminalPort.read_key`. This module holds the vocabulary — the
event kinds and the source protocol — and nothing that produces them: the
terminal-backed sources live in :mod:`drei.terminal`, and the agent-backed
source of §C.2 will live with the process port. Keeping the vocabulary in its
own module is what lets both sides depend on it without depending on each
other.

Nothing here reaches the deterministic core. The core still sees only a
serialized sequence of commands; an event is what the *adapter* hands the
loop, one at a time, before the loop decides which command it becomes.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Key:
    """One raw input unit, exactly as `TerminalPort.read_key` returns it.

    Not a symbolic key: `KeyAssembler` still runs in the loop, downstream of
    the source, so escape-sequence assembly is untouched by the seam (plan
    0015 D1). `char` is therefore a raw character, or a platform key event's
    symbolic name where no byte form exists (Windows extended keys).
    """

    char: str


@dataclass(frozen=True, slots=True)
class Resize:
    """The terminal became `width` x `height` character cells.

    An input event rather than a callback or a setter because frame size is
    semantic — it gates `C-x 2` — so it has to enter the same totally ordered
    stream as the keys it interleaves with, and reach the session as a command
    that the transcript records (plan 0015 D3).
    """

    width: int
    height: int


# The union grows only when a producer exists (plan 0015 D1):
# `AgentBytes`/`AgentExited` join in §C.2. A member with no producer would be
# the speculative framework layer the rules forbid.
InputEvent = Key | Resize


class EndOfInput(Exception):
    """The stream is closed and drained: there will be no more input.

    Not an error — it is how a run ends when nothing is left to consume. The
    loop's own exit is a quit key; this is the backstop for a stream that ran
    out from underneath it.
    """


@dataclass(frozen=True, slots=True)
class _ProducerFailed:
    """Queued by a producer that is about to die, carrying why.

    A thread cannot raise into the loop, and a dead producer must not look
    like a quiet one: without this the loop blocks in `next_event` forever
    holding the terminal in raw mode, and `C-g` cannot reach it because the
    thread that would have delivered it is the one that died. It rides the
    queue rather than jumping it, so input the producer already delivered is
    still consumed first.
    """

    error: BaseException


class _Closed:
    """End-of-stream marker. Queued by `close`, never consumed."""


_CLOSED = _Closed()


class EventQueue:
    """The blocking, totally ordered stream: the seam that replaced
    `port.read_key()` in the loop.

    Design 0005 D2 places asynchrony here and nowhere else: producer threads
    own the blocking, the queue owns the ordering, and the loop below it sees
    one event at a time. The queue itself owns no thread and no clock — it is
    the meeting point, not a participant, which is why it can live in this
    module and be shared by the terminal side and the agent side without
    either importing the other.

    Slice 15 made the *source* the abstraction and let each implementation own
    its own queue. Adding a second producer showed that was the wrong seam:
    what varies is the producers, and what must be singular is the mailbox —
    "one totally ordered input stream" is only true if there is exactly one
    queue. So the abstract source is gone and this is the concrete seam.
    """

    def __init__(self) -> None:
        self._events: queue.Queue[InputEvent | _ProducerFailed | _Closed] = (
            queue.Queue()
        )
        self._closing = threading.Event()

    def put(self, event: InputEvent) -> None:
        """Enqueue one event. Dropped once the queue is closed."""
        if not self._closing.is_set():
            self._events.put(event)

    def fail(self, error: BaseException) -> None:
        """Report that a producer died; `next_event` re-raises it in turn."""
        if not self._closing.is_set():
            self._events.put(_ProducerFailed(error))

    def next_event(self) -> InputEvent:
        event = self._events.get()
        if isinstance(event, _Closed):
            # Put it back: the end of input is a state, not a one-shot signal
            # that the next call would forget.
            self._events.put(event)
            raise EndOfInput("the input stream is closed")
        if isinstance(event, _ProducerFailed):
            # Re-raised on the loop's thread, which restores the terminal on
            # the way out — the same end state a synchronous read failure
            # produced before any thread existed.
            raise event.error
        return event

    def close(self) -> None:
        """Stop accepting events and release a consumer already waiting.

        The marker goes to the *back* of the queue, so whatever a producer
        already delivered is still consumed before the run ends.
        """
        self._closing.set()
        self._events.put(_CLOSED)
