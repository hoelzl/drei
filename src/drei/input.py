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

import abc
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


# The union grows only when a producer exists (plan 0015 D1). `Resize` joins
# it in V3 of this slice; `AgentBytes`/`AgentExited` in §C.2. A member with no
# producer would be the speculative framework layer the rules forbid.
InputEvent = Key


class InputSource(abc.ABC):
    """A blocking, totally ordered stream of input events.

    The seam that replaces `port.read_key()` in the loop. Implementations may
    own threads, queues, and clocks — all of which are adapter concerns and
    all of which stay on this side of the boundary.
    """

    @abc.abstractmethod
    def next_event(self) -> InputEvent:
        """Block until the next event is available, then return it."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release whatever the source owns. Idempotent."""
