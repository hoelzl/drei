"""Pure ACP client session state machine (design 0003 §B.6, plan 0010).

The machine is a frozen value folded over inbound JSON-RPC envelopes, emitting
outbound envelopes and typed ``SessionEffect`` values. Pinned to
``agent-client-protocol 0.9.0`` (camelCase keys, snake_case discriminators).
"""

from __future__ import annotations

import pytest

from drei.acp.machine import (
    AcpMachine,
    AcpStateError,
    AgentTextChunk,
    Initialized,
    PermissionRequested,
    PlanUpdated,
    PromptCompleted,
    ProtocolError,
    SessionEstablished,
    ThoughtChunk,
    ToolCallStarted,
    ToolCallUpdated,
    cancel,
    handle,
    new_session,
    prompt,
    start,
)
from drei.acp.messages import (
    INITIALIZE,
    SESSION_CANCEL,
    SESSION_NEW,
    SESSION_PROMPT,
    SESSION_REQUEST_PERMISSION,
    SESSION_UPDATE,
    JsonValue,
    Notification,
    Request,
    Response,
    ResponseError,
)

# ---------------------------------------------------------------------------
# Scripted server traces (the design-0003 §B.6 verify). A trace is a list of
# (client_action, agent_message) steps; the scripted server responds to each
# outbound request with a canned reply, and interleaves session/update
# notifications.
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = 1
AGENT_CAPS = {"loadSession": False, "promptCapabilities": {"image": False}}


def _init_result() -> dict[str, object]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "agentCapabilities": AGENT_CAPS,
        "agentInfo": {"name": "hermes", "version": "0.0.0"},
        "authMethods": [],
    }


def _drive_handshake() -> tuple[AcpMachine, list[object]]:
    """Run initialize + session/new to a READY/SESSION_ACTIVE machine."""
    machine, init_req = start()
    machine, out, effects = handle(
        machine, Response(id=init_req.id, result=_init_result())
    )
    assert out == []
    machine, new_req = new_session(machine, "/repo")
    machine, out, effects2 = handle(
        machine, Response(id=new_req.id, result={"sessionId": "sess-1"})
    )
    assert out == []
    return machine, [*effects, *effects2]


class TestHandshake:
    def test_start_emits_initialize_request(self) -> None:
        machine, req = start()
        assert isinstance(req, Request)
        assert req.method == INITIALIZE
        assert req.id == 0
        assert req.params["protocolVersion"] == PROTOCOL_VERSION
        assert req.params["clientCapabilities"] == {}
        assert "clientInfo" in req.params
        assert machine.phase == "INITIALIZING"
        assert machine.next_request_id == 1

    def test_initialize_response_yields_initialized_and_ready(self) -> None:
        machine, req = start()
        machine, out, effects = handle(
            machine, Response(id=req.id, result=_init_result())
        )
        assert out == []
        assert machine.phase == "READY"
        assert effects == [Initialized(agent_capabilities=AGENT_CAPS)]

    def test_new_session_emits_request_with_cwd_and_empty_mcp(self) -> None:
        machine, _ = start()
        machine, _, _ = handle(machine, Response(id=0, result=_init_result()))
        machine, req = new_session(machine, "/repo")
        assert isinstance(req, Request)
        assert req.method == SESSION_NEW
        assert req.id == 1  # ids increment across requests
        assert req.params == {"cwd": "/repo", "mcpServers": []}
        assert machine.phase == "SESSION_ACTIVE" or machine.phase == "READY"

    def test_session_new_response_yields_session_established(self) -> None:
        machine, _ = start()
        machine, _, _ = handle(machine, Response(id=0, result=_init_result()))
        machine, req = new_session(machine, "/repo")
        machine, out, effects = handle(
            machine, Response(id=req.id, result={"sessionId": "sess-1"})
        )
        assert out == []
        assert machine.session_id == "sess-1"
        assert machine.phase == "SESSION_ACTIVE"
        assert effects == [SessionEstablished(session_id="sess-1")]

    def test_new_session_before_initialize_raises(self) -> None:
        machine = AcpMachine()
        with pytest.raises(AcpStateError):
            new_session(machine, "/repo")

    def test_prompt_before_session_raises(self) -> None:
        machine, _ = start()
        machine, _, _ = handle(machine, Response(id=0, result=_init_result()))
        with pytest.raises(AcpStateError):
            prompt(machine, "hi")

    def test_start_twice_raises(self) -> None:
        machine, _ = start()
        # start() always begins a fresh machine; calling start() is only valid
        # on a pristine machine, so we test that a second initialize response
        # (unknown id) is a ProtocolError, not a crash.
        machine, out, effects = handle(machine, Response(id=99, result=_init_result()))
        assert effects and isinstance(effects[-1], ProtocolError)


