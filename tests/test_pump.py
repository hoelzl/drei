"""The ACP pump: bytes from a child become commands in the editor.

Design 0005 D3/D6, plan 0016 D5/D6/D8. This is verification layer 1 — a fake
`AgentProcess`, no thread, no clock, no real child — which is where every
interleaving worth naming lives. The reader threads are proved separately in
`test_terminal.py`, and the whole path against a real child in
`test_agent_end_to_end.py`.
"""

from __future__ import annotations

import json
import threading

import pytest

from drei.acp.codec import encode
from drei.acp.machine import PermissionRequested
from drei.acp.messages import (
    SESSION_CANCEL,
    SESSION_REQUEST_PERMISSION,
    SESSION_UPDATE,
    JsonValue,
)
from drei.commands import (
    AgentTranscriptUpdated,
    BufferCreated,
    PromptPermission,
)
from drei.harness import EditorHarness
from drei.input import (
    AgentBytes,
    AgentExited,
    AgentStderr,
    EndOfInput,
    EventQueue,
)
from drei.pump import AgentIo, AgentPump, DirectAgentChannel
from drei.streaming import AgentProcess

_AGENT_CAPS: JsonValue = {"loadSession": False, "promptCapabilities": {"image": False}}


class FakeAgentProcess:
    """A child that records what Drei sent it and never reads a pipe."""

    def __init__(self) -> None:
        self.written = bytearray()
        self.terminated = 0
        self.status: int | None = None

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    def read(self, size: int = 65536) -> bytes:  # pragma: no cover - readers only
        return b""

    def read_stderr(self, size: int = 65536) -> bytes:  # pragma: no cover
        return b""

    def poll(self) -> int | None:
        return self.status

    def terminate(self) -> None:
        self.terminated += 1
        self.status = -15

    # -- test reader --------------------------------------------------------
    def sent(self) -> list[JsonValue]:
        return [json.loads(line) for line in bytes(self.written).splitlines() if line]


class FakeStreamingPort:
    """Hands out fake children, or refuses to, on demand."""

    def __init__(self, *, error: OSError | None = None) -> None:
        self.spawned: list[tuple[tuple[str, ...], str | None]] = []
        self.children: list[FakeAgentProcess] = []
        self._error = error

    def spawn(self, argv: tuple[str, ...], *, cwd: str | None = None) -> AgentProcess:
        if self._error is not None:
            raise self._error
        self.spawned.append((argv, cwd))
        child = FakeAgentProcess()
        self.children.append(child)
        return child


def _pump(port: FakeStreamingPort) -> AgentPump:
    return AgentPump(
        port,
        EventQueue(),
        argv=("fake-agent",),
        cwd="/work",
        # The synchronous channel: writes land in the fake child immediately
        # and nothing reads, because the test feeds the bytes itself.
        start_channel=lambda process, events: DirectAgentChannel(process),
    )


def _frames(*messages: JsonValue) -> bytes:
    return b"".join(encode(message) for message in messages)


def _init_response(request_id: JsonValue) -> JsonValue:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"protocolVersion": 1, "agentCapabilities": _AGENT_CAPS},
    }


def _new_session_response(request_id: JsonValue, session_id: str) -> JsonValue:
    return {"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": session_id}}


def _chunk(session_id: str, text: str) -> JsonValue:
    return {
        "jsonrpc": "2.0",
        "method": SESSION_UPDATE,
        "params": {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            },
        },
    }


def _completed(request_id: JsonValue, reason: str = "end_turn") -> JsonValue:
    return {"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": reason}}


def _permission_request(request_id: JsonValue, session_id: str) -> JsonValue:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": SESSION_REQUEST_PERMISSION,
        "params": {
            "sessionId": session_id,
            "toolCall": {"toolCallId": "t1", "title": "write file"},
            "options": [
                {"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                {"optionId": "no", "name": "Deny", "kind": "reject_once"},
            ],
        },
    }


def _method_of(sent: list[JsonValue], index: int) -> str:
    method = sent[index].get("method")
    assert isinstance(method, str), sent
    return method


def _handshake(
    pump: AgentPump, harness: EditorHarness, port: FakeStreamingPort, text: str = "hi"
) -> FakeAgentProcess:
    """Drive spawn -> initialize -> session/new -> session/prompt."""
    pump.submit(text, harness)
    child = port.children[-1]
    pump.receive(_frames(_init_response(child.sent()[0]["id"])), harness)
    pump.receive(
        _frames(_new_session_response(child.sent()[1]["id"], "sess-1")), harness
    )
    return child


def _agent_text(harness: EditorHarness) -> str:
    """The agent buffer's text, read through the session's own binding."""
    buffer_id = harness.agent_buffer_id("sess-1")
    assert buffer_id is not None
    return harness._session._buffers[buffer_id].current.text  # noqa: SLF001


