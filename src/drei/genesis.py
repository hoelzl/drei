"""Immutable, versioned semantic evidence for session construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from drei.files import (
    CRLF,
    LF,
    LineEnding,
    VisitOpened,
    detect_line_ending,
    to_buffer_text,
)
from drei.model import Buffer

BufferOrigin = Literal["scratch", "existing_file", "missing_file", "provided"]
BufferKind = Literal["ordinary"]


@dataclass(frozen=True, slots=True)
class KnownFrame:
    width: int
    height: int

    def __post_init__(self) -> None:
        dimensions = (self.width, self.height)
        if any(type(value) is not int or value < 0 for value in dimensions):
            raise ValueError("known frame dimensions must be non-negative integers")


@dataclass(frozen=True, slots=True)
class UnknownFrame:
    """Explicit absence of initial semantic geometry."""


UNKNOWN_FRAME = UnknownFrame()
FrameGenesis = KnownFrame | UnknownFrame


@dataclass(frozen=True, slots=True, kw_only=True)
class InitialBufferGenesis:
    buffer_id: str
    origin: BufferOrigin
    kind: BufferKind
    text: str
    point: int
    mark: int | None
    file_path: str | None
    modified: bool
    saved_text: str | None
    line_ending: LineEnding

    def __post_init__(self) -> None:
        if type(self.buffer_id) is not str or not self.buffer_id:
            raise TypeError("initial buffer id must be a non-empty string")
        if type(self.origin) is not str:
            raise TypeError("initial buffer origin must be a string")
        # Dynamic callers can bypass annotations; the malformed-kind test pins
        # this guard even though mypy treats annotated ``str`` as exact here.
        if type(self.kind) is not str:  # type: ignore[unreachable]
            raise TypeError("initial buffer kind must be a string")
        if type(self.text) is not str:
            raise TypeError("initial buffer text must be a string")
        if type(self.point) is not int:
            raise TypeError("initial point must be an integer")
        if self.mark is not None and type(self.mark) is not int:
            raise TypeError("initial mark must be an integer or None")
        if self.file_path is not None and type(self.file_path) is not str:
            raise TypeError("initial file path must be a string or None")
        if type(self.modified) is not bool:
            raise TypeError("initial modified flag must be a boolean")
        if self.saved_text is not None and type(self.saved_text) is not str:
            raise TypeError("initial saved text must be a string or None")
        if type(self.line_ending) is not str:
            raise TypeError("initial line ending must be a string")
        if self.kind != "ordinary":
            raise ValueError("v1 initial buffer must be ordinary")
        if not 0 <= self.point <= len(self.text):
            raise ValueError("initial point outside canonical text")
        if self.mark is not None and not 0 <= self.mark <= len(self.text):
            raise ValueError("initial mark outside canonical text")
        if self.line_ending not in (LF, CRLF):
            raise ValueError("unsupported initial line ending")
        if detect_line_ending(self.text) == CRLF or (
            self.line_ending == CRLF and CRLF in self.text
        ):
            raise ValueError("CRLF genesis text must already be canonical")

        clean_basis = not self.modified and self.saved_text == self.text
        origin = cast(object, self.origin)
        if origin == "scratch":
            valid = (
                self.buffer_id == "scratch"
                and self.file_path is None
                and self.text == ""
                and clean_basis
                and self.line_ending == LF
            )
        elif origin == "existing_file":
            valid = bool(self.file_path) and clean_basis
        elif origin == "missing_file":
            valid = (
                bool(self.file_path)
                and self.text == ""
                and clean_basis
                and self.line_ending == LF
            )
        elif origin == "provided":
            valid = clean_basis if not self.modified else self.saved_text is None
        else:
            valid = False
        if not valid:
            raise ValueError("invalid initial origin/identity/clean-basis combination")


@dataclass(frozen=True, slots=True)
class InitialWindowGenesis:
    buffer_id: str
    point: int
    mark: int | None

    def __post_init__(self) -> None:
        if type(self.buffer_id) is not str or not self.buffer_id:
            raise TypeError("initial window buffer id must be a non-empty string")
        if type(self.point) is not int:
            raise TypeError("initial window point must be an integer")
        if self.mark is not None and type(self.mark) is not int:
            raise TypeError("initial window mark must be an integer or None")
        if self.point < 0 or (self.mark is not None and self.mark < 0):
            raise ValueError("initial window coordinates must be non-negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionGenesisV1:
    initial_buffer: InitialBufferGenesis
    initial_windows: tuple[InitialWindowGenesis, ...]
    focused_window: int
    frame: FrameGenesis
    version: Literal[1] = 1

    def __post_init__(self) -> None:
        if type(self.initial_buffer) is not InitialBufferGenesis:
            raise TypeError("unsupported initial buffer value")
        if type(self.initial_windows) is not tuple:
            raise TypeError("initial windows must be an immutable tuple")
        if any(
            type(window) is not InitialWindowGenesis for window in self.initial_windows
        ):
            raise TypeError("unsupported initial window value")
        if type(self.version) is not int or self.version != 1:
            raise ValueError("unsupported session genesis version")
        if len(self.initial_windows) != 1:
            raise ValueError("v1 genesis requires exactly one initial window")
        if type(self.focused_window) is not int:
            raise TypeError("focused window must be an integer")
        if self.focused_window != 0:
            raise ValueError("v1 focused window must be index zero")
        window = self.initial_windows[0]
        initial = self.initial_buffer
        if (
            window.buffer_id != initial.buffer_id
            or window.point != initial.point
            or window.mark != initial.mark
        ):
            raise ValueError("initial window must match the initial buffer")
        if type(self.frame) not in (KnownFrame, UnknownFrame):
            raise TypeError("unsupported genesis frame value")


def _genesis(initial: InitialBufferGenesis, frame: FrameGenesis) -> SessionGenesisV1:
    return SessionGenesisV1(
        initial_buffer=initial,
        initial_windows=(
            InitialWindowGenesis(initial.buffer_id, initial.point, initial.mark),
        ),
        focused_window=0,
        frame=frame,
    )


def scratch_genesis(frame: FrameGenesis) -> SessionGenesisV1:
    return _genesis(
        InitialBufferGenesis(
            buffer_id="scratch",
            origin="scratch",
            kind="ordinary",
            text="",
            point=0,
            mark=None,
            file_path=None,
            modified=False,
            saved_text="",
            line_ending=LF,
        ),
        frame,
    )


def opened_genesis(opened: VisitOpened, frame: FrameGenesis) -> SessionGenesisV1:
    return _genesis(
        InitialBufferGenesis(
            buffer_id=opened.buffer_id,
            origin=opened.origin,
            kind="ordinary",
            text=opened.text,
            point=0,
            mark=None,
            file_path=opened.path,
            modified=False,
            saved_text=opened.saved_text,
            line_ending=opened.line_ending,
        ),
        frame,
    )


def _shift_index(file_text: str, index: int) -> int:
    return index - file_text.count(CRLF, 0, index)


def provided_genesis(buffer: Buffer, frame: FrameGenesis) -> SessionGenesisV1:
    """Prepare the legacy direct profile before genesis exists."""
    return _genesis(prepare_provided_buffer(buffer), frame)


def prepare_provided_buffer(buffer: Buffer) -> InitialBufferGenesis:
    """Canonicalize a caller-provided buffer before installation."""
    raw = buffer.current
    line_ending = detect_line_ending(raw.text)
    text = to_buffer_text(raw.text, line_ending)
    point = _shift_index(raw.text, raw.point) if text != raw.text else raw.point
    mark = (
        None
        if raw.mark is None
        else _shift_index(raw.text, raw.mark)
        if text != raw.text
        else raw.mark
    )
    return InitialBufferGenesis(
        buffer_id=buffer.buffer_id.value,
        origin="provided",
        kind="ordinary",
        text=text,
        point=point,
        mark=mark,
        file_path=raw.file_path,
        modified=raw.modified,
        saved_text=None if raw.modified else text,
        line_ending=line_ending,
    )
