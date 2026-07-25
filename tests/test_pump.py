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
from drei.acp.messages import (
    SESSION_REQUEST_PERMISSION,
    SESSION_UPDATE,
    JsonValue,
)
from drei.commands import AgentTranscriptUpdated, BufferCreated
from drei.harness import EditorHarness
from drei.input import (
    AgentBytes,
    AgentExited,
    AgentStderr,
    EndOfInput,
    EventQueue,
)
from drei.pump import AgentPump, AgentReaders
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


class _NoReaders:
    """The reader-thread stand-in for layer 1: the test feeds bytes itself."""

    def close(self) -> None:
        pass


def _pump(port: FakeStreamingPort) -> AgentPump:
    return AgentPump(
        port,
        EventQueue(),
        argv=("fake-agent",),
        cwd="/work",
        start_readers=lambda process, events: _NoReaders(),
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

        pump.receive(
            _frames(
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "fs/read_text_file",
                    "params": {"sessionId": "sess-1", "path": "/etc/passwd"},
                }
            ),
            harness,
        )

        answer = child.sent()[-1]
        assert answer["id"] == 11
        assert "error" in answer


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
        port = FakeStreamingPort()
        pump = _pump(port)
        harness = EditorHarness(width=40, height=8)
        _handshake(pump, harness, port)

        pump.receive(_frames(_permission_request(7, "sess-1")), harness)

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


class TestAgentReaders:
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
        readers = AgentReaders(ScriptedChild(), stream)
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
        readers = AgentReaders(NoisyChild(), stream)
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
        readers = AgentReaders(BrokenChild(), stream)
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
        readers = AgentReaders(BuggyChild(), stream)
        try:
            with pytest.raises(AssertionError, match="drei is broken"):
                stream.next_event()
        finally:
            readers.close()

    def test_a_reader_closed_mid_stream_stops_after_the_chunk_in_hand(self) -> None:
        """`close` is checked between reads, not during one: a reader parked in
        a blocking read finishes that read, delivers what it got, and only then
        notices. It must not then report an exit — shutdown is not a peer
        failure."""
        stream = EventQueue()
        handle: list[AgentReaders] = []
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

        readers = AgentReaders(ClosingChild(), stream)
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
        readers = AgentReaders(NoisyBug(), stream)
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
        readers = AgentReaders(ParkedChild(), stream)
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