class TestHandshake:
    def test_the_first_prompt_spawns_the_child_and_starts_the_handshake(self) -> None:
        """Lazily, on the first agent command (0005 D6): `drei file.txt` on a
        machine with no agent installed must pay nothing for one."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        assert port.spawned == []

        pump.submit("hello", harness)

        assert port.spawned == [(("fake-agent",), "/work")]
        assert _method_of(port.children[0].sent(), 0) == "initialize"

    def test_the_prompt_is_held_until_the_session_exists(self) -> None:
        """`session/prompt` needs a session id, which arrives two round trips
        later. A prompt typed before then must not be dropped — the user
        pressed RET and nothing on screen says it went nowhere."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)

        child = _handshake(pump, harness, port, text="summarize this")

        methods = [_method_of(child.sent(), i) for i in range(3)]
        assert methods == ["initialize", "session/new", "session/prompt"]
        params = child.sent()[2]["params"]
        assert params["sessionId"] == "sess-1"
        assert params["prompt"] == [{"type": "text", "text": "summarize this"}]

    def test_the_agent_buffer_is_bound_to_the_acp_session(self) -> None:
        """Design 0004 D1: one buffer per ACP session, minted by the session
        itself. The pump never guesses a buffer identity."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)

        _handshake(pump, harness, port)

        assert harness.agent_buffer_id("sess-1") is not None

    def test_the_transcript_is_shown_without_taking_focus(self) -> None:
        """The gap the end-to-end scenario found: a transcript nowhere on
        screen is a feature the user cannot use. Asserted here and not only in
        the Windows-only ConPTY scenario, because a Linux CI leg would
        otherwise never notice the display going away.
        """
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=12)

        _handshake(pump, harness, port)

        session = harness._session  # noqa: SLF001 - layout has no public reader
        transcript = harness.agent_buffer_id("sess-1")
        assert len(session.windows) == 2
        assert session.windows[1].buffer_id == transcript
        # ...and the user is still where they were.
        assert session.focused == 0
        assert session.windows[0].buffer_id != transcript

    def test_a_second_prompt_reuses_the_child_and_the_session(self) -> None:
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)
        pump.receive(_frames(_completed(child.sent()[2]["id"])), harness)

        pump.submit("again", harness)

        assert len(port.spawned) == 1
        methods = [_method_of(child.sent(), i) for i in range(4)]
        assert methods == [
            "initialize",
            "session/new",
            "session/prompt",
            "session/prompt",
        ]

    def test_every_prompt_typed_mid_turn_is_sent_in_order(self) -> None:
        """Two prompts during one turn, not one. A held-prompt *slot* would
        keep the last and swallow the rest — the same silence the slot exists
        to avoid, just at n > 1."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port, text="first")

        pump.submit("second", harness)
        pump.submit("third", harness)
        for _ in range(2):
            in_flight = [
                message
                for message in child.sent()
                if message.get("method") == "session/prompt"
            ][-1]
            pump.receive(_frames(_completed(in_flight["id"])), harness)

        sent = [
            message["params"]["prompt"][0]["text"]
            for message in child.sent()
            if message.get("method") == "session/prompt"
        ]
        assert sent == ["first", "second", "third"]

    def test_a_prompt_typed_mid_turn_is_sent_when_the_turn_completes(self) -> None:
        """ACP allows one prompt per turn. A second one typed while the agent
        is still answering waits rather than raising out of the machine."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)

        pump.submit("and another thing", harness)
        assert len(child.sent()) == 3  # still only the first prompt

        pump.receive(_frames(_completed(child.sent()[2]["id"])), harness)
        assert _method_of(child.sent(), 3) == "session/prompt"
        assert child.sent()[3]["params"]["prompt"][0]["text"] == "and another thing"


class TestDelivery:
    def test_streamed_text_reaches_the_agent_buffer(self) -> None:
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)

        pump.receive(_frames(_chunk("sess-1", "Hello, ")), harness)
        pump.receive(_frames(_chunk("sess-1", "world.")), harness)

        assert _agent_text(harness).endswith("Hello, world.")

    def test_many_frames_in_one_read_are_one_delivery(self) -> None:
        """0005 D3: drain, then deliver once. A chatty agent costs one
        transcript event and one redraw per loop iteration, not one per frame
        — the property that keeps a burst from turning into N screen writes.
        """
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)
        before = len(harness.outcomes)

        pump.receive(
            _frames(
                _chunk("sess-1", "a"),
                _chunk("sess-1", "b"),
                _chunk("sess-1", "c"),
            ),
            harness,
        )

        new = harness.outcomes[before:]
        deliveries = [
            event
            for outcome in new
            for event in outcome.events
            if isinstance(event, AgentTranscriptUpdated)
        ]
        assert len(deliveries) == 1
        assert deliveries[0].rendered.endswith("abc")

    def test_a_frame_split_across_two_reads_is_not_lost(self) -> None:
        """A pipe splits where it likes, including *inside* a multi-byte
        character. The decoder buffers bytes and only parses at a newline, so
        the halves rejoin — but the wire has to be built by hand to show it,
        because Drei's own encoder escapes non-ASCII and a real agent's need
        not.
        """
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)

        wire = (
            json.dumps(_chunk("sess-1", "über"), ensure_ascii=False).encode("utf-8")
            + b"\n"
        )
        split = wire.index(b"\xc3\xbc") + 1  # between the two bytes of "ü"
        pump.receive(wire[:split], harness)
        assert "über" not in _agent_text(harness)
        pump.receive(wire[split:], harness)

        assert _agent_text(harness).endswith("über")

    def test_a_malformed_frame_is_recorded_and_the_editor_survives(self) -> None:
        """The peer is non-deterministic by design; the transcript must
        survive it. A decode error becomes a transcript line, not a crash."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)

        pump.receive(b"{not json at all}\n", harness)

        assert "protocol error" in _agent_text(harness)

    def test_a_frame_that_is_not_a_jsonrpc_envelope_is_recorded(self) -> None:
        """Valid JSON, invalid envelope: the codec is happy and the message
        layer is not. Both failures land in the same place."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)

        pump.receive(b'{"jsonrpc":"2.0"}\n', harness)

        assert "protocol error" in _agent_text(harness)

    def test_bytes_arriving_before_a_session_exists_are_not_dropped(self) -> None:
        """Handshake milestones are consumed by the pump rather than
        delivered, because there is no agent buffer yet — and they render to
        the empty string, so the fold loses nothing. Anything else that
        arrives early is a protocol error and must still be visible."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        pump.submit("hi", harness)
        child = port.children[0]

        # A chunk before session/new has even been answered.
        pump.receive(_frames(_chunk("sess-1", "early")), harness)
        pump.receive(_frames(_init_response(child.sent()[0]["id"])), harness)
        pump.receive(
            _frames(_new_session_response(child.sent()[1]["id"], "sess-1")), harness
        )

        # The early frame was recorded as a protocol error against the machine
        # rather than silently discarded.
        assert "protocol error" in _agent_text(harness)

    def test_a_request_the_machine_refuses_is_answered_immediately(self) -> None:
        """The machine refuses `fs/*` and `terminal/*` outright (Drei declares
        no such capability). Those responses are not a delivery — they are the
        protocol's own answers, and an unanswered request stalls the agent, so
        they go on the wire during the drain rather than after it.
        """
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)
        # Record how far the editor had got each time something was written.
        dispatched_at_write: list[int] = []
        inner = child.write

        def watched(data: bytes) -> None:
            dispatched_at_write.append(len(harness.outcomes))
            inner(data)

        child.write = watched  # type: ignore[method-assign]
        before = len(harness.outcomes)

        pump.receive(
            _frames(
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "fs/read_text_file",
                    "params": {"sessionId": "sess-1", "path": "/etc/passwd"},
                },
                _chunk("sess-1", "and some text"),
            ),
            harness,
        )

        answer = child.sent()[-1]
        assert answer["id"] == 11
        assert "error" in answer
        # Written *during* the drain: the delivery that the same read produced
        # had not been dispatched yet. Deferring it until after would stall a
        # peer that is waiting on the answer to continue the turn.
        assert dispatched_at_write == [before]
        assert len(harness.outcomes) > before


