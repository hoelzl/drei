"""The pump against a real child process (design 0005 verification layer 3).

Everything below the loop, unfaked: `SystemStreamingProcessPort` spawns a real
Python child, `AgentReaders` runs real threads over real pipes, and the frames
travel over real stdio. What it does not drive is the shipped executable — that
is the TermVerify scenario in `tests/termverify/`.

The consumer runs on its own thread so the assertions can carry a deadline. A
blocking `next_event` in the test body would turn any regression here into a
hung CI job instead of a failing test; nothing about the *editor* depends on
that thread, which is the point of the queue.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from drei.harness import EditorHarness
from drei.input import AgentBytes, AgentExited, AgentStderr, EndOfInput, EventQueue
from drei.model import BufferId
from drei.pump import AgentIo, AgentPump
from drei.streaming import SystemStreamingProcessPort

pytestmark = pytest.mark.integration

FAKE_AGENT = Path(__file__).resolve().parent / "fake_acp_agent.py"
AGENT_ARGV = (sys.executable, str(FAKE_AGENT))


class _Consumer:
    """`run_editor`'s agent arms, on a thread, with nothing else attached."""

    def __init__(self, stream: EventQueue, pump: AgentPump, harness: EditorHarness):
        self._stream = stream
        self._pump = pump
        self._harness = harness
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            while True:
                event = self._stream.next_event()
                if isinstance(event, AgentBytes):
                    self._pump.receive(event.data, self._harness)
                elif isinstance(event, AgentStderr):
                    self._pump.diagnostics(event.data, self._harness)
                elif isinstance(event, AgentExited):
                    self._pump.exited(event.status, self._harness)
        except EndOfInput:
            return
        except BaseException as error:  # noqa: BLE001 - reported, not swallowed
            self.error = error


def _await(consumer: _Consumer, condition: object, timeout: float = 20.0) -> None:
    """Wait for a condition, failing rather than hanging."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if consumer.error is not None:
            raise consumer.error
        if condition():  # type: ignore[operator]
            return
        time.sleep(0.01)
    raise AssertionError("the condition never held within the deadline")


def _text(harness: EditorHarness, buffer_id: BufferId | None) -> str:
    if buffer_id is None:
        return ""
    return harness._session._buffers[buffer_id].current.text  # noqa: SLF001


def test_a_real_child_answers_a_real_prompt(tmp_path: Path) -> None:
    """The whole transport, end to end: spawn, handshake, prompt, stream.

    Two chunks come back, so what this proves is a *fold* — the agent buffer's
    text is the concatenation of what was delivered, not one lucky write.
    """
    stream = EventQueue()
    harness = EditorHarness(width=60, height=10)
    pump = AgentPump(
        SystemStreamingProcessPort(),
        stream,
        argv=AGENT_ARGV,
        cwd=str(tmp_path),
        start_channel=AgentIo,
    )
    consumer = _Consumer(stream, pump, harness)
    try:
        pump.submit("hello there", harness)

        _await(
            consumer,
            lambda: (
                "echo hello there"
                in _text(harness, harness.agent_buffer_id("fake-session"))
            ),
        )
        transcript = _text(harness, harness.agent_buffer_id("fake-session"))
        assert "── agent ──" in transcript
        assert "── end turn (end_turn) ──" in transcript
    finally:
        pump.close()
        stream.close()
        consumer.thread.join(timeout=5)


def test_c_g_cancels_a_held_turn_and_the_next_prompt_still_works(
    tmp_path: Path,
) -> None:
    """Design 0005 D5 end to end (plan 0020 V3): a real child holding a turn
    open, a real `C-g`, `session/cancel` on the real wire, and the protocol's
    `cancelled` answer ending the turn in the transcript. The wire *order*
    (cancel before any further prompt) is pinned at layer 1
    (`test_pump.TestTurnCancellation`); what this proves is that nothing
    between the key and the child drops it — and that the editor and agent
    both survive to run the next turn."""
    stream = EventQueue()
    harness = EditorHarness(width=60, height=10)
    pump = AgentPump(
        SystemStreamingProcessPort(),
        stream,
        argv=AGENT_ARGV,
        cwd=str(tmp_path),
        start_channel=AgentIo,
    )
    consumer = _Consumer(stream, pump, harness)
    try:
        pump.submit("hold", harness)
        _await(consumer, lambda: pump.phase == "PROMPT_IN_FLIGHT")

        outcome = harness.send("C-g")
        assert outcome is not None
        pump.after_command(outcome, harness)

        _await(
            consumer,
            lambda: (
                "end turn (cancelled)"
                in _text(harness, harness.agent_buffer_id("fake-session"))
            ),
        )

        pump.submit("ping", harness)
        _await(
            consumer,
            lambda: (
                "echo ping" in _text(harness, harness.agent_buffer_id("fake-session"))
            ),
        )
    finally:
        pump.close()
        stream.close()
        consumer.thread.join(timeout=5)


def test_the_childs_stderr_reaches_the_diagnostics_buffer(tmp_path: Path) -> None:
    """The line the fake agent writes before it answers anything. A real
    `hermes acp` on a misconfigured machine says why it is dying here, and a
    pump that discarded it would be a black box exactly then."""
    stream = EventQueue()
    harness = EditorHarness(width=60, height=10)
    pump = AgentPump(
        SystemStreamingProcessPort(),
        stream,
        argv=AGENT_ARGV,
        cwd=str(tmp_path),
        start_channel=AgentIo,
    )
    consumer = _Consumer(stream, pump, harness)
    try:
        pump.submit("anything", harness)

        _await(
            consumer,
            lambda: (
                "fake-agent: ready"
                in _text(harness, harness.generated_buffer_id("*agent-log*"))
            ),
        )
    finally:
        pump.close()
        stream.close()
        consumer.thread.join(timeout=5)


def test_a_child_that_dies_is_reported_and_the_editor_survives(
    tmp_path: Path,
) -> None:
    """The exit path over a real pipe: a child that exits immediately produces
    an `AgentExited` from the reader thread, not a hung editor."""
    stream = EventQueue()
    harness = EditorHarness(width=60, height=10)
    pump = AgentPump(
        SystemStreamingProcessPort(),
        stream,
        argv=(sys.executable, "-c", "raise SystemExit(7)"),
        cwd=str(tmp_path),
        start_channel=AgentIo,
    )
    consumer = _Consumer(stream, pump, harness)
    try:
        pump.submit("hello", harness)

        _await(
            consumer,
            lambda: (
                "agent exited"
                in _text(harness, harness.generated_buffer_id("*agent-log*"))
            ),
        )
        # Still an editor.
        harness.send("x")
        assert harness.observation.text == "x"
    finally:
        pump.close()
        stream.close()
        consumer.thread.join(timeout=5)


def test_the_editor_leaves_no_child_behind(tmp_path: Path) -> None:
    """0005 D6: `pump.close()` runs in `run_editor`'s `finally`, and a leaked
    `hermes acp` holding a pipe is worse than a garbled terminal."""
    stream = EventQueue()
    harness = EditorHarness(width=60, height=10)
    port = SystemStreamingProcessPort()
    pump = AgentPump(
        port,
        stream,
        argv=(sys.executable, "-c", "import sys; sys.stdin.read()"),
        cwd=str(tmp_path),
        start_channel=AgentIo,
    )
    consumer = _Consumer(stream, pump, harness)
    try:
        pump.submit("hello", harness)
        child = pump._process  # noqa: SLF001 - the assertion is about this object
        assert child is not None

        pump.close()

        assert child.poll() is not None
    finally:
        stream.close()
        consumer.thread.join(timeout=5)
