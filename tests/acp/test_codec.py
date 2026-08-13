"""NDJSON framing codec for the ACP wire (design 0003 §B.5).

The codec is pure: bytes in, bytes out, no I/O. The contract is pinned
byte-for-byte to the official ACP Python SDK (the real ``hermes acp`` peer):
``json.dumps(payload, separators=(",", ":")) + "\\n"``, utf-8.
"""

from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from drei.acp.codec import DecodedFrame, DecodeFailure, JsonRpcDecoder, encode

# A representative JSON-RPC request payload.
_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "session/new",
    "params": {"cwd": "/work", "mcpServers": []},
}

# JSON values the codec must round-trip: scalars, containers, unicode, and
# nested structures with ACP-ish shapes.
_json_scalars = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(),
    st.booleans(),
    st.none(),
)
_json_values = st.recursive(
    _json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(min_size=1, max_size=8), children, max_size=5),
    ),
    max_leaves=12,
)


def test_encode_matches_sdk_bytes_exactly() -> None:
    # The SDK framing: json.dumps(payload, separators=(",", ":")) + "\n", utf-8.
    expected = (json.dumps(_REQUEST, separators=(",", ":")) + "\n").encode("utf-8")
    assert encode(_REQUEST) == expected


def test_encode_is_compact_and_newline_terminated() -> None:
    frame = encode({"a": 1, "b": [True, None]})
    assert frame.endswith(b"\n")
    assert frame.count(b"\n") == 1  # single line, no embedded newlines
    assert b", " not in frame and b": " not in frame  # compact separators


def test_encode_utf8_non_ascii_escaped_on_wire() -> None:
    # The SDK uses json.dumps' default ensure_ascii=True, so non-ASCII is
    # \uXXXX-escaped on the wire. Match it byte-for-byte.
    assert (
        encode({"text": "héllo → 世界"})
        == b'{"text":"h\\u00e9llo \\u2192 \\u4e16\\u754c"}\n'
    )


def test_decoder_accepts_literal_utf8_from_peer() -> None:
    # A peer may send literal (unescaped) UTF-8; json.loads accepts both.
    decoder = JsonRpcDecoder()
    decoder.feed('{"text":"héllo"}\n'.encode())
    assert decoder.messages() == [DecodedFrame({"text": "héllo"})]


def test_decoder_round_trip_single_frame() -> None:
    decoder = JsonRpcDecoder()
    decoder.feed(encode(_REQUEST))
    assert decoder.messages() == [DecodedFrame(_REQUEST)]
    assert decoder.messages() == []  # drained


def test_decoder_multiple_frames_one_chunk() -> None:
    decoder = JsonRpcDecoder()
    decoder.feed(encode({"n": 1}) + encode({"n": 2}) + encode({"n": 3}))
    assert decoder.messages() == [
        DecodedFrame({"n": 1}),
        DecodedFrame({"n": 2}),
        DecodedFrame({"n": 3}),
    ]


def test_decoder_partial_line_across_feeds() -> None:
    frame = encode(_REQUEST)
    decoder = JsonRpcDecoder()
    mid = len(frame) // 2
    decoder.feed(frame[:mid])
    assert decoder.messages() == []  # incomplete: nothing yet
    decoder.feed(frame[mid:])
    assert decoder.messages() == [DecodedFrame(_REQUEST)]


def test_decoder_split_inside_multibyte_utf8() -> None:
    frame = encode({"text": "世界"})
    decoder = JsonRpcDecoder()
    # Feed byte-by-byte so a multibyte char is split across feeds.
    for i in range(len(frame)):
        decoder.feed(frame[i : i + 1])
    assert decoder.messages() == [DecodedFrame({"text": "世界"})]


def test_decoder_malformed_line_is_an_ordered_failure() -> None:
    decoder = JsonRpcDecoder()
    decoder.feed(b"{not json}\n")
    assert decoder.messages() == [DecodeFailure(b"{not json}")]