class TestPermissions:
    def test_a_permission_request_opens_a_prompt_and_the_answer_is_sent(self) -> None:
        """The half TD-2 says hangs today: the session already turns a request
        into a choice prompt and the human's key into a `PermissionDecided`
        event, and nothing ever put the resulting response on the wire."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)

        pump.receive(_frames(_permission_request(7, "sess-1")), harness)
        assert harness.observation.minibuffer_prompt is not None
        assert "write file" in harness.observation.minibuffer_prompt

        outcome = harness.send("y")  # the allow option's first letter
        assert outcome is not None
        pump.after_command(outcome, harness)

        answer = child.sent()[-1]
        assert answer["id"] == 7
        assert answer["result"] == {
            "outcome": {"outcome": "selected", "optionId": "yes"}
        }

    def test_the_request_is_in_the_transcript_before_the_prompt_opens(self) -> None:
        """Order, not just presence: the transcript records that the agent
        asked, and only then does the prompt appear. Reversed, a user who
        answered instantly would see the request logged after their answer."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)
        before = len(harness.outcomes)

        pump.receive(_frames(_permission_request(7, "sess-1")), harness)

        kinds = [
            type(event).__name__
            for outcome in harness.outcomes[before:]
            for event in outcome.events
        ]
        assert kinds.index("AgentTranscriptUpdated") < kinds.index("MinibufferOpened")
        assert "permission requested (id 7)" in _agent_text(harness)

    def test_a_prompt_typed_at_the_keyboard_reaches_the_agent(self) -> None:
        """The whole `C-c a` path, end to end at layer 1: the session records
        `AgentPromptSubmitted`, the pump reads it out of the outcome, and the
        child gets spawned. Neither side knows about the other's vocabulary.
        """
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)

        harness.send("C-c")
        harness.send("a")
        for char in "do the thing":
            harness.send(char)
        outcome = harness.send("RET")
        assert outcome is not None
        pump.after_command(outcome, harness)

        assert port.spawned == [(("fake-agent",), "/work")]
        child = port.children[0]
        pump.receive(_frames(_init_response(child.sent()[0]["id"])), harness)
        pump.receive(
            _frames(_new_session_response(child.sent()[1]["id"], "sess-1")), harness
        )
        assert child.sent()[2]["params"]["prompt"][0]["text"] == "do the thing"

    def test_a_key_that_decides_nothing_sends_nothing(self) -> None:
        """`after_command` runs after every key, so it must be silent for the
        overwhelming majority that have nothing to do with the agent."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)
        before = len(child.sent())

        outcome = harness.send("x")
        assert outcome is not None
        pump.after_command(outcome, harness)

        assert len(child.sent()) == before


class TestChildFailure:
    def test_a_child_that_exits_mid_turn_leaves_the_editor_usable(self) -> None:
        """0005 D6: a peer failure, not a crash. Recorded in the transcript,
        the machine back to DISCONNECTED, and the next prompt starts fresh."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)

        pump.exited(1, harness)

        assert "agent exited" in _agent_text(harness)
        # The editor still edits.
        harness.send("x")
        assert harness.observation.text == "x"
        # And the next prompt launches a new child rather than writing to a
        # pipe nobody is reading.
        pump.submit("again", harness)
        assert len(port.spawned) == 2

    def test_an_exit_sweeps_a_permission_prompt_the_dead_child_asked_for(self) -> None:
        """An open choice prompt for a peer that no longer exists can never be
        answered; leaving it up would wedge the editor's input in choice mode.
        """
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)
        pump.receive(_frames(_permission_request(7, "sess-1")), harness)
        assert harness.observation.minibuffer is not None

        pump.exited(None, harness)

        assert harness.observation.minibuffer is None

    def test_the_next_child_starts_from_a_clean_decoder(self) -> None:
        """A child that dies mid-frame leaves half a line buffered. Carried
        into the next child, that fragment is prefixed onto its first frame and
        the handshake fails on a syntax error nobody sent."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)

        pump.receive(b'{"jsonrpc":"2.0","meth', harness)  # cut off mid-frame
        pump.exited(1, harness)

        pump.submit("again", harness)
        second = port.children[1]
        pump.receive(_frames(_init_response(second.sent()[0]["id"])), harness)
        pump.receive(
            _frames(_new_session_response(second.sent()[1]["id"], "sess-2")), harness
        )
        pump.receive(_frames(_chunk("sess-2", "clean")), harness)

        buffer_id = harness.agent_buffer_id("sess-2")
        assert buffer_id is not None
        text = harness._session._buffers[buffer_id].current.text  # noqa: SLF001
        assert text.endswith("clean")
        assert "protocol error" not in text

    def test_the_dead_sessions_transcript_receives_nothing_more(self) -> None:
        """Design 0004 D1 is one buffer per ACP *session*. A stale transcript
        id surviving the reset would deliver the next child's handshake
        failures into the previous session's buffer."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)
        pump.exited(1, harness)
        first = harness.agent_buffer_id("sess-1")
        assert first is not None
        before = harness._session._buffers[first].current.text  # noqa: SLF001

        pump.submit("again", harness)
        pump.receive(b"garbage before the new session\n", harness)

        after = harness._session._buffers[first].current.text  # noqa: SLF001
        assert after == before

    def test_prompts_the_dead_child_never_received_are_reported(self) -> None:
        """The queue exists so a prompt is never silently lost. If the child
        dies holding some, saying nothing would be exactly the loss it was
        built to prevent."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)
        pump.submit("never sent", harness)  # held behind the turn in flight

        pump.exited(1, harness)

        assert "unsent prompts dropped: 'never sent'" in _diagnostics(harness)

    def test_a_permission_answered_after_the_child_died_is_not_fatal(self) -> None:
        """Every other entry point degrades a peer problem to a transcript
        line. `after_command` must too: the human's key arrives on its own
        schedule, and answering a prompt the machine no longer tracks cannot
        be allowed to take the editor down."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)
        pump.receive(_frames(_permission_request(7, "sess-1")), harness)
        # The child dies while the choice prompt is open, and the sweep leaves
        # the machine with nothing tracked.
        pump.exited(1, harness)
        # ...and a prompt for that dead request is somehow open again, which
        # is the state the human's key is about to answer.
        harness.apply(PromptPermission(PermissionRequested(7, {"options": []})))
        outcome = harness.send("C-g")
        assert outcome is not None

        pump.after_command(outcome, harness)  # must not raise

        harness.send("x")
        assert harness.observation.text == "x"

    def test_an_exit_with_no_status_says_so_rather_than_guessing(self) -> None:
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)

        pump.exited(None, harness)

        assert "agent exited (status unknown)" in _agent_text(harness)

    def test_a_child_that_dies_during_the_handshake_says_why(self) -> None:
        """Effects produced before the agent buffer exists have nowhere to go
        yet, so the pump holds them. If the child dies before a session was
        ever established, that backlog is the only account of the failure and
        goes to the diagnostics buffer rather than the void.
        """
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        pump.submit("hello", harness)

        pump.receive(b"garbage, not json\n", harness)  # before session/new
        pump.exited(2, harness)

        log = _diagnostics(harness)
        assert "invalid JSON" in log
        assert "agent exited (status 2)" in log

    def test_a_launch_failure_is_a_normalized_token_not_a_traceback(self) -> None:
        """`drei` must survive a machine with no agent installed. The token is
        the same vocabulary `run_process` reports."""
        port = FakeStreamingPort(error=FileNotFoundError("no hermes"))
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)

        pump.submit("hello", harness)

        assert "not-found" in _diagnostics(harness)
        harness.send("x")
        assert harness.observation.text == "x"

    def test_a_write_to_a_dead_child_is_reported_not_raised(self) -> None:
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)

        def boom(data: bytes) -> None:
            raise BrokenPipeError("the child is gone")

        child.write = boom  # type: ignore[method-assign]
        pump.receive(_frames(_permission_request(7, "sess-1")), harness)
        outcome = harness.send("y")
        assert outcome is not None
        pump.after_command(outcome, harness)

        assert "the child is gone" in _diagnostics(harness)


