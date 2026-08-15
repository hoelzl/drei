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
    note: str = "",
) -> Frame:
    if height == 0:
        return Frame(rows=(), cursor=(0, 0), width=width, height=height)

    if height == 1:
        return _render_single_row(
            observation,
            width,
            echo=echo,
            note=note,
            minibuffer=observation.minibuffer,
            minibuffer_prompt=observation.minibuffer_prompt,
        )

    body_rows = _render_body(observation.text, width, height - 2)
    modeline = _clip(_modeline(observation), width)

    if observation.minibuffer is not None:
        # The minibuffer occupies the echo row; the cursor sits at the end
        # of the prompt + input, and the body point is ignored.
        prompt = _noted_prompt(observation.minibuffer_prompt or "", note)
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
    note: str = "",
) -> Frame:
    """Draw one pane per window (design 0003 §A.2, plan 0012 D5).

    The frame is a focus-centered contiguous projection of complete stacked
    windows plus one shared echo row. The focused pane is admitted first;
    non-focused panes appear only when body and modeline both fit. Window
    heights are distributed evenly (remainder to the bottom visible window,
    Emacs-style). The cursor lives in the focused window at its window-point;
    while the minibuffer is open it sits at the end of the prompt on the
    shared echo row. A single window renders byte-identically to :func:`render`
    of the focused window's buffer observation.
    """
    if height == 0:
        return Frame(rows=(), cursor=(0, 0), width=width, height=height)

    window_count = len(observation.windows)
    # A session always has ≥1 window; the fallback only guards a hand-built
    # observation, so exclude the whole branch from the coverage ratchet.
    if window_count == 0:  # pragma: no cover — defensive fallback
        return Frame(rows=(), cursor=(0, 0), width=width, height=height)

    if height == 1:
        focused_buffer = observation.windows[observation.focused].buffer
        return _render_single_row(
            focused_buffer,
            width,
            echo=echo,
            note=note,
            minibuffer=observation.minibuffer,
            minibuffer_prompt=observation.minibuffer_prompt,
        )
    pane_budget = height - 1  # reserve the shared echo row
    visible_count = min(window_count, max(pane_budget // 2, 1))
    visible_indices = _visible_window_indices(
        window_count, observation.focused, visible_count
    )
    heights = _window_heights(pane_budget, visible_count)

    if observation.minibuffer is not None:
        prompt = _noted_prompt(observation.minibuffer_prompt or "", note)
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
    for semantic_index, pane_height in zip(visible_indices, heights, strict=True):
        window = observation.windows[semantic_index]
        pane_body = pane_height - 1  # one modeline per window
        body_rows = _render_body(window.buffer.text, width, pane_body)
        modeline = _clip(_modeline(window.buffer), width)
        rows.extend(body_rows)
        rows.append(modeline)
        if semantic_index == observation.focused and observation.minibuffer is None:
            cursor_row, cursor_col = _cursor_position(
                window.buffer, width, pane_body, point=window.point
            )
            cursor_row += row_offset
        row_offset += pane_height

    rows.append(echo_row)
    return Frame(
        rows=tuple(rows), cursor=(cursor_row, cursor_col), width=width, height=height
    )


def _noted_prompt(prompt: str, note: str) -> str:
    """A message raised while a prompt is open rides it as a SUFFIX (plan
    0019 D3), and that ordering is the decision. The echo row is
    hard-clipped — `_clip` does not wrap or scroll, and the shipped ConPTY
    scenarios run at 40 columns — so one half of the row is going to be
    sacrificed. Prefixing sacrificed the question: at 40 the exit gate read
    `<path>: permission-denied. Modif`, a truncated error with no visible
    question, on the row where `y` discards the buffer. A suffix sacrifices
    the annotation instead, which is the right way round: the question and
    its answer set must be readable at every width, and a cut-off reason is
    still a visible sign that something went wrong.
    """
    return f"{prompt}[{note}]" if note else prompt


def _render_single_row(
    observation: BufferObservation,
    width: int,
    *,
    echo: str,
    note: str,
    minibuffer: str | None,
    minibuffer_prompt: str | None,
) -> Frame:
    if minibuffer is not None:
        prompt = _noted_prompt(minibuffer_prompt or "", note)
        content = prompt + minibuffer
        cursor_col = min(len(_sanitize(content)), max(width - 1, 0))
        return Frame(
            rows=(_clip(content, width),),
            cursor=(0, cursor_col),
            width=width,
            height=1,
        )
    content = echo or _modeline(observation)
    return Frame(rows=(_clip(content, width),), cursor=(0, 0), width=width, height=1)


def _window_heights(body_height: int, window_count: int) -> tuple[int, ...]:
    """Heights for already-admitted panes, remainder to the bottom pane."""
    if window_count <= 0:  # pragma: no cover — guarded by the caller
        return ()
    base = body_height // window_count
    heights = [base] * window_count
    heights[-1] += body_height - base * window_count
    return tuple(heights)


def _visible_window_indices(
    window_count: int, focused: int, visible_count: int
) -> tuple[int, ...]:
    """Choose a contiguous, focus-centered stack slice.

    For an even visible count, focus occupies the upper-middle slot when
    boundaries permit, favoring the following semantic window.
    """
    focus_slot = (visible_count - 1) // 2
    start = min(max(focused - focus_slot, 0), window_count - visible_count)
    return tuple(range(start, start + visible_count))


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
