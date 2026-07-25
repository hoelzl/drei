"""Design 0003 §B.7 verify: the transcript-fold property, end to end.

Drive the B.6 machine over scripted server traces (handshake → prompt →
streamed updates → completion, and a cancel variant), threading every emitted
``SessionEffect`` through ``EditorSession.apply_session_effects``. The
agent-buffer text must satisfy **two independent oracles**:

1. the concatenation of every ``AgentTranscriptUpdated.rendered`` recorded in
   the event transcript, and
2. a fresh refold of the same effects through ``TranscriptFold.advance``.

Not just equality between two runs: each trace also pins an exact golden.
"""

from __future__ import annotations

from drei.acp.machine import (
    AcpMachine,
    SessionEffect,
    handle,
    new_session,
    prompt,
    start,
)
from drei.acp.messages import (
    SESSION_UPDATE,
    Message,
    Notification,
    Response,
)
from drei.acp.transcript import TranscriptFold, advance
from drei.commands import AgentTranscriptUpdated, CreateAgentBuffer
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession

PROTOCOL_VERSION = 1
ACP_SESSION = "s"
AGENT_CAPS = {"loadSession": False, "promptCapabilities": {"image": False}}


def _init_result() -> dict[str, object]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "agentCapabilities": AGENT_CAPS,
        "agentInfo": {"name": "hermes", "version": "0.0.0"},
        "authMethods": [],
    }


def _update(session_id: str, update: dict[str, object]) -> Notification:
    return Notification(
        method=SESSION_UPDATE, params={"sessionId": session_id, "update": update}
    )


def _chunk(session_id: str, kind: str, text: str) -> Notification:
    return _update(
        session_id,
        {"sessionUpdate": kind, "content": {"type": "text", "text": text}},
    )


def _agent_text(session: EditorSession) -> str:
    """The transcript lives in the ACP session's own agent buffer, which is
    not the focused one (design 0004 D1/D2) — the focused buffer is the
    user's, and it must stay empty here."""
    agent_id = session.agent_buffer_id(ACP_SESSION)
    assert agent_id is not None
    assert session.buffer.buffer_id != agent_id  # focus never followed a delivery
    assert session.buffer.current.text == ""
    return session._buffers[agent_id].current.text


def _drive(
    agent_messages: list[Message],
    prompt_text: str = "do it",
    stop_reason: str = "end_turn",
) -> tuple[EditorSession, list[SessionEffect]]:
    """Run the handshake + prompt, feed every agent message through the
    machine, and apply every emitted effect list to a fresh session. The
    prompt resolves with ``stop_reason`` after the streamed messages.

    The agent buffer is minted up front and every delivery names it, which is
    what the §C pump will do (design 0005): resolve the target once per ACP
    session and never ask what is focused."""
    session = EditorSession(Buffer(BufferId("scratch"), BufferValue(text="", point=0)))
    session.dispatch(CreateAgentBuffer(ACP_SESSION))
    agent_id = session.agent_buffer_id(ACP_SESSION)
    assert agent_id is not None
    effects: list[SessionEffect] = []

    machine, init_req = start()
    machine, out, eff = handle(machine, Response(id=init_req.id, result=_init_result()))
    assert out == []
    effects += eff
    session.apply_session_effects(tuple(eff), agent_id)

    machine, new_req = new_session(machine, "/repo")
    machine, out, eff = handle(
        machine, Response(id=new_req.id, result={"sessionId": ACP_SESSION})
    )
    assert out == []
    effects += eff
    session.apply_session_effects(tuple(eff), agent_id)

    machine, prompt_req = prompt(machine, prompt_text)
    for message in agent_messages:
        machine, out, eff = handle(machine, message)
        effects += eff
        if eff:
            session.apply_session_effects(tuple(eff), agent_id)
    machine, out, eff = handle(
        machine, Response(id=prompt_req.id, result={"stopReason": stop_reason})
    )
    effects += eff
    if eff:
        session.apply_session_effects(tuple(eff), agent_id)
    assert isinstance(machine, AcpMachine)
    return session, effects


def _assert_two_oracles(session: EditorSession, effects: list[SessionEffect]) -> None:
    buffer_text = _agent_text(session)
    # Oracle 1: the concatenation of the recorded delivery events.
    from_events = "".join(
        e.rendered for e in session.transcript if isinstance(e, AgentTranscriptUpdated)
    )
    # Oracle 2: an independent refold of the effects.
    fold = TranscriptFold()
    parts: list[str] = []
    for effect in effects:
        fold, text = advance(fold, effect)
        parts.append(text)
    from_refold = "".join(parts)
    assert buffer_text == from_events == from_refold


class TestCompletionTrace:
    def _trace(self) -> list[Message]:
        return [
            _chunk("s", "agent_message_chunk", "I'll run "),
            _chunk("s", "agent_thought_chunk", "checking the tests first"),
            _update(
                "s",
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tc-1",
                    "title": "Run pytest",
                    "kind": "execute",
                    "status": "in_progress",
                    "locations": [{"path": "tests/test_x.py", "line": 4}],
                },
            ),
            _update(
                "s",
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tc-1",
                    "status": "completed",
                },
            ),
            _update(
                "s",
                {
                    "sessionUpdate": "plan",
                    "entries": [
                        {"content": "fix the bug", "status": "in_progress"},
                        {"content": "re-run tests", "status": "pending"},
                    ],
                },
            ),
            _chunk("s", "agent_message_chunk", "the tests."),
        ]

    def test_two_oracles(self) -> None:
        session, effects = _drive(self._trace())
        _assert_two_oracles(session, effects)

    def test_exact_golden(self) -> None:
        session, _ = _drive(self._trace())
        assert _agent_text(session) == (
            "\n── agent ──\nI'll run "
            "\n  ┆ thought ┆\nchecking the tests first"
            "\n[tool:execute] Run pytest (in_progress)\n  tests/test_x.py:4\n"
            "\n[tool-update] tc-1: status=completed\n"
            "\nPlan:\n  1. [in_progress] fix the bug\n  2. [pending] re-run tests\n"
            "the tests."
            "\n── end turn (end_turn) ──\n"
        )


class TestCancelTrace:
    def _trace(self) -> list[Message]:
        return [
            _chunk("s", "agent_message_chunk", "partial ans"),
        ]

    def test_two_oracles(self) -> None:
        session, effects = _drive(self._trace(), stop_reason="cancelled")
        _assert_two_oracles(session, effects)

    def test_exact_golden(self) -> None:
        session, _ = _drive(self._trace(), stop_reason="cancelled")
        assert _agent_text(session) == (
            "\n── agent ──\npartial ans\n── end turn (cancelled) ──\n"
        )