def _diagnostics(harness: EditorHarness) -> str:
    from drei.model import BufferId

    return harness._session._buffers[BufferId("*agent-log*")].current.text  # noqa: SLF001


class TestDiagnostics:
    def test_stderr_goes_to_its_own_buffer_and_never_to_the_wire(self) -> None:
        """0005 D6. The first thing a misconfigured agent does is die with a
        message on stderr; a pump that discards it is a black box exactly when
        the user needs a window."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)

        pump.diagnostics(b"warning: model unavailable\n", harness)

        assert "warning: model unavailable" in _diagnostics(harness)
        assert "warning" not in _agent_text(harness)

    def test_the_diagnostics_buffer_is_created_once(self) -> None:
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)

        pump.diagnostics(b"one\n", harness)
        outcomes = len(harness.outcomes)
        pump.diagnostics(b"two\n", harness)

        created = [
            event
            for outcome in harness.outcomes[outcomes:]
            for event in outcome.events
            if isinstance(event, BufferCreated)
        ]
        assert created == []
        assert _diagnostics(harness) == "one\ntwo\n"

    def test_undecodable_stderr_is_shown_rather_than_dropped(self) -> None:
        """Diagnostics are not a protocol: bytes that are not utf-8 are still
        the only clue the user has."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)

        pump.diagnostics(b"bad \xff byte\n", harness)

        assert "bad" in _diagnostics(harness)


