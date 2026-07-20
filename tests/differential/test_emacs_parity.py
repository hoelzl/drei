"""Pinned GNU Emacs differential scenario for the first editor slice.

Runs one semantic scenario (startup in an empty scratch-like buffer, insert
text, move one character backward and forward) against GNU Emacs 29.3 and
against Drei's production session, then compares normalized observations.

Pinning strategy (per docs/developer-guide/development.md): the pinned
reference is GNU Emacs 29.3 from `ubuntu:24.04` (`apt-get install emacs-nox`).
Locally the scenario runs in that container via Docker; in CI a dedicated
parity job uses the pinned `ubuntu-24.04` runner with `emacs-nox` installed.
If neither Docker nor a host `emacs` binary is available, the scenario skips.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

import pytest

from drei.commands import BackwardChar, ForwardChar, InsertText, TextKilled
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession

EMACS_EVAL = (
    '(progn (insert "hello") (backward-char) (forward-char)'
    ' (message "POINT=%d TEXT=%s" (point) (buffer-string)))'
)

EMACS_SAVE_EVAL = (
    '(progn (find-file "drei-parity-save.txt") (insert "hi")'
    ' (message "POINT=%d MODIFIED=%s" (point) (buffer-modified-p))'
    " (save-buffer)"
    ' (message "AFTER MODIFIED=%s" (buffer-modified-p)))'
)

EMACS_KILL_EVAL = (
    '(progn (switch-to-buffer (get-buffer-create "drei-kill")) (insert "ab\\ncd")'
    " (goto-char (point-min))"
    " (kill-line)"
    ' (message "R1=%S TEXT=%S" (car kill-ring) (buffer-string))'
    " (kill-line)"
    ' (message "R2=%S TEXT=%S" (car kill-ring) (buffer-string))'
    " (yank)"
    ' (message "YANKED POINT=%d TEXT=%S" (point) (buffer-string)))'
)

_OBSERVATION_RE = re.compile(r"POINT=(\d+) TEXT=(.*)")
_SAVE_BEFORE_RE = re.compile(r"POINT=(\d+) MODIFIED=(t|nil)")
_SAVE_AFTER_RE = re.compile(r"AFTER MODIFIED=(t|nil)")
_KILL_R1_RE = re.compile(r'R1="(.+?)" TEXT="(.*?)"\r?\nR2=', re.DOTALL)
_KILL_R2_RE = re.compile(
    r'R2="(.*?)" TEXT="(.*?)"\r?\n(?:Mark set\r?\n)?YANKED', re.DOTALL
)
_KILL_YANK_RE = re.compile(r'YANKED POINT=(\d+) TEXT="(.*?)"\s*$', re.DOTALL)

EMACS_YANK_POP_EVAL = (
    '(progn (switch-to-buffer (get-buffer-create "drei-yp")) (insert "one\\nthree!")'
    " (goto-char (point-min))"
    " (kill-line)"  # "one" -> ring ("one")
    " (forward-line 1)"
    " (kill-line)"  # "three!" -> ring ("three!" "one")
    ' (message "RING=%S" kill-ring)'
    " (yank)"  # inserts "three!" at point
    " (setq last-command (quote yank))"  # batch cannot propagate it; force it
    " (yank-pop)"  # replaces with "one" (different length)
    ' (message "POP POINT=%d TEXT=%S" (point) (buffer-string)))'
)

_YP_POP_RE = re.compile(r'POP POINT=(\d+) TEXT="(.*?)"\s*$', re.DOTALL)

PINNED_IMAGE = "ubuntu:24.04"
PINNED_VERSION_PREFIX = "GNU Emacs 29."


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    """Drei-normalized observation: zero-based point, raw buffer text."""

    text: str
    point: int


def _drei_observation() -> NormalizedObservation:
    session = EditorSession(Buffer(BufferId("scratch"), BufferValue(text="", point=0)))
    session.dispatch(InsertText("hello"))
    session.dispatch(BackwardChar())
    outcome = session.dispatch(ForwardChar())
    return NormalizedObservation(
        text=outcome.observation.text,
        point=outcome.observation.point,
    )


def _parse_emacs_output(output: str) -> NormalizedObservation:
    match = _OBSERVATION_RE.fullmatch(output.strip())
    assert match is not None, f"unparseable Emacs output: {output!r}"
    # Emacs point is 1-based; normalize to Drei's 0-based point.
    return NormalizedObservation(text=match.group(2), point=int(match.group(1)) - 1)


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        timeout=30,
        check=False,
    )
    return result.returncode == 0


def _run_emacs_in_pinned_container(eval_form: str = EMACS_EVAL) -> list[str]:
    setup = "apt-get update -qq && apt-get install -y -qq emacs-nox"
    check = "emacs --version | head -1"
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            PINNED_IMAGE,
            "bash",
            "-c",
            f"{setup} >/dev/null 2>&1 && {check} && cd /tmp && "
            f"emacs -Q --batch --eval '{eval_form}' 2>&1",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=True,
    )
    lines = result.stdout.strip().splitlines()
    version_line = lines[0]
    assert version_line.startswith(PINNED_VERSION_PREFIX), (
        f"pinned Emacs version drifted: {version_line!r}"
    )
    return lines[1:]


def _run_emacs_on_host(emacs: str, eval_form: str = EMACS_EVAL) -> list[str]:
    version = subprocess.run(
        [emacs, "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.splitlines()[0]
    assert version.startswith(PINNED_VERSION_PREFIX), (
        f"host Emacs is not the pinned 29.x series: {version!r}; "
        "use the pinned container instead"
    )
    result = subprocess.run(
        [emacs, "-Q", "--batch", "--eval", eval_form],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        cwd=os.environ.get("TMPDIR", "/tmp")
        if os.name != "nt"
        else os.environ.get("TEMP"),
    )
    output = result.stderr.strip() or result.stdout.strip()
    return output.splitlines()


def _select_line(lines: list[str], pattern: re.Pattern[str], eval_form: str) -> str:
    """Select an observation line by content, not position: apt/dpkg notices
    or shell warnings must never be parsed as the baseline."""
    matches = [line for line in lines if pattern.search(line)]
    assert len(matches) == 1, (
        f"expected exactly one line matching {pattern.pattern!r}, "
        f"got {matches!r} in output {lines!r}"
    )
    return matches[0]


def _run_pinned_emacs(eval_form: str = EMACS_EVAL) -> list[str]:
    """Run the pinned GNU Emacs (host 29.x if available, else container)."""
    host_emacs = shutil.which("emacs")
    if host_emacs is not None:
        try:
            return _run_emacs_on_host(host_emacs, eval_form)
        except AssertionError:
            if not _docker_available():
                pytest.skip("host Emacs is not pinned 29.x and Docker is unavailable")
            return _run_emacs_in_pinned_container(eval_form)
    if _docker_available():
        return _run_emacs_in_pinned_container(eval_form)
    pytest.skip("no pinned GNU Emacs available (no host emacs, no Docker)")
    raise AssertionError("unreachable")


@pytest.mark.integration
def test_emacs_differential_insert_and_horizontal_movement() -> None:
    # The pinned-container path installs emacs-nox on every run (~2 min).
    # Keep the default local suite fast: opt in via DREI_PARITY=1 (CI sets it
    # in the dedicated parity job).
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")

    lines = _run_pinned_emacs(EMACS_EVAL)
    emacs_obs = _parse_emacs_output(_select_line(lines, _OBSERVATION_RE, EMACS_EVAL))
    drei_obs = _drei_observation()
    assert drei_obs == emacs_obs


@pytest.mark.integration
def test_emacs_differential_save_clears_modified() -> None:
    """Save semantics parity: insert sets modified, save clears it.

    Verdict: parity required on insert-sets-modified, save-clears-modified.
    The Emacs side compares observable `buffer-modified-p` semantics; file
    content is asserted Drei-side in the fake port. Point after inserting
    "hi" is 2 (Drei, 0-based) / 3 (Emacs, 1-based) — same normalization as
    slice 1.
    """
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")

    lines = _run_pinned_emacs(EMACS_SAVE_EVAL)
    before = _select_line(lines, _SAVE_BEFORE_RE, EMACS_SAVE_EVAL)
    after = _select_line(lines, _SAVE_AFTER_RE, EMACS_SAVE_EVAL)
    before_match = _SAVE_BEFORE_RE.fullmatch(before.strip())
    after_match = _SAVE_AFTER_RE.fullmatch(after.strip())
    assert before_match is not None and after_match is not None

    # Emacs: modified after insert, unmodified after save.
    assert before_match.group(2) == "t"
    assert after_match.group(1) == "nil"

    # Drei drives the identical semantic sequence through the production
    # dispatch path with a fake port.
    from conftest import FakeFilePort

    from drei.commands import SaveBuffer

    port = FakeFilePort()
    session = EditorSession(
        Buffer(
            BufferId("drei-parity-save.txt"),
            BufferValue(text="", point=0, file_path="drei-parity-save.txt"),
        ),
        file_port=port,
    )
    session.dispatch(InsertText("hi"))
    mid = session.buffer.current
    assert mid.modified is True
    assert mid.point == 2  # Emacs point 3, 1-based → 2, matching before_match.group(1)
    assert int(before_match.group(1)) - 1 == mid.point

    session.dispatch(SaveBuffer())
    end = session.buffer.current
    assert end.modified is False
    assert port.files["drei-parity-save.txt"] == "hi"


@pytest.mark.integration
def test_emacs_differential_kill_line_and_yank() -> None:
    """Kill-line/yank parity: EOL text, newline kill, yank text and point.

    Verdict: parity required on the non-append pieces — kill-to-EOL text,
    kill-at-EOL kills the newline, yank inserts the newest entry leaving
    point after it. The append chain is an intentional deviation: batch
    Emacs does not kill-append consecutive kill-line calls (RING=("\\n" "ab")
    after two kills), while Drei specifies append-on-consecutive-kill
    (motivated by interactive Emacs, batch-unverifiable); the chain is
    pinned by unit/property tests instead.
    """
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")

    lines = _run_pinned_emacs(EMACS_KILL_EVAL)
    # Kill/yank output contains literal newlines inside quoted strings, so
    # match against the joined blob instead of per-line. Normalize CRLF.
    blob = "\n".join(lines).replace("\r\n", "\n")
    r1 = _KILL_R1_RE.search(blob)
    r2 = _KILL_R2_RE.search(blob)
    yank = _KILL_YANK_RE.search(blob)
    assert r1 is not None and r2 is not None and yank is not None, blob

    # Emacs evidence: first kill takes "ab" leaving "\ncd"; second takes the
    # newline (batch Emacs pushes it as a SEPARATE entry) leaving "cd"; yank
    # inserts the newest entry ("\n") leaving point after it.
    assert r1.group(1) == "ab" and r1.group(2) == "\ncd"
    assert r2.group(1) == "\n" and r2.group(2) == "cd"
    assert yank.group(2) == "\ncd"
    emacs_point_after_yank = int(yank.group(1)) - 1  # 1-based → 0-based

    # Drei drives the same sequence; the append chain means the ring head is
    # "ab\n" (deviation), so yank restores the full original text.
    from drei.commands import KillLine, Yank

    session = EditorSession(
        Buffer(BufferId("drei-kill"), BufferValue(text="ab\ncd", point=0))
    )
    o1 = session.dispatch(KillLine())
    assert TextKilled("ab", 0, 2, "forward") in o1.events
    assert o1.observation.text == "\ncd"

    o2 = session.dispatch(KillLine())
    assert TextKilled("\n", 0, 1, "forward") in o2.events
    assert o2.observation.text == "cd"

    o3 = session.dispatch(Yank())
    # Parity on yank semantics: newest entry inserted at point, point after.
    assert o3.observation.text == "ab\ncd"  # Drei yanks the appended "ab\n"
    assert o3.observation.point == 3
    # Emacs yanked the 1-char "\n" at point 0 → point 1; same rule, different
    # ring content (deviation). Verify the rule shape matches.
    assert emacs_point_after_yank == 1


def test_yank_pop_first_pop_parity() -> None:
    """First yank-pop replaces the yank with the next-older ring entry.

    Verdict: parity required on the first pop — older entry replaces the
    yanked span in place, point = start + len(new) (length-changing entries
    pin the placement rule). The pop CYCLE (second pop wrapping) is an
    intentional batch-unverifiable deviation: batch Emacs falls back to
    read-from-kill-ring (stdin) once last-command propagation ends, so the
    cycle is pinned by unit/property tests instead. Pops on empty/1-entry
    rings and pop-without-active-yank are Drei silent no-ops vs Emacs error
    signals — intentional deviations recorded in the registry.
    """
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")

    lines = _run_pinned_emacs(EMACS_YANK_POP_EVAL)
    blob = "\n".join(lines).replace("\r\n", "\n")
    pop = _YP_POP_RE.search(blob)
    assert pop is not None, blob

    # Emacs evidence: yank "three!" at point 1 (after the leftover "\n" —
    # batch kill-line leaves point after it), pop replaces with "one" →
    # text "\none", point after the inserted "one" (0-based 4).
    assert pop.group(2) == "\none"
    emacs_point_after_pop = int(pop.group(1)) - 1  # 1-based → 0-based
    assert emacs_point_after_pop == 1 + len("one")

    # Drei drives the same sequence.
    from drei.commands import (
        ForwardChar,
        KillLine,
        TextYanked,
        TextYankPopped,
        Yank,
        YankPop,
    )

    session = EditorSession(
        Buffer(BufferId("drei-yp"), BufferValue(text="one\nthree!", point=0))
    )
    session.dispatch(KillLine())  # "one"; text "\nthree!", point 0
    session.dispatch(ForwardChar())  # point 1 = start of "three!"; breaks chain
    session.dispatch(KillLine())  # "three!"; text "\n", point 1
    yanked = session.dispatch(Yank())
    assert TextYanked("three!", 1, 7) in yanked.events

    popped = session.dispatch(YankPop())
    assert TextYankPopped("three!", "one", 1, 4) in popped.events
    assert popped.observation.text == "\none"
    assert popped.observation.point == emacs_point_after_pop == 4
