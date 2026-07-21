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
from pathlib import Path

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

EMACS_REGION_EVAL = (
    '(progn (switch-to-buffer (get-buffer-create "drei-rg")) (insert "hello world")'
    " (goto-char (point-min))"
    " (set-mark (point))"
    " (forward-char 5)"
    " (kill-region (mark) (point))"  # forward kill "hello"
    ' (message "FWD POINT=%d TEXT=%S RING=%S" (point) (buffer-string) (car kill-ring))'
    " (yank)"  # restore
    " (goto-char 7)"  # 1-based: before 'w'
    " (set-mark (point))"
    " (backward-char 5)"
    " (kill-region (point) (mark))"  # backward kill of "lo wo" (0-based 1..6)
    ' (message "BWD POINT=%d TEXT=%S RING=%S" (point) (buffer-string) (car kill-ring))'
    " (yank)"
    " (goto-char 7)"
    " (set-mark (point))"
    " (backward-char 5)"
    " (copy-region-as-kill (point) (mark))"  # copy, text unchanged
    ' (message "CPY POINT=%d MARK=%d TEXT=%S RING=%S MOD=%S"'
    " (point) (mark) (buffer-string) (car kill-ring) (buffer-modified-p))"
    # clean copy: fresh unmodified buffer — does copy alone set the flag?
    ' (switch-to-buffer (get-buffer-create "drei-rg2")) (insert "clean")'
    " (set-buffer-modified-p nil)"
    " (goto-char 1) (set-mark (point)) (forward-char 3)"
    " (copy-region-as-kill (mark) (point))"
    ' (message "CLEAN MOD=%S" (buffer-modified-p))))'
)

_REGION_RE = re.compile(
    r'FWD POINT=(\d+) TEXT="(.*?)" RING="(.*?)"\r?\n.*?'
    r'BWD POINT=(\d+) TEXT="(.*?)" RING="(.*?)"\r?\n.*?'
    r'CPY POINT=(\d+) MARK=(\d+) TEXT="(.*?)" RING="(.*?)" MOD=(\S+)\r?\n.*?'
    r"CLEAN MOD=(\S+)\s*$",
    re.DOTALL,
)

EMACS_MARKER_EVAL = (
    '(progn (switch-to-buffer (get-buffer-create "drei-mk"))'
    ' (insert "abc\\ndef")'
    " (goto-char 6) (set-mark (point))"  # mark before 'e' (1-based 6)
    " (goto-char (point-min))"
    " (kill-line)"  # kill "abc" [1,4); mark 6 survives + shifts to 3
    ' (message "M1 MARK=%d TEXT=%S" (mark) (buffer-string))'
    ' (goto-char (point-min)) (insert "XY")'  # insert before mark
    ' (message "M2 MARK=%d TEXT=%S" (mark) (buffer-string))'
    ' (goto-char 3) (set-mark (point)) (insert "Z")'  # insert AT mark 3
    ' (message "M3 MARK=%d TEXT=%S" (mark) (buffer-string)))'
)

_MARKER_RE = re.compile(
    r'M1 MARK=(\d+) TEXT="(.*?)"\r?\n.*?'
    r'M2 MARK=(\d+) TEXT="(.*?)"\r?\n.*?'
    r'M3 MARK=(\d+) TEXT="(.*?)"\s*$',
    re.DOTALL,
)

_UNDO_MSG = (
    ' (message "{tag} TEXT=%S POINT=%d MOD=%S"'
    " (buffer-string) (point) (buffer-modified-p))"
)

EMACS_UNDO_EVAL = (
    '(progn (switch-to-buffer (get-buffer-create "drei-un"))'
    ' (insert "hello") (undo-boundary)'  # close the group (batch has no loop)
    + _UNDO_MSG.replace("{tag}", "INS")
    + " (undo)"
    + _UNDO_MSG.replace("{tag}", "U1")
    + ' (insert "X") (undo-boundary)'
    " (undo)"  # undo the fresh insert — previously undone groups NOT resurrected
     + _UNDO_MSG.replace("{tag}", "U2") + ")"
)
_UNDO_RE = re.compile(
    r'INS TEXT="(.*?)" POINT=(\d+) MOD=(\S+)\r?\n.*?'
    r'U1 TEXT="(.*?)" POINT=(\d+) MOD=(\S+)\r?\n.*?'
    r'U2 TEXT="(.*?)" POINT=(\d+) MOD=(\S+)\s*$',
    re.DOTALL,
)

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


