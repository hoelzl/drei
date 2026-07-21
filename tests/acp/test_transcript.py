"""Transcript renderer: SessionEffect → agent-buffer text (design 0003 §B.7, plan 0011).

The renderer is pure: no imports from effect modules, total formatting
(malformed peer payloads degrade to ``?``, never raise), and the rendered
transcript is a pure concatenation of ``advance`` suffixes.
"""

from __future__ import annotations

import pytest

from drei.acp.machine import (
    AgentTextChunk,
    Cancelled,
    Initialized,
    PermissionRequested,
    PermissionResolved,
    PlanUpdated,
    PromptCompleted,
    ProtocolError,
    Selected,
    SessionEffect,
    SessionEstablished,
    ThoughtChunk,
    ToolCallStarted,
)
from drei.acp.transcript import (
    TranscriptFold,
    advance,
    format_plan,
    format_tool_call_started,
    format_tool_call_updated,
)


def _render(effects: list[SessionEffect]) -> str:
    """Fold effects from the initial state; return the concatenated text."""
    fold = TranscriptFold()
    out = []
    for effect in effects:
        fold, text = advance(fold, effect)
        out.append(text)
    return "".join(out)


class TestAgentTextChunks:
    def test_first_chunk_opens_turn(self) -> None:
        fold, text = advance(TranscriptFold(), AgentTextChunk(text="hello"))
        assert fold.turn_open is True
        assert text == "\n── agent ──\nhello"

    def test_second_chunk_appends_verbatim(self) -> None:
        text = _render([AgentTextChunk(text="a"), AgentTextChunk(text="b")])
        assert text == "\n── agent ──\nab"

    def test_empty_chunk_still_opens_turn(self) -> None:
        fold, text = advance(TranscriptFold(), AgentTextChunk(text=""))
        assert fold.turn_open is True
        assert text == "\n── agent ──\n"


class TestThoughtChunks:
    def test_thought_inside_turn_opens_thought_block(self) -> None:
        text = _render([AgentTextChunk(text="a"), ThoughtChunk(text="hmm")])
        assert text == "\n── agent ──\na\n  ┆ thought ┆\nhmm"

    def test_thought_before_any_text_opens_turn_first(self) -> None:
        text = _render([ThoughtChunk(text="hmm")])
        assert text == "\n── agent ──\n\n  ┆ thought ┆\nhmm"

    def test_text_after_thought_closes_thought_block(self) -> None:
        fold, _ = advance(TranscriptFold(), ThoughtChunk(text="t"))
        fold, text = advance(fold, AgentTextChunk(text="answer"))
        assert fold.thought_open is False
        assert text == "answer"  # no re-prefix, no re-open

    def test_thought_text_appended_verbatim_across_chunks(self) -> None:
        # Line-prefixing per chunk would make the fold context-sensitive;
        # thought chunks concatenate raw.
        text = _render([ThoughtChunk(text="line1\n"), ThoughtChunk(text="line2")])
        assert text.endswith("line1\nline2")


class TestPromptCompleted:
    def test_closes_turn_and_prints_stop_reason(self) -> None:
        fold, _ = advance(TranscriptFold(), AgentTextChunk(text="a"))
        fold, text = advance(fold, PromptCompleted(stop_reason="end_turn"))
        assert fold.turn_open is False
        assert fold.turns == 1
        assert text == "\n── end turn (end_turn) ──\n"

    @pytest.mark.parametrize(
        "reason",
        ["end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"],
    )
    def test_every_pinned_stop_reason_printed_verbatim(self, reason: str) -> None:
        _, text = advance(TranscriptFold(), PromptCompleted(stop_reason=reason))
        assert text == f"\n── end turn ({reason}) ──\n"

    def test_second_prompt_opens_fresh_turn(self) -> None:
        text = _render(
            [
                AgentTextChunk(text="one"),
                PromptCompleted(stop_reason="end_turn"),
                AgentTextChunk(text="two"),
            ]
        )
        assert (
            text == "\n── agent ──\none\n── end turn (end_turn) ──\n\n── agent ──\ntwo"
        )

    def test_completion_closes_open_thought(self) -> None:
        fold, _ = advance(TranscriptFold(), ThoughtChunk(text="t"))
        fold, text = advance(fold, PromptCompleted(stop_reason="cancelled"))
        assert fold.thought_open is False
        assert text == "\n── end turn (cancelled) ──\n"


