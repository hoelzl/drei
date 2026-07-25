"""Input events and the queue seam (design 0005 D2, plans 0015/0016).

`run_editor` consumes one totally ordered stream of input events instead of
blocking in `TerminalPort.read_key`. This module holds the vocabulary — the
event kinds — and the mailbox they meet in, and nothing that produces them:
the terminal producers live in :mod:`drei.terminal` and the agent producers in
:mod:`drei.pump`. Keeping this in its own module is what lets both sides
depend on it without depending on each other.

Nothing here reaches the deterministic core. The core still sees only a
serialized sequence of commands; an event is what the *adapter* hands the
loop, one at a time, before the loop decides which command it becomes.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Key:
    """One raw input unit, exactly as `TerminalPort.read_key` returns it.

    Not a symbolic key: `KeyAssembler` still runs in the loop, downstream of
    the queue, so escape-sequence assembly is untouched by the seam (plan
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


@dataclass(frozen=True, slots=True)
class AgentBytes:
    """Wire bytes from the agent child, exactly as the pipe delivered them.

    Bytes, not frames: framing is the decoder's job and `JsonRpcDecoder` is
    chunk-safe by construction, so a producer that tried to split on newlines
    would be doing the decoder's work worse (a multi-byte character can be
    split too).
    """

    data: bytes


@dataclass(frozen=True, slots=True)
class AgentStderr:
    """Diagnostics from the agent child. Never enters the wire decoder."""

    data: bytes


@dataclass(frozen=True, slots=True)
class AgentExited:
    """The agent child is gone.

    ``status`` is ``int | None`` rather than ``int`` because a child that
    vanished between the read returning ``b""`` and the ``poll()`` can
    legitimately have no status yet. ``None`` means "gone, status unknown",
    and it is rendered as such rather than guessed at.
    """

    status: int | None


InputEvent = Key | Resize | AgentBytes | AgentStderr | AgentExited

# The events a *verifier* dispatches, and therefore the events that must not
# queue behind anything (design 0005 D3) — and, for the same underlying
# reason, exactly the events that carry a readiness marker. An event in this
# lane is one somebody is waiting on; an event outside it arrived on the
# peer's schedule and belongs to no input epoch.
_PRIORITY = (Key, Resize)


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
    *priority* lane, so input the producer already delivered is still consumed
    first — but queued agent output is not: if the terminal producer is dead,
    nothing after this can be typed, and there is no reason to hand the peer's
    backlog to an editor that is about to unwind.

    Only the *terminal* producers use it. An agent reader that fails reports
    `AgentExited`: a dead child is a peer failure the editor survives, and
    killing the editor because a pipe broke would be the wrong trade.
    """

    error: BaseException


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
    queue.

    **Two lanes, not one** (design 0005 D3). A human's keystroke must not
    queue behind a paragraph of streamed agent text; agent output is bursty by
    nature and the human is not. The starvation is deliberately asymmetric:
    agent events wait only while keys keep arriving, and a human cannot type
    indefinitely.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = threading.Condition(self._lock)
        self._priority: deque[InputEvent | _ProducerFailed] = deque()
        self._background: deque[InputEvent] = deque()
        self._closed = False

    def put(self, event: InputEvent) -> None:
        """Enqueue one event, in its lane. Dropped once the queue is closed."""
        with self._ready:
            if self._closed:
                return
            if isinstance(event, _PRIORITY):
                self._priority.append(event)
            else:
                self._background.append(event)
            self._ready.notify()

    def fail(self, error: BaseException) -> None:
        """Report that a producer died; `next_event` re-raises it in turn."""
        with self._ready:
            if self._closed:
                return
            self._priority.append(_ProducerFailed(error))
            self._ready.notify()

    def next_event(self) -> InputEvent:
        with self._ready:
            while True:
                if self._priority:
                    item = self._priority.popleft()
                    if isinstance(item, _ProducerFailed):
                        # Re-raised on the loop's thread, which restores the
                        # terminal on the way out — the same end state a
                        # synchronous read failure produced before any thread
                        # existed.
                        raise item.error
                    return item
                if self._background:
                    return self._background.popleft()
                if self._closed:
                    raise EndOfInput("the input stream is closed")
                self._ready.wait()

    def close(self) -> None:
        """Stop accepting events and release a consumer already waiting.

        Whatever a producer already delivered is still consumed first: the
        closed flag is checked only once both lanes are empty.
        """
        with self._ready:
            self._closed = True
            self._ready.notify_all()
