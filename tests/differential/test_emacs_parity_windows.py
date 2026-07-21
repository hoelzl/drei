"""Pinned GNU Emacs differential scenarios for A.2 (buffers and windows).

Runs the A.2 semantic scenarios — switch-to-buffer with an MRU default,
find-file create-or-select naming, split/other/collapse window focus with
per-window points — against GNU Emacs 29.3 and against Drei's production
session, then compares normalized observations.

Pinning strategy per docs/developer-guide/development.md and
test_emacs_parity.py: the pinned reference is GNU Emacs 29.3 from
`ubuntu:24.04` (`apt-get install emacs-nox`), locally via Docker or in CI
on the pinned runner. Opt in via DREI_PARITY=1; skips otherwise.

Verdicts are stated per scenario; each intentional deviation names its
pinning tests.
"""

from __future__ import annotations

import os
import re

import pytest

# -- Emacs evaluation forms -------------------------------------------------

EMACS_SWITCH_EVAL = (
    '(progn (switch-to-buffer (get-buffer-create "drei-sw-a")) (insert "AA")'
    ' (switch-to-buffer "drei-sw-b") (insert "BB")'
    " (switch-to-buffer nil)"
    ' (message "SWITCHED=%s TEXT=%S" (buffer-name) (buffer-string))'
    " (switch-to-buffer nil)"
    ' (message "BACK=%s TEXT=%S" (buffer-name) (buffer-string)))'
)

EMACS_FIND_FILE_EVAL = (
    '(progn (find-file "notes.txt") (message "FIRST=%s" (buffer-name))'
    ' (find-file "notes.txt") (message "SECOND=%s" (buffer-name)))'
)

EMACS_WINDOWS_EVAL = (
    '(progn (switch-to-buffer (get-buffer-create "drei-win")) (insert "ab")'
    " (split-window-below)"
    ' (message "AFTER-SPLIT COUNT=%d CURRENT-POINT=%d"'
    "  (count-windows) (window-point (selected-window)))"
    " (other-window 1)"
    ' (message "FOCUS2 POINT=%d SAME-BUFFER=%s" (window-point (selected-window))'
    '  (eq (window-buffer (selected-window)) (get-buffer "drei-win")))'
    ' (insert "!")'
    " (other-window 1)"
    ' (message "BACK-TO-TOP POINT=%d TEXT=%S"'
    "  (window-point (selected-window)) (buffer-string))"
    " (delete-other-windows)"
    ' (message "AFTER-COLLAPSE COUNT=%d" (count-windows)))'
)

_SWITCHED_RE = re.compile(r'SWITCHED=(\S+) TEXT="(.*?)"$', re.MULTILINE)
_BACK_RE = re.compile(r'BACK=(\S+) TEXT="(.*?)"$', re.MULTILINE)
_FIRST_RE = re.compile(r"FIRST=(\S+)$", re.MULTILINE)
_SECOND_RE = re.compile(r"SECOND=(\S+)$", re.MULTILINE)
_AFTER_SPLIT_RE = re.compile(
    r"AFTER-SPLIT COUNT=(\d+) CURRENT-POINT=(\d+)$", re.MULTILINE
)
_FOCUS2_RE = re.compile(r"FOCUS2 POINT=(\d+) SAME-BUFFER=(t|nil)$", re.MULTILINE)
_BACK_TO_TOP_RE = re.compile(r'BACK-TO-TOP POINT=(\d+) TEXT="(.*?)"$', re.MULTILINE)
_AFTER_COLLAPSE_RE = re.compile(r"AFTER-COLLAPSE COUNT=(\d+)$", re.MULTILINE)


def _select(blob: str, pattern: re.Pattern[str], label: str) -> re.Match[str]:
    match = pattern.search(blob)
    assert match is not None, f"{label}: no match for {pattern.pattern!r} in {blob!r}"
    return match


def _blob(lines: list[str]) -> str:
    return "\n".join(lines).replace("\r\n", "\n")


@pytest.mark.integration
def test_emacs_differential_switch_buffer_mru_default() -> None:
    """C-x b with empty input selects the MRU other buffer.

    Verdict: parity required — Emacs `(switch-to-buffer nil)` selects the
    most recently selected buffer that is not current; Drei's C-x b empty
    input takes ``_mru[1]`` (plan 0012 D7). Buffer content and per-buffer
    point survive each switch on both sides.
    """
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")

    from test_emacs_parity import _run_pinned_emacs

    lines = _run_pinned_emacs(EMACS_SWITCH_EVAL)
    blob = _blob(lines)
    switched = _select(blob, _SWITCHED_RE, "switched")
    back = _select(blob, _BACK_RE, "back")

    # Emacs: nil switch from "drei-sw-b" lands back on "drei-sw-a" (the
    # other MRU buffer), its edit intact; the next nil switch returns to b.
    assert switched.group(1) == "drei-sw-a"
    assert switched.group(2) == "AA"
    assert back.group(1) == "drei-sw-b"
    assert back.group(2) == "BB"

    # Drei drives the identical sequence through the production dispatch.
    from conftest import FakeFilePort

    from drei.commands import (
        InsertText,
        MinibufferAccept,
        MinibufferInput,
        SwitchBuffer,
    )
    from drei.model import Buffer, BufferId, BufferValue
    from drei.session import EditorSession

    def _switch(session: EditorSession, name: str) -> None:
        session.dispatch(SwitchBuffer())
        for char in name:
            session.dispatch(MinibufferInput(char))
        session.dispatch(MinibufferAccept())

    session = EditorSession(
        Buffer(BufferId("drei-sw-a"), BufferValue(text="", point=0)),
        file_port=FakeFilePort(),
    )
    session.dispatch(InsertText("AA"))
    _switch(session, "drei-sw-b")
    session.dispatch(InsertText("BB"))
    assert session.buffer.buffer_id.value == "drei-sw-b"
    assert session.buffer.current.text == "BB"

    # Empty input = MRU default (index 1): back to drei-sw-a.
    _switch(session, "")
    assert session.buffer.buffer_id.value == "drei-sw-a"
    assert session.buffer.current.text == "AA"

    # And once more: MRU is now drei-sw-b.
    _switch(session, "")
    assert session.buffer.buffer_id.value == "drei-sw-b"
    assert session.buffer.current.text == "BB"


