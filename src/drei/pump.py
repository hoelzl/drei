"""The ACP pump: the child process, the machine, and the editor, joined.

Design 0005 D3 (serialization and fairness), D6 (child lifecycle and failure),
and D7 (the pump owns the machine; the session stays machine-free). This is
the adapter that makes the five merged ACP slices reachable: it holds the
`AcpMachine` and the `JsonRpcDecoder`, spawns the child on the first prompt,
and turns bytes into editor commands.

Everything here is on the *transport* side of the boundary. The pump never
mutates editor state directly — it dispatches commands through the harness,
one at a time, so the session still sees nothing but a serialized sequence of
commands and the transcript still records every state change.

Nothing here imports `subprocess`: the child comes from an injected
:class:`~drei.streaming.StreamingProcessPort`. The reader threads are here
too — `AgentReaders` is the agent-side twin of `TerminalReaders` — but the
pump takes them as a parameter, so every test in `test_pump.py` drives it with
scripted bytes and no thread at all.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Protocol

from drei.acp.codec import AcpDecodeError, JsonRpcDecoder, encode
from drei.acp.machine import (
    AcpMachine,
    AcpStateError,
    Initialized,
    PermissionRequested,
    PromptCompleted,
    ProtocolError,
    SessionEffect,
    SessionEstablished,
    handle,
    new_session,
    prompt,
    resolve_permission,
    start,
)
from drei.acp.messages import (
    AcpProtocolError,
    JsonValue,
    Message,
    parse_message,
    to_json,
)
from drei.commands import (
    AbortPendingPermissions,
    AgentPromptSubmitted,
    CommandOutcome,
    CreateAgentBuffer,
    CreateGeneratedBuffer,
    DeliverSessionEffects,
    DisplayBuffer,
    InsertAgentText,
    PermissionDecided,
    PromptPermission,
)
from drei.harness import EditorHarness
from drei.input import AgentBytes, AgentExited, AgentStderr, EventQueue
from drei.model import BufferId
from drei.process import normalize_process_error
from drei.streaming import AgentProcess, StreamingProcessPort

DIAGNOSTICS_BUFFER = "*agent-log*"

# Design 0003's launcher target. Overridable so the end-to-end evidence can
# drive a fake 0.9.0 peer through the same code path the real binary uses,
# rather than through a seam that exists only for tests.
DEFAULT_AGENT_ARGV = ("hermes", "acp")


class AgentChannel(Protocol):
    """Everything that talks to the child, for as long as the child lives."""

    def write(self, data: bytes) -> None: ...
    def close(self) -> None: ...


class AgentIo:
    """The agent child's three threads: two readers and a writer.

    The readers are symmetric with :class:`~drei.terminal.TerminalReaders`, and
    for the same reason: there is no portable way to wait on a pipe and a
    console handle at once, so each gets a thread that is allowed to block, and
    they meet on the one shared queue.

    **The writer exists so the loop cannot block.** A pipe write blocks once
    the kernel buffer is full and the peer is not draining it — measured at one
    4 KB chunk against a child that never reads. On the loop's thread that
    means the editor stops consuming input entirely, with the terminal still in
    raw mode and `C-g` unable to reach it, which is precisely the wedge slice
    15's review found in the reader. Drei's own writes are small, but the
    number of them is peer-driven: the machine answers every `fs/*` and
    `terminal/*` request the agent sends. So writes are queued and drained by a
    thread that is allowed to block instead.

    One asymmetry with the terminal, deliberate. A terminal reader that dies
    calls ``EventQueue.fail``, because an editor that cannot be typed into is
    over. A pipe error here is instead reported as ``AgentExited``: the agent
    is optional, the child dying is an ordinary peer failure, and killing the
    editor because a pipe closed would be the wrong trade. An exception that is
    *not* a pipe error still fails the queue — that is Drei being broken, not
    the peer.
    """

    def __init__(self, process: AgentProcess, events: EventQueue) -> None:
        self._process = process
        self._events = events
        self._stopped = threading.Event()
        self._outbox: queue.Queue[bytes | None] = queue.Queue()
        self._wire = threading.Thread(
            target=self._read_wire, name="drei-agent-wire", daemon=True
        )
        self._errors = threading.Thread(
            target=self._read_stderr, name="drei-agent-stderr", daemon=True
        )
        self._writer = threading.Thread(
            target=self._write_loop, name="drei-agent-writer", daemon=True
        )
        self._wire.start()
        self._errors.start()
        self._writer.start()

    def write(self, data: bytes) -> None:
        """Hand ``data`` to the writer thread. Never blocks the caller."""
        self._outbox.put(data)

    def close(self) -> None:
        """Stop reporting and release the writer.

        No thread is joined: the readers are parked in a blocking read that
        only the child's exit can end, and the pump terminates the child
        immediately after calling this. Daemons, like the key reader, for the
        same reason. The writer gets a sentinel so it stops even if it is idle.
        """
        self._stopped.set()
        self._outbox.put(None)

    def _write_loop(self) -> None:
        while True:
            data = self._outbox.get()
            if data is None or self._stopped.is_set():
                return
            try:
                self._process.write(data)
            except (OSError, ValueError):
                # The child is gone. The wire reader is the one that reports
                # that, so this thread just stops: reporting it twice would
                # put two exits in the transcript for one death.
                return
            except Exception as error:
                self._events.fail(error)
                return

    def _read_wire(self) -> None:
        try:
            while not self._stopped.is_set():
                data = self._process.read()
                if not data:
                    break  # end of stream: the child is gone
                self._events.put(AgentBytes(data))
        except (OSError, ValueError):
            # OSError: the pipe broke. ValueError: it was closed under us by a
            # concurrent shutdown. Both mean the child is gone.
            pass
        except Exception as error:
            self._events.fail(error)
            return
        if not self._stopped.is_set():
            # Only the wire reader reports the exit, so it is reported once.
            self._events.put(AgentExited(self._process.poll()))

    def _read_stderr(self) -> None:
        try:
            while not self._stopped.is_set():
                data = self._process.read_stderr()
                if not data:
                    return
                self._events.put(AgentStderr(data))
        except (OSError, ValueError):
            pass
        except Exception as error:
            self._events.fail(error)


class DirectAgentChannel:
    """Writes straight through on the caller's thread, and starts nothing.

    The test-side channel (design 0005's verification layer 1): the pump's
    behavioural tests feed bytes themselves, so there is nothing to read, and a
    synchronous write keeps the ``OSError`` path — a write to a dead child —
    observable where it is raised.
    """

    def __init__(self, process: AgentProcess) -> None:
        self._process = process

    def write(self, data: bytes) -> None:
        self._process.write(data)

    def close(self) -> None:
        """Nothing to release: this channel owns no thread and no queue."""


class AgentPump:
    """Drives one agent at a time: spawn, handshake, turn, teardown.

    The pump is constructed once per editor run and spawns nothing until the
    first prompt (design 0005 D6). A machine with no agent installed pays
    exactly the cost of this object.
    """

    def __init__(
        self,
        port: StreamingProcessPort,
        events: EventQueue,
        *,
        argv: tuple[str, ...],
        cwd: str | None = None,
        start_channel: Callable[[AgentProcess, EventQueue], AgentChannel],
    ) -> None:
        self._port = port
        self._events = events
        self._argv = argv
        self._cwd = cwd
        self._start_channel = start_channel
        self._machine = AcpMachine()
        self._decoder = JsonRpcDecoder()
        self._process: AgentProcess | None = None
        self._channel: AgentChannel | None = None
        # Prompts the user has submitted that the protocol cannot carry yet:
        # before the session exists, or while a turn is in flight. A list, not
        # a slot — overwriting would swallow the second of two prompts typed
        # during one turn, and the user pressed RET with nothing on screen to
        # say it went nowhere.
        self._pending_prompts: list[str] = []
        self._transcript: BufferId | None = None
        self._diagnostics: BufferId | None = None
        # Effects produced before the transcript buffer exists. The handshake
        # is two round trips long, and anything that goes wrong inside it —
        # a malformed frame, an out-of-phase notification, a child that dies —
        # produces an effect with nowhere yet to go. Dropping those would
        # hide precisely the failures that happen most: the early ones.
        self._backlog: list[SessionEffect] = []

    @property
    def phase(self) -> str:
        """The machine's protocol phase. Transport state, not editor state."""
        return self._machine.phase

    # -- the loop's four entry points ---------------------------------------

    def submit(self, text: str, harness: EditorHarness) -> None:
        """The human pressed RET on an agent prompt."""
        if self._process is None and not self._spawn(harness):
            return
        self._pending_prompts.append(text)
        self._send_pending(harness)

    def receive(self, data: bytes, harness: EditorHarness) -> None:
        """Wire bytes arrived from the child.

        Drains *every* complete frame the bytes finished, folds them all
        through the machine, and delivers the accumulated effects as **one**
        dispatch (0005 D3): a chatty agent costs one transcript event and one
        redraw per loop iteration, not one per frame.
        """
        if self._process is None:
            return
        self._decoder.feed(data)
        effects: list[SessionEffect] = []
        for frame in self._drain(effects):
            try:
                message = parse_message(frame)
            except AcpProtocolError as error:
                # Valid JSON, invalid envelope. The peer is untrusted input,
                # so this is a transcript line rather than a traceback.
                effects.append(ProtocolError(detail=str(error)))
                continue
            self._machine, outbound, produced = handle(self._machine, message)
            for reply in outbound:
                self._write(reply, harness)
            effects.extend(produced)
        self._apply(effects, harness)

    def exited(self, status: int | None, harness: EditorHarness) -> None:
        """The child is gone (0005 D6): a peer failure, not a crash.

        Rendered into the transcript in the same shape as a ``ProtocolError``,
        the machine back to ``DISCONNECTED``, and any prompt the dead child
        asked for swept — an open choice prompt for a peer that no longer
        exists can never be answered, and leaving it up would wedge the
        editor's input in choice mode.
        """
        detail = "status unknown" if status is None else f"status {status}"
        self._deliver([ProtocolError(detail=f"agent exited ({detail})")], harness)
        harness.apply(AbortPendingPermissions())
        self._reset(harness)

    def diagnostics(self, data: bytes, harness: EditorHarness) -> None:
        """Child stderr. Never enters the wire decoder (0005 D6)."""
        self._log(data.decode("utf-8", errors="replace"), harness)

    def after_command(self, outcome: CommandOutcome, harness: EditorHarness) -> None:
        """Act on what a just-dispatched command asked of the agent.

        Both directions across the session/pump seam are *events* (design 0005
        D7): the session records that the user submitted a prompt or answered a
        permission request, and the pump reads that out of the outcome. The
        session never holds a machine and never learns what ACP is.

        The permission half is the one TD-2 names: `resolve_permission` has
        always produced the exact 0.9.0 response, and nothing ever put it on
        the wire, so a real agent asking permission waited forever.
        """
        for event in outcome.events:
            match event:
                case AgentPromptSubmitted(text=text):
                    self.submit(text, harness)
                case PermissionDecided(request_id=request_id, decision=decision):
                    try:
                        self._machine, responses, resolved = resolve_permission(
                            self._machine, request_id, decision
                        )
                    except AcpStateError as error:
                        # The machine no longer tracks the request — a child
                        # that died between the prompt opening and the human
                        # answering. Every other entry point degrades to a
                        # transcript line; this one must too, or answering a
                        # stale prompt takes the editor down.
                        self._deliver([ProtocolError(detail=str(error))], harness)
                        continue
                    for response in responses:
                        self._write(response, harness)
                    self._deliver(resolved, harness)
                case _:
                    pass

    def close(self) -> None:
        """Terminate the child, if there is one (0005 D6).

        Runs in ``run_editor``'s ``finally`` alongside ``port.restore()``: a
        leaked child holding a pipe is worse than a garbled terminal.
        """
        if self._channel is not None:
            self._channel.close()
            self._channel = None
        if self._process is not None:
            self._process.terminate()
            self._process = None

    # -- internals ----------------------------------------------------------

    def _spawn(self, harness: EditorHarness) -> bool:
        try:
            process = self._port.spawn(self._argv, cwd=self._cwd)
        except OSError as error:
            # The same normalized vocabulary `run_process` reports: raw
            # exception text is platform- and locale-dependent, and `drei` on
            # a machine with no agent installed must stay a usable editor.
            token = normalize_process_error(error)
            self._log(
                f"agent launch failed: {token} ({' '.join(self._argv)})\n", harness
            )
            return False
        self._process = process
        self._channel = self._start_channel(process, self._events)
        self._machine, request = start(self._machine)
        self._write(request, harness)
        return True

    def _drain(self, effects: list[SessionEffect]) -> list[JsonValue]:
        try:
            return self._decoder.messages()
        except AcpDecodeError as error:
            # The offending line is consumed and frames already parsed from
            # the same drain are retained for the next call, so nothing valid
            # is lost by reporting this and moving on.
            effects.append(ProtocolError(detail=str(error)))
            return []

    def _apply(self, effects: list[SessionEffect], harness: EditorHarness) -> None:
        """Split the drain's effects into what the pump acts on and what the
        transcript receives.

        ``Initialized`` and ``SessionEstablished`` are handshake milestones:
        the pump answers them with the next request, and they render to the
        empty string, so consuming them costs the fold nothing. That matters
        because they arrive *before* the agent buffer exists — the buffer is
        minted from the session id the second one carries.
        """
        deliverable: list[SessionEffect] = []
        requests: list[PermissionRequested] = []
        for effect in effects:
            match effect:
                case Initialized():
                    self._machine, request = new_session(
                        self._machine, self._cwd or "."
                    )
                    self._write(request, harness)
                case SessionEstablished(session_id=session_id):
                    self._bind(session_id, harness)
                case PermissionRequested():
                    # Delivered *and* prompted, in that order: the transcript
                    # records that the agent asked before the prompt opens.
                    deliverable.append(effect)
                    requests.append(effect)
                case _:
                    deliverable.append(effect)
        self._deliver(deliverable, harness)
        for request_effect in requests:
            harness.apply(PromptPermission(request_effect))
        # A turn that completed frees the protocol for a prompt the user
        # submitted while it was still running.
        if any(isinstance(effect, PromptCompleted) for effect in effects):
            self._send_pending(harness)

    def _bind(self, session_id: str, harness: EditorHarness) -> None:
        harness.apply(CreateAgentBuffer(session_id))
        self._transcript = harness.agent_buffer_id(session_id)
        assert self._transcript is not None  # the command just minted it
        # Show it. A transcript that exists but is nowhere on screen is a
        # feature the user cannot use — `C-c a` would send a prompt and
        # nothing visible would happen. Focus stays where the user put it.
        harness.apply(DisplayBuffer(self._transcript))
        held, self._backlog = self._backlog, []
        self._deliver(held, harness)
        self._send_pending(harness)

    def _send_pending(self, harness: EditorHarness) -> None:
        """Send the oldest held prompt if the protocol can carry one now.

        ACP allows one prompt per turn, and a session id is needed before the
        first. A prompt submitted at any other moment waits here rather than
        raising out of the machine; the rest of the queue goes out one per
        completed turn.
        """
        if not self._pending_prompts or self._machine.phase != "SESSION_ACTIVE":
            return
        text = self._pending_prompts.pop(0)
        self._machine, request = prompt(self._machine, text)
        # Through `_write`, not `_write_raw`: a child that died between the
        # handshake and this call would otherwise raise BrokenPipeError out of
        # the pump and take the editor with it.
        self._write(request, harness)

    def _deliver(self, effects: list[SessionEffect], harness: EditorHarness) -> None:
        if not effects:
            return
        if self._transcript is None:
            self._backlog.extend(effects)
            return
        harness.apply(DeliverSessionEffects(tuple(effects), self._transcript))

    def _write(self, message: Message, harness: EditorHarness) -> None:
        try:
            self._write_raw(message)
        except OSError as error:
            # The child is gone; its reader will report the exit. What must
            # not happen is the editor dying because a pipe closed.
            self._log(f"agent write failed: {error}\n", harness)

    def _write_raw(self, message: Message) -> None:
        if self._channel is None:  # pragma: no cover - guarded by every caller
            return
        self._channel.write(encode(to_json(message)))

    def _log(self, text: str, harness: EditorHarness) -> None:
        if self._diagnostics is None:
            harness.apply(CreateGeneratedBuffer(DIAGNOSTICS_BUFFER))
            self._diagnostics = harness.generated_buffer_id(DIAGNOSTICS_BUFFER)
            assert self._diagnostics is not None  # the command just minted it
        harness.apply(InsertAgentText(text, self._diagnostics))

    def _reset(self, harness: EditorHarness) -> None:
        """Back to DISCONNECTED, keeping the buffers the run has accumulated.

        The transcript and the diagnostics stay on screen — they are the
        record of what happened — but the machine, the decoder, and the child
        are all replaced, so the next prompt starts a genuinely fresh peer
        rather than writing to a pipe nobody is reading.
        """
        self.close()
        if self._backlog:
            # The child died before a session existed, so these effects have
            # no transcript they could belong to. They are the only account of
            # why the run failed, so they go where the other account of a
            # failed child goes.
            held, self._backlog = self._backlog, []
            self._log("".join(f"{effect}\n" for effect in held), harness)
        if self._pending_prompts:
            # Say so. A prompt the user typed and never got an answer to is
            # exactly the silence the held-prompt queue exists to avoid.
            dropped = ", ".join(repr(text) for text in self._pending_prompts)
            self._log(f"agent gone; unsent prompts dropped: {dropped}\n", harness)
            self._pending_prompts = []
        self._machine = AcpMachine()
        self._decoder = JsonRpcDecoder()
        self._transcript = None
