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

from drei.commands import BackwardChar, ForwardChar, InsertText
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession

EMACS_EVAL = (
    '(progn (insert "hello") (backward-char) (forward-char)'
    ' (message "POINT=%d TEXT=%s" (point) (buffer-string)))'
)

_OBSERVATION_RE = re.compile(r"POINT=(\d+) TEXT=(.*)")

PINNED_IMAGE = "ubuntu:24.04"
PINNED_VERSION_PREFIX = "GNU Emacs 29."


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    """Drei-normalized observation: zero-based point, raw buffer text."""

    text: str
    point: int


def _drei_observation() -> NormalizedObservation:
    session = EditorSession(Buffer(BufferId("scratch"), BufferValue("", 0)))
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


def _run_emacs_in_pinned_container() -> str:
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
            f"{setup} >/dev/null 2>&1 && {check} && "
            f"emacs -Q --batch --eval '{EMACS_EVAL}' 2>&1",
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
    # Select the observation line by content, not position: apt/dpkg notices
    # or shell warnings must never be parsed as the baseline.
    observation_lines = [line for line in lines[1:] if _OBSERVATION_RE.search(line)]
    assert len(observation_lines) == 1, (
        f"expected exactly one observation line, got {observation_lines!r} "
        f"in output {lines!r}"
    )
    return observation_lines[0]


def _run_emacs_on_host(emacs: str) -> str:
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
        [emacs, "-Q", "--batch", "--eval", EMACS_EVAL],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return result.stderr.strip() or result.stdout.strip()


@pytest.mark.integration
def test_emacs_differential_insert_and_horizontal_movement() -> None:
    # The pinned-container path installs emacs-nox on every run (~2 min).
    # Keep the default local suite fast: opt in via DREI_PARITY=1 (CI sets it
    # in the dedicated parity job).
    if os.environ.get("DREI_PARITY") != "1":
        pytest.skip("set DREI_PARITY=1 to run the pinned Emacs differential")

    host_emacs = shutil.which("emacs")
    if host_emacs is not None:
        try:
            output = _run_emacs_on_host(host_emacs)
        except AssertionError:
            if not _docker_available():
                pytest.skip("host Emacs is not pinned 29.x and Docker is unavailable")
            output = _run_emacs_in_pinned_container()
    elif _docker_available():
        output = _run_emacs_in_pinned_container()
    else:
        pytest.skip("no pinned GNU Emacs available (no host emacs, no Docker)")

    emacs_obs = _parse_emacs_output(output)
    drei_obs = _drei_observation()
    assert drei_obs == emacs_obs
