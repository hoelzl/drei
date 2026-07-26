"""A fake ACP 0.9.0 agent: the peer for the end-to-end evidence.

Design 0005's verification layer 3/4. Run as a child process by
``tests/test_agent_end_to_end.py`` and by the TermVerify scenario, so the whole
path — streaming port, reader threads, decoder, machine, pump, session,
renderer — is proved against a real child without ``hermes`` installed.

Deliberately thin. It replays the exact frames the pinned 0.9.0 traces in
``tests/acp`` already assert against; a fake elaborate enough to drift from
the real wire would prove nothing. It answers three methods and streams one
chunk per prompt — plus one opt-in mode (slice 20): a prompt starting with
``hold`` is *not* answered, leaving the turn in flight until a
``session/cancel`` notification arrives, which answers it with
``stopReason: "cancelled"`` (the response ACP 0.9.0 requires). Default
behavior is byte-identical for every other prompt.

Not a test module (no ``test_`` name): pytest must not collect it, and it is
executed, not imported.
"""

from __future__ import annotations

import json
import sys
from typing import Any

SESSION_ID = "fake-session"


def _send(message: dict[str, Any]) -> None:
    # Byte-for-byte the framing `drei.acp.codec.encode` produces.
    frame = json.dumps(message, separators=(",", ":")) + "\n"
    sys.stdout.buffer.write(frame.encode())
    sys.stdout.buffer.flush()


def _update(update: dict[str, Any]) -> None:
    _send(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": SESSION_ID, "update": update},
        }
    )


def main() -> None:
    # A line on stderr before anything else, so the diagnostics path is
    # exercised by the same run rather than by a test of its own.
    sys.stderr.write("fake-agent: ready\n")
    sys.stderr.flush()
    held_prompt_id: Any = None
    while True:
        # readline, not iteration: iterating a buffered stream reads ahead,
        # which would hold a request hostage until the next one arrived.
        line = sys.stdin.buffer.readline()
        if not line:
            return
        if not line.strip():
            continue
        message = json.loads(line)
        method = message.get("method")
        if method == "initialize":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {
                            "loadSession": False,
                            "promptCapabilities": {"image": False},
                        },
                    },
                }
            )
        elif method == "session/new":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"sessionId": SESSION_ID},
                }
            )
        elif method == "session/prompt":
            text = message["params"]["prompt"][0]["text"]
            if text.startswith("hold"):
                # Held-turn mode: the turn stays in flight. Only
                # session/cancel ends it — nothing else is answered.
                held_prompt_id = message["id"]
                continue
            # Two chunks, so the test proves a fold rather than a single write.
            _update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "echo "},
                }
            )
            _update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                }
            )
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"stopReason": "end_turn"},
                }
            )
        elif method == "session/cancel" and held_prompt_id is not None:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": held_prompt_id,
                    "result": {"stopReason": "cancelled"},
                }
            )
            held_prompt_id = None


if __name__ == "__main__":
    main()
