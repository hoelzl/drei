"""NDJSON framing codec for the ACP wire (design 0003 §B.5).

Pure functions and values over bytes — no I/O, no ``subprocess``, no
``asyncio``. The contract is pinned byte-for-byte to the official ACP Python
SDK (the real ``hermes acp`` peer): one JSON-RPC value per line,
``\\n``-terminated, compact separators, utf-8 (``acp/task/sender.py:33``).
"""

from __future__ import annotations

import json
from typing import Any

JsonValue = Any


class AcpDecodeError(Exception):
    """A wire line was not valid JSON. Carries the offending bytes.

    Raised instead of letting a bare ``json.JSONDecodeError`` escape across
    the boundary — the same normalized-error discipline as ``drei.files`` /
    ``drei.process``.
    """

    def __init__(self, line: bytes, cause: Exception) -> None:
        super().__init__(f"invalid JSON on ACP wire: {line!r} ({cause})")
        self.line = line
        self.__cause__ = cause


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
    ``messages`` drains and returns each complete parsed frame in order. A
    malformed line raises :class:`AcpDecodeError`; the offending line is
    consumed, and frames already parsed from the same drain are retained and
    returned by the next ``messages()`` call — no valid frame is ever lost
    (a dropped initialize response would wedge the machine; a dropped
    permission request would hang the agent).
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._parsed: list[JsonValue] = []

    def feed(self, data: bytes) -> None:
        self._buffer.extend(data)

    def messages(self) -> list[JsonValue]:
        """Drain and parse every complete ``\\n``-terminated frame buffered so far."""
        while (idx := self._buffer.find(b"\n")) != -1:
            line = bytes(self._buffer[:idx])
            del self._buffer[: idx + 1]
            if not line.strip():
                continue  # tolerate blank lines between frames
            try:
                self._parsed.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                # Parsed frames stay parked on the instance for the next call.
                raise AcpDecodeError(line, error) from error
        out = self._parsed
        self._parsed = []
        return out
