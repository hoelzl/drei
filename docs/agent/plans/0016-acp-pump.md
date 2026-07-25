# Sixteenth slice: the ACP pump (§C.2)

**Status:** ready (issue #40).

**Architecture gate:** design `0005-acp-pump.md` — **D1** (the streaming port),
**D3** (serialization and fairness), **D6** (child lifecycle and failure), and
the agent event kinds **D2** listed but slice 15 deliberately did not build.
D5 (cancellation) stays out; see D9 below.

**Goal:** make the five merged ACP slices reachable from the shipped editor.
Today `drei` can open a file and split a window; it cannot talk to an agent,
because nothing spawns a long-lived child, nothing feeds the decoder, and the
`Response` that `apply_permission_decision` returns has no sender. After this
slice, `C-c a`, a line of text, and `RET` produce a real `hermes acp` child, a
real `session/prompt`, and the agent's streamed answer accumulating in an agent
buffer on screen — with permission requests presented and *answered*.

This is the payoff slice for **TD-2**. It does not close the entry: the
cancellation half stays, and TD-2 is edited rather than removed.

## What exists today (the delta is nameable)

- `run_editor` consumes `InputSource.next_event()` (slice 15). `InputEvent` is
  `Key | Resize`, and `input.py` says in as many words that `AgentBytes` joins
  in §C.2.
- `ProcessPort.run` is run-to-completion and carries a `TODO: [tech-debt]`
  saying it cannot speak to a long-lived child. Design 0005 D1 keeps it exactly
  as it is.
- `drei.acp` is complete and pure: `JsonRpcDecoder` is chunk-safe and documents
  that "the §C streaming pump feeds whatever the pipe delivered";
  `machine.handle` returns `(machine, outbound messages, session effects)`;
  `start`/`new_session`/`prompt`/`cancel` build the four client requests.
- `EditorSession` already has every command the pump needs to *reach* the
  editor: `apply_session_effects` (one atomic dispatch since slice 15),
  `CreateAgentBuffer` (binds one agent buffer per ACP session, design 0004 D1),
  `PromptPermission`, `AbortPendingPermissions`, and `InsertAgentText` — which
  exists precisely for "append text without advancing a transcript fold".
- `PermissionDecided(request_id, decision)` is already an **event**, so the
  human's answer already crosses back out of the session as data. Nothing reads
  it.
- The minibuffer already has a `_minibuffer_kind` discriminator
  (`find-file` / `switch-buffer`) and a text-accept path.

So the missing pieces are a transport, a producer, and roughly two hundred
lines of orchestration. Almost nothing in the deterministic core changes.

## Design decisions

### D1. The streaming port, and one departure from 0005's illustrative shape

```text
StreamingProcessPort.spawn(argv, *, cwd) -> AgentProcess
AgentProcess.write(data: bytes) -> None
AgentProcess.read(size: int) -> bytes      blocking; b"" at end of stream
AgentProcess.read_stderr(size: int) -> bytes
AgentProcess.poll() -> int | None
AgentProcess.terminate() -> None           cooperative, then hard kill
```

0005 sketched `read_available()` — "bytes ready now, never blocking on more".
**This slice picks a blocking `read` instead**, which the record explicitly
permits ("the slice picks the signatures from its tests"), for one reason: a
never-blocking read has no way to wait, so every caller must invent a poll
interval, and the pump's caller is a thread that is *allowed* to block. The
non-blocking shape would put a clock dependency into the transport to serve a
consumer that does not want one. `read` blocking until at least one byte (or
end of stream) is the same contract `TerminalPort.read_key` already has, and
the same reader-thread pattern slice 15 shipped. Amend 0005 D1 in this change.

`terminate()` is cooperative-then-hard because a `hermes acp` child holding a
pipe outlives a killed parent on both platforms; 0005 D6 already requires it in
`run_editor`'s `finally`.

### D2. Two new event kinds, both with producers in this slice

```text
InputEvent = Key | Resize | AgentBytes(data: bytes) | AgentExited(status: int | None)
```

`AgentBytes` carries bytes, not frames: framing is the decoder's job and the
decoder is chunk-safe by construction. `AgentExited.status` is `int | None`
because a child that vanished between the read returning `b""` and the `poll()`
can legitimately have no status yet on Windows; `None` means "gone, status
unknown", and it is rendered as such rather than guessed.

### D3. One queue, two lanes — and the lanes are exactly the marker rule

0005 D3 requires keys ahead of agent bytes. A plain FIFO cannot do that, so the
queue gets two lanes and `next_event` drains the priority lane first.

The interesting part is *where the line falls*. The priority lane is not
"keys"; it is **`Key` and `Resize`** — and that is precisely the set of events
that carry a readiness marker, for the same underlying reason. An event in the
priority lane is one the *verifier dispatched* and is waiting on; an event in
the other lane arrived on the peer's schedule and belongs to no input epoch.
One distinction, two consequences: fairness and evidence. The plan records this
because the coincidence is load-bearing — if a later slice adds an event kind,
"does the verifier dispatch it?" answers both questions at once.

Starvation is bounded and deliberate: agent bytes wait only while keys keep
arriving, and a human cannot type indefinitely. The reverse — a paragraph of
streamed text delaying a keystroke — is the one 0005 rules out, because it is
the one a user feels.

### D4. The queue is the seam; producers attach to it

Slice 15 put the queue inside `ThreadedTerminalSource`. The agent reader must
push into the *same* queue, so the queue becomes a value in its own right:

```text
EventQueue(InputSource)        put(event) / fail(error) / next_event() / close()
TerminalReaders(port, queue)   the two threads slice 15 shipped
AgentReaders(process, queue)   stdout -> AgentBytes/AgentExited, stderr -> diagnostics
```

`EventQueue` lives in `input.py` — the module that exists so the terminal side
and the agent side can share a vocabulary without importing each other. It owns
no thread and no clock; it is a lane-aware mailbox. The `_ReaderFailed`
sentinel slice 15 added (a dead reader must not look like a quiet one) becomes
`EventQueue.fail`, shared by both producers, because the failure mode it fixes
is a property of the queue, not of the terminal.

`ThreadedTerminalSource` is therefore renamed and narrowed rather than
extended. Its docstring's ownership claims survive intact.

### D5. The pump drains, folds, and delivers exactly once

On `AgentBytes(data)`:

1. `decoder.feed(data)`; `decoder.messages()` drains **every** complete frame.
2. Each frame is parsed (`parse_message`) and folded through `machine.handle`,
   accumulating outbound messages and session effects across the whole drain.
3. Outbound messages are written to the child immediately — they are the
   protocol's answers (fs/terminal refusals today), and a delayed answer is a
   stalled agent.
4. The accumulated effects become **one** `apply_session_effects` dispatch, so
   a chatty agent costs one redraw per loop iteration, not one per frame
   (0005 D3).

**Effects the pump consumes rather than delivers.** `Initialized` and
`SessionEstablished` are handshake milestones the pump acts on, and they render
to the empty string, so consuming them costs the fold nothing. That matters
because they arrive *before* the agent buffer exists — `CreateAgentBuffer`
needs the ACP session id that `SessionEstablished` carries — and
`apply_session_effects` correctly refuses a delivery with no target buffer
(design 0004 D3). `PermissionRequested` is the opposite: it is delivered
(it renders a transcript line) **and** dispatched as `PromptPermission`, in
that order, so the transcript records the request before the prompt opens.

**Malformed peer input never raises into the loop.** `AcpDecodeError` and
`AcpProtocolError` are caught at the pump boundary and turned into a
`ProtocolError` effect — the same fail-visible discipline the codec and the
machine already follow. A peer is not trusted to be well-formed.

### D6. The permission response actually gets sent

The loop already sees every `CommandOutcome`. After a key is dispatched, the
pump scans its events for `PermissionDecided` and calls the existing
`session.apply_permission_decision(machine, request_id, decision)`, writing the
returned `Response` to the child and delivering the returned
`PermissionResolved` effect.

This is ten lines, and it is the difference between a permission prompt that
works and one that hangs the agent forever. It is in this slice, not the
cancellation slice, because the *request* arrives in this slice: shipping the
prompt without the answer would be shipping the defect TD-2 describes.

### D7. `C-c a` prompts, and the session stays protocol-free

A new `C-c` prefix (Emacs reserves `C-c` for the user and the major mode, which
is exactly what a Drei-specific command is) and one binding, `C-c a`:

- `PromptAgent()` opens the minibuffer with kind `agent-prompt`.
- `MinibufferAccept` on that kind emits **`AgentPromptSubmitted(text)`** and
  nothing else. The session does not know what ACP is, does not hold an
  `AcpMachine`, and does not spawn anything — 0005 D7, unchanged.
- The pump reads `AgentPromptSubmitted` out of the outcome, exactly as it reads
  `PermissionDecided`. Both directions across that seam are events.

Empty input closes the prompt with no event, matching the existing text-accept
arm.

`decode_key` gains `\x03 -> C-c`. Raw mode already clears `ISIG` (POSIX
`tty.setraw`) and `ENABLE_PROCESSED_INPUT` (Windows), so ETX reaches the editor
as a byte rather than a signal. **Owned risk:** if the TermVerify scenario
shows ConPTY intercepting it anyway, the fallback is `C-x a` with a parity row
noting the collision with Emacs's abbrev prefix — decided in V5, not guessed
here.

### D8. Lazy launch, and what happens when there is no agent

0005 D6: spawn on the first `AgentPromptSubmitted`, never at startup, because
`drei file.txt` on a machine with no `hermes` must stay exactly as fast and as
functional as it is today.

- **argv and cwd are injected**, not read from the environment inside the pump:
  `run_editor(agent_argv=..., agent_cwd=...)`, defaulting to `("hermes", "acp")`
  and the CLI's working directory. The `--agent-command` flag is what the fake
  agent uses in the end-to-end test, so the injection point is exercised by the
  evidence rather than existing for it.
- **A failed spawn is a normalized token**, not a traceback:
  `normalize_process_error` already maps `FileNotFoundError` to `not-found`.
  The editor stays alive.
- **Stderr goes to its own generated buffer**, `*agent-stderr*`, via a new
  `CreateGeneratedBuffer(name)` command and the existing `InsertAgentText`.
  This is 0005 D6's "a second generated buffer in 0004's sense". It is in scope
  for a blunt reason: the first thing a real `hermes acp` will do on a
  misconfigured machine is die with a message on stderr, and a pump that
  discards it is a black box exactly when the user needs a window.
- **The child is terminated in `run_editor`'s `finally`**, alongside
  `port.restore()` and `events.close()`.

### D9. What this slice does NOT do

- **No cancellation** (0005 D5). `C-g` still exits the editor, which is the
  blocker 0005 records as needing its own change; overloading an exit key with
  turn cancellation by accident is exactly what that record warns against. A
  user who quits mid-turn gets a terminated child from D8, not a leak.
- **No real-`hermes` smoke scenario** (design 0003 §C.10's gated half). The
  fake agent proves the wire; the real binary behind an availability check is
  its own slice, mirroring the pinned-Emacs pattern.
- **No echo-area reporting of agent status** (TD-4). A spawn failure is
  visible in the agent buffer, not in the echo row, because Drei has no
  message mechanism and inventing one here would entrench the ad-hoc shape
  TD-4 exists to prevent.
- **No fix for TD-10.** A pump that streams into a two-row frame still drops
  the echo row first.
- **No backpressure toward the agent** (0005 open question, trigger: measured
  against a real `hermes acp`).

## Verification (0005's four layers)

1. **Loop and pump semantics — scripted events, no threads.** The pump takes an
   injectable reader-starter, so tests drive it with a *synchronous* drainer
   that reads a fake `AgentProcess` to end-of-stream and enqueues the events
   immediately. Same shape as slice 15's `SynchronousTerminalSource`, and the
   same reason: the interesting cases must not be timing-dependent. This is
   where the interleavings live — a delivery while a text prompt is open, a key
   during a burst, a permission request answered, a child exiting mid-turn, a
   malformed frame, a second turn reusing the session.
2. **The queue and the readers — the concurrency tests.** The lane rule proved
   directly (`AgentBytes` queued first, a `Key` queued second, the key comes
   out first), and one thread-starting test per reader failure mode, mirroring
   the four slice 15 ended up needing after its review.
3. **The port and a real child.** `SystemStreamingProcessPort` against a small
   Python child: bytes written arrive, bytes read come back in order, stderr is
   separate, `poll()` reports the status, `terminate()` ends it.
4. **End to end.** A fake ACP agent (a script speaking the pinned 0.9.0 wire)
   driven through the *shipped* `drei` executable: `C-c a`, a prompt, `RET`,
   and the agent's text in the frame. Gated on nothing but ConPTY's platform.

**The readiness-marker gap is reached here, one slice after 0005 predicted.**
An agent delivery is a redraw the verifier did not dispatch, so it carries no
marker (D3's lane rule, stated from the other side). The end-to-end scenario
therefore cannot wait on quiescence for the agent's text and must wait on frame
content with an explicit deadline — weaker evidence than Drei uses anywhere
else, and the only such place in the suite. `AGENTS.md`'s obligation is to
reduce it to a concrete test and take it to TermVerify as its own issue rather
than invent a private marker: the concrete test is the scenario itself, and the
shape to propose upstream is a second marker kind for non-input-driven
quiescence. **This slice must not add a Drei-private marker**, however
tempting, because a marker the verifier's epoch counter does not know about
would corrupt every epoch after it — the exact failure slice 15 demonstrated
from the opposite direction.

## Pins that change

1. `tests/test_terminal.py` injects `SynchronousTerminalSource` at 15 sites and
   `ThreadedTerminalSource` in four failure tests. The rename to
   `TerminalReaders` + `EventQueue` touches those four; the 15 are untouched
   because the synchronous source does not change.
2. `input.py`'s comment "the union grows only when a producer exists …
   `AgentBytes`/`AgentExited` join in §C.2" becomes true and must be rewritten
   rather than left as a promise.
3. `process.py`'s `TODO: [tech-debt]` on `ProcessPort` says the streaming port
   does not exist yet. After D1 it does; the marker is re-scoped to what TD-2
   still owns, not deleted (the port stays run-to-completion **by design**, and
   that half of the comment is a decision, not a debt).
4. `session.py`'s `apply_permission_decision` docstring carries a
   `TODO: [tech-debt]` reading "no pump exists, so no caller ever sends it".
   D6 makes that false; the marker goes.

## Implementation order (vertical slices, strict TDD)

1. **V1 — the streaming port.** Protocols plus `SystemStreamingProcessPort`,
   proven against a real Python child (layer 3). No editor code touched.
2. **V2 — event kinds and the two-lane queue.** `AgentBytes`/`AgentExited`,
   `EventQueue` with the priority lane, `TerminalReaders` refactored onto it,
   `AgentReaders` over a fake process. The lane rule and the reader-failure
   tests (layer 2). The loop grows an `AgentExited` arm but no pump yet.
3. **V3 — the pump.** `AgentPump` owning machine, decoder, and process:
   drain-then-deliver-once, outbound writes, handshake milestones consumed,
   `PermissionRequested` delivered *and* prompted, `PermissionDecided`
   answered, malformed input degraded to `ProtocolError`. All layer 1.
4. **V4 — the key and the launcher.** `C-c` prefix, `PromptAgent`,
   `agent-prompt` minibuffer kind, `AgentPromptSubmitted`, lazy spawn,
   `CreateGeneratedBuffer` + stderr buffer, terminate in `finally`, CLI flag.
5. **V5 — end to end.** The fake ACP agent script, the integration test over
   the real port, and the TermVerify scenario through the shipped executable.
   Settle D7's `C-c` risk here.
6. **V6 — adversarial review → fix → code PR (`Closes #40`) → merge.**

## Acceptance criteria

- Typing `C-c a`, a prompt, and `RET` in the shipped executable spawns the
  configured agent, completes the ACP handshake, and renders the agent's
  streamed answer into an agent buffer — proven through ConPTY against a fake
  0.9.0 agent, not only in-process.
- One `AgentBytes` event carrying **n** complete frames produces exactly one
  `AgentTranscriptUpdated` and at most one redraw (0005 D3).
- A `Key` enqueued after an `AgentBytes` is returned by `next_event` first.
- A `session/request_permission` from the agent opens a choice prompt, and the
  human's answer is **written to the child** as the 0.9.0 response — the
  property TD-2 says is missing today.
- A child that exits mid-turn leaves the editor usable: the transcript records
  the exit, the machine is `DISCONNECTED`, pending prompts are swept, and the
  next `C-c a` starts a fresh child.
- Killing the editor mid-turn leaves no child process behind.
- `drei file.txt` on a machine with no `hermes` behaves exactly as it does
  today; a failed spawn produces a normalized token, not a traceback.
- The deterministic core is unchanged in kind: no `subprocess`, `asyncio`, or
  thread in `drei.acp` or in `EditorSession`, and the session still holds no
  `AcpMachine`. The purity tests are the gate.
- Full quality gate green on 3.12–3.14 and both CI OSes; coverage floor held
  at 100%.
- TD-2 is **edited, not removed** — cancellation is what remains.

## Risks / open questions

- **This is the biggest slice so far**, and its risk is breadth, not depth:
  five layers (port, queue, pump, keymap, launcher) each small. The V-order is
  chosen so every step but V4 is independently testable before the next
  exists, and V1–V3 touch no user-visible behavior at all.
- **The handshake is a state machine driven by an event loop**, which is the
  one genuinely new control shape here: a prompt typed before the child is
  ready must be held and sent after `SessionEstablished`. V3 pins the held
  prompt explicitly; getting it wrong looks like a prompt that silently never
  runs.
- **`C-c` may not survive ConPTY.** Owned in D7 with a decided fallback.
- **The fake agent script is a second implementation of the peer**, and a fake
  that drifts from the real wire proves nothing. Mitigation: it is a *thin*
  script that replays pinned 0.9.0 frames the existing `tests/acp` suite
  already asserts against, not a general-purpose agent.
- **Non-UTF-8 bytes on the wire.** The decoder raises `AcpDecodeError` on a
  `UnicodeDecodeError` already, so D5's boundary catch covers it — but a
  chunk boundary splitting a multi-byte character must *not* be an error. The
  decoder buffers bytes and decodes per line, so it is already correct; V3
  should pin it rather than assume it.
