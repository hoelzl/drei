"""Cross-check Drei's ACP framing against the real ACP SDK (gated).

Design 0003 §B.5: the codec must match the official ACP Python SDK — the real
``hermes acp`` peer — byte-for-byte on the wire. This test is skipped when the
SDK isn't importable (it lives in Hermes's venv, not Drei's deps). No
dependency is added: we replicate the SDK's exact framing call
(``acp/task/sender.py``) rather than import its asyncio-bound sender.
"""

from __future__ import annotations

import json

import pytest

from drei.acp.codec import encode

acp = pytest.importorskip("acp", reason="ACP SDK not installed (Hermes venv)")

_PAYLOADS: list[dict[str, object]] = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": 1},
    },
    {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "s1"}},
    {"jsonrpc": "2.0", "id": "abc", "result": {"sessionId": "s1"}},
    {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32601, "message": "Method not found"},
    },
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "session/prompt",
        "params": {"text": "héllo → 世界"},
    },
]


def _sdk_frame(payload: dict[str, object]) -> bytes:
    # The SDK's exact serialization (acp/task/sender.py MessageSender.send).
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


@pytest.mark.parametrize("payload", _PAYLOADS)
def test_encode_matches_sdk_wire_bytes(payload: dict[str, object]) -> None:
    assert encode(payload) == _sdk_frame(payload)