class TestLifecycle:
    def test_close_terminates_the_child(self) -> None:
        """0005 D6: a leaked child holding a pipe is worse than a garbled
        terminal, so this runs in `run_editor`'s `finally`."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)

        pump.close()

        assert port.children[0].terminated == 1

    def test_close_without_a_child_is_a_no_op(self) -> None:
        """The overwhelmingly common case: `drei` was never asked for an
        agent, and quitting must not construct one to tear it down."""
        port = FakeStreamingPort()
        _pump(port).close()
        assert port.spawned == []

    def test_close_is_idempotent(self) -> None:
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)

        pump.close()
        pump.close()

        assert port.children[0].terminated == 1


class TestAgentIo:
    """Verification layer 2: the agent's producer threads, in isolation.

    Every failure here is immediate and deterministic — a scripted fake child
    that runs out of bytes — so the suite stays so.
    """

    @staticmethod
    def _drain(stream: EventQueue, count: int) -> list[object]:
        seen: list[object] = []
        for _ in range(count):
            seen.append(stream.next_event())
        return seen

    def test_wire_bytes_become_events_in_order_and_the_exit_closes_them(self) -> None:
        class ScriptedChild(FakeAgentProcess):
            def __init__(self) -> None:
                super().__init__()
                self._chunks = [b"one", b"two"]
                self.status = 0

            def read(self, size: int = 65536) -> bytes:
                return self._chunks.pop(0) if self._chunks else b""

        stream = EventQueue()
        readers = AgentIo(ScriptedChild(), stream)
        try:
            assert self._drain(stream, 3) == [
                AgentBytes(b"one"),
                AgentBytes(b"two"),
                AgentExited(0),
            ]
        finally:
            readers.close()

    def test_stderr_arrives_on_its_own_kind(self) -> None:
        class NoisyChild(FakeAgentProcess):
            def __init__(self) -> None:
                super().__init__()
                self._diagnostics = [b"boom\n"]

            def read_stderr(self, size: int = 65536) -> bytes:
                return self._diagnostics.pop(0) if self._diagnostics else b""

        stream = EventQueue()
        readers = AgentIo(NoisyChild(), stream)
        try:
            # The wire reader also reports the exit; stderr is what we assert.
            seen = self._drain(stream, 2)
            assert AgentStderr(b"boom\n") in seen
        finally:
            readers.close()

    def test_a_broken_pipe_is_an_exit_rather_than_an_editor_failure(self) -> None:
        """The asymmetry with `TerminalReaders`, pinned: an editor that cannot
        be typed into is over, but an agent that died is an ordinary peer
        failure the editor is expected to survive."""

        class BrokenChild(FakeAgentProcess):
            def read(self, size: int = 65536) -> bytes:
                raise BrokenPipeError("gone")

            def read_stderr(self, size: int = 65536) -> bytes:
                # Both pipes die together when a child does; the diagnostics
                # reader must be as quiet about it as the wire reader.
                raise BrokenPipeError("gone")

        stream = EventQueue()
        readers = AgentIo(BrokenChild(), stream)
        try:
            assert stream.next_event() == AgentExited(None)
            readers._errors.join(timeout=5)  # noqa: SLF001 - shutdown assertion
            assert not readers._errors.is_alive()  # noqa: SLF001
        finally:
            readers.close()

    def test_a_bug_in_the_reader_still_reaches_the_loop(self) -> None:
        """A pipe error is the peer's problem; anything else is Drei's, and a
        thread that dies silently would leave the editor waiting on a producer
        that no longer exists."""

        class BuggyChild(FakeAgentProcess):
            def read(self, size: int = 65536) -> bytes:
                raise AssertionError("drei is broken")

            def read_stderr(self, size: int = 65536) -> bytes:
                raise AssertionError("drei is broken")

        stream = EventQueue()
        readers = AgentIo(BuggyChild(), stream)
        try:
            with pytest.raises(AssertionError, match="drei is broken"):
                stream.next_event()
        finally:
            readers.close()

    def test_a_write_never_blocks_the_caller(self) -> None:
        """The merge-blocker the adversarial review found.

        A pipe write blocks once the kernel buffer fills and the peer is not
        draining — measured at a single 4 KB chunk against a child that never
        reads. On the loop's thread that means the editor stops consuming
        input, with the terminal still in raw mode and `C-g` unable to reach
        it: the exact wedge slice 15's review found in the reader, arriving
        from the other direction. Drei's own writes are small, but how *many*
        it makes is peer-driven — the machine answers every `fs/*` and
        `terminal/*` request the agent sends.
        """
        released = threading.Event()
        wrote = threading.Event()

        class StuckChild(FakeAgentProcess):
            def write(self, data: bytes) -> None:
                released.wait(timeout=5)
                wrote.set()

            def read(self, size: int = 65536) -> bytes:
                released.wait(timeout=5)
                return b""

            def read_stderr(self, size: int = 65536) -> bytes:
                released.wait(timeout=5)
                return b""

        stream = EventQueue()
        io = AgentIo(StuckChild(), stream)
        try:
            # Would hang here if the write happened on this thread.
            for _ in range(64):
                io.write(b'{"jsonrpc":"2.0"}\n')
            assert not wrote.is_set()
            # Released, the writer drains what it was holding — the bytes are
            # queued, not discarded.
            released.set()
            assert wrote.wait(timeout=5)
        finally:
            io.close()

    def test_a_write_to_a_dead_child_stops_the_writer_without_a_second_exit(
        self,
    ) -> None:
        """The wire reader is what reports a death. A writer that reported it
        too would put two exits in the transcript for one child."""

        class BrokenStdin(FakeAgentProcess):
            def __init__(self) -> None:
                super().__init__()
                self.status = 4

            def write(self, data: bytes) -> None:
                raise BrokenPipeError("gone")

        stream = EventQueue()
        io = AgentIo(BrokenStdin(), stream)
        try:
            io.write(b"anything\n")
            assert stream.next_event() == AgentExited(4)  # from the reader
            io._writer.join(timeout=5)  # noqa: SLF001 - shutdown assertion
            assert not io._writer.is_alive()  # noqa: SLF001
            stream.close()
            with pytest.raises(EndOfInput):
                stream.next_event()
        finally:
            io.close()

    def test_a_bug_in_the_writer_reaches_the_loop(self) -> None:
        parked = threading.Event()

        class BuggyStdin(FakeAgentProcess):
            def write(self, data: bytes) -> None:
                raise AssertionError("writer is broken")

            def read(self, size: int = 65536) -> bytes:
                parked.wait(timeout=5)
                return b""

            def read_stderr(self, size: int = 65536) -> bytes:
                parked.wait(timeout=5)
                return b""

        stream = EventQueue()
        io = AgentIo(BuggyStdin(), stream)
        try:
            io.write(b"anything\n")
            with pytest.raises(AssertionError, match="writer is broken"):
                stream.next_event()
        finally:
            parked.set()
            io.close()

    def test_close_releases_an_idle_writer(self) -> None:
        """The writer parks on an empty queue, so the stop flag alone would
        never reach it — shutdown would leave a thread waiting forever."""
        parked = threading.Event()

        class ParkedChild(FakeAgentProcess):
            def read(self, size: int = 65536) -> bytes:
                parked.wait(timeout=5)
                return b""

            def read_stderr(self, size: int = 65536) -> bytes:
                parked.wait(timeout=5)
                return b""

        stream = EventQueue()
        io = AgentIo(ParkedChild(), stream)
        io.close()
        io._writer.join(timeout=5)  # noqa: SLF001 - shutdown assertion
        parked.set()

        assert not io._writer.is_alive()  # noqa: SLF001

    def test_a_reader_closed_mid_stream_stops_after_the_chunk_in_hand(self) -> None:
        """`close` is checked between reads, not during one: a reader parked in
        a blocking read finishes that read, delivers what it got, and only then
        notices. It must not then report an exit — shutdown is not a peer
        failure."""
        stream = EventQueue()
        handle: list[AgentIo] = []
        # The threads start inside the constructor, so they can call `read`
        # before the test has a reference to close.
        wired = threading.Event()

        class ClosingChild(FakeAgentProcess):
            def read(self, size: int = 65536) -> bytes:
                wired.wait(timeout=5)
                handle[0].close()
                return b"last"

            def read_stderr(self, size: int = 65536) -> bytes:
                wired.wait(timeout=5)
                handle[0].close()
                return b"note"

        readers = AgentIo(ClosingChild(), stream)
        handle.append(readers)
        wired.set()
        readers._wire.join(timeout=5)  # noqa: SLF001 - shutdown assertion
        readers._errors.join(timeout=5)  # noqa: SLF001

        stream.close()
        seen = []
        while True:
            try:
                seen.append(stream.next_event())
            except EndOfInput:
                break
        assert AgentBytes(b"last") in seen
        assert not any(isinstance(event, AgentExited) for event in seen)

    def test_a_bug_in_the_stderr_reader_also_reaches_the_loop(self) -> None:
        """Asserted separately from the wire reader's: with both raising, which
        failure the queue reports is a race, and a test that passes either way
        proves neither."""
        parked = threading.Event()

        class NoisyBug(FakeAgentProcess):
            def read(self, size: int = 65536) -> bytes:
                parked.wait(timeout=5)
                return b""

            def read_stderr(self, size: int = 65536) -> bytes:
                raise AssertionError("stderr reader is broken")

        stream = EventQueue()
        readers = AgentIo(NoisyBug(), stream)
        try:
            with pytest.raises(AssertionError, match="stderr reader is broken"):
                stream.next_event()
        finally:
            parked.set()
            readers.close()

    def test_a_closed_reader_reports_no_exit(self) -> None:
        """Shutdown is not a peer failure. `close()` then a child ending must
        not push an `AgentExited` into a queue the loop has stopped reading —
        or into the *next* run's transcript."""
        released = threading.Event()

        class ParkedChild(FakeAgentProcess):
            def read(self, size: int = 65536) -> bytes:
                released.wait(timeout=5)
                return b""

            def read_stderr(self, size: int = 65536) -> bytes:
                released.wait(timeout=5)
                return b""

        stream = EventQueue()
        readers = AgentIo(ParkedChild(), stream)
        readers.close()
        released.set()
        readers._wire.join(timeout=5)  # noqa: SLF001 - shutdown assertion

        assert not readers._wire.is_alive()  # noqa: SLF001
        stream.close()
        with pytest.raises(EndOfInput):
            stream.next_event()


