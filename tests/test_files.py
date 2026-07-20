"""File-backed buffer semantics: modified flag and the SaveBuffer command."""

from __future__ import annotations

import pytest
from conftest import FakeFilePort

from drei.commands import (
    BackwardChar,
    BufferSaved,
    ForwardChar,
    InsertText,
    SaveBuffer,
    SaveFailed,
)
from drei.files import FilePort
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session(
    text: str = "",
    point: int = 0,
    file_path: str | None = None,
    port: FilePort | None = None,
) -> EditorSession:
    value = BufferValue(text=text, point=point, file_path=file_path)
    buffer_id = BufferId(file_path.rsplit("/", 1)[-1] if file_path else "scratch")
    return EditorSession(Buffer(buffer_id, value), file_port=port or FakeFilePort())


def test_insert_sets_modified() -> None:
    session = _session()
    before = session.buffer.current
    assert not before.modified
    outcome = session.dispatch(InsertText("x"))
    after = session.buffer.current
    assert after.modified
    assert outcome.observation.modified


def test_movement_preserves_modified() -> None:
    session = _session("ab", 1, file_path="f.txt")
    session.dispatch(InsertText("c"))
    assert session.buffer.current.modified is True
    for command in (BackwardChar(), ForwardChar()):
        session.dispatch(command)
        assert session.buffer.current.modified is True


def test_save_writes_through_port_and_clears_modified() -> None:
    port = FakeFilePort()
    session = _session(file_path="/tmp/notes.txt", port=port)
    session.dispatch(InsertText("hello"))
    outcome = session.dispatch(SaveBuffer())
    assert port.files["/tmp/notes.txt"] == "hello"
    assert session.buffer.current.modified is False
    assert outcome.observation.modified is False
    assert BufferSaved("/tmp/notes.txt") in outcome.events


def test_save_without_file_path_is_failure_event_not_crash() -> None:
    session = _session()  # scratch: no file
    session.dispatch(InsertText("x"))
    outcome = session.dispatch(SaveBuffer())
    assert any(isinstance(e, SaveFailed) for e in outcome.events)
    assert session.buffer.current.modified is True


def test_save_failure_is_atomic_and_normalized() -> None:
    port = FakeFilePort(fail="Permission denied")
    session = _session(file_path="/root/x.txt", port=port)
    session.dispatch(InsertText("data"))
    before = session.buffer.current
    outcome = session.dispatch(SaveBuffer())
    # Buffer unchanged (still modified), failure recorded with a token.
    assert session.buffer.current == before
    failures = [e for e in outcome.events if isinstance(e, SaveFailed)]
    assert len(failures) == 1
    assert failures[0].error in {"not-found", "permission-denied", "io-error"}
    assert failures[0].path == "/root/x.txt"


def test_file_path_threaded_through_observation() -> None:
    session = _session(file_path="/tmp/a.txt")
    outcome = session.dispatch(ForwardChar())
    assert outcome.observation.file_path == "/tmp/a.txt"


def test_error_token_mapping() -> None:
    from drei.files import normalize_os_error

    assert normalize_os_error(FileNotFoundError("x")) == "not-found"
    assert normalize_os_error(PermissionError("x")) == "permission-denied"
    assert normalize_os_error(OSError("disk on fire")) == "io-error"


def test_rejected_save_keeps_buffer_identity() -> None:
    session = _session()
    shell = session.buffer
    session.dispatch(SaveBuffer())
    assert session.buffer is shell


def test_default_null_port_fails_saves_with_token() -> None:
    # A session constructed without a port must still fail saves
    # deterministically (normalized token), never crash.
    buffer = Buffer(
        BufferId("notes.txt"),
        BufferValue(text="x", point=1, file_path="/nope/notes.txt", modified=True),
    )
    session = EditorSession(buffer)
    outcome = session.dispatch(SaveBuffer())
    failures = [e for e in outcome.events if isinstance(e, SaveFailed)]
    assert len(failures) == 1
    assert failures[0].error == "io-error"


def test_null_port_read_raises_not_found() -> None:
    from drei.session import _NullFilePort

    with pytest.raises(FileNotFoundError):
        _NullFilePort().read("/anything")