@pytest.mark.integration
def test_emacs_differential_find_file_reuses_buffer_name() -> None:
    """find-file on an already-visited path reuses the existing buffer.

    Verdict: parity required on buffer-name reuse — Emacs visits the file
    in one buffer (no ``notes.txt<2>`` duplicate on the second find-file).
    Drei's create-or-select on file_path (plan 0012 D2) matches; the
    collision-suffix naming for two DIFFERENT files with the same basename
    is pinned by unit tests (batch Emacs behaviour for that case is an
    intentional deviation, registry row).
    """
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")

    from test_emacs_parity import _run_pinned_emacs

    lines = _run_pinned_emacs(EMACS_FIND_FILE_EVAL)
    blob = _blob(lines)
    first = _select(blob, _FIRST_RE, "first")
    second = _select(blob, _SECOND_RE, "second")

    assert first.group(1) == "notes.txt"
    assert second.group(1) == "notes.txt"  # reused, not notes.txt<2>

    from conftest import FakeFilePort

    from drei.commands import FindFile, MinibufferAccept, MinibufferInput
    from drei.model import Buffer, BufferId, BufferValue
    from drei.session import EditorSession

    def _find_file(session: EditorSession, file_path: str) -> None:
        session.dispatch(FindFile())
        for char in file_path:
            session.dispatch(MinibufferInput(char))
        session.dispatch(MinibufferAccept())

    port = FakeFilePort({"notes.txt": "content"})
    session = EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text="", point=0)),
        file_port=port,
    )
    _find_file(session, "notes.txt")
    first_id = session.buffer.buffer_id
    assert first_id.value == "notes.txt"

    # Second find-file of the SAME path selects the same buffer (no <2>).
    _find_file(session, "notes.txt")
    assert session.buffer.buffer_id == first_id


@pytest.mark.integration
def test_emacs_differential_window_focus_keeps_window_points() -> None:
    """Two windows over one buffer hold independent window points.

    Verdict: parity required on the observable invariants — split-window
    copies the point into both windows, other-window cycles focus, each
    window's point survives the round-trip, delete-other-windows collapses
    to one. Point numbering is normalized (Emacs 1-based → Drei 0-based).
    The split height gate (Emacs errors 'too small', Drei no-ops below
    MIN_WINDOW_ROWS when frame_size is known) is an intentional deviation
    pinned by unit tests.
    """
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")

    from test_emacs_parity import _run_pinned_emacs

    lines = _run_pinned_emacs(EMACS_WINDOWS_EVAL)
    blob = _blob(lines)
    after_split = _select(blob, _AFTER_SPLIT_RE, "after-split")
    focus2 = _select(blob, _FOCUS2_RE, "focus2")
    back_top = _select(blob, _BACK_TO_TOP_RE, "back-to-top")
    collapsed = _select(blob, _AFTER_COLLAPSE_RE, "collapsed")

    # Emacs: two windows after the split, both at the buffer point (3,
    # 1-based); the second window is over the same buffer; after editing in
    # window 2 and returning, window 1's point is UNCHANGED (3) while the
    # shared text is "ab!"; collapse leaves one window.
    assert after_split.group(1) == "2"
    assert after_split.group(2) == "3"
    assert focus2.group(1) == "3"
    assert focus2.group(2) == "t"
    assert back_top.group(1) == "3"
    assert back_top.group(2) == "ab!"
    assert collapsed.group(1) == "1"

    # Drei drives the same sequence; no frame_size (height gate inert).
    from conftest import FakeFilePort

    from drei.commands import (
        DeleteOtherWindows,
        InsertText,
        OtherWindow,
        SplitWindow,
    )
    from drei.model import Buffer, BufferId, BufferValue
    from drei.session import EditorSession

    session = EditorSession(
        Buffer(BufferId("drei-win"), BufferValue(text="", point=0)),
        file_port=FakeFilePort(),
    )
    session.dispatch(InsertText("ab"))  # point 2 (0-based) = Emacs 3
    outcome = session.dispatch(SplitWindow())
    observation = session.session_observation()
    assert len(observation.windows) == 2
    assert observation.windows[0].point == 2
    assert observation.windows[1].point == 2
    assert "WindowSplit" in {type(event).__name__ for event in outcome.events}

    session.dispatch(OtherWindow())
    observation = session.session_observation()
    assert observation.focused == 1
    assert observation.windows[1].buffer.buffer_id == "drei-win"

    # Edit in the second window; the shared buffer text grows.
    session.dispatch(InsertText("!"))
    assert session.buffer.current.text == "ab!"

    # Back to the first window: its stored point survived (2 = Emacs 3).
    session.dispatch(OtherWindow())
    observation = session.session_observation()
    assert observation.focused == 0
    assert observation.windows[0].point == 2
    assert session.buffer.current.text == "ab!"

    session.dispatch(DeleteOtherWindows())
    observation = session.session_observation()
    assert len(observation.windows) == 1
