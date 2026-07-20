"""ACP client core (design 0003 §B) — pure protocol values, no I/O.

This package models the Agent Client Protocol's wire format and message
envelopes as pure functions/values. Effects stay behind Drei's ports; nothing
here imports ``subprocess``/``asyncio``/``os``-to-launch. The §C launcher
wires these to the ``ProcessPort`` delivery seam.

The public surface re-exports the codec and the message-envelope layer.
``drei.acp.messages`` imports only ``dataclasses``/``typing`` — it must never
import ``drei.commands`` (the process/session layer), or this package would
pull effect-adjacent modules into the pure protocol core. The purity guard and
the import tests pin that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from drei.acp.codec import AcpDecodeError, JsonRpcDecoder, encode

if TYPE_CHECKING:
    # Re-export the message-layer symbols for static typing without importing
    # the submodule eagerly at package import time. Runtime access to these
    # resolves through ``drei.acp.messages`` (imported on demand below).
    from drei.acp.messages import (
        AUTHENTICATE,
        FS_READ_TEXT_FILE,
        FS_WRITE_TEXT_FILE,
        INITIALIZE,
        SESSION_CANCEL,
        SESSION_LOAD,
        SESSION_NEW,
        SESSION_PROMPT,
        SESSION_REQUEST_PERMISSION,
        SESSION_UPDATE,
        TERMINAL_CREATE,
        TERMINAL_KILL,
        TERMINAL_OUTPUT,
        TERMINAL_RELEASE,
        TERMINAL_WAIT_FOR_EXIT,
        AcpProtocolError,
        Message,
        Notification,
        Request,
        RequestId,
        Response,
        ResponseError,
        parse_message,
        to_json,
    )

__all__ = [
    "AUTHENTICATE",
    "FS_READ_TEXT_FILE",
    "FS_WRITE_TEXT_FILE",
    "INITIALIZE",
    "SESSION_CANCEL",
    "SESSION_LOAD",
    "SESSION_NEW",
    "SESSION_PROMPT",
    "SESSION_REQUEST_PERMISSION",
    "SESSION_UPDATE",
    "TERMINAL_CREATE",
    "TERMINAL_KILL",
    "TERMINAL_OUTPUT",
    "TERMINAL_RELEASE",
    "TERMINAL_WAIT_FOR_EXIT",
    "AcpDecodeError",
    "AcpProtocolError",
    "JsonRpcDecoder",
    "Message",
    "Notification",
    "Request",
    "RequestId",
    "Response",
    "ResponseError",
    "encode",
    "messages",
    "parse_message",
    "to_json",
]


def __getattr__(name: str) -> object:
    # Resolve message-layer symbols (constants, envelope types, parse/build)
    # on demand, so importing drei.acp for the codec does not import the
    # message layer unless it is used.
    import drei.acp.messages as messages

    return getattr(messages, name)


def __dir__() -> list[str]:
    return sorted(__all__)
