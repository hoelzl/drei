"""ACP client core (design 0003 §B) — pure protocol values, no I/O.

This package models the Agent Client Protocol's wire format and message
envelopes as pure functions/values. Effects stay behind Drei's ports; nothing
here imports ``subprocess``/``asyncio``/``os``-to-launch. The §C launcher
wires these to the ``ProcessPort`` delivery seam.
"""

from __future__ import annotations

from drei.acp.codec import AcpDecodeError, JsonRpcDecoder, encode

__all__ = [
    "AcpDecodeError",
    "JsonRpcDecoder",
    "encode",
    "messages",
]


def __getattr__(name: str) -> object:
    # Lazily expose the method-name constants (INITIALIZE, SESSION_NEW, ...)
    # and envelope types without importing drei.commands (which drei.process
    # imports) at codec import time — avoids a potential import cycle.
    import drei.acp.messages as messages

    return getattr(messages, name)


def __dir__() -> list[str]:
    return sorted(__all__)
