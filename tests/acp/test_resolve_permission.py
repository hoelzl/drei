"""B.8: the approval-answer path on the ACP machine (design 0003 §B.8, plan 0013).

``resolve_permission`` is the seam the choice-minibuffer (V3) feeds: a human
decision crosses into the pure machine, which reads/clears
``in_flight_incoming`` and emits the exact 0.9.0 ``RequestPermissionResponse``.
"""

from __future__ import annotations

import pytest

from drei.acp.machine import (
    AcpMachine,
    AcpStateError,
    Cancelled,
    PermissionRequested,
    PermissionResolved,
    Selected,
    handle,
    new_session,
    resolve_permission,
    start,
)
from drei.acp.messages import Request, Response


def _handshake() -> AcpMachine:
    """Drive start → initialize response → new_session → session/new response."""
    machine, init_req = start()
    machine, _, _ = handle(
        machine, Response(id=init_req.id, result={"agentCapabilities": {}})
    )
    machine, new_req = new_session(machine, cwd="/tmp")
    machine, _, _ = handle(machine, Response(id=new_req.id, result={"sessionId": "s1"}))
    return machine


def _permission_request(request_id: int = 42) -> Request:
    return Request(
        id=request_id,
        method="session/request_permission",
        params={
            "sessionId": "s1",
            "toolCall": {"toolCallId": "tc-1", "title": "run tests"},
            "options": [
                {"kind": "allow_once", "name": "Allow once", "optionId": "o1"},
                {"kind": "reject_once", "name": "No", "optionId": "o2"},
            ],
        },
    )


class TestResolvePermission:
    def test_selected_emits_exact_response_and_clears_in_flight(self) -> None:
        machine = _handshake()
        machine, out, effects = handle(machine, _permission_request())
        assert isinstance(effects[0], PermissionRequested)
        assert 42 in machine.in_flight_incoming

        machine, out, effects = resolve_permission(machine, 42, Selected("o1"))

        assert out == [
            Response(
                id=42,
                result={"outcome": {"outcome": "selected", "optionId": "o1"}},
            )
        ]
        assert 42 not in machine.in_flight_incoming  # read AND clear
        assert effects == [PermissionResolved(request_id=42, decision=Selected("o1"))]

    def test_cancelled_emits_cancelled_outcome(self) -> None:
        machine = _handshake()
        machine, _, _ = handle(machine, _permission_request())
        machine, out, effects = resolve_permission(machine, 42, Cancelled())
        assert out == [Response(id=42, result={"outcome": {"outcome": "cancelled"}})]
        assert effects == [PermissionResolved(request_id=42, decision=Cancelled())]

    def test_resolving_unknown_id_raises(self) -> None:
        machine = _handshake()
        with pytest.raises(AcpStateError, match="not in flight"):
            resolve_permission(machine, 999, Selected("o1"))

    def test_resolving_twice_raises(self) -> None:
        machine = _handshake()
        machine, _, _ = handle(machine, _permission_request())
        machine, _, _ = resolve_permission(machine, 42, Selected("o1"))
        with pytest.raises(AcpStateError, match="not in flight"):
            resolve_permission(machine, 42, Selected("o1"))

    def test_selected_camelcase_option_id_in_response(self) -> None:
        # The wire key is optionId (camelCase), never option_id — pin the
        # exact dict, not a round-trip.
        machine = _handshake()
        machine, _, _ = handle(machine, _permission_request())
        _, out, _ = resolve_permission(machine, 42, Selected("allow-xyz"))
        result = out[0].result
        assert result == {"outcome": {"outcome": "selected", "optionId": "allow-xyz"}}
        assert "option_id" not in str(result)


class TestInboundPhaseGating:
    def test_permission_request_outside_session_is_protocol_error(self) -> None:
        # 0010 deferred note: an inbound permission request before any session
        # must not be tracked. DISCONNECTED machine.
        machine = start()[0]
        machine, out, effects = handle(machine, _permission_request())
        assert 42 not in machine.in_flight_incoming
        assert any(
            "phase" in getattr(e, "detail", "") or "out of" in getattr(e, "detail", "")
            for e in effects
        ), effects
        # No PermissionRequested surfaced.
        assert not any(isinstance(e, PermissionRequested) for e in effects)

    def test_permission_request_in_ready_phase_is_protocol_error(self) -> None:
        # READY (initialized, no session yet) — still out of phase.
        machine, init_req = start()
        machine, _, _ = handle(
            machine, Response(id=init_req.id, result={"agentCapabilities": {}})
        )
        assert machine.phase == "READY"
        machine, _, effects = handle(machine, _permission_request())
        assert 42 not in machine.in_flight_incoming
        assert not any(isinstance(e, PermissionRequested) for e in effects)

    def test_permission_request_in_session_active_tracked(self) -> None:
        machine = _handshake()
        machine, _, effects = handle(machine, _permission_request())
        assert 42 in machine.in_flight_incoming
        assert isinstance(effects[0], PermissionRequested)