class TestSilentEffects:
    def test_initialized_is_transcript_silent(self) -> None:
        fold, text = advance(TranscriptFold(), Initialized(agent_capabilities={}))
        assert text == ""
        assert fold == TranscriptFold()

    def test_session_established_is_transcript_silent(self) -> None:
        fold, text = advance(TranscriptFold(), SessionEstablished(session_id="s"))
        assert text == ""
        assert fold == TranscriptFold()

    def test_non_effect_raises_type_error(self) -> None:
        # Totality mirrors session.dispatch: a non-SessionEffect is a caller
        # bug, never peer data (peer payloads arrive as opaque JsonValue
        # inside effects and degrade to placeholders instead).
        with pytest.raises(TypeError, match="unsupported effect"):
            advance(TranscriptFold(), object())  # type: ignore[arg-type]


class TestAuditLines:
    def test_permission_requested_renders_audit_line(self) -> None:
        _, text = advance(
            TranscriptFold(), PermissionRequested(request_id=7, params={})
        )
        assert text == "\n── permission requested (id 7) ──\n"

    def test_permission_requested_inside_thought_closes_it(self) -> None:
        fold, _ = advance(TranscriptFold(), ThoughtChunk(text="t"))
        fold, text = advance(fold, PermissionRequested(request_id=1, params={}))
        assert fold.thought_open is False
        assert text == "\n── permission requested (id 1) ──\n"

    def test_permission_resolved_granted_renders_option_id(self) -> None:
        _, text = advance(
            TranscriptFold(),
            PermissionResolved(request_id=7, decision=Selected("allow-xyz")),
        )
        assert text == "\n── permission granted: allow-xyz ──\n"

    def test_permission_resolved_cancelled_renders_denied(self) -> None:
        _, text = advance(
            TranscriptFold(),
            PermissionResolved(request_id=7, decision=Cancelled()),
        )
        assert text == "\n── permission denied ──\n"

    def test_protocol_error_is_never_dropped(self) -> None:
        # Dropping a protocol error would silently misalign the live text
        # with any recomputed fold and hide agent misbehavior.
        _, text = advance(TranscriptFold(), ProtocolError(detail="bad thing"))
        assert text == "\n── protocol error: bad thing ──\n"


class TestToolCallStarted:
    def test_full_payload_golden(self) -> None:
        update = {
            "sessionUpdate": "tool_call",
            "toolCallId": "tc-1",
            "title": "Run tests",
            "kind": "execute",
            "status": "in_progress",
            "locations": [{"path": "src/x.py", "line": 12}],
            "content": [
                {"type": "diff", "path": "src/x.py", "oldText": "a", "newText": "b"}
            ],
        }
        text = _render([ToolCallStarted(update=update)])
        assert text == (
            "\n[tool:execute] Run tests (in_progress)\n"
            "  src/x.py:12\n"
            "  ── diff src/x.py ──\n  - a\n  + b\n"
        )

    def test_minimal_payload_falls_back_to_tool_call_id(self) -> None:
        text = format_tool_call_started({"toolCallId": "tc-9"})
        assert text == "\n[tool:?] tc-9 (?)\n"

    def test_mistyped_fields_degrade_without_raising(self) -> None:
        text = format_tool_call_started(
            {"kind": 3, "title": None, "status": ["x"], "locations": "nope"}
        )
        assert text == "\n[tool:?] ? (?)\n"

    def test_location_without_line_defaults_to_1(self) -> None:
        text = format_tool_call_started({"locations": [{"path": "f.py"}]})
        assert "  f.py:1\n" in text

    def test_boolean_line_is_malformed_and_defaults_to_1(self) -> None:
        # bool is int in Python; a boolean "line" must not render as f:True.
        text = format_tool_call_started({"locations": [{"path": "f.py", "line": True}]})
        assert "  f.py:1\n" in text

    def test_diff_renders_old_and_new_verbatim(self) -> None:
        text = format_tool_call_started(
            {
                "toolCallId": "t",
                "content": [
                    {"type": "diff", "path": "p", "oldText": "old", "newText": "new"}
                ],
            }
        )
        assert "  - old\n  + new\n" in text

    def test_non_diff_content_is_transcript_silent(self) -> None:
        text = format_tool_call_started(
            {"toolCallId": "t", "content": [{"type": "terminal", "terminalId": "x"}]}
        )
        assert text == "\n[tool:?] t (?)\n"


