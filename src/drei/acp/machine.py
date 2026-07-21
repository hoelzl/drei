"""Pure ACP client session state machine (design 0003 §B.6, plan 0010).

Models the client side of the ACP session lifecycle —
``initialize`` → ``session/new`` → ``session/prompt`` → stream of
``session/update`` → completion/cancel — as a frozen value folded over inbound
JSON-RPC envelopes (``drei.acp.messages``), emitting outbound envelopes and
typed :class:`SessionEffect` values.

**Pure:** no I/O, no ``subprocess``, no ``asyncio``, no editor change. The §C
launcher drives the machine over the ``ProcessPort`` delivery seam; B.7/B.8
translate its ``SessionEffect`` values into editor commands and approval
prompts. Pinned to ``agent-client-protocol 0.9.0`` (camelCase keys, snake_case
discriminators).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from drei.acp.messages import (
    FS_READ_TEXT_FILE,
    FS_WRITE_TEXT_FILE,
    INITIALIZE,
    SESSION_CANCEL,
    SESSION_NEW,
    SESSION_PROMPT,
    SESSION_REQUEST_PERMISSION,
    SESSION_UPDATE,
    JsonValue,
    Message,
    Notification,
    Request,
    RequestId,
    Response,
    ResponseError,
)

# Drei advertises the minimum capabilities until §C wires the fs/terminal
# ports, so the agent cannot request what Drei cannot serve (plan 0010 §risks).
CLIENT_CAPABILITIES: dict[str, JsonValue] = {}
PROTOCOL_VERSION = 1
_METHOD_NOT_FOUND = -32601

Phase = Literal[
    "DISCONNECTED",
    "INITIALIZING",
    "READY",
    "SESSION_ACTIVE",
    "PROMPT_IN_FLIGHT",
]

# The StopReason literal, pinned from agent-client-protocol 0.9.0 schema.py:14.
_STOP_REASONS = frozenset(
    {"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"}
)


class AcpStateError(Exception):
    """An out-of-phase transition was attempted on the session machine."""


# ---------------------------------------------------------------------------
# SessionEffect — typed observations of the session, consumed by B.7/B.8.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Initialized:
    agent_capabilities: JsonValue


@dataclass(frozen=True, slots=True)
class SessionEstablished:
    session_id: str


@dataclass(frozen=True, slots=True)
class AgentTextChunk:
    text: str


@dataclass(frozen=True, slots=True)
class ThoughtChunk:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    update: JsonValue


@dataclass(frozen=True, slots=True)
class ToolCallUpdated:
    update: JsonValue


@dataclass(frozen=True, slots=True)
class PlanUpdated:
    update: JsonValue


@dataclass(frozen=True, slots=True)
class PromptCompleted:
    stop_reason: str


@dataclass(frozen=True, slots=True)
class PermissionRequested:
    request_id: RequestId
    params: JsonValue


@dataclass(frozen=True, slots=True)
class ProtocolError:
    detail: str


SessionEffect = (
    Initialized
    | SessionEstablished
    | AgentTextChunk
    | ThoughtChunk
    | ToolCallStarted
    | ToolCallUpdated
    | PlanUpdated
    | PromptCompleted
    | PermissionRequested
    | ProtocolError
)


# ---------------------------------------------------------------------------
# The machine.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcpMachine:
    """Immutable client-side ACP session state."""

    phase: Phase = "DISCONNECTED"
    agent_capabilities: JsonValue = None
    session_id: str | None = None
    next_request_id: int = 0
    # Outbound client→agent requests awaiting a response: id → method.
    in_flight_outgoing: dict[RequestId, str] = field(default_factory=dict)
    # Inbound agent→client requests Drei must answer: id → method.
    in_flight_incoming: dict[RequestId, str] = field(default_factory=dict)


def start(machine: AcpMachine | None = None) -> tuple[AcpMachine, Request]:
    """Begin the handshake: emit the ``initialize`` request."""
    machine = machine or AcpMachine()
    if machine.phase != "DISCONNECTED":
        raise AcpStateError(f"start() requires DISCONNECTED, got {machine.phase}")
    request = Request(
        id=machine.next_request_id,
        method=INITIALIZE,
        params={
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": CLIENT_CAPABILITIES,
            "clientInfo": {"name": "drei", "version": "0.0.0"},
        },
    )
    machine = replace(
        machine,
        phase="INITIALIZING",
        next_request_id=machine.next_request_id + 1,
        in_flight_outgoing={**machine.in_flight_outgoing, request.id: INITIALIZE},
    )
    return machine, request


def new_session(machine: AcpMachine, cwd: str) -> tuple[AcpMachine, Request]:
    """Emit ``session/new`` (only from READY)."""
    if machine.phase != "READY":
        raise AcpStateError(f"new_session() requires READY, got {machine.phase}")
    request = Request(
        id=machine.next_request_id,
        method=SESSION_NEW,
        params={"cwd": cwd, "mcpServers": []},
    )
    machine = replace(
        machine,
        next_request_id=machine.next_request_id + 1,
        in_flight_outgoing={**machine.in_flight_outgoing, request.id: SESSION_NEW},
    )
    return machine, request


def prompt(machine: AcpMachine, text: str) -> tuple[AcpMachine, Request]:
    """Emit ``session/prompt`` with a single text content block."""
    if machine.phase != "SESSION_ACTIVE":
        raise AcpStateError(f"prompt() requires SESSION_ACTIVE, got {machine.phase}")
    request = Request(
        id=machine.next_request_id,
        method=SESSION_PROMPT,
        params={
            "sessionId": machine.session_id,
            "prompt": [{"type": "text", "text": text}],
        },
    )
    machine = replace(
        machine,
        phase="PROMPT_IN_FLIGHT",
        next_request_id=machine.next_request_id + 1,
        in_flight_outgoing={**machine.in_flight_outgoing, request.id: SESSION_PROMPT},
    )
    return machine, request


def cancel(machine: AcpMachine) -> tuple[AcpMachine, Notification]:
    """Emit the ``session/cancel`` notification (prompt must be in flight)."""
    if machine.phase != "PROMPT_IN_FLIGHT":
        raise AcpStateError(f"cancel() requires PROMPT_IN_FLIGHT, got {machine.phase}")
    notification = Notification(
        method=SESSION_CANCEL,
        params={"sessionId": machine.session_id},
    )
    return machine, notification


# ---------------------------------------------------------------------------
# The fold.
# ---------------------------------------------------------------------------


def handle(
    machine: AcpMachine, message: Message
) -> tuple[AcpMachine, list[Message], list[SessionEffect]]:
    """Fold one inbound envelope into the machine.

    Returns the new machine, any outbound messages to send (responses to
    agent→client requests), and the ``SessionEffect`` values observed.
    """
    if isinstance(message, (Response, ResponseError)):
        return _handle_response(machine, message)
    if isinstance(message, Notification):
        return _handle_notification(machine, message)
    return _handle_inbound_request(machine, message)


def _handle_response(
    machine: AcpMachine, message: Response | ResponseError
) -> tuple[AcpMachine, list[Message], list[SessionEffect]]:
    method = machine.in_flight_outgoing.get(message.id)
    if method is None:
        return (
            machine,
            [],
            [ProtocolError(detail=f"response for unknown/duplicate id {message.id!r}")],
        )
    in_flight = {k: v for k, v in machine.in_flight_outgoing.items() if k != message.id}
    machine = replace(machine, in_flight_outgoing=in_flight)

    if isinstance(message, ResponseError):
        # Restore the phase so the machine is not stuck: a failed prompt returns
        # to SESSION_ACTIVE, a failed initialize to DISCONNECTED (re-startable),
        # a failed session/new stays READY. (Adversarial-review B1.)
        recovery: dict[str, Phase] = {
            INITIALIZE: "DISCONNECTED",
            SESSION_NEW: "READY",
            SESSION_PROMPT: "SESSION_ACTIVE",
        }
        machine = replace(machine, phase=recovery.get(method, machine.phase))
        return (
            machine,
            [],
            [
                ProtocolError(
                    detail=f"{method} failed: {message.code} {message.message}"
                )
            ],
        )

    result = message.result if isinstance(message.result, dict) else {}
    if method == INITIALIZE:
        caps = result.get("agentCapabilities")
        return (
            replace(machine, phase="READY", agent_capabilities=caps),
            [],
            [Initialized(agent_capabilities=caps)],
        )
    if method == SESSION_NEW:
        # 0.9.0 requires sessionId; a missing/non-str value is malformed and
        # must not advance the machine to an empty-id session. (Review N1.)
        session_id = result.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            return (
                machine,
                [],
                [ProtocolError(detail="session/new response missing sessionId")],
            )
        return (
            replace(machine, phase="SESSION_ACTIVE", session_id=session_id),
            [],
            [SessionEstablished(session_id=session_id)],
        )
    if method == SESSION_PROMPT:
        # 0.9.0 requires stopReason; validate against the StopReason literal
        # rather than defaulting to a spurious end_turn. (Review N1.)
        stop = result.get("stopReason")
        if not isinstance(stop, str) or stop not in _STOP_REASONS:
            return (
                machine,
                [],
                [ProtocolError(detail=f"session/prompt bad stopReason {stop!r}")],
            )
        return (
            replace(machine, phase="SESSION_ACTIVE"),
            [],
            [PromptCompleted(stop_reason=stop)],
        )
    # Unreachable: handle() only ever tracks INITIALIZE/SESSION_NEW/SESSION_PROMPT
    # outbound requests, so a tracked method is always one of the three above.
    return machine, [], []  # pragma: no cover - defensive fallthrough


def _handle_notification(
    machine: AcpMachine, message: Notification
) -> tuple[AcpMachine, list[Message], list[SessionEffect]]:
    if message.method != SESSION_UPDATE:
        # Other agent→client notifications are not modelled; ignore.
        return machine, [], []
    params = message.params if isinstance(message.params, dict) else {}
    # A session/update is only meaningful once a session exists. Without this
    # guard, sessionId=None matches a fresh machine (None == None) and a
    # pre-session update is folded into a DISCONNECTED machine. (Review B2.)
    if machine.session_id is None:
        return (
            machine,
            [],
            [ProtocolError(detail="session/update before any session is established")],
        )
    if params.get("sessionId") != machine.session_id:
        return (
            machine,
            [],
            [
                ProtocolError(
                    detail="session/update for unknown session "
                    f"{params.get('sessionId')!r}"
                )
            ],
        )
    update = params.get("update")
    if not isinstance(update, dict):
        return (
            machine,
            [],
            [ProtocolError(detail="session/update missing update object")],
        )
    kind = update.get("sessionUpdate")
    content = update.get("content")
    text = content.get("text", "") if isinstance(content, dict) else ""
    if kind == "agent_message_chunk":
        return machine, [], [AgentTextChunk(text=text)]
    if kind == "agent_thought_chunk":
        return machine, [], [ThoughtChunk(text=text)]
    if kind == "tool_call":
        return machine, [], [ToolCallStarted(update=update)]
    if kind == "tool_call_update":
        return machine, [], [ToolCallUpdated(update=update)]
    if kind == "plan":
        return machine, [], [PlanUpdated(update=update)]
    # user_message_chunk / available_commands_update / current_mode_update /
    # session_info_update / usage_update / config_option_update — not modelled.
    return machine, [], []


def _handle_inbound_request(
    machine: AcpMachine, message: Request
) -> tuple[AcpMachine, list[Message], list[SessionEffect]]:
    method = message.method
    if method == SESSION_REQUEST_PERMISSION:
        machine = replace(
            machine,
            in_flight_incoming={**machine.in_flight_incoming, message.id: method},
        )
        return (
            machine,
            [],
            [PermissionRequested(request_id=message.id, params=message.params)],
        )
    if method in (FS_READ_TEXT_FILE, FS_WRITE_TEXT_FILE) or method.startswith(
        "terminal/"
    ):
        # Capability-gated: Drei advertises no fs/terminal support yet, so the
        # agent must not ask. Refuse with a JSON-RPC method-not-found error.
        return (
            machine,
            [
                ResponseError(
                    id=message.id,
                    code=_METHOD_NOT_FOUND,
                    message=f"{method} not supported by this client",
                )
            ],
            [],
        )
    return (
        machine,
        [
            ResponseError(
                id=message.id,
                code=_METHOD_NOT_FOUND,
                message=f"unknown method {method}",
            )
        ],
        [],
    )