def test_bytes_for_a_pump_that_never_spawned_are_ignored() -> None:
    """Defensive, and cheap: an `AgentBytes` event can only exist if a child
    was spawned, but the loop must not depend on that to stay alive."""
    port = FakeStreamingPort()
    pump = _pump(port)
    harness = EditorHarness(width=40, height=8)

    pump.receive(b'{"jsonrpc":"2.0"}\n', harness)

    assert harness.observation.text == ""


def test_the_pump_holds_the_machine_and_the_session_does_not() -> None:
    """0005 D7. Protocol phase is transport state; putting it in the session
    would mean replay had to reproduce it."""
    port = FakeStreamingPort()
    pump = _pump(port)
    harness = EditorHarness(width=40, height=8)
    _handshake(pump, harness, port)

    assert pump.phase == "PROMPT_IN_FLIGHT"
    assert not hasattr(harness._session, "_machine")  # noqa: SLF001


@pytest.mark.parametrize("status", [0, 1, None])
def test_every_exit_status_is_rendered(status: int | None) -> None:
    port = FakeStreamingPort()
    pump = _pump(port)
    harness = EditorHarness(width=40, height=8)
    _handshake(pump, harness, port)

    pump.exited(status, harness)

    assert "agent exited" in _agent_text(harness)


class TestTurnCancellation:
    """Design 0005 D5, plan 0020 D1–D4: `C-g` cancels a turn in flight.

    The trigger is read out of the command outcome (the 0005 D7 seam — the
    session stays machine-free), so every test drives a real key through
    the harness and hands the outcome to the pump, the terminal loop's own
    shape.
    """

    def test_c_g_mid_turn_writes_session_cancel_and_the_turn_can_complete(self) -> None:
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)
        assert pump.phase == "PROMPT_IN_FLIGHT"

        outcome = harness.send("C-g")
        assert outcome is not None
        pump.after_command(outcome, harness)

        methods = [message.get("method") for message in child.sent()]
        assert methods == [
            "initialize",
            "session/new",
            "session/prompt",
            SESSION_CANCEL,
        ]
        # The phase stays PROMPT_IN_FLIGHT until the agent answers; the
        # protocol-required cancelled response completes the turn and the
        # transcript says so.
        pump.receive(
            _frames(_completed(child.sent()[2]["id"], reason="cancelled")), harness
        )
        assert "end turn (cancelled)" in _agent_text(harness)
        assert pump.phase == "SESSION_ACTIVE"

    def test_c_g_with_no_agent_is_a_plain_keyboard_quit(self) -> None:
        """The phase guard: no child, nothing to cancel, nothing written."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)

        outcome = harness.send("C-g")
        assert outcome is not None
        pump.after_command(outcome, harness)

        assert port.spawned == []

    def test_c_g_during_the_handshake_does_not_cancel(self) -> None:
        """INITIALIZING is not PROMPT_IN_FLIGHT: `cancel()` would raise, so
        the guard is what a `C-g` while the agent starts survives by."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        pump.submit("hi", harness)
        assert pump.phase == "INITIALIZING"

        outcome = harness.send("C-g")
        assert outcome is not None
        pump.after_command(outcome, harness)

        methods = [message.get("method") for message in port.children[0].sent()]
        assert methods == ["initialize"]

    def test_first_c_g_denies_the_permission_second_cancels_the_turn(self) -> None:
        """Plan 0020 D2: no new prompt behavior — the shipped deny composes
        with the new trigger. The first `C-g` answers the agent; the second
        cancels the turn."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)
        pump.receive(_frames(_permission_request(7, "sess-1")), harness)

        first = harness.send("C-g")
        assert first is not None
        pump.after_command(first, harness)

        answer = child.sent()[-1]
        assert answer["id"] == 7
        assert answer["result"] == {"outcome": {"outcome": "cancelled"}}
        assert pump.phase == "PROMPT_IN_FLIGHT"
        assert SESSION_CANCEL not in [m.get("method") for m in child.sent()]

        second = harness.send("C-g")
        assert second is not None
        pump.after_command(second, harness)

        methods = [m.get("method") for m in child.sent()]
        assert methods[-1] == SESSION_CANCEL

    def test_c_g_at_an_exit_prompt_abandons_the_exit_without_cancelling(self) -> None:
        """Plan 0020 D3: one `C-g` peels one layer. The exit prompt outranks
        the turn; the turn stays in flight for the `C-g` that follows."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)
        harness.send("x")  # the scratch buffer is modified — and pathless
        harness.send("C-x")
        harness.send("C-c")  # straight to the gate (plan 0018 D2)
        assert "exit anyway" in (harness.observation.minibuffer_prompt or "")

        outcome = harness.send("C-g")
        assert outcome is not None
        pump.after_command(outcome, harness)

        assert harness.observation.minibuffer is None  # the exit is abandoned
        assert SESSION_CANCEL not in [m.get("method") for m in child.sent()]
        assert pump.phase == "PROMPT_IN_FLIGHT"

    def test_a_second_c_g_before_the_answer_recancels_without_raising(self) -> None:
        """Plan 0020 D4: the phase stays PROMPT_IN_FLIGHT until the agent
        answers, so a repeated `C-g` re-sends the (idempotent) notification
        rather than raising."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)

        for _ in range(2):
            outcome = harness.send("C-g")
            assert outcome is not None
            pump.after_command(outcome, harness)

        cancels = [m for m in child.sent() if m.get("method") == SESSION_CANCEL]
        assert len(cancels) == 2

    def test_a_request_queued_behind_a_prompt_is_presented_on_close(self) -> None:
        """The routing invariant that keeps the post-cancel sweep defensive:
        a request queued behind a text prompt is presented the moment the
        prompt closes — *accept* drains too (`session.py:942–945`), not only
        abandon — so a top-level `C-g` (the turn-cancelling one) never meets
        an unpresented request. The queue detour then composes with D2:
        deny the presented request, then cancel the turn."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)
        harness.send("C-x")
        harness.send("C-f")
        pump.receive(_frames(_permission_request(7, "sess-1")), harness)
        assert harness.observation.minibuffer_prompt == "Find file: "

        for char in "/x.txt":
            harness.send(char)
        accepted = harness.send("RET")
        assert accepted is not None

        # The accept drained the queue and presented the request.
        prompt = harness.observation.minibuffer_prompt or ""
        assert "Allow write file" in prompt

        denied = harness.send("C-g")
        assert denied is not None
        pump.after_command(denied, harness)
        assert child.sent()[-1]["result"] == {"outcome": {"outcome": "cancelled"}}

        outcome = harness.send("C-g")
        assert outcome is not None
        pump.after_command(outcome, harness)
        methods = [m.get("method") for m in child.sent()]
        assert methods[-1] == SESSION_CANCEL
        assert harness._session._permission_queue == []  # noqa: SLF001

    def test_c_g_at_a_text_prompt_mid_turn_aborts_only_the_prompt(self) -> None:
        """Round-1 review finding 2, pinned: a text prompt peels *alone*.
        `C-g` at `Find file:` while a turn is in flight aborts the prompt
        (`MinibufferAborted`, no `KeyboardQuitEvent`), the turn waits, and
        the next top-level `C-g` is the one that cancels it."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)
        harness.send("C-x")
        harness.send("C-f")
        assert harness.observation.minibuffer_prompt == "Find file: "

        aborted = harness.send("C-g")
        assert aborted is not None
        pump.after_command(aborted, harness)

        assert harness.observation.minibuffer is None
        assert SESSION_CANCEL not in [m.get("method") for m in child.sent()]
        assert pump.phase == "PROMPT_IN_FLIGHT"

        outcome = harness.send("C-g")
        assert outcome is not None
        pump.after_command(outcome, harness)
        methods = [m.get("method") for m in child.sent()]
        assert methods[-1] == SESSION_CANCEL

    def test_c_g_with_a_pending_prefix_cancels_prefix_and_turn_together(self) -> None:
        """Round-1 review finding 1, pinned as intended: a prefix peels
        *with* the turn. `C-x C-g` resolves to the same `KeyboardQuit()`
        as a bare `C-g` (`keys.py:91–97`), so the event carries no prefix
        provenance and the pump cannot — and should not — distinguish:
        Emacs's own `C-g` also quits from a prefix. The user escaping a
        half-typed chord mid-turn cancels the turn too; the peeling order
        in the registry row says so."""
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        child = _handshake(pump, harness, port)

        harness.send("C-x")  # a pending prefix, not a prompt
        outcome = harness.send("C-g")
        assert outcome is not None
        pump.after_command(outcome, harness)

        methods = [m.get("method") for m in child.sent()]
        assert methods[-1] == SESSION_CANCEL