def test_decoder_invalid_utf8_is_an_ordered_failure() -> None:
    decoder = JsonRpcDecoder()
    decoder.feed(b'{"t":"\xff\xfe"}\n')
    assert decoder.messages() == [DecodeFailure(b'{"t":"\xff\xfe"}')]


def test_decoder_literal_utf8_split_mid_multibyte_char() -> None:
    # A peer may send literal (unescaped) UTF-8; a multibyte char can split
    # across feed() calls. Feed byte-by-byte to force the split.
    decoder = JsonRpcDecoder()
    frame = '{"text":"世界"}\n'.encode()
    for i in range(len(frame)):
        decoder.feed(frame[i : i + 1])
    assert decoder.messages() == [DecodedFrame({"text": "世界"})]


def test_decoder_recovers_after_malformed_line() -> None:
    decoder = JsonRpcDecoder()
    decoder.feed(b"garbage\n" + encode({"ok": True}))
    assert decoder.messages() == [
        DecodeFailure(b"garbage"),
        DecodedFrame({"ok": True}),
    ]
    assert decoder.messages() == []


def test_decoder_preserves_frames_parsed_before_malformed_line() -> None:
    decoder = JsonRpcDecoder()
    decoder.feed(encode({"a": 1}) + b"notjson\n" + encode({"b": 2}))
    assert decoder.messages() == [
        DecodedFrame({"a": 1}),
        DecodeFailure(b"notjson"),
        DecodedFrame({"b": 2}),
    ]


def test_decoder_preserves_multiple_failures_in_wire_order() -> None:
    decoder = JsonRpcDecoder()
    decoder.feed(b"bad-one\nbad-two\n" + encode({"ok": 3}))
    assert decoder.messages() == [
        DecodeFailure(b"bad-one"),
        DecodeFailure(b"bad-two"),
        DecodedFrame({"ok": 3}),
    ]


def test_decoder_returns_complete_results_once_and_keeps_only_partial_bytes() -> None:
    decoder = JsonRpcDecoder()
    decoder.feed(encode({"a": 1}) + b'{"b":')
    assert decoder.messages() == [DecodedFrame({"a": 1})]
    assert decoder.messages() == []
    decoder.feed(b"2}\n")
    assert decoder.messages() == [DecodedFrame({"b": 2})]


def test_decoder_blank_lines_ignored() -> None:
    decoder = JsonRpcDecoder()
    decoder.feed(b"\n" + encode({"x": 1}) + b"\n\n")
    assert decoder.messages() == [DecodedFrame({"x": 1})]


@settings(max_examples=100, deadline=None, derandomize=True)
@given(value=_json_values)
def test_encode_decode_round_trip(value: object) -> None:
    decoder = JsonRpcDecoder()
    decoder.feed(encode(value))
    assert decoder.messages() == [DecodedFrame(value)]


@st.composite
def _chunked_stream(
    draw: st.DrawFn,
) -> tuple[list[object], list[bytes]]:
    """A list of JSON values plus an arbitrary byte-chunking of their stream."""
    values = draw(st.lists(_json_values, min_size=1, max_size=6))
    blob = b"".join(encode(v) for v in values)
    points = sorted(
        draw(
            st.lists(
                st.integers(min_value=0, max_value=len(blob)),
                max_size=len(blob),
                unique=True,
            )
        )
    )
    chunks: list[bytes] = []
    prev = 0
    for point in [*points, len(blob)]:
        chunks.append(blob[prev:point])
        prev = point
    return values, chunks


@settings(max_examples=100, deadline=None, derandomize=True)
@given(stream=_chunked_stream())
def test_chunked_delivery_yields_all_messages_in_order(
    stream: tuple[list[object], list[bytes]],
) -> None:
    """Arbitrary chunk splits of a multi-frame stream decode to exactly the
    original sequence, in order (encode∘decode = id under chunking)."""
    values, chunks = stream
    decoder = JsonRpcDecoder()
    out: list[object] = []
    for chunk in chunks:
        decoder.feed(chunk)
        results = decoder.messages()
        assert all(isinstance(result, DecodedFrame) for result in results)
        out.extend(
            result.value for result in results if isinstance(result, DecodedFrame)
        )
    assert out == values
