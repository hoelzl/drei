"""Validation of DeliverProcessOutput (the ACP pump's injection seam).

Machine-generated deliveries must not record corrupt provenance into the
transcript. ``DeliverProcessOutput.__post_init__`` enforces the contract at
construction, the way ``BufferValue.__post_init__`` enforces buffer bounds.
"""

from __future__ import annotations

import pytest

from drei.commands import DeliverProcessOutput
from drei.process import ProcessResult


def _result(argv: tuple[str, ...] = ("cmd",), exit_code: int = 0) -> ProcessResult:
    return ProcessResult(argv=argv, exit_code=exit_code, stdout="o", stderr="e")


def test_result_only_is_valid() -> None:
    delivery = DeliverProcessOutput(("cmd",), _result(), None)
    assert delivery.result is not None
    assert delivery.error is None


def test_error_only_is_valid() -> None:
    delivery = DeliverProcessOutput(("cmd",), None, "not-found")
    assert delivery.result is None
    assert delivery.error == "not-found"


def test_both_result_and_error_is_rejected() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DeliverProcessOutput(("cmd",), _result(), "not-found")


def test_neither_result_nor_error_is_rejected() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DeliverProcessOutput(("cmd",), None, None)


def test_result_argv_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="argv"):
        DeliverProcessOutput(("cmd",), _result(argv=("OTHER",)), None)


def test_unrecognized_error_token_is_rejected() -> None:
    with pytest.raises(ValueError, match="error"):
        DeliverProcessOutput(("cmd",), None, "some-free-string")


def test_empty_error_token_is_rejected() -> None:
    with pytest.raises(ValueError, match="error"):
        DeliverProcessOutput(("cmd",), None, "")


@pytest.mark.parametrize(
    "token", ["not-found", "permission-denied", "io-error", "timeout"]
)
def test_all_normalized_tokens_accepted(token: str) -> None:
    delivery = DeliverProcessOutput(("cmd",), None, token)
    assert delivery.error == token
