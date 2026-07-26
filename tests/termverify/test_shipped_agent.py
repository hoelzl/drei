"""The ACP pump through the shipped `drei` executable (design 0005 §C.2).

Design 0005's verification layer 3: a fake ACP agent speaking the pinned 0.9.0
wire, spawned by the *real* `drei` process over a real ConPTY. Nothing is
stubbed — the streaming port, the reader threads, the decoder, the machine, the
pump, the session and the renderer all run in the child, and the only thing
this file supplies is the peer.

**The one place Drei's evidence is weaker than everywhere else.** An agent
delivery is a redraw the verifier did not dispatch, so it carries no readiness
marker and there is no epoch to complete on it. This scenario therefore waits
on *frame content with an explicit deadline* rather than on quiescence. Design
0005 records the gap and the obligation: reduce it to a concrete test — this
one — and take a second marker kind for non-input-driven quiescence to
TermVerify as its own issue. Drei must not invent a private marker, because a
marker the verifier's epoch counter does not know about would corrupt every
epoch after it.

The waiting is done by dispatching *no-op* inputs. Each one is an ordinary
input epoch that completes on its own marker, so the loop is still driven by
the cooperation protocol; what the deadline bounds is only how many turns of
the crank the agent's answer needs.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from termverify import (
    ClockConfiguration,
    EpochCompleted,
    ExitStatus,
    FilesystemConfiguration,
    KeyInput,
    ManualTime,
    NetworkConfiguration,
    Observation,
    RunConfiguration,
    RunFinished,
    Started,
    TerminalConfiguration,
    TerminalResult,
    TextInput,
)
from termverify.conpty import ConptyAdapter, ConptyBinding
from termverify.cooperation import CooperationConstraintPorts

pytestmark = [
    pytest.mark.termverify,
    pytest.mark.skipif(sys.platform != "win32", reason="ConPTY is Windows-only"),
]

_COLUMNS = 60
_ROWS = 12
# How long the agent's answer is allowed to take. Generous, and it bounds a
# failure rather than measuring a latency: most of it is a Python interpreter
# starting, which is not what this scenario is about.
_SETTLE_SECONDS = 60.0

_FAKE_AGENT = Path(__file__).resolve().parent.parent / "fake_acp_agent.py"


def _configuration() -> RunConfiguration:
    return RunConfiguration(
        seed=42,
        clock=ClockConfiguration(initial_ms=0),
        locale="en-US",
        timezone="UTC",
        terminal=TerminalConfiguration(columns=_COLUMNS, rows=_ROWS, capabilities=()),
        filesystem=FilesystemConfiguration(root_id="drei-root"),
        network=NetworkConfiguration.deny(),
    )


@contextmanager
def _reaped(adapter: ConptyAdapter) -> Iterator[ConptyAdapter]:
    """Never leak a child past a failure (cleanup, not evidence)."""
    try:
        yield adapter
    finally:
        child = adapter._child  # noqa: SLF001 - cleanup-only access
        if child is not None:
            child.close(force=True)


def _frame_lines(observation: Observation) -> tuple[str, ...]:
    assert observation.frame is not None, observation
    return tuple(observation.frame.lines)


def _adapter(tmp_path: Path) -> ConptyAdapter:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(exist_ok=True)
    return ConptyAdapter(
        [
            sys.executable,
            "-c",
            "from drei.cli import main; main()",
            # One occurrence per argument: the interpreter path routinely
            # contains a space on Windows.
            "--agent-command",
            sys.executable,
            "--agent-command",
            str(_FAKE_AGENT),
        ],
        binding=ConptyBinding(),
        abort_deadline_ms=20_000,
        constraint_ports=CooperationConstraintPorts({"drei-root": str(sandbox)}),
    )


def _type(adapter: ConptyAdapter, text: str) -> tuple[str, ...]:
    lines: tuple[str, ...] = ()
    for char in text:
        completed = adapter.dispatch(TextInput(ManualTime(0), char))
        assert type(completed) is EpochCompleted, completed
        lines = _frame_lines(completed.observation)
    return lines


def _chord(adapter: ConptyAdapter, *chord: str) -> tuple[str, ...]:
    completed = adapter.dispatch(KeyInput(ManualTime(0), chord))
    assert type(completed) is EpochCompleted, completed
    return _frame_lines(completed.observation)


def _settle_until(adapter: ConptyAdapter, needle: str) -> tuple[str, ...]:
    """Turn the crank with no-op inputs until `needle` appears on screen.

    `C-f` at end-of-buffer is the no-op: a real key through the real keymap, so
    each dispatch is an ordinary marked epoch and the loop is still driven by
    the cooperation protocol. What the deadline bounds is only how many turns
    of the crank the agent's answer needs — and it needs some, because no
    marker follows an agent delivery for the verifier to wait on.
    """
    deadline = time.monotonic() + _SETTLE_SECONDS
    lines: tuple[str, ...] = ()
    while time.monotonic() < deadline:
        lines = _chord(adapter, "Control", "f")
        if any(needle in line for line in lines):
            return lines
        time.sleep(0.05)
    raise AssertionError(
        f"{needle!r} never reached the frame within {_SETTLE_SECONDS}s; "
        f"last frame was {lines!r}"
    )


def test_shipped_editor_runs_an_agent_turn(tmp_path: Path) -> None:
    """`C-c a`, a prompt, RET — and the agent's answer appears in a buffer.

    The scenario TD-2 said was impossible: five merged ACP slices, none of
    them reachable from the shipped editor. This drives the real executable
    against a real child speaking the real wire.
    """
    adapter = _adapter(tmp_path)

    with _reaped(adapter):
        started = adapter.start("drei-agent-turn", _configuration())
        assert type(started) is Started, started
        initial = _frame_lines(started.observation)
        assert any("Drei: scratch" in line for line in initial), initial

        # C-c a opens the agent prompt on the echo row. The `a` is an
        # ordinary character, so it is TextInput: only modified chords and
        # named keys are KeyInput under termverify.key/v1.
        _chord(adapter, "Control", "c")
        prompted = _type(adapter, "a")
        assert any(line.startswith("Agent: ") for line in prompted), prompted

        typed = _type(adapter, "ping")
        assert any("Agent: ping" in line for line in typed), typed

        # RET submits: the child is spawned here and not before (0005 D6).
        submitted = _chord(adapter, "Enter")
        assert all("Agent: " not in line for line in submitted), submitted

        # No marker follows an agent delivery, so this is the one wait in the
        # suite that is bounded by a count rather than by quiescence. The
        # transcript appears in the *other* window on its own: focus never
        # moved, so this is still the scratch buffer's cursor being nudged.
        answered = _settle_until(adapter, "echo ping")
        assert any("Drei: *agent*" in line for line in answered), answered
        assert any("Drei: scratch" in line for line in answered), answered

        prefix = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        assert type(prefix) is EpochCompleted, prefix
        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "c")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final


def test_shipped_editor_survives_an_agent_that_will_not_start(tmp_path: Path) -> None:
    """`drei` on a machine with no agent installed is still an editor.

    The most likely real-world failure by a wide margin, and the one that must
    not produce a traceback in a raw terminal.
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(exist_ok=True)
    adapter = ConptyAdapter(
        [
            sys.executable,
            "-c",
            "from drei.cli import main; main()",
            "--agent-command",
            "drei-no-such-agent-xyz-123",
        ],
        binding=ConptyBinding(),
        abort_deadline_ms=20_000,
        constraint_ports=CooperationConstraintPorts({"drei-root": str(sandbox)}),
    )

    with _reaped(adapter):
        started = adapter.start("drei-agent-missing", _configuration())
        assert type(started) is Started, started

        _chord(adapter, "Control", "c")
        _type(adapter, "ahello")
        _chord(adapter, "Enter")

        # Still editing: the failure went to a buffer, not to the terminal.
        typed = _type(adapter, "xy")
        assert any(line.startswith("xy") for line in typed), typed

        # And the reason is readable.
        _chord(adapter, "Control", "x")
        _type(adapter, "b*agent-log*")
        logged = _chord(adapter, "Enter")
        assert any("not-found" in line for line in logged), logged

        prefix = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "x")))
        assert type(prefix) is EpochCompleted, prefix
        final = adapter.dispatch(KeyInput(ManualTime(0), ("Control", "c")))
        assert isinstance(final, TerminalResult), final
        assert final.outcome == RunFinished(ExitStatus("code", 0)), final
