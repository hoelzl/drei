"""B.8: the choice minibuffer (design 0003 §A.4 choice variant, plan 0013 D3).

A ``session/request_permission`` opens a *choice* prompt: the agent's
``PermissionOption``\\ s are presented, one key resolves to a decision, abort
maps to ``Cancelled``. The decision feeds back to the machine via
``apply_permission_decision`` (mirroring ``apply_session_effects``), which
returns the outbound ``Response`` for the §C pump.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from drei.acp.machine import AcpMachine

from drei.acp.machine import (
    Cancelled,
    PermissionRequested,
    Selected,
)
from drei.commands import (
    FindFile,
    MinibufferAbort,
    MinibufferAccept,
    MinibufferBackspace,
    MinibufferInput,
    PermissionDecided,
    PromptPermission,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def make_session() -> EditorSession:
    return EditorSession(Buffer(BufferId("scratch"), BufferValue(text="", point=0)))


def _permission(request_id: int | str = 42) -> PermissionRequested:
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

    def test_deny_without_reject_option_maps_to_cancelled(self) -> None:
        # Fail-closed: a deny with no reject_* option is a Cancelled, never
        # an invented optionId.
        request = PermissionRequested(
            request_id=42,
            params={
                "toolCall": {"toolCallId": "tc-1"},
                "options": [
                    {"kind": "allow_once", "name": "Allow", "optionId": "o-once"},
                ],
            },
        )
        session = make_session()
        session.dispatch(PromptPermission(request))
        outcome = session.dispatch(MinibufferInput("n"))
        decided = [e for e in outcome.events if isinstance(e, PermissionDecided)]
        assert decided == [PermissionDecided(request_id=42, decision=Cancelled())]

    def test_accept_without_allow_option_maps_to_cancelled(self) -> None:
        # Fail-closed on the accept path too.
        request = PermissionRequested(
            request_id=42,
            params={
                "toolCall": {"toolCallId": "tc-1"},
                "options": [
                    {"kind": "reject_once", "name": "No", "optionId": "o-no"},
                ],
            },
        )
        session = make_session()
        session.dispatch(PromptPermission(request))
        outcome = session.dispatch(MinibufferAccept())
        decided = [e for e in outcome.events if isinstance(e, PermissionDecided)]
        assert decided == [PermissionDecided(request_id=42, decision=Cancelled())]

    def test_backspace_in_choice_mode_is_a_no_op(self) -> None:
        session = make_session()
        session.dispatch(PromptPermission(_permission()))
        outcome = session.dispatch(MinibufferBackspace())
        assert not any(isinstance(e, PermissionDecided) for e in outcome.events)
        assert session.minibuffer is not None  # still open


class TestChoiceHelperTotality:
    """Malformed payloads exercise the total helper branches directly."""

    def test_key_to_decision_ignores_option_without_option_id(self) -> None:
        request = PermissionRequested(
            request_id=1,
            params={
                "options": [
                    {"kind": "reject_once"},  # no optionId → skipped
                    {"kind": "reject_once", "optionId": "o-real"},
                ]
            },
        )
        decision = EditorSession._choice_key_to_decision(request, "n")
        assert decision == Selected("o-real")

    def test_key_to_decision_allow_option_without_id_returns_none(self) -> None:
        request = PermissionRequested(
            request_id=1,
            params={"options": [{"kind": "allow_once"}]},  # no optionId
        )
        assert EditorSession._choice_key_to_decision(request, "y") is None

    def test_accept_skips_allow_option_without_id(self) -> None:
        request = PermissionRequested(
            request_id=1,
            params={
                "options": [
                    {"kind": "allow_once"},  # no optionId → skipped
                    {"kind": "reject_once", "optionId": "o-no"},
                ]
            },
        )
        assert EditorSession._choice_accept_decision(request) == Cancelled()

    def test_accept_returns_first_valid_allow_option(self) -> None:
        # An allow option with no id is skipped; the next valid allow wins.
        request = PermissionRequested(
            request_id=1,
            params={
                "options": [
                    {"kind": "allow_once"},  # no optionId → skipped
                    {"kind": "allow_always", "optionId": "o-always"},
                ]
            },
        )
        assert EditorSession._choice_accept_decision(request) == Selected("o-always")

    def test_choice_options_total_over_malformed(self) -> None:
        # Non-dict params, non-list options, non-dict entries all total to [].
        assert (
            EditorSession._choice_options(
                PermissionRequested(request_id=1, params=None)
            )
            == []
        )
        assert (
            EditorSession._choice_options(
                PermissionRequested(request_id=1, params={"options": "notalist"})
            )
            == []
        )
        assert EditorSession._choice_options(
            PermissionRequested(
                request_id=1, params={"options": [{"kind": "allow_once"}, "junk", 7]}
            )
        ) == [{"kind": "allow_once"}]

    def test_choice_prompt_handles_missing_and_blank_titles(self) -> None:
        # Missing toolCall → generic label; blank title falls back to id.
        generic = EditorSession._choice_prompt(
            PermissionRequested(request_id=1, params={"options": []})
        )
        assert "permission" in generic
        blank = EditorSession._choice_prompt(
            PermissionRequested(
                request_id=1,
                params={
                    "toolCall": {"title": "", "toolCallId": "tc-fallback"},
                    "options": [],
                },
            )
        )
        assert "tc-fallback" in blank

    def test_choice_prompt_non_dict_tool_call_and_non_string_title(self) -> None:
        # toolCall present but not a dict → generic label.
        non_dict = EditorSession._choice_prompt(
            PermissionRequested(
                request_id=1, params={"toolCall": "notadict", "options": []}
            )
        )
        assert "permission" in non_dict
        # title/toolCallId both non-strings → generic label (the `t` guard).
        non_string = EditorSession._choice_prompt(
            PermissionRequested(
                request_id=1,
                params={"toolCall": {"title": 7, "toolCallId": None}, "options": []},
            )
        )
        assert "permission" in non_string

    def test_choice_prompt_uses_title(self) -> None:
        # The happy path: a non-empty title wins (closes the 621->627 branch).
        prompt = EditorSession._choice_prompt(
            PermissionRequested(
                request_id=1,
                params={
                    "toolCall": {"title": "deploy to prod", "toolCallId": "tc-1"},
                    "options": [],
                },
            )
        )
        assert "deploy to prod" in prompt

    def test_choice_prompt_non_dict_params(self) -> None:
        # params not a dict → generic label (the outer isinstance guard).
        prompt = EditorSession._choice_prompt(
            PermissionRequested(request_id=1, params="notadict")
        )
        assert "permission" in prompt


class TestPermissionQueue:
    def test_concurrent_requests_presented_one_at_a_time(self) -> None:
        session = make_session()
        session.dispatch(PromptPermission(_permission(1)))
        session.dispatch(PromptPermission(_permission(2)))
        # First is open, second queued.
        assert session.minibuffer is not None
        assert session.pending_permission_count() == 1
        # Resolve the first; the second is presented next (FIFO drain).
        session.dispatch(MinibufferInput("y"))
        assert session.minibuffer is not None  # second prompt now open
        assert session.pending_permission_count() == 0
        session.dispatch(MinibufferInput("n"))
        assert session.minibuffer is None  # all resolved

    def test_request_during_text_prompt_presented_after(self) -> None:
        session = make_session()
        session.dispatch(FindFile())
        session.dispatch(PromptPermission(_permission(7)))
        assert session.pending_permission_count() == 1
        # Resolve the text prompt (accept with a path); the queued permission
        # prompt is then presented.
        for ch in "x.py":
            session.dispatch(MinibufferInput(ch))
        session.dispatch(MinibufferAccept())
        assert session.pending_permission_count() == 0

    def test_request_during_text_prompt_presented_after_abort(self) -> None:
        # Aborting the text prompt drains the queue too (same hang class).
        session = make_session()
        session.dispatch(FindFile())
        session.dispatch(PromptPermission(_permission(9)))
        assert session.pending_permission_count() == 1
        session.dispatch(MinibufferAbort())
        assert session.pending_permission_count() == 0
        assert session.minibuffer is not None  # permission prompt now open

    def test_string_request_id_flows_through(self) -> None:
        # ACP RequestId is int | str; a string id must survive
        # prompt → decision → machine answer unchanged.
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
                id="perm-abc",
                method="session/request_permission",
                params=_permission("perm-abc").params,
            ),
        )
        session = make_session()
        session.dispatch(PromptPermission(_permission("perm-abc")))
        outcome = session.dispatch(MinibufferInput("y"))
        decided = [e for e in outcome.events if isinstance(e, PermissionDecided)]
        assert decided == [
            PermissionDecided(request_id="perm-abc", decision=Selected("o-once"))
        ]
        machine, out, _ = session.apply_permission_decision(
            machine, "perm-abc", decided[0].decision
        )
        assert isinstance(out[0], Response)
        assert out[0].id == "perm-abc"


class TestApplyPermissionDecision:
    def _machine_with_in_flight(self) -> AcpMachine:
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
        from drei.acp.messages import Response

        session = make_session()
        machine = self._machine_with_in_flight()
        machine, out, effects = session.apply_permission_decision(
            machine, 42, Selected("o-once")
        )
        assert isinstance(out[0], Response)
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
