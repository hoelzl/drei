"""Public surface of the drei.acp package (design 0003 §B.5)."""

from __future__ import annotations

import drei.acp as acp_pkg
from drei.acp import (
    DecodedFrame,
    DecodeFailure,
    DecodeResult,
    JsonRpcDecoder,
    JsonValue,
    encode,
)


def test_top_level_reexports_codec() -> None:
    assert acp_pkg.encode is encode
    assert acp_pkg.JsonRpcDecoder is JsonRpcDecoder
    assert acp_pkg.DecodedFrame is DecodedFrame
    assert acp_pkg.DecodeFailure is DecodeFailure
    assert acp_pkg.DecodeResult is DecodeResult
    assert acp_pkg.JsonValue is JsonValue


def test_lazy_method_constants_resolve() -> None:
    # Message-layer constants resolve on demand and match the message module.
    from drei.acp.messages import INITIALIZE, SESSION_UPDATE, Request

    assert acp_pkg.INITIALIZE == "initialize" == INITIALIZE
    assert acp_pkg.SESSION_UPDATE == "session/update" == SESSION_UPDATE
    assert acp_pkg.Request is Request


def test_message_layer_is_typed_not_object() -> None:
    # The constants come from drei.acp.messages with real types, so assigning a
    # method constant to a str stays well-typed (the prior bare __getattr__
    # returned object). Runtime check: the value is a str.
    assert isinstance(acp_pkg.SESSION_NEW, str)
    value: JsonValue = {"typed": True}
    result: DecodeResult = DecodedFrame(value)
    assert result == DecodedFrame({"typed": True})


def test_dir_lists_public_surface() -> None:
    expected = {
        "encode",
        "JsonRpcDecoder",
        "DecodedFrame",
        "DecodeFailure",
        "DecodeResult",
        "JsonValue",
        "messages",
    }
    assert set(dir(acp_pkg)) >= expected
