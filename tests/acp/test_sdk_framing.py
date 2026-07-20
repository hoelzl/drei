"""Cross-check Drei's ACP framing against the real ACP SDK (gated).

Design 0003 §B.5: the codec must match the official ACP Python SDK — the real
``hermes acp`` peer — byte-for-byte on the wire. This test drives the SDK's
*actual* ``MessageSender`` through a fake ``StreamWriter`` and captures the
bytes it writes, then asserts Drei's ``encode`` produces identical bytes.

The SDK is not a Drei dependency, so it is imported lazily: the module is
skipped unless the SDK is importable. To run it for real::

    uv run --isolated --no-project --with agent-client-protocol \\
        --with pytest python -m pytest tests/acp/test_sdk_framing.py

(The codec must be importable too, so run from the repo with ``--with .`` or
install drei into that environment.)
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from drei.acp.codec import encode

_PAYLOADS: list[dict[str, Any]] = [
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


@pytest.fixture(scope="module")
def sdk_sender() -> Any:
    """Import the real SDK lazily; skip the whole module if it is absent.

    ``acp`` may partially resolve (some other ``acp`` distribution without the
    ``task`` subpackage), so probe the full dotted path before importing.
    """
    import importlib.util

    for module in ("acp", "acp.task", "acp.task.sender", "acp.task.supervisor"):
        if importlib.util.find_spec(module) is None:
            pytest.skip(f"ACP SDK not importable ({module} missing)")
    from acp.task.sender import MessageSender
    from acp.task.supervisor import TaskSupervisor

    return (MessageSender, TaskSupervisor)


class _FakeWriter:
    """Minimal asyncio.StreamWriter stand-in that captures written bytes."""

    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, chunk: bytes) -> None:
        self.data.extend(chunk)

    async def drain(self) -> None:
        return None


def _sdk_frame(sdk_sender: Any, payload: dict[str, Any]) -> bytes:
    """Bytes the SDK's MessageSender actually writes for one payload."""
    message_sender, task_supervisor = sdk_sender

    async def _capture() -> bytes:
        writer = _FakeWriter()
        supervisor = task_supervisor(source="drei-test")
        sender = message_sender(writer, supervisor)
        await sender.send(payload)
        await sender.close()
        return bytes(writer.data)

    return asyncio.run(_capture())


@pytest.mark.parametrize("payload", _PAYLOADS)
def test_encode_matches_sdk_wire_bytes(
    sdk_sender: Any, payload: dict[str, Any]
) -> None:
    assert encode(payload) == _sdk_frame(sdk_sender, payload)
