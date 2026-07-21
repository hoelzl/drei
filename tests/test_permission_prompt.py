"""B.8: the choice minibuffer (design 0003 §A.4 choice variant, plan 0013 D3).

A ``session/request_permission`` opens a *choice* prompt: the agent's
``PermissionOption``\\ s are presented, one key resolves to a decision, abort
maps to ``Cancelled``. The decision feeds back to the machine via
``apply_permission_decision`` (mirroring ``apply_session_effects``), which
returns the outbound ``Response`` for the §C pump.
"""

from __future__ import annotations

import pytest

from drei.acp.machine import (
    Cancelled,
    PermissionRequested,
    Selected,
)
from drei.commands import (
    FindFile,
    MinibufferAbort,
    MinibufferInput,
    PermissionDecided,
    PromptPermission,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def make_session() -> EditorSession:
    return EditorSession(Buffer(BufferId("scratch"), BufferValue(text="", point=0)))


def _permission(request_id: int = 42) -> PermissionRequested:
    return PermissionRequested(
        request_id=request_id,
        params={
            "sessionId": "s1",
            "toolCall": {"toolCallId": "tc-1", "title": "run tests"},
            "options": [
                {"kind": "allow_once", "name": "Allow once", "optionId": "o-once"},
                {"kind": "allow_session", "name": "Session", "optionId": "o-sess"},
                {"kind": "allow_always", "name": "Always", "optionId": "o-always"},
                {"kind": "reject_once", "name": "No", "optionId": "o-no"},
            ],
        },
    )


class TestPromptPermissionOpensChoiceMinibuffer:
    def test_opens_choice_prompt_with_options(self) -> None:
        session = make_session()
        session.dispatch(PromptPermission(_permission()))
        assert session.minibuffer is not None
        prompt = session.minibuffer_prompt or ""
        assert "run tests" in prompt or "tc-1" in prompt  # tool-call summary

    def test_is_gate_exempt_like_a_delivery(self) -> None:
        # A permission request arriving while a text prompt is open must not
        # be swallowed (the agent would hang). Delivery-class: it queues and
        # is presented after the text prompt resolves.
        session = make_session()
        session.dispatch(FindFile())  # text prompt open
        assert session.minibuffer is not None
        session.dispatch(PromptPermission(_permission()))
        # It was not silently dropped: the request is queued for after the
        # text prompt resolves.
        assert session.pending_permission_count() == 1


class TestChoiceKeymap:
    @pytest.mark.parametrize(
        ("key", "option_id"),
        [("y", "o-once"), ("s", "o-sess"), ("a", "o-always")],
    )
    def test_allow_keys_select_the_option(self, key: str, option_id: str) -> None:
        session = make_session()
        session.dispatch(PromptPermission(_permission()))
        outcome = session.dispatch(MinibufferInput(key))
        decided = [e for e in outcome.events if isinstance(e, PermissionDecided)]
        assert decided == [
            PermissionDecided(request_id=42, decision=Selected(option_id))
        ]
        assert session.minibuffer is None  # prompt closed on resolution

    def test_reject_key_maps_to_a_reject_option(self) -> None:
        session = make_session()
        session.dispatch(PromptPermission(_permission()))
        outcome = session.dispatch(MinibufferInput("n"))
        decided = [e for e in outcome.events if isinstance(e, PermissionDecided)]
        assert len(decided) == 1
        assert decided[0].decision == Selected("o-no")

    def test_unmapped_key_is_a_no_op(self) -> None:
        session = make_session()
        session.dispatch(PromptPermission(_permission()))
        outcome = session.dispatch(MinibufferInput("z"))
        assert not any(isinstance(e, PermissionDecided) for e in outcome.events)
        assert session.minibuffer is not None  # still open

    def test_abort_maps_to_cancelled(self) -> None:
        session = make_session()
        session.dispatch(PromptPermission(_permission()))
        outcome = session.dispatch(MinibufferAbort())
        decided = [e for e in outcome.events if isinstance(e, PermissionDecided)]
        assert decided == [PermissionDecided(request_id=42, decision=Cancelled())]
        assert session.minibuffer is None


class TestApplyPermissionDecision:
    def _machine_with_in_flight(self):
        from drei.acp.machine import handle, new_session, start
        from drei.acp.messages import Request, Response

        machine, init_req = start()
        machine, _, _ = handle(
            machine, Response(id=init_req.id, result={"agentCapabilities": {}})
        )
        machine, new_req = new_session(machine, cwd="/tmp")
        machine, _, _ = handle(
            machine, Response(id=new_req.id, result={"sessionId": "s1"})
        )
        machine, _, _ = handle(
            machine,
            Request(
                id=42,
                method="session/request_permission",
                params=_permission().params,
            ),
        )
        return machine

    def test_feeds_decision_to_machine_and_returns_response(self) -> None:
        session = make_session()
        machine = self._machine_with_in_flight()
        machine, out, effects = session.apply_permission_decision(
            machine, 42, Selected("o-once")
        )
        assert out[0].id == 42
        assert out[0].result == {
            "outcome": {"outcome": "selected", "optionId": "o-once"}
        }

    def test_unknown_request_raises(self) -> None:
        session = make_session()
        machine = self._machine_with_in_flight()
        from drei.acp.machine import AcpStateError

        with pytest.raises(AcpStateError):
            session.apply_permission_decision(machine, 999, Selected("x"))
