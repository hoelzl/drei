"""File effect port: all filesystem access behind an explicit boundary.

The deterministic command path never touches the filesystem directly; the
session calls an injected ``FilePort`` from ``SaveBuffer`` dispatch and
records the result as immutable events. The real port is used only at the
CLI boundary (startup load and saves in the shipped executable).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


class FilePort(Protocol):
    """Effect port for file reads (startup) and writes (save).

    The port translates nothing: ``read`` returns the file's characters as
    stored (CRLF stays CRLF) and ``write`` stores exactly the characters it
    is given. Line-ending policy belongs to the session, which decides what
    a buffer holds and what a save writes back.
    """

    def read(self, path: str) -> str: ...

    def write(self, path: str, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class VisitOpened:
    """Canonical semantic facts for a valid literal visit target."""

    origin: Literal["existing_file", "missing_file"]
    path: str
    buffer_id: str
    text: str
    saved_text: str
    line_ending: LineEnding


@dataclass(frozen=True, slots=True)
class VisitRejected:
    """A path that cannot become an ordinary visiting buffer."""

    path: str
    error: str


VisitResolution = VisitOpened | VisitRejected


def resolve_visit(port: FilePort, path: str) -> VisitResolution:
    """Resolve one literal visit request through ``port``."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if not name:
        return VisitRejected(path, "empty-basename")
    try:
        file_text = port.read(path)
    except FileNotFoundError:
        return VisitOpened(
            origin="missing_file",
            path=path,
            buffer_id=name,
            text="",
            saved_text="",
            line_ending=LF,
        )
    except UnicodeDecodeError:
        return VisitRejected(path, "io-error")
    except OSError as error:
        return VisitRejected(path, normalize_os_error(error))
    line_ending = detect_line_ending(file_text)
    text = to_buffer_text(file_text, line_ending)
    return VisitOpened(
        origin="existing_file",
        path=path,
        buffer_id=name,
        text=text,
        saved_text=text,
        line_ending=line_ending,
    )


type LineEnding = Literal["\n", "\r\n"]

LF: Literal["\n"] = "\n"
CRLF: Literal["\r\n"] = "\r\n"


def detect_line_ending(text: str) -> LineEnding:
    """The line ending a save should write back for this file text.

    ``CRLF`` only when *every* line separator in the text is CRLF; ``LF``
    otherwise — including for text with mixed endings, where there is no
    single convention to preserve and the CRs stay literal buffer characters
    (Emacs shows them as ``^M``), and for a lone CR, which is not treated as
    a line ending here.
    """
    if CRLF not in text:
        return LF
    rest = text.replace(CRLF, "")
    return LF if "\r" in rest or "\n" in rest else CRLF


def to_buffer_text(file_text: str, eol: str) -> str:
    """File text as the buffer holds it: LF-separated when the file is
    uniformly CRLF, untouched otherwise."""
    return file_text.replace(CRLF, LF) if eol == CRLF else file_text


def to_file_text(buffer_text: str, eol: str) -> str:
    """Buffer text as it is written back, restoring the file's endings."""
    if eol != CRLF:
        return buffer_text
    # Collapse first so a CR the user typed before a newline cannot double up.
    return buffer_text.replace(CRLF, LF).replace(LF, CRLF)


def normalize_os_error(error: OSError) -> str:
    """Map an ``OSError`` to a normalized, Drei-owned error token.

    Raw exception text is platform- and locale-dependent; events and echo
    text carry only these tokens so replay and golden assertions are
    portable.
    """
    if isinstance(error, FileNotFoundError):
        return "not-found"
    if isinstance(error, PermissionError):
        return "permission-denied"
    return "io-error"


class SystemFilePort:
    """Production file port using the real filesystem (utf-8, as-is).

    ``newline=""`` on both sides makes "as-is" true: without it the read
    applies universal-newline translation and the write turns every ``\\n``
    into ``os.linesep``, so saving an unedited file rewrote its line endings
    (review 0001 finding 1). Line-ending *policy* is the session's — the port
    only moves bytes.
    """

    def read(self, path: str) -> str:  # pragma: no cover - exercised via CLI/TermVerify
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()

    def write(self, path: str, text: str) -> None:  # pragma: no cover
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
