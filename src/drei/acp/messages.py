"""JSON-RPC 2.0 envelope model for ACP (design 0003 §B.5).

Frozen dataclasses + ``to_json``/``parse_message`` so the state machine never
hand-builds dicts. Framing-agnostic: these are the *envelopes*;
``drei.acp.codec`` frames them onto the wire. Structurally invalid envelopes
raise :class:`AcpProtocolError`. Pure — no I/O, no effect imports.

JSON-RPC 2.0 envelope rules enforced here:
- ``jsonrpc`` must be exactly ``"2.0"``.
- A *request* has ``id`` + ``method`` (+ optional ``params``).
- A *notification* has ``method`` (+ optional ``params``) and **no** ``id``.
- A *response* has ``id`` + exactly one of ``result`` / ``error``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ACP method names (the subset Drei's client speaks or handles). Constants so
# downstream dispatch is never stringly-typed against literals.
INITIALIZE = "initialize"
AUTHENTICATE = "authenticate"
SESSION_NEW = "session/new"
SESSION_LOAD = "session/load"
SESSION_PROMPT = "session/prompt"
SESSION_CANCEL = "session/cancel"
SESSION_UPDATE = "session/update"
SESSION_REQUEST_PERMISSION = "session/request_permission"
FS_READ_TEXT_FILE = "fs/read_text_file"
FS_WRITE_TEXT_FILE = "fs/write_text_file"
TERMINAL_CREATE = "terminal/create"
TERMINAL_OUTPUT = "terminal/output"
TERMINAL_RELEASE = "terminal/release"
TERMINAL_WAIT_FOR_EXIT = "terminal/wait_for_exit"
TERMINAL_KILL = "terminal/kill"

type JsonValue = Any
type RequestId = int | str


class AcpProtocolError(Exception):
    """An envelope is structurally invalid JSON-RPC 2.0."""


@dataclass(frozen=True, slots=True)
class Request:
    """A method call expecting a response (carries an ``id``)."""

    id: RequestId
    method: str
    params: JsonValue = None


@dataclass(frozen=True, slots=True)
class Notification:
    """A one-way method call (no ``id``, no response expected)."""

    method: str
    params: JsonValue = None


@dataclass(frozen=True, slots=True)
class Response:
    """A successful result to a request (matched by ``id``)."""

    id: RequestId
    result: JsonValue


@dataclass(frozen=True, slots=True)
class ResponseError:
    """An error result to a request (matched by ``id``)."""

    id: RequestId
    code: int
    message: str
    data: JsonValue = None


type Message = Request | Notification | Response | ResponseError


def to_json(message: Message) -> dict[str, JsonValue]:
    """Serialize an envelope to its JSON-RPC 2.0 dict form."""
    if isinstance(message, Request):
        payload: dict[str, JsonValue] = {
            "jsonrpc": "2.0",
            "id": message.id,
            "method": message.method,
        }
        if message.params is not None:
            payload["params"] = message.params
        return payload
    if isinstance(message, Notification):
        payload = {"jsonrpc": "2.0", "method": message.method}
        if message.params is not None:
            payload["params"] = message.params
        return payload
    if isinstance(message, Response):
        return {"jsonrpc": "2.0", "id": message.id, "result": message.result}
    # ResponseError
    error: dict[str, JsonValue] = {"code": message.code, "message": message.message}
    if message.data is not None:
        error["data"] = message.data
    return {"jsonrpc": "2.0", "id": message.id, "error": error}


def parse_message(payload: JsonValue) -> Message:
    """Parse a decoded JSON-RPC 2.0 object into a typed envelope.

    Raises :class:`AcpProtocolError` for a structurally invalid envelope.
    """
    if not isinstance(payload, dict):
        raise AcpProtocolError(
            f"envelope must be a JSON object, got {type(payload).__name__}"
        )
    if payload.get("jsonrpc") != "2.0":
        raise AcpProtocolError(
            f'envelope must carry jsonrpc="2.0", got {payload.get("jsonrpc")!r}'
        )

    has_id = "id" in payload
    has_method = "method" in payload
    has_result = "result" in payload
    has_error = "error" in payload

    if has_result and has_error:
        raise AcpProtocolError("response carries both result and error")

    if has_method:
        method = payload["method"]
        if not isinstance(method, str):
            raise AcpProtocolError(f"method must be a string, got {method!r}")
        params = payload.get("params")
        if has_id:
            return Request(id=payload["id"], method=method, params=params)
        return Notification(method=method, params=params)

    if has_id and has_result:
        return Response(id=payload["id"], result=payload["result"])

    if has_id and has_error:
        error = payload["error"]
        if not isinstance(error, dict) or "code" not in error or "message" not in error:
            raise AcpProtocolError(
                f"error must be an object with code and message, got {error!r}"
            )
        return ResponseError(
            id=payload["id"],
            code=error["code"],
            message=error["message"],
            data=error.get("data"),
        )

    raise AcpProtocolError(f"unrecognized envelope shape: {sorted(payload)}")
