from __future__ import annotations

from dataclasses import dataclass

from drei.commands import BufferObservation


@dataclass(frozen=True, slots=True)
class Frame:
    rows: tuple[str, ...]
    cursor: tuple[int, int]
    width: int
    height: int


def render(
    observation: BufferObservation,
    width: int,
    height: int,
    echo: str = "",
) -> Frame:
    if height == 0:
        return Frame(rows=(), cursor=(0, 0), width=width, height=height)

    if height == 1:
        modeline = _clip(f"Drei: {observation.buffer_id}", width)
        return Frame(rows=(modeline,), cursor=(0, 0), width=width, height=height)

    body_rows = _render_body(observation.text, width, height - 2)
    modeline = _clip(f"Drei: {observation.buffer_id}", width)
    echo_row = _clip(echo, width)

    cursor_row, cursor_col = _cursor_position(observation, width, height - 2)
    rows = body_rows + (modeline, echo_row)
    return Frame(rows=rows, cursor=(cursor_row, cursor_col), width=width, height=height)


def _clip(text: str, width: int) -> str:
    if width == 0:
        return ""
    clipped = _sanitize(text)[:width]
    return clipped.ljust(width)


def _sanitize(text: str) -> str:
    """Replace control characters with caret notation (Emacs convention).

    The frame is written verbatim to the terminal; raw C0/C1 bytes would
    allow escape-sequence injection (screen clear, OSC hyperlinks, clipboard
    exfiltration) from buffer text into the controlling terminal. Newlines
    never reach this function: body rendering splits on them first.
    """
    out = []
    for char in text:
        code = ord(char)
        if code < 0x20:
            out.append("^" + chr(code + 0x40))
        elif code == 0x7F:
            out.append("^?")
        elif 0x80 <= code <= 0x9F:
            out.append("^" + chr(code - 0x40))
        else:
            out.append(char)
    return "".join(out)


def _render_body(text: str, width: int, body_height: int) -> tuple[str, ...]:
    if body_height <= 0:
        return ()
    lines = text.split("\n")
    rows = []
    for i in range(body_height):
        line = lines[i] if i < len(lines) else ""
        rows.append(_clip(line, width))
    return tuple(rows)


def _cursor_position(
    observation: BufferObservation,
    width: int,
    body_height: int,
) -> tuple[int, int]:
    if body_height <= 0 or width == 0:
        return (0, 0)

    lines = observation.text.split("\n")
    remaining = observation.point
    for row, line in enumerate(lines):
        line_len = len(line)
        if remaining <= line_len:
            # Map the point through sanitization: control characters expand
            # to caret notation, so the cursor column is the *rendered*
            # column of the text before point.
            rendered_col = len(_sanitize(line[:remaining]))
            col = min(rendered_col, width - 1)
            return (min(row, body_height - 1), col)
        remaining -= line_len + 1

    # Unreachable: point <= len(text) always lands inside some line.
    return (body_height - 1, 0)  # pragma: no cover