def _docker_mount_spec(cwd: Path) -> str:
    """Docker CLI mount spec for a host dir: Docker Desktop requires forward
    slashes in the host path (`C:/Users/...`), backslashes break the mount."""
    return f"{cwd.as_posix()}:/work"


def _run_emacs_in_dir(eval_form: str, cwd: Path) -> list[str]:
    """Pinned Emacs with a controlled working directory (find-file probes
    need a fixture on disk and relative paths)."""
    host_emacs = shutil.which("emacs")
    if host_emacs is not None:
        version = subprocess.run(
            [host_emacs, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.splitlines()[0]
        if version.startswith(PINNED_VERSION_PREFIX):
            result = subprocess.run(
                [host_emacs, "-Q", "--batch", "--eval", eval_form],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
                cwd=cwd,
            )
            output = result.stderr.strip() or result.stdout.strip()
            return output.splitlines()
        # Same policy as _run_pinned_emacs: an unpinned host Emacs is not a
        # baseline — fall through to the pinned container without asserting.
    if not _docker_available():
        pytest.skip("no pinned GNU Emacs available (no host emacs, no Docker)")
    setup = "apt-get update -qq && apt-get install -y -qq emacs-nox"
    check = "emacs --version | head -1"
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            _docker_mount_spec(cwd),
            "-w",
            "/work",
            PINNED_IMAGE,
            "bash",
            "-c",
            f"{setup} >/dev/null 2>&1 && {check} && "
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


def test_region_kill_copy_parity() -> None:
    """Forward kill, backward kill, copy — parity on text/point/ring/mark.

    Parity required on the region semantics; deactivation-on-edit is
    batch-unverifiable (deviation, registry). Emacs point/mark are
    1-based; Drei 0-based.
    """
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")

    lines = _run_pinned_emacs(EMACS_REGION_EVAL)
    blob = "\n".join(lines).replace("\r\n", "\n")
    region = _REGION_RE.search(blob)
    assert region is not None, blob

    # Emacs evidence — forward kill of "hello": text " world", point 0
    # (0-based), ring head "hello".
    assert region.group(2) == " world"
    assert int(region.group(1)) - 1 == 0
    assert region.group(3) == "hello"
    # Backward kill of "ello " (0-based [1,6)): text "hworld", point 1.
    assert region.group(5) == "hworld"
    assert int(region.group(4)) - 1 == 1
    assert region.group(6) == "ello "
    # Copy: text unchanged, point unchanged; the flag is sticky (earlier
    # kills), but the clean fresh-buffer arm proves copy alone sets nothing.
    assert region.group(9) == "hello world"
    assert region.group(10) == "ello "
    assert region.group(12) == "nil"  # CLEAN: copy leaves modified nil

    # Drei drives the same sequence.
    from drei.commands import (
        BackwardChar,
        CopyRegionAsKill,
        ForwardChar,
        KillRegion,
        RegionCopied,
        RegionKilled,
        SetMark,
        Yank,
    )

    session = EditorSession(
        Buffer(BufferId("drei-rg"), BufferValue(text="hello world", point=0))
    )
    session.dispatch(SetMark())
    for _ in range(5):
        session.dispatch(ForwardChar())
    killed = session.dispatch(KillRegion())
    assert RegionKilled("hello", 0, 5, "forward") in killed.events
    assert killed.observation.text == region.group(2) == " world"
    assert killed.observation.point == 0
    assert session.kill_ring[0] == region.group(3) == "hello"
    assert killed.observation.mark is None

    session.dispatch(Yank())  # restore "hello world", point 5
    session.dispatch(ForwardChar())  # point 6 (0-based) = before 'w'
    session.dispatch(SetMark())
    for _ in range(5):
        session.dispatch(BackwardChar())
    killed_bwd = session.dispatch(KillRegion())
    assert RegionKilled("ello ", 1, 6, "backward") in killed_bwd.events
    assert killed_bwd.observation.text == region.group(5) == "hworld"
    assert killed_bwd.observation.point == 1
    assert session.kill_ring[0] == region.group(6) == "ello "

    session.dispatch(Yank())  # restore, point 6
    session.dispatch(SetMark())
    for _ in range(5):
        session.dispatch(BackwardChar())
    copied = session.dispatch(CopyRegionAsKill())
    assert RegionCopied("ello ") in copied.events
    assert copied.observation.text == region.group(9) == "hello world"
    assert copied.observation.point == 1
    # Copy does not SET the flag: the buffer was already modified by the
    # kills above; a clean-session copy keeps it clear (CLEAN arm, and
    # tests/test_mark_region.py::test_copy_region_pushes_ring...).
    assert copied.observation.modified  # sticky from the kills, not the copy
    assert session.kill_ring[0] == region.group(10) == "ello "


def test_marker_adjustment_parity() -> None:
    """Markers shift on edits (probed rule): delete before, insert before,
    insert AT. Parity required on the resulting mark position.
    """
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")

    lines = _run_pinned_emacs(EMACS_MARKER_EVAL)
    blob = "\n".join(lines).replace("\r\n", "\n")
    marker = _MARKER_RE.search(blob)
    assert marker is not None, blob

    # Emacs evidence (1-based marks): kill-line [1,4) with mark 6 → 3;
    # insert "XY" before → 5; insert "Z" AT mark 3 → stays 3.
    assert (int(marker.group(1)), int(marker.group(3)), int(marker.group(5))) == (
        3,
        5,
        3,
    )

    # Drei drives the identical command sequence (0-based marks).
    from drei.commands import (
        BackwardChar,
        InsertText,
        KillLine,
        SetMark,
    )

    session = EditorSession(
        Buffer(BufferId("drei-mk"), BufferValue(text="abc\ndef", point=5))
    )
    session.dispatch(SetMark())  # mark 5 (before 'e')
    for _ in range(5):
        session.dispatch(BackwardChar())  # point 0
    session.dispatch(KillLine())  # kills "abc" [0,3); mark survives
    assert session.buffer.current.mark == int(marker.group(1)) - 1 == 2
    assert session.buffer.current.text == "\ndef"
    session.dispatch(InsertText("XY"))  # insert before the mark
    assert session.buffer.current.mark == int(marker.group(3)) - 1 == 4
    assert session.buffer.current.text == "XY\ndef"
    session.dispatch(SetMark())  # mark 2 (point is 2 after the insert)
    session.dispatch(InsertText("Z"))  # insert AT the mark: stays before
    assert session.buffer.current.mark == int(marker.group(5)) - 1 == 2
    assert session.buffer.current.text == "XYZ\ndef"


def test_undo_parity() -> None:
    """Single undo restores text/point/modified; fresh insert after undo
    truncates (no resurrection). Batch grouping amalgamates (deviation —
    one group per command is Drei's interactive-equivalent rule), but a
    single insert + undo is exact on both sides."""
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")
    lines = _run_pinned_emacs(EMACS_UNDO_EVAL)
    blob = "\n".join(lines)
    undo = _UNDO_RE.search(blob)
    assert undo is not None, blob

    # Emacs evidence (1-based point): insert "hello" → point 6, MOD t;
    # undo → "" point 1 MOD nil. Fresh "X" + undo → "hello" MOD t: stock
    # Emacs does NOT truncate — the fresh edit flips direction and the
    # next undo REDOES the buried undo (the review's B2, made visible).
    assert undo.group(1) == "hello"
    assert (int(undo.group(2)), undo.group(3)) == (6, "t")
    assert (undo.group(4), int(undo.group(5)), undo.group(6)) == ("", 1, "nil")
    assert (undo.group(7), int(undo.group(8)), undo.group(9)) == ("hello", 1, "t")

    from drei.commands import InsertText, TextUndone, Undo

    session = EditorSession(Buffer(BufferId("drei-un"), BufferValue(text="", point=0)))
    inserted = session.dispatch(InsertText("hello"))
    assert inserted.observation.text == undo.group(1)
    assert inserted.observation.point == int(undo.group(2)) - 1
    assert inserted.observation.modified

    undone = session.dispatch(Undo())
    assert TextUndone(0, "hello", "", 5, 0, None, None) in undone.events
    assert undone.observation.text == undo.group(4) == ""
    assert undone.observation.point == int(undo.group(5)) - 1 == 0
    assert not undone.observation.modified  # MOD nil — restored from the group

    # DEVIATION (registry): Drei truncates the redo tail on a fresh edit —
    # undo of the fresh insert returns to "" (Emacs redoes to "hello").
    session.dispatch(InsertText("X"))
    undone2 = session.dispatch(Undo())
    assert undone2.observation.text == "" != undo.group(7)
    assert undone2.observation.point == 0
    assert not undone2.observation.modified
    # The truncated redo tail: undo now has nothing left.
    assert session.dispatch(Undo()).events == ()


EMACS_FIND_FILE_EVAL = (
    '(progn (find-file "drei-parity-find.txt")'
    ' (message "EXIST TEXT=%S POINT=%d MOD=%S"'
    " (buffer-string) (point) (buffer-modified-p))"
    ' (find-file "drei-no-such-file.txt")'
    ' (message "MISSING TEXT=%S POINT=%d MOD=%S"'
    " (buffer-string) (point) (buffer-modified-p))"
    ' (find-file "drei-no-such-dir/x.txt")'
    ' (message "MISSINGDIR TEXT=%S POINT=%d MOD=%S"'
    " (buffer-string) (point) (buffer-modified-p)))"
)

_FIND_FILE_RE = re.compile(
    r'EXIST TEXT="(.*?)" POINT=(\d+) MOD=(\S+)\r?\n.*?'
    r'MISSING TEXT="(.*?)" POINT=(\d+) MOD=(\S+)\r?\n.*?'
    r'MISSINGDIR TEXT="(.*?)" POINT=(\d+) MOD=(\S+)\s*$',
    re.DOTALL,
)


@pytest.mark.integration
def test_find_file_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """find-file semantics parity: existing file loads with point at start
    and MOD nil; a missing file (or missing directory) yields an empty
    unmodified buffer with no error.

    Verdict: parity required on all three arms. Directory paths themselves
    (dired) are an intentional Drei deviation (OpenFailed) and are NOT
    probed here; batch minibuffer reads stdin, so the prompt interaction
    itself is TermVerify's job, not this scenario's.
    """
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")

    existing = tmp_path / "drei-parity-find.txt"
    existing.write_text("hi there", encoding="utf-8")
    lines = _run_emacs_in_dir(EMACS_FIND_FILE_EVAL, tmp_path)
    blob = "\n".join(lines)
    match = _FIND_FILE_RE.search(blob)
    assert match is not None, blob

    # Emacs evidence: existing file → contents, point 1, MOD nil.
    assert match.group(1) == "hi there"
    assert (int(match.group(2)), match.group(3)) == (1, "nil")
    # Missing file → empty buffer, point 1, MOD nil, no error.
    assert (match.group(4), int(match.group(5)), match.group(6)) == ("", 1, "nil")
    # Missing DIRECTORY → also an empty buffer, no error (probed twice).
    assert (match.group(7), int(match.group(8)), match.group(9)) == ("", 1, "nil")

    # Drei drives the identical semantic sequence through the production
    # dispatch path with the real file port rooted at tmp_path.
    from drei.commands import (
        BufferOpened,
        CommandOutcome,
        FindFile,
        MinibufferAccept,
        MinibufferInput,
    )
    from drei.files import SystemFilePort

    def drei_open(session: EditorSession, path: str) -> CommandOutcome:
        session.dispatch(FindFile())
        for char in path:
            session.dispatch(MinibufferInput(char))
        return session.dispatch(MinibufferAccept())

    session = EditorSession(
        Buffer(BufferId("scratch"), BufferValue(text="", point=0)),
        file_port=SystemFilePort(),
    )
    # SystemFilePort resolves relative paths against the process cwd, so
    # the Drei side runs from the fixture dir (monkeypatch restores cwd
    # even on failure).
    monkeypatch.chdir(tmp_path)
    opened = drei_open(session, "drei-parity-find.txt")
    assert BufferOpened("drei-parity-find.txt", 8) in opened.events
    assert opened.observation.text == match.group(1)
    assert opened.observation.point == int(match.group(2)) - 1 == 0
    assert not opened.observation.modified

    missing = drei_open(session, "drei-no-such-file.txt")
    assert BufferOpened("drei-no-such-file.txt", 0) in missing.events
    assert missing.observation.text == match.group(4) == ""
    assert missing.observation.point == int(match.group(5)) - 1 == 0
    assert not missing.observation.modified

    missing_dir = drei_open(session, "drei-no-such-dir/x.txt")
    assert BufferOpened("drei-no-such-dir/x.txt", 0) in missing_dir.events
    assert missing_dir.observation.text == match.group(7) == ""
    assert not missing_dir.observation.modified
