"""NDJSON framing codec for the ACP wire (design 0003 §B.5).

Pure functions and values over bytes — no I/O, no ``subprocess``, no
``asyncio``. The contract is pinned byte-for-byte to the official ACP Python
SDK (the real ``hermes acp`` peer): one JSON-RPC value per line,
``\\n``-terminated, compact separators, utf-8 (``acp/task/sender.py:33``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

JsonValue = Any


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    """One complete, valid JSON value from the ACP wire."""

    value: JsonValue


@dataclass(frozen=True, slots=True)
class DecodeFailure:
    """One complete ACP wire line that was not valid JSON."""

    line: bytes


DecodeResult = DecodedFrame | DecodeFailure


def encode(message: JsonValue) -> bytes:
    """Encode one JSON-RPC value as a single NDJSON frame.

    Matches the SDK byte-for-byte: ``json.dumps(m, separators=(",", ":"))``
    then a ``\\n`` terminator, utf-8.
    """
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


class JsonRpcDecoder:
    """Incremental, chunk-safe NDJSON decoder.

    Bytes arrive from the child in arbitrary chunks, not line-aligned (the
    §C streaming pump feeds whatever the pipe delivered). ``feed`` buffers;
    ``messages`` drains every complete line as an ordered decoded-frame or
    decode-failure result. Only an incomplete trailing line remains buffered.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> None:
        self._buffer.extend(data)

    def messages(self) -> list[DecodeResult]:
        """Drain every complete ``\n``-terminated wire line in order."""
        out: list[DecodeResult] = []
        while (idx := self._buffer.find(b"\n")) != -1:
            line = bytes(self._buffer[:idx])
            del self._buffer[: idx + 1]
            if not line.strip():
                continue  # tolerate blank lines between frames
            try:
                out.append(DecodedFrame(json.loads(line)))
            except (json.JSONDecodeError, UnicodeDecodeError):
                out.append(DecodeFailure(line))
        return out