class TestPromptLifecycle:
    def test_prompt_emits_request_with_text_block(self) -> None:
        machine, _ = _drive_handshake()
        machine, req = prompt(machine, "hello agent")
        assert isinstance(req, Request)
        assert req.method == SESSION_PROMPT
        assert req.params["sessionId"] == "sess-1"
        assert req.params["prompt"] == [{"type": "text", "text": "hello agent"}]
        assert machine.phase == "PROMPT_IN_FLIGHT"

    def test_agent_message_chunk_maps_to_agent_text_chunk(self) -> None:
        machine, _ = _drive_handshake()
        machine, _ = prompt(machine, "hi")
        update = Notification(
            method=SESSION_UPDATE,
            params={
                "sessionId": "sess-1",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Hello"},
                },
            },
        )
        machine, out, effects = handle(machine, update)
        assert out == []
        assert effects == [AgentTextChunk(text="Hello")]

    def test_agent_thought_chunk_maps_to_thought_chunk(self) -> None:
        machine, _ = _drive_handshake()
        machine, _ = prompt(machine, "hi")
        update = Notification(
            method=SESSION_UPDATE,
            params={
                "sessionId": "sess-1",
                "update": {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "thinking…"},
                },
            },
        )
        machine, out, effects = handle(machine, update)
        assert effects == [ThoughtChunk(text="thinking…")]

    def _update(self, update: JsonValue) -> Notification:
        return Notification(
            method=SESSION_UPDATE,
            params={"sessionId": "sess-1", "update": update},
        )

    def test_tool_call_maps_to_tool_call_started(self) -> None:
        machine, _ = _drive_handshake()
        machine, _ = prompt(machine, "hi")
        upd = {"sessionUpdate": "tool_call", "toolCallId": "tc-1", "title": "ls"}
        _, out, effects = handle(machine, self._update(upd))
        assert out == []
        assert effects == [ToolCallStarted(update=upd)]

    def test_tool_call_update_maps_to_tool_call_updated(self) -> None:
        machine, _ = _drive_handshake()
        machine, _ = prompt(machine, "hi")
        upd = {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "tc-1",
            "status": "completed",
        }
        _, out, effects = handle(machine, self._update(upd))
        assert effects == [ToolCallUpdated(update=upd)]

    def test_plan_maps_to_plan_updated(self) -> None:
        machine, _ = _drive_handshake()
        machine, _ = prompt(machine, "hi")
        upd = {"sessionUpdate": "plan", "entries": []}
        _, out, effects = handle(machine, self._update(upd))
        assert effects == [PlanUpdated(update=upd)]

    def test_unmodelled_update_variants_are_ignored(self) -> None:
        machine, _ = _drive_handshake()
        machine, _ = prompt(machine, "hi")
        for kind in (
            "user_message_chunk",
            "available_commands_update",
            "current_mode_update",
            "usage_update",
        ):
            upd = {"sessionUpdate": kind, "content": {"type": "text", "text": "x"}}
            _, out, effects = handle(machine, self._update(upd))
            assert out == [] and effects == []

    def test_non_update_notification_is_ignored(self) -> None:
        machine, _ = _drive_handshake()
        _, out, effects = handle(machine, Notification(method="some/other", params={}))
        assert out == [] and effects == []

    def test_update_missing_update_object_is_protocol_error(self) -> None:
        machine, _ = _drive_handshake()
        machine, _ = prompt(machine, "hi")
        bad = Notification(method=SESSION_UPDATE, params={"sessionId": "sess-1"})
        _, out, effects = handle(machine, bad)
        assert out == []
        assert len(effects) == 1 and isinstance(effects[0], ProtocolError)

    def test_start_on_non_disconnected_raises(self) -> None:
        machine, _ = start()
        with pytest.raises(AcpStateError):
            start(machine)

    def test_prompt_response_yields_prompt_completed(self) -> None:
        machine, _ = _drive_handshake()
        machine, req = prompt(machine, "hi")
        machine, out, effects = handle(
            machine, Response(id=req.id, result={"stopReason": "end_turn"})
        )
        assert out == []
        assert machine.phase == "SESSION_ACTIVE"
        assert effects == [PromptCompleted(stop_reason="end_turn")]

    def test_wrong_session_update_yields_protocol_error_not_crash(self) -> None:
        machine, _ = _drive_handshake()
        machine, _ = prompt(machine, "hi")
        update = Notification(
            method=SESSION_UPDATE,
            params={
                "sessionId": "OTHER",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "x"},
                },
            },
        )
        machine, out, effects = handle(machine, update)
        assert out == []
        assert len(effects) == 1 and isinstance(effects[0], ProtocolError)

    def test_cancel_emits_notification_and_cancels(self) -> None:
        machine, _ = _drive_handshake()
        machine, _ = prompt(machine, "hi")
        machine, notif = cancel(machine)
        assert isinstance(notif, Notification)
        assert notif.method == SESSION_CANCEL
        assert notif.params == {"sessionId": "sess-1"}
        # A cancelled prompt completes with stopReason cancelled via response.
        machine, out, effects = handle(
            machine, Response(id=2, result={"stopReason": "cancelled"})
        )
        assert effects == [PromptCompleted(stop_reason="cancelled")]

    def test_cancel_without_prompt_in_flight_raises(self) -> None:
        machine, _ = _drive_handshake()
        with pytest.raises(AcpStateError):
            cancel(machine)

    def test_unknown_response_id_yields_protocol_error(self) -> None:
        machine, _ = _drive_handshake()
        machine, out, effects = handle(machine, Response(id=12345, result={}))
        assert out == []
        assert len(effects) == 1 and isinstance(effects[0], ProtocolError)

    def test_error_response_to_prompt_yields_protocol_error(self) -> None:
        machine, _ = _drive_handshake()
        machine, req = prompt(machine, "hi")
        machine, out, effects = handle(
            machine, ResponseError(id=req.id, code=-32603, message="boom")
        )
        assert out == []
        assert len(effects) == 1 and isinstance(effects[0], ProtocolError)


