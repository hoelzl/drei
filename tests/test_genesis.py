from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest
from conftest import FakeFilePort

from drei.commands import InsertText, SaveBuffer
from drei.files import VisitOpened
from drei.genesis import (
    UNKNOWN_FRAME,
    InitialBufferGenesis,
    InitialWindowGenesis,
    KnownFrame,
    SessionGenesisV1,
    opened_genesis,
    provided_genesis,
    scratch_genesis,
)
from drei.harness import EditorHarness
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def test_scratch_genesis_is_exact_and_closed() -> None:
    genesis = scratch_genesis(UNKNOWN_FRAME)

    assert genesis == SessionGenesisV1(
        initial_buffer=InitialBufferGenesis(
            buffer_id="scratch",
            origin="scratch",
            kind="ordinary",
            text="",
            point=0,
            mark=None,
            file_path=None,
            modified=False,
            saved_text="",
            line_ending="\n",
        ),
        initial_windows=(InitialWindowGenesis("scratch", 0, None),),
        focused_window=0,
        frame=UNKNOWN_FRAME,
    )


def test_provided_genesis_canonicalizes_before_recording_evidence() -> None:
    raw = Buffer(
        BufferId("provided"),
        BufferValue(
            text="a\r\nb\r\n",
            point=6,
            mark=3,
            modified=True,
        ),
    )

    genesis = provided_genesis(raw, UNKNOWN_FRAME)

    assert genesis.initial_buffer == InitialBufferGenesis(
        buffer_id="provided",
        origin="provided",
        kind="ordinary",
        text="a\nb\n",
        point=4,
        mark=2,
        file_path=None,
        modified=True,
        saved_text=None,
        line_ending="\r\n",
    )
    assert genesis.initial_windows == (InitialWindowGenesis("provided", 4, 2),)


def test_session_installs_canonical_crlf_genesis_without_rederiving_policy() -> None:
    port = FakeFilePort({"notes.txt": "a\r\nb\r\n"})
    genesis = opened_genesis(
        VisitOpened(
            origin="existing_file",
            path="notes.txt",
            buffer_id="notes.txt",
            text="a\nb\n",
            saved_text="a\nb\n",
            line_ending="\r\n",
        ),
        KnownFrame(80, 24),
    )

    session = EditorSession.from_genesis(genesis, file_port=port)
    session.dispatch(InsertText("x"))
    session.dispatch(SaveBuffer())

    assert session.genesis is genesis
    assert session.buffer.current.text == "xa\nb\n"
    assert port.files["notes.txt"] == "xa\r\nb\r\n"


def test_harness_installs_exact_genesis() -> None:
    genesis = scratch_genesis(KnownFrame(80, 24))

    harness = EditorHarness.from_genesis(genesis)

    assert harness.genesis is genesis


def test_direct_constructor_records_known_and_unknown_geometry() -> None:
    buffer = Buffer(BufferId("provided"), BufferValue(text="", point=0))

    unknown = EditorSession(buffer).genesis
    known = EditorSession(
        Buffer(BufferId("provided"), BufferValue(text="", point=0)),
        frame_size=(80, 24),
    ).genesis

    assert unknown.frame == UNKNOWN_FRAME
    assert known.frame == KnownFrame(80, 24)


@pytest.mark.parametrize(
    "invalid",
    [
        lambda: KnownFrame(-1, 24),
        lambda: KnownFrame(80, -1),
        lambda: KnownFrame(cast(int, True), 24),
        lambda: KnownFrame(cast(int, "80"), 24),
    ],
)
def test_invalid_known_geometry_is_rejected(invalid: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        invalid()


def _scratch_buffer() -> InitialBufferGenesis:
    return scratch_genesis(UNKNOWN_FRAME).initial_buffer


@pytest.mark.parametrize(
    "invalid",
    [
        lambda: replace(_scratch_buffer(), buffer_id=""),
        lambda: replace(_scratch_buffer(), buffer_id="other"),
        lambda: replace(_scratch_buffer(), kind=cast(Any, "generated")),
        lambda: replace(_scratch_buffer(), point=1),
        lambda: replace(_scratch_buffer(), mark=-1),
        lambda: replace(_scratch_buffer(), file_path="scratch"),
        lambda: replace(_scratch_buffer(), text="x"),
        lambda: replace(_scratch_buffer(), modified=True, saved_text=None),
        lambda: replace(_scratch_buffer(), saved_text=None),
        lambda: replace(_scratch_buffer(), line_ending=cast(Any, "invalid")),
        lambda: replace(_scratch_buffer(), origin=cast(Any, "invalid")),
        lambda: replace(_scratch_buffer(), line_ending="\r\n"),
        lambda: replace(
            _scratch_buffer(),
            origin="existing_file",
            buffer_id="f",
            text="x",
            saved_text="x",
        ),
        lambda: replace(
            _scratch_buffer(),
            origin="missing_file",
            buffer_id="f",
            file_path="f",
            text="x",
            saved_text="x",
        ),
        lambda: replace(
            _scratch_buffer(), origin="provided", modified=True, saved_text=""
        ),
    ],
)
def test_invalid_initial_buffer_combinations_are_rejected(
    invalid: Callable[[], object],
) -> None:
    with pytest.raises(ValueError):
        invalid()


@pytest.mark.parametrize(
    "invalid",
    [
        lambda base: replace(base, version=cast(object, 2)),
        lambda base: replace(base, initial_windows=()),
        lambda base: replace(
            base, initial_windows=(base.initial_windows[0], base.initial_windows[0])
        ),
        lambda base: replace(base, focused_window=1),
        lambda base: replace(
            base,
            initial_windows=(replace(base.initial_windows[0], buffer_id="other"),),
        ),
        lambda base: replace(
            base, initial_windows=(replace(base.initial_windows[0], point=1),)
        ),
        lambda base: replace(
            base, initial_windows=(replace(base.initial_windows[0], mark=0),)
        ),
        lambda base: replace(base, frame=cast(object, (80, 24))),
    ],
)
def test_invalid_closed_session_shape_is_rejected(
    invalid: Callable[[SessionGenesisV1], object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        invalid(scratch_genesis(UNKNOWN_FRAME))
