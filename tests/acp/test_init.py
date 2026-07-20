"""Public surface of the drei.acp package (design 0003 §B.5)."""

from __future__ import annotations

import drei.acp as acp_pkg
from drei.acp import AcpDecodeError, JsonRpcDecoder, encode


def test_top_level_reexports_codec() -> None:
    assert acp_pkg.encode is encode
    assert acp_pkg.JsonRpcDecoder is JsonRpcDecoder
    assert acp_pkg.AcpDecodeError is AcpDecodeError


def test_lazy_method_constants_resolve() -> None:
    # The package lazily exposes the message-layer constants without importing
    # drei.commands at codec import time.
    assert acp_pkg.INITIALIZE == "initialize"
    assert acp_pkg.SESSION_UPDATE == "session/update"
    from drei.acp.messages import Request

    assert acp_pkg.Request is Request


def test_dir_lists_public_surface() -> None:
    expected = {"encode", "JsonRpcDecoder", "AcpDecodeError", "messages"}
    assert set(dir(acp_pkg)) >= expected
