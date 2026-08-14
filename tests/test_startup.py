from __future__ import annotations

from typing import Any, cast

import pytest

from drei.files import (
    CRLF,
    FilePort,
    VisitError,
    VisitOpened,
    VisitRejected,
    resolve_visit,
)


class RecordingFilePort(FilePort):
    def __init__(
        self,
        files: dict[str, str] | None = None,
        failure: BaseException | None = None,
    ) -> None:
        self.files = dict(files or {})
        self.failure = failure
        self.reads: list[str] = []

    def read(self, path: str) -> str:
        self.reads.append(path)
        if self.failure is not None:
            raise self.failure
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write(self, path: str, text: str) -> None:
        raise AssertionError("not used")


def test_empty_basename_rejects_before_file_access() -> None:
    port = RecordingFilePort()

    result = resolve_visit(port, "missing-dir/")

    assert result == VisitRejected("missing-dir/", "empty-basename")
    assert port.reads == []


def test_existing_crlf_file_resolves_to_canonical_buffer_facts() -> None:
    port = RecordingFilePort({"notes.txt": "a\r\nb\r\n"})

    result = resolve_visit(port, "notes.txt")

    assert result == VisitOpened(
        origin="existing_file",
        path="notes.txt",
        buffer_id="notes.txt",
        text="a\nb\n",
        saved_text="a\nb\n",
        line_ending=CRLF,
    )
    assert port.reads == ["notes.txt"]


def test_missing_valid_path_resolves_as_an_empty_visiting_buffer() -> None:
    port = RecordingFilePort()

    result = resolve_visit(port, "new/notes.txt")

    assert result == VisitOpened(
        origin="missing_file",
        path="new/notes.txt",
        buffer_id="notes.txt",
        text="",
        saved_text="",
        line_ending="\n",
    )


@pytest.mark.parametrize(
    ("failure", "token"),
    [
        (PermissionError("denied"), "permission-denied"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"), "io-error"),
        (OSError("disk"), "io-error"),
    ],
)
def test_unreadable_path_resolves_to_a_normalized_rejection(
    failure: BaseException, token: VisitError
) -> None:
    port = RecordingFilePort(failure=failure)

    result = resolve_visit(port, "notes.txt")

    assert result == VisitRejected("notes.txt", token)


@pytest.mark.parametrize("token", ["not-found", "invalid", 1])
def test_visit_rejection_requires_a_closed_error_token(token: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        VisitRejected("notes.txt", cast(Any, token))


def test_visit_rejection_requires_a_literal_string_path() -> None:
    with pytest.raises(TypeError):
        VisitRejected(cast(Any, 1), "io-error")
