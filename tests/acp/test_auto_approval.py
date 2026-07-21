"""B.8: session-scoped auto-approval cache (design 0003 §B.8, plan 0013 D2).

``allow_session``/``allow_always`` populate an ``auto_approvals`` cache keyed
on tool **identity + arguments** (never the per-call ``toolCallId``); a cached
request is answered without re-prompting. The cache resets on ``new_session()``
(the design's verify line). Adversarial pins: argument drift re-prompts
(fail-closed), duplicate optionIds cannot poison the cache, and invented
``allow_*`` kinds never auto-approve.
"""

from __future__ import annotations

import pytest

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


def _request(
    request_id: int | str,
    tool_call_id: str = "tc-1",
    title: str = "run tests",
    options: list[dict[str, str]] | None = None,
) -> Request:
    return Request(
        id=request_id,
        method="session/request_permission",
        params={
            "sessionId": "s1",
            "toolCall": {"toolCallId": tool_call_id, "title": title},
            "options": options
            or [
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
        # Second request for the SAME tool call (same id, same args):
        # auto-answered, no prompt.
        machine, out2, effects = handle(machine, _request(2))
        assert not any(isinstance(e, PermissionRequested) for e in effects)
        # The auto-answer is a real Response for request 2, still recorded.
        assert out2 and isinstance(out2[0], Response) and out2[0].id == 2
        resolved = [e for e in effects if isinstance(e, PermissionResolved)]
        assert resolved and resolved[0].granted is True
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

    def test_different_arguments_re_prompts(self) -> None:
        # Fail-closed (review HIGH): identity is kind/title + canonical params,
        # NOT the per-call toolCallId. Same tool title but a different
        # toolCallId (→ different params) is a different operation and must
        # re-prompt — an agent cannot inherit a grant across changed arguments.
        machine = _handshake()
        machine, _, _ = handle(machine, _request(1, tool_call_id="tc-A"))
        machine, _, _ = resolve_permission(machine, 1, Selected("o-sess"))
        machine, _, effects = handle(machine, _request(2, tool_call_id="tc-B"))
        assert any(isinstance(e, PermissionRequested) for e in effects)

    def test_different_title_re_prompts(self) -> None:
        machine = _handshake()
        machine, _, _ = handle(machine, _request(1, title="run tests"))
        machine, _, _ = resolve_permission(machine, 1, Selected("o-sess"))
        machine, _, effects = handle(machine, _request(2, title="delete prod"))
        assert any(isinstance(e, PermissionRequested) for e in effects)

    def test_cache_resets_on_new_session(self) -> None:
        # The design's verify line: session-scoped cache resets on new session.
        machine, init_req = start()
        machine, _, _ = handle(
            machine, Response(id=init_req.id, result={"agentCapabilities": {}})
        )
        assert machine.phase == "READY"
        from dataclasses import replace as _replace

        machine = _replace(machine, auto_approvals=("some-identity",))
        machine, new_req = new_session(machine, cwd="/tmp")
        assert machine.auto_approvals == ()  # cleared at the boundary
        machine, _, _ = handle(
            machine, Response(id=new_req.id, result={"sessionId": "s2"})
        )
        # The previously-approved identity now prompts again.
        machine, _, effects = handle(machine, _request(2))
        assert any(isinstance(e, PermissionRequested) for e in effects)

    def test_malformed_request_yields_stable_fallback_key(self) -> None:
        # Totality: a request whose toolCall lacks a discriminator still gets
        # a deterministic identity (canonical-JSON of params) — auto-approval
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

    def test_auto_answer_prefers_granted_scope_over_allow_once(self) -> None:
        # Review MED (scope erosion): a cached allow_session grant must not be
        # reported to the agent as a downgraded allow_once. The auto-answer
        # prefers the broadest cached scope's option.
        machine = _handshake()
        machine, _, _ = handle(machine, _request(1))
        machine, _, _ = resolve_permission(machine, 1, Selected("o-sess"))
        machine, out2, _ = handle(machine, _request(2))
        assert isinstance(out2[0], Response)
        # o-sess (session scope) chosen, not the first-listed o-once.
        assert out2[0].result == {
            "outcome": {"outcome": "selected", "optionId": "o-sess"}
        }

    def test_changed_options_re_prompts(self) -> None:
        # The identity key includes the options list, so a request whose
        # options changed is a DIFFERENT identity and re-prompts (fail-closed)
        # even for the same tool. (This is why the "cached identity with no
        # allow option" corner is unreachable over the fold — a changed options
        # list always yields a new key; the no-allow selection itself is unit-
        # tested on _select_auto_option.)
        machine = _handshake()
        machine, _, _ = handle(machine, _request(1))
        machine, _, _ = resolve_permission(machine, 1, Selected("o-sess"))
        reject_only = [
            {"kind": "reject_once", "name": "No", "optionId": "o-no"},
        ]
        machine, out2, effects = handle(machine, _request(2, options=reject_only))
        assert out2 == []  # no auto-answer
        assert any(isinstance(e, PermissionRequested) for e in effects)

    def test_cached_identity_with_only_allow_once_auto_answers(self) -> None:
        # Unit pin for the _id_of scope-scan-miss → allow_once fallback: seed
        # the cache with the identity of a request that offers ONLY allow_once
        # (no session/always option), then feed that exact request. The scope
        # scan finds nothing; the fallback picks allow_once. Covers the
        # 561->581 auto-answer branch directly.
        from dataclasses import replace as _replace

        from drei.acp.machine import _permission_identity

        once_only = [
            {"kind": "allow_once", "name": "Once", "optionId": "o-once"},
            {"kind": "reject_once", "name": "No", "optionId": "o-no"},
        ]
        req = _request(2, options=once_only)
        machine = _replace(
            _handshake(), auto_approvals=(_permission_identity(req.params),)
        )
        machine, out2, effects = handle(machine, req)
        assert not any(isinstance(e, PermissionRequested) for e in effects)
        assert isinstance(out2[0], Response)
        assert out2[0].result == {
            "outcome": {"outcome": "selected", "optionId": "o-once"}
        }

    def test_select_auto_option_prefers_scope_then_once(self) -> None:
        # Unit pin for the scope-scan → fallback selection (both _id_of arms).
        from drei.acp.machine import _select_auto_option

        scope = [
            {"kind": "allow_once", "optionId": "o-once"},
            {"kind": "allow_session", "optionId": "o-sess"},
        ]
        # Broadest scope wins even when listed after allow_once.
        assert _select_auto_option(scope) == "o-sess"
        once_only = [{"kind": "allow_once", "optionId": "o-once"}]
        assert _select_auto_option(once_only) == "o-once"  # fallback
        # Non-enum allow kinds and missing optionIds are ignored.
        assert _select_auto_option([{"kind": "allow_evil", "optionId": "x"}]) is None
        assert _select_auto_option([{"kind": "allow_once"}]) is None
        assert (
            _select_auto_option([{"kind": "reject_once", "optionId": "o-no"}]) is None
        )

    def test_duplicate_option_id_last_match_wins_no_cache_poison(self) -> None:
        # Review HIGH: a hostile agent orders a duplicate allow_always BEFORE
        # the reject the human picks, sharing an optionId. First-match would
        # read allow_always and poison the cache; last-match (shadowing) reads
        # the reject the human actually resolved → no cache write.
        machine = _handshake()
        dupes = [
            {"kind": "allow_always", "name": "A", "optionId": "dup"},
            {"kind": "reject_once", "name": "No", "optionId": "dup"},
        ]
        machine, _, _ = handle(machine, _request(1, options=dupes))
        machine, _, effects = resolve_permission(machine, 1, Selected("dup"))
        # The human's deny is respected: not granted, and nothing cached.
        resolved = [e for e in effects if isinstance(e, PermissionResolved)]
        assert resolved and resolved[0].granted is False
        assert machine.auto_approvals == ()
        # A repeat of the same request re-prompts (no poisoned grant).
        machine, _, effects2 = handle(machine, _request(2, options=dupes))
        assert any(isinstance(e, PermissionRequested) for e in effects2)

    def test_invented_allow_kind_never_auto_approves(self) -> None:
        # Review MED-HIGH: a bogus "allow_evil" kind matches startswith("allow")
        # but is not an enum kind; it must not auto-approve on a cache hit, and
        # selecting it must not populate the cache.
        machine = _handshake()
        evil = [
            {"kind": "allow_evil", "name": "Trust me", "optionId": "o-evil"},
        ]
        machine, _, _ = handle(machine, _request(1, options=evil))
        machine, _, _ = resolve_permission(machine, 1, Selected("o-evil"))
        assert machine.auto_approvals == ()  # not a cacheable enum kind

    def test_string_request_id_auto_approves(self) -> None:
        # Composition pin: str ids flow through the auto-approval path too.
        machine = _handshake()
        machine, _, _ = handle(machine, _request("p1"))
        machine, _, _ = resolve_permission(machine, "p1", Selected("o-sess"))
        machine, out2, effects = handle(machine, _request("p2"))
        assert not any(isinstance(e, PermissionRequested) for e in effects)
        assert isinstance(out2[0], Response) and out2[0].id == "p2"


class TestIdentityKeyTotality:
    """_permission_identity / _permission_options are total over malformed
    payloads; exercise the defensive branches directly."""

    def test_non_dict_params_fall_back_to_canonical_json(self) -> None:
        from drei.acp.machine import _permission_identity

        assert _permission_identity(None) == "|params:null"
        assert "params:" in _permission_identity("x")

    def test_non_dict_tool_call_falls_back(self) -> None:
        from drei.acp.machine import _permission_identity

        assert _permission_identity({"toolCall": "notadict"}).startswith("|params:")

    def test_discriminator_prefers_kind_then_title(self) -> None:
        from drei.acp.machine import _permission_identity

        by_kind = _permission_identity(
            {"toolCall": {"kind": "shell", "title": "run tests"}}
        )
        assert by_kind.startswith("kind:shell|params:")
        by_title = _permission_identity({"toolCall": {"title": "run tests"}})
        assert by_title.startswith("title:run tests|params:")

    def test_no_discriminator_fields_yields_empty_prefix(self) -> None:
        # Neither kind nor title present/valid → empty discriminator (the loop
        # completes without break). Covers the 302->307 fall-through.
        from drei.acp.machine import _permission_identity

        assert _permission_identity({"toolCall": {"toolCallId": "tc"}}).startswith(
            "|params:"
        )
        # Non-string / empty kind and title also yield no discriminator.
        assert _permission_identity({"toolCall": {"kind": 7, "title": ""}}).startswith(
            "|params:"
        )

    def test_unserializable_params_do_not_crash(self) -> None:
        from drei.acp.machine import _permission_identity

        class Unjsonable:
            pass

        # default=str serializes unknown objects; the key stays total.
        assert "params:" in _permission_identity({"k": Unjsonable()})

    def test_dump_failure_falls_back_to_question_mark(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The except arm: if json.dumps raises, identity is still total. This
        # intentionally pins the fallback *literal* tail ("params: ?") — it
        # proves the except arm produces the documented placeholder, not that
        # a particular raise is reachable (json.dumps is patched to force it).
        # toolCall here has no kind/title discriminator, so the key is exactly
        # "|params: ?".
        import json as _json

        from drei.acp.machine import _permission_identity

        def _boom(*a: object, **k: object) -> str:
            raise ValueError("boom")

        monkeypatch.setattr(_json, "dumps", _boom)
        assert _permission_identity({"toolCall": "x"}) == "|params:?"

    def test_permission_options_total(self) -> None:
        from drei.acp.machine import _permission_options

        assert _permission_options(None) == []
        assert _permission_options({"options": "notalist"}) == []
        assert _permission_options({"options": [{"kind": "allow_once"}, "junk"]}) == [
            {"kind": "allow_once"}
        ]
