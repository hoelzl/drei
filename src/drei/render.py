from __future__ import annotations

from dataclasses import dataclass

from drei.commands import BufferObservation, SessionObservation


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
        modeline = _clip(_modeline(observation), width)
        return Frame(rows=(modeline,), cursor=(0, 0), width=width, height=height)

    body_rows = _render_body(observation.text, width, height - 2)
    modeline = _clip(_modeline(observation), width)

    if observation.minibuffer is not None:
        # The minibuffer occupies the echo row; the cursor sits at the end
        # of the prompt + input, and the body point is ignored.
        prompt = observation.minibuffer_prompt or ""
        echo_row = _clip(prompt + observation.minibuffer, width)
        cursor_row = len(body_rows) + 1  # echo row index
        cursor_col = min(len(_sanitize(prompt + observation.minibuffer)), width - 1)
        rows = body_rows + (modeline, echo_row)
        return Frame(
            rows=rows,
            cursor=(cursor_row, max(cursor_col, 0)),
            width=width,
            height=height,
        )

    echo_row = _clip(echo, width)
    cursor_row, cursor_col = _cursor_position(observation, width, height - 2)
    rows = body_rows + (modeline, echo_row)
    return Frame(rows=rows, cursor=(cursor_row, cursor_col), width=width, height=height)


def render_session(
    observation: SessionObservation,
    width: int,
    height: int,
    echo: str = "",
) -> Frame:
    """Draw one pane per window (design 0003 §A.2, plan 0012 D5).

    The frame is N stacked windows (each with its own modeline) plus one
    shared echo row. Window heights are distributed evenly (remainder to the
    bottom window, Emacs-style). The cursor lives in the focused window at
    its window-point; while the minibuffer is open it sits at the end of the
    prompt on the shared echo row. A single window renders byte-identically
    to :func:`render` of the focused window's buffer observation.
    """
    if height == 0:
        return Frame(rows=(), cursor=(0, 0), width=width, height=height)

    window_count = len(observation.windows)
    # A session always has ≥1 window; the fallback only guards a hand-built
    # observation, so exclude the whole branch from the coverage ratchet.
    if window_count == 0:  # pragma: no cover — defensive fallback
        return Frame(rows=(), cursor=(0, 0), width=width, height=height)

    body_height = height - 1  # shared echo row
    # Each window needs at least its modeline; body rows distribute evenly.
    heights = _window_heights(body_height, window_count)

    if observation.minibuffer is not None:
        prompt = observation.minibuffer_prompt or ""
        echo_row = _clip(prompt + observation.minibuffer, width)
        cursor_row, cursor_col = (
            height - 1,
            min(len(_sanitize(prompt + observation.minibuffer)), max(width - 1, 0)),
        )
    else:
        echo_row = _clip(echo, width)
        cursor_row, cursor_col = (0, 0)

    rows: list[str] = []
    row_offset = 0
    for index, (window, pane_height) in enumerate(
        zip(observation.windows, heights, strict=True)
    ):
        pane_body = pane_height - 1  # one modeline per window
        body_rows = _render_body(window.buffer.text, width, pane_body)
        modeline = _clip(_modeline(window.buffer), width)
        rows.extend(body_rows)
        rows.append(modeline)
        if index == observation.focused and observation.minibuffer is None:
            cursor_row, cursor_col = _cursor_position(
                window.buffer, width, pane_body, point=window.point
            )
            cursor_row += row_offset
        row_offset += pane_height

    rows.append(echo_row)
    return Frame(
        rows=tuple(rows), cursor=(cursor_row, cursor_col), width=width, height=height
    )


def _window_heights(body_height: int, window_count: int) -> tuple[int, ...]:
    """Stacked window heights (each ≥1: the modeline row), remainder to the
    bottom window. Never returns a zero-height pane."""
    if window_count <= 0:  # pragma: no cover — guarded by the caller
        return ()
    base = max(body_height // window_count, 1)
    heights = [base] * window_count
    # Give any leftover rows to the bottom window (Emacs rounds down to the
    # last window when the frame doesn't divide evenly).
    heights[-1] += body_height - base * window_count
    if heights[-1] < 1:  # pragma: no cover — base ≥ 1 keeps this unreachable
        heights[-1] = 1
    return tuple(heights)


def _modeline(observation: BufferObservation) -> str:
    indicator = "**" if observation.modified else "--"
    return f"Drei: {observation.buffer_id} {indicator}"


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
    point: int | None = None,
) -> tuple[int, int]:
    if body_height <= 0 or width == 0:
        return (0, 0)

    at = observation.point if point is None else point
    lines = observation.text.split("\n")
    remaining = at
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
