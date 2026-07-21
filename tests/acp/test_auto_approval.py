"""B.8: session-scoped auto-approval cache (design 0003 §B.8, plan 0013 D2).

``allow_session``/``allow_always`` populate an ``auto_approvals`` cache keyed
on tool-call identity; a cached request is answered without re-prompting. The
cache resets on ``new_session()`` (the design's verify line).
"""

from __future__ import annotations

from drei.acp.machine import (
    AcpMachine,
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
    machine, init_req = start()
    machine, _, _ = handle(
        machine, Response(id=init_req.id, result={"agentCapabilities": {}})
    )
    machine, new_req = new_session(machine, cwd="/tmp")
    machine, _, _ = handle(machine, Response(id=new_req.id, result={"sessionId": "s1"}))
    return machine


def _request(request_id: int, tool_call_id: str = "tc-1") -> Request:
    return Request(
        id=request_id,
        method="session/request_permission",
        params={
            "sessionId": "s1",
            "toolCall": {"toolCallId": tool_call_id, "title": "run tests"},
            "options": [
                {"kind": "allow_once", "name": "Once", "optionId": "o-once"},
                {"kind": "allow_session", "name": "Session", "optionId": "o-sess"},
                {"kind": "allow_always", "name": "Always", "optionId": "o-always"},
                {"kind": "reject_once", "name": "No", "optionId": "o-no"},
            ],
        },
    )


class TestAutoApprovalCache:
    def test_allow_session_caches_and_next_request_auto_answers(self) -> None:
        machine = _handshake()
        # First request: human chooses the session-scoped option.
        machine, _, _ = handle(machine, _request(1))
        machine, out1, _ = resolve_permission(machine, 1, Selected("o-sess"))
        # Second request for the SAME tool-call identity: auto-answered, no prompt.
        machine, out2, effects = handle(machine, _request(2))
        assert not any(isinstance(e, PermissionRequested) for e in effects)
        # The auto-answer is a real Response for request 2, still recorded.
        assert out2 != [] and out2[0].id == 2
        assert any(isinstance(e, PermissionResolved) for e in effects)
        assert 2 not in machine.in_flight_incoming  # answered, not pending

    def test_allow_once_does_not_cache(self) -> None:
        machine = _handshake()
        machine, _, _ = handle(machine, _request(1))
        machine, _, _ = resolve_permission(machine, 1, Selected("o-once"))
        machine, _, effects = handle(machine, _request(2))
        assert any(isinstance(e, PermissionRequested) for e in effects)
        assert 2 in machine.in_flight_incoming

    def test_allow_always_caches(self) -> None:
        machine = _handshake()
        machine, _, _ = handle(machine, _request(1))
        machine, _, _ = resolve_permission(machine, 1, Selected("o-always"))
        machine, _, effects = handle(machine, _request(2))
        assert not any(isinstance(e, PermissionRequested) for e in effects)

    def test_reject_does_not_cache(self) -> None:
        machine = _handshake()
        machine, _, _ = handle(machine, _request(1))
        machine, _, _ = resolve_permission(machine, 1, Selected("o-no"))
        machine, _, effects = handle(machine, _request(2))
        assert any(isinstance(e, PermissionRequested) for e in effects)

    def test_different_tool_call_identity_not_cached(self) -> None:
        machine = _handshake()
        machine, _, _ = handle(machine, _request(1, tool_call_id="tc-A"))
        machine, _, _ = resolve_permission(machine, 1, Selected("o-sess"))
        # Different toolCallId → different identity → still prompts.
        machine, _, effects = handle(machine, _request(2, tool_call_id="tc-B"))
        assert any(isinstance(e, PermissionRequested) for e in effects)

    def test_cache_resets_on_new_session(self) -> None:
        # The design's verify line: session-scoped cache resets on new session.
        # new_session() requires READY, so drive a READY machine that carries
        # a seeded cache (as a prior session would leave it) and assert the
        # cache is cleared at the session/new boundary.
        machine, init_req = start()
        machine, _, _ = handle(
            machine, Response(id=init_req.id, result={"agentCapabilities": {}})
        )
        assert machine.phase == "READY"
        from dataclasses import replace as _replace

        machine = _replace(machine, auto_approvals=("tool:tc-1",))
        machine, new_req = new_session(machine, cwd="/tmp")
        assert machine.auto_approvals == ()  # cleared at the boundary
        machine, _, _ = handle(
            machine, Response(id=new_req.id, result={"sessionId": "s2"})
        )
        # The previously-approved identity now prompts again.
        machine, _, effects = handle(machine, _request(2))
        assert any(isinstance(e, PermissionRequested) for e in effects)

    def test_malformed_request_yields_stable_fallback_key(self) -> None:
        # Totality: a request whose toolCall lacks toolCallId still gets a
        # deterministic identity (canonical-JSON fallback) — auto-approval
        # over it must not crash. An allow_session option makes the cache
        # write fire.
        machine = _handshake()
        opts = [{"kind": "allow_session", "name": "S", "optionId": "x"}]
        bad = Request(
            id=1,
            method="session/request_permission",
            params={"sessionId": "s1", "options": opts},
        )
        machine, _, _ = handle(machine, bad)
        machine, _, _ = resolve_permission(machine, 1, Selected("x"))
        bad2 = Request(
            id=2,
            method="session/request_permission",
            params={"sessionId": "s1", "options": opts},
        )
        machine, _, effects = handle(machine, bad2)
        # Identical malformed payloads share the fallback key → auto-answered.
        assert not any(isinstance(e, PermissionRequested) for e in effects)