class TestInboundAgentRequests:
    def _active(self) -> AcpMachine:
        machine, _ = _drive_handshake()
        machine, _ = prompt(machine, "hi")
        return machine

    def test_request_permission_tracked_and_surfaces_effect(self) -> None:
        machine = self._active()
        req = Request(
            id="perm-1",
            method=SESSION_REQUEST_PERMISSION,
            params={
                "sessionId": "sess-1",
                "toolCall": {"toolCallId": "tc-1", "title": "run ls"},
                "options": [],
            },
        )
        machine, out, effects = handle(machine, req)
        assert out == []  # B.6 does not answer; B.8 bridges approval
        assert any(isinstance(e, PermissionRequested) for e in effects)
        assert "perm-1" in machine.in_flight_incoming

    def test_fs_read_capability_gated(self) -> None:
        # Default clientCapabilities advertise no fs support, so an fs/read
        # request is refused with a protocol error response, not executed.
        machine = self._active()
        req = Request(
            id="fs-1",
            method="fs/read_text_file",
            params={"sessionId": "sess-1", "path": "/x", "line": None, "limit": None},
        )
        machine, out, effects = handle(machine, req)
        assert len(out) == 1 and isinstance(out[0], ResponseError)

    def test_unknown_inbound_method_yields_method_not_found(self) -> None:
        machine = self._active()
        req = Request(id="z-1", method="bogus/method", params={})
        machine, out, effects = handle(machine, req)
        assert len(out) == 1 and isinstance(out[0], ResponseError)
        assert out[0].code == -32601  # JSON-RPC method not found


class TestScriptedTrace:
    """Design 0003 §B.6 verify: replay a recorded ACP server trace and assert
    the EXACT outbound-message and SessionEffect sequences."""

    def test_handshake_prompt_completion_trace(self) -> None:
        outbound: list[object] = []
        effects: list[object] = []

        machine, init_req = start()
        outbound.append(init_req)
        machine, out, eff = handle(
            machine, Response(id=init_req.id, result=_init_result())
        )
        outbound += out
        effects += eff

        machine, new_req = new_session(machine, "/repo")
        outbound.append(new_req)
        machine, out, eff = handle(
            machine, Response(id=new_req.id, result={"sessionId": "s"})
        )
        outbound += out
        effects += eff

        machine, prompt_req = prompt(machine, "do it")
        outbound.append(prompt_req)
        # Stream two chunks, then completion.
        for text in ("part-1", "part-2"):
            machine, out, eff = handle(
                machine,
                Notification(
                    method=SESSION_UPDATE,
                    params={
                        "sessionId": "s",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": text},
                        },
                    },
                ),
            )
            outbound += out
            effects += eff
        machine, out, eff = handle(
            machine, Response(id=prompt_req.id, result={"stopReason": "end_turn"})
        )
        outbound += out
        effects += eff

        assert [
            m.method for m in outbound if isinstance(m, (Request, Notification))
        ] == [
            INITIALIZE,
            SESSION_NEW,
            SESSION_PROMPT,
        ]
        assert [m.id for m in outbound if isinstance(m, Request)] == [0, 1, 2]
        assert effects == [
            Initialized(agent_capabilities=AGENT_CAPS),
            SessionEstablished(session_id="s"),
            AgentTextChunk(text="part-1"),
            AgentTextChunk(text="part-2"),
            PromptCompleted(stop_reason="end_turn"),
        ]

    def test_handshake_prompt_cancel_trace(self) -> None:
        outbound: list[object] = []
        machine, init_req = start()
        outbound.append(init_req)
        machine, out, _ = handle(
            machine, Response(id=init_req.id, result=_init_result())
        )
        outbound += out
        machine, new_req = new_session(machine, "/repo")
        outbound.append(new_req)
        machine, out, _ = handle(
            machine, Response(id=new_req.id, result={"sessionId": "s"})
        )
        outbound += out
        machine, prompt_req = prompt(machine, "do it")
        outbound.append(prompt_req)
        machine, cancel_notif = cancel(machine)
        outbound.append(cancel_notif)
        machine, out, eff = handle(
            machine, Response(id=prompt_req.id, result={"stopReason": "cancelled"})
        )
        outbound += out

        methods = [m.method for m in outbound if isinstance(m, (Request, Notification))]
        assert methods == [INITIALIZE, SESSION_NEW, SESSION_PROMPT, SESSION_CANCEL]
        assert isinstance(cancel_notif, Notification)
        assert eff == [PromptCompleted(stop_reason="cancelled")]
