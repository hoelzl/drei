"""JSON-RPC 2.0 envelope model for ACP (design 0003 §B.5).

Frozen dataclasses + builders/parsers so the state machine never hand-builds
dicts. Framing-agnostic: these are the *envelopes*; ``drei.acp.codec`` frames
them. Structurally invalid envelopes raise ``AcpProtocolError``.
"""

from __future__ import annotations

import pytest

from drei.acp.messages import (
    INITIALIZE,
    SESSION_CANCEL,
    SESSION_NEW,
    SESSION_PROMPT,
    SESSION_REQUEST_PERMISSION,
    SESSION_UPDATE,
    AcpProtocolError,
    Notification,
    Request,
    Response,
    ResponseError,
    parse_message,
    to_json,
)


def test_request_round_trip() -> None:
    request = Request(
        id=1, method=SESSION_NEW, params={"cwd": "/work", "mcpServers": []}
    )
    assert to_json(request) == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "session/new",
        "params": {"cwd": "/work", "mcpServers": []},
    }
    assert parse_message(to_json(request)) == request


def test_request_without_params_omits_params_key() -> None:
    request = Request(id="abc", method=INITIALIZE, params=None)
    assert to_json(request) == {"jsonrpc": "2.0", "id": "abc", "method": "initialize"}
    assert parse_message(to_json(request)) == request


def test_notification_has_no_id() -> None:
    notification = Notification(method=SESSION_CANCEL, params={"sessionId": "s1"})
    payload = to_json(notification)
    assert "id" not in payload
    assert payload == {
        "jsonrpc": "2.0",
        "method": "session/cancel",
        "params": {"sessionId": "s1"},
    }
    assert parse_message(payload) == notification


def test_response_round_trip() -> None:
    response = Response(id=1, result={"sessionId": "s1"})
    assert to_json(response) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"sessionId": "s1"},
    }
    assert parse_message(to_json(response)) == response


def test_response_error_round_trip() -> None:
    error = ResponseError(id=2, code=-32601, message="Method not found", data=None)
    assert to_json(error) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32601, "message": "Method not found"},
    }
    assert parse_message(to_json(error)) == error


def test_response_error_with_data() -> None:
    error = ResponseError(id=3, code=-32000, message="boom", data={"detail": "x"})
    assert to_json(error)["error"] == {
        "code": -32000,
        "message": "boom",
        "data": {"detail": "x"},
    }
    assert parse_message(to_json(error)) == error


def test_session_update_notification_parses() -> None:
    payload = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "s1",
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "hello"},
            },
        },
    }
    message = parse_message(payload)
    assert isinstance(message, Notification)
    assert message.method == SESSION_UPDATE


def test_request_permission_is_a_request() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "session/request_permission",
        "params": {"sessionId": "s1", "toolCall": {}, "options": []},
    }
    message = parse_message(payload)
    assert isinstance(message, Request)
    assert message.method == SESSION_REQUEST_PERMISSION
    assert message.id == 7


def test_parse_rejects_both_result_and_error() -> None:
    with pytest.raises(AcpProtocolError):
        parse_message({"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}})


def test_parse_rejects_missing_jsonrpc_version() -> None:
    with pytest.raises(AcpProtocolError):
        parse_message({"id": 1, "method": "initialize"})


def test_parse_rejects_wrong_jsonrpc_version() -> None:
    with pytest.raises(AcpProtocolError):
        parse_message({"jsonrpc": "1.0", "id": 1, "method": "initialize"})


def test_parse_rejects_non_object() -> None:
    with pytest.raises(AcpProtocolError):
        parse_message([1, 2, 3])


def test_parse_rejects_method_without_method_name() -> None:
    with pytest.raises(AcpProtocolError):
        parse_message({"jsonrpc": "2.0", "id": 1})


def test_method_constants_match_acp_names() -> None:
    assert INITIALIZE == "initialize"
    assert SESSION_NEW == "session/new"
    assert SESSION_PROMPT == "session/prompt"
    assert SESSION_CANCEL == "session/cancel"
    assert SESSION_UPDATE == "session/update"
    assert SESSION_REQUEST_PERMISSION == "session/request_permission"


def test_notification_without_params_omits_params_key() -> None:
    assert to_json(Notification(method=SESSION_CANCEL)) == {
        "jsonrpc": "2.0",
        "method": "session/cancel",
    }


def test_parse_rejects_non_string_method() -> None:
    with pytest.raises(AcpProtocolError):
        parse_message({"jsonrpc": "2.0", "id": 1, "method": 42})


def test_parse_rejects_malformed_error_object() -> None:
    with pytest.raises(AcpProtocolError):
        parse_message({"jsonrpc": "2.0", "id": 1, "error": "not-an-object"})
    with pytest.raises(AcpProtocolError):
        parse_message({"jsonrpc": "2.0", "id": 1, "error": {"code": -1}})  # no message
