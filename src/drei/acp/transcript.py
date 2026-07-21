"""Pure transcript renderer for the agent buffer (design 0003 §B.7, plan 0011).

The renderer is the *text* side of the update→command translation: a frozen
:class:`TranscriptFold` interpreter maps each B.6 ``SessionEffect`` to the
text it appends to the agent buffer. Formatting is **total** — a malformed or
partial peer payload degrades to ``?`` placeholders, never an exception (the
peer is non-deterministic by design; the transcript must survive it).

Line structure is emitted whole by each ``advance`` call so the fold is a pure
concatenation: the rendered transcript equals ``"".join`` of every returned
suffix, and chunk boundaries never split a line-prefix decision (thought
text is appended verbatim, not re-prefixed per line).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from drei.acp.machine import (
    AgentTextChunk,
    Initialized,
    PermissionRequested,
    PlanUpdated,
    PromptCompleted,
    ProtocolError,
    SessionEffect,
    SessionEstablished,
    ThoughtChunk,
    ToolCallStarted,
    ToolCallUpdated,
)
from drei.acp.messages import JsonValue

_TURN_HEADER = "\n── agent ──\n"
_THOUGHT_HEADER = "\n  ┆ thought ┆\n"
_MISSING = "?"


@dataclass(frozen=True, slots=True)
class TranscriptFold:
    """Interpreter state for the agent-buffer transcript.

    ``turn_open`` / ``thought_open`` track the structural blocks so headers
    are emitted exactly once and closed in order. ``turns`` counts completed
    turns — a second ``session/prompt`` round opens a fresh turn, never
    re-opens a completed one.
    """

    turn_open: bool = False
    thought_open: bool = False
    turns: int = 0


def advance(fold: TranscriptFold, effect: SessionEffect) -> tuple[TranscriptFold, str]:
    """Map one ``SessionEffect`` to (new fold, text to append)."""
    match effect:
        case AgentTextChunk(text=text):
            prefix = ""
            if fold.thought_open:
                fold = replace(fold, thought_open=False)
            if not fold.turn_open:
                fold = replace(fold, turn_open=True)
                prefix = _TURN_HEADER
            return fold, prefix + text
        case ThoughtChunk(text=text):
            prefix = ""
            if not fold.turn_open:
                fold = replace(fold, turn_open=True)
                prefix = _TURN_HEADER
            if not fold.thought_open:
                fold = replace(fold, thought_open=True)
                prefix += _THOUGHT_HEADER
            return fold, prefix + text
        case ToolCallStarted(update=update):
            fold, prefix = _close_thought(fold)
            return fold, prefix + format_tool_call_started(update)
        case ToolCallUpdated(update=update):
            fold, prefix = _close_thought(fold)
            return fold, prefix + format_tool_call_updated(update)
        case PlanUpdated(update=update):
            fold, prefix = _close_thought(fold)
            return fold, prefix + format_plan(update)
        case PromptCompleted(stop_reason=reason):
            fold = replace(
                fold, turn_open=False, thought_open=False, turns=fold.turns + 1
            )
            return fold, f"\n── end turn ({reason}) ──\n"
        case PermissionRequested(request_id=request_id):
            fold, prefix = _close_thought(fold)
            return fold, prefix + f"\n── permission requested (id {request_id!r}) ──\n"
        case ProtocolError(detail=detail):
            fold, prefix = _close_thought(fold)
            return fold, prefix + f"\n── protocol error: {detail} ──\n"
        case Initialized() | SessionEstablished():
            # Handshake milestones carry no agent-visible text; the §C
            # launcher surfaces them as status, not transcript.
            return fold, ""


def _close_thought(fold: TranscriptFold) -> tuple[TranscriptFold, str]:
    """Structured blocks close an open thought; they never close the turn —
    the prompt response is the only turn boundary, so interleaved chunks and
    tool calls cannot split a header onto the wrong side of a completion."""
    if fold.thought_open:
        return replace(fold, thought_open=False), ""
    return fold, ""


# ---------------------------------------------------------------------------
# Formatting helpers — total over malformed 0.9.0 payloads.
# ---------------------------------------------------------------------------


def _as_str(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def _as_dict(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _as_list(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _format_locations(locations: JsonValue) -> str:
    lines = []
    for entry in _as_list(locations):
        loc = _as_dict(entry)
        path = _as_str(loc.get("path")) or _MISSING
        line = loc.get("line")
        line_no = line if isinstance(line, int) else 1
        lines.append(f"  {path}:{line_no}\n")
    return "".join(lines)


def _format_diffs(content: JsonValue) -> str:
    """Render ``diff`` content items verbatim; ``newText`` duplicates the
    diff and is elided. Non-diff content (terminal handles, raw content) is
    transcript-silent in B.7."""
    out = []
    for item in _as_list(content):
        block = _as_dict(item)
        if block.get("type") != "diff":
            continue
        path = _as_str(block.get("path")) or _MISSING
        old = _as_str(block.get("oldText")) or _MISSING
        new = _as_str(block.get("newText")) or _MISSING
        out.append(f"  ── diff {path} ──\n  - {old}\n  + {new}\n")
    return "".join(out)


def format_tool_call_started(update: JsonValue) -> str:
    """``tool_call`` → header + locations + diffs (0.9.0 ToolCall)."""
    call = _as_dict(update)
    kind = _as_str(call.get("kind")) or _MISSING
    title = _as_str(call.get("title")) or _as_str(call.get("toolCallId")) or _MISSING
    status = _as_str(call.get("status")) or _MISSING
    return (
        f"\n[tool:{kind}] {title} ({status})\n"
        + _format_locations(call.get("locations"))
        + _format_diffs(call.get("content"))
    )


def format_tool_call_updated(update: JsonValue) -> str:
    """``tool_call_update`` → a compact delta naming only the fields present
    (0.9.0 ToolCallUpdate; every field except ``toolCallId`` is optional)."""
    call = _as_dict(update)
    call_id = _as_str(call.get("toolCallId")) or _MISSING
    parts: list[str] = []
    title = _as_str(call.get("title"))
    if title is not None:
        parts.append(f"title={title}")
    status = _as_str(call.get("status"))
    if status is not None:
        parts.append(f"status={status}")
    if call.get("locations"):
        parts.append("locations")
    if any(_as_dict(c).get("type") == "diff" for c in _as_list(call.get("content"))):
        parts.append("diff")
    summary = ", ".join(parts) if parts else "update"
    return (
        f"\n[tool-update] {call_id}: {summary}\n"
        + _format_locations(call.get("locations"))
        + _format_diffs(call.get("content"))
    )


def format_plan(update: JsonValue) -> str:
    """``plan`` → a numbered list (0.9.0 Plan: ``entries`` of PlanEntry with
    ``content`` / ``status``)."""
    plan = _as_dict(update)
    entries = _as_list(plan.get("entries"))
    if not entries:
        return "\nPlan: (empty)\n"
    lines = ["\nPlan:\n"]
    for index, entry in enumerate(entries, start=1):
        item = _as_dict(entry)
        status = _as_str(item.get("status")) or _MISSING
        content = _as_str(item.get("content")) or _MISSING
        lines.append(f"  {index}. [{status}] {content}\n")
    return "".join(lines)