class TestToolCallUpdated:
    def test_names_only_fields_present(self) -> None:
        text = format_tool_call_updated({"toolCallId": "tc-1", "status": "completed"})
        assert text == "\n[tool-update] tc-1: status=completed\n"

    def test_title_and_status(self) -> None:
        text = format_tool_call_updated(
            {"toolCallId": "tc-1", "title": "T", "status": "failed"}
        )
        assert text == "\n[tool-update] tc-1: title=T, status=failed\n"

    def test_locations_and_diff_named_and_rendered(self) -> None:
        text = format_tool_call_updated(
            {
                "toolCallId": "tc-1",
                "locations": [{"path": "a.py", "line": 3}],
                "content": [
                    {"type": "diff", "path": "a.py", "oldText": "x", "newText": "y"}
                ],
            }
        )
        assert text == (
            "\n[tool-update] tc-1: locations, diff\n"
            "  a.py:3\n"
            "  ── diff a.py ──\n  - x\n  + y\n"
        )

    def test_empty_delta_still_names_the_call(self) -> None:
        assert format_tool_call_updated({"toolCallId": "tc-1"}) == (
            "\n[tool-update] tc-1: update\n"
        )


class TestPlan:
    def test_numbered_entries_golden(self) -> None:
        update = {
            "sessionUpdate": "plan",
            "entries": [
                {"content": "step one", "status": "completed", "priority": "high"},
                {"content": "step two", "status": "in_progress", "priority": "low"},
            ],
        }
        assert format_plan(update) == (
            "\nPlan:\n  1. [completed] step one\n  2. [in_progress] step two\n"
        )

    def test_empty_entries(self) -> None:
        assert format_plan({"entries": []}) == "\nPlan: (empty)\n"

    def test_malformed_entries_degrade(self) -> None:
        assert format_plan({"entries": [{"status": 1}, "junk"]}) == (
            "\nPlan:\n  1. [?] ?\n  2. [?] ?\n"
        )

    def test_plan_inside_thought_closes_it(self) -> None:
        fold, _ = advance(TranscriptFold(), ThoughtChunk(text="t"))
        fold, text = advance(fold, PlanUpdated(update={"entries": []}))
        assert fold.thought_open is False
        assert fold.turn_open is True  # plans do NOT close the turn
        assert text == "\nPlan: (empty)\n"


class TestFoldConcatenation:
    def test_rendered_transcript_is_pure_concatenation(self) -> None:
        effects: list[SessionEffect] = [
            AgentTextChunk(text="hi"),
            ThoughtChunk(text="thinking"),
            ToolCallStarted(update={"toolCallId": "t", "status": "pending"}),
            AgentTextChunk(text="done"),
            PromptCompleted(stop_reason="end_turn"),
        ]
        fold = TranscriptFold()
        parts = []
        for effect in effects:
            fold, part = advance(fold, effect)
            parts.append(part)
        assert "".join(parts) == _render(effects)
        # And the exact golden, so a formatting drift fails loudly:
        assert "".join(parts) == (
            "\n── agent ──\nhi"
            "\n  ┆ thought ┆\nthinking"
            "\n[tool:?] t (pending)\n"
            "done"
            "\n── end turn (end_turn) ──\n"
        )
