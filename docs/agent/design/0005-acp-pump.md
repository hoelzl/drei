# 0005: The ACP pump (design 0003 §C)

**Status:** implemented except **D5** (cancellation). D2 (the injection point)
and D4 (atomic delivery) shipped in plan `0015-input-events-and-resize.md`;
D1 (streaming port), D3 (fairness), D6 (launcher and lifecycle) and D7 (the
pump owns the machine) shipped in plan `0016-acp-pump.md`. D5 stays proposed
and is blocked on the `C-g` overload recorded in it. Where a slice departed
from this record, the departure is noted in place below.
**Builds on:** `0003-hermes-drei-integration.md`, `0004-agent-buffer-identity.md`
**Does not revise:** 0001/0002/0003. It supplies the §C boundary 0003 assumed
but never placed, and amends one 0003 §A.1 sentence (see *Amendments*).
**Occasioned by:** adversarial review 0001 finding 11 (`docs/technical-debt.md`
TD-2) — five merged slices are unreachable from the shipped editor and the
record that would connect them does not exist.

## Problem

Slices 0008–0011 and 0013 built a subprocess port, an NDJSON codec, a session
state machine, `session/update` translation, and an approval bridge. All of it
is pure, tested, and **unreachable**: `EditorHarness` accepts no process port,
`run_editor`'s loop blocks in `read_key()` with no other input source,
`keys.py` binds no agent command, and `apply_permission_decision` returns a
`Response` that nobody sends. A real agent asking permission would block
forever, because there is no code path that could answer it.

The root cause is a missing boundary, and its absence explains three separate
review findings (5, 9, 10) that each looked like a local bug. Nothing in the
project says where asynchrony enters the editor loop, how a delivery is
ordered against a keystroke, or who owns the machine. Every slice that touched
the area had to invent an answer locally and defer the rest.

Two facts make this a design decision rather than an implementation detail:

- **The A.1 port cannot do it.** `ProcessPort.run` is blocking
  run-to-completion by deliberate scoping (plan 0008 chose it over streaming
  to avoid a speculative framework layer). It cannot speak to a long-lived
  `hermes acp` child.
- **The loop has no second input.** `run_editor` is a synchronous
  read-key/dispatch/draw loop. Agent bytes arrive on a pipe on their own
  schedule. Something must merge two sources without making editor semantics
  depend on timing — which the non-negotiable rules forbid.

## Decision

**Asynchrony enters through one totally ordered input-event queue; the
deterministic core still sees nothing but a sequence of commands.**

### D1. A streaming port, separate from `ProcessPort`

A new effect port owns the long-lived child. It does **not** replace or widen
`ProcessPort`: `run(argv) -> ProcessResult` stays exactly as it is, because
conflating "run a tool and collect its output" with "hold a conversation with
a child process" would make the simple, heavily-used port carry lifecycle
concerns it does not need. Two ports at the same architectural level, like
`FilePort` and `TerminalPort`.

Shape (illustrative — the slice picks the signatures from its tests):

```text
StreamingProcessPort.spawn(argv, cwd) -> AgentProcess
AgentProcess.write(bytes)            send a framed request
AgentProcess.read_available()        bytes ready now, never blocking on more
AgentProcess.read_stderr_available() diagnostics, kept separate from the wire
AgentProcess.poll() -> int | None    exit status once the child is gone
AgentProcess.terminate()             cooperative shutdown, then hard kill
```

Bytes, not lines: the codec (`JsonRpcDecoder`) is already chunk-safe and
explicitly documents that the pump feeds it "whatever the pipe delivered".
The port does no framing.

**Amended by plan 0016 V1: `read` blocks.** The shape above was illustrative
and this record left the signatures to the slice with tests. A never-blocking
`read_available` has no way to *wait*, so every caller must invent a poll
interval — and the caller is a reader thread that is allowed to block. The
shipped port is `read(size) -> bytes`, blocking until at least one byte or end
of stream, which is the contract `TerminalPort.read_key` already has and is
read by the same kind of thread. `terminate()` is cooperative-then-hard, and
the escalation is driven by a fake `Popen` because a child that survives a
signal is platform-dependent to provoke and must not go untested.

### D2. The injection point: one event queue, not two blocking reads

`run_editor` stops blocking on `read_key()` and instead consumes a single
ordered stream of input events:

```text
InputEvent = Key(str) | AgentBytes(bytes) | AgentExited(int) | Resize(w, h)
```

The adapter that produces them owns whatever platform mechanism is needed —
concretely a reader thread per source pushing onto a thread-safe queue,
because there is no portable `select` over a Windows console handle and a
pipe. **The threads live entirely in the adapter.** The loop pops one event at
a time; the session sees only commands, in a fixed order.

This is what keeps determinism intact under a nondeterministic peer. The
*interleaving* of keys and agent bytes is genuinely nondeterministic, and no
design can make it otherwise; what the rules require is that editor semantics
not depend on the clock, and that replay be well-defined. Both hold, because
the interleaving is *recorded*: the transcript is the totally ordered sequence
of commands that actually ran, and replaying it reproduces the state. Tests
never run a thread — they feed a scripted `InputEvent` list.

Rejected alternatives:

- **Non-blocking `read_key` with a poll loop.** Burns CPU, invents a polling
  interval (a clock dependency in the input path, which the `KeyAssembler`
  work in review 0001 cluster B just finished removing), and still needs a
  platform-specific wait.
- **Async/await throughout.** Would rewrite the loop, the harness, and every
  test for a system with exactly two input sources, and would put an event
  loop inside the boundary the core is supposed to be free of.
- **Callbacks from a reader thread into the session.** Ambient mutation from
  another thread — exactly what 0002's serialized command boundary forbids.

`Resize` is in the list deliberately: TD-6 (terminal size read once) is the
same missing boundary wearing a different hat, and it costs nothing to give it
a home now rather than inventing a second mechanism later.

### D3. Serialization and fairness

- **One event per iteration.** The loop pops one event, turns it into zero or
  more dispatches, redraws, and returns to the queue. A delivery is never
  interleaved *within* another command's dispatch — the command boundary
  stays serialized, as 0002 requires.
- **Drain, then deliver once.** On `AgentBytes`, the pump feeds the decoder,
  folds *every* frame the drain produced through `AcpMachine.handle`, and
  dispatches **one** delivery carrying all resulting effects. A chatty agent
  therefore costs one redraw per iteration, not one per frame.
- **Keys are never starved.** When both a key and agent bytes are ready, the
  key goes first. A human's keystroke must not queue behind a paragraph of
  streamed text; agent output is by nature bursty and the human is not.

  **Sharpened by plan 0016.** The priority lane is not "keys": it is
  `Key | Resize`, which is *exactly* the set of events a verifier dispatches
  and therefore exactly the set that carries a readiness marker. An event in
  the lane is one somebody is waiting on; an event outside it arrived on the
  peer's schedule and belongs to no input epoch. Fairness and evidence are the
  same distinction, so a later slice adding an event kind answers both
  questions at once by asking "does the verifier dispatch it?".
- **Deliveries bypass the minibuffer gate** — already true and unchanged; a
  swallowed delivery would desync the fold, and a swallowed permission request
  would hang the agent.

### D4. Delivery becomes genuinely atomic

**Implemented in plan 0015 V5.** `apply_session_effects` claimed atomicity and
was two dispatches (`DeliverSessionEffects` then `InsertAgentText`) with an
observable seam: the fold advanced in the first whether or not the second ran.
Nothing exploited the seam only because nothing drove deliveries concurrently
— which the pump was about to change.

One delivery becomes **one dispatch** emitting both `AgentTranscriptUpdated`
and `AgentTextInserted`. `InsertAgentText` survives as a command in its own
right; what goes away is the delivery path's dependence on two of them landing
in sequence. Design 0003's consequence 2 ("the live model applies each
`session/update` as one atomic command/event") then becomes true as written
instead of aspirational.

### D5. Cancellation

`AcpMachine.cancel()` already answers every pending `session/request_permission`
with `cancelled`, and the session already has `AbortPendingPermissions` to
close an open choice prompt and drain the queue. Nothing calls either. The
pump calls both, in that order, on turn cancellation: answer the agent first
(it is blocked), then clear the UI.

Trigger: `C-g` **while a turn is in flight** cancels the turn. Otherwise `C-g`
keeps its current meaning.

**Unblocked by slice 17.** This record used to name `C-g` as the blocking
question: it *exited the editor*, a slice-1 shortcut Emacs does not share, and
overloading an exit key with turn cancellation would have been a bad end
state. Plan `0017-keyboard-quit-and-exit.md` took that as its own change, with
the registry rows it falsified. `C-g` is now `keyboard-quit` and `C-x C-c`
exits, so the trigger above is available and means what it says.

### D6. Child lifecycle and failure

- **Spawn** lazily, on the first agent command — not at startup. Drei must
  remain a usable editor on a machine with no `hermes` installed, and paying a
  subprocess launch for every `drei file.txt` would be a regression in the
  editor's own value.
- **Terminate** in `run_editor`'s existing `finally`, alongside
  `port.restore()`. A leaked child holding a pipe is worse than a garbled
  terminal.
- **Stderr** never enters the wire decoder. It goes to a diagnostics buffer —
  a second generated buffer in 0004's sense, `*agent-log*`, minted by a
  `CreateGeneratedBuffer` command. Launch failures land there too, as the
  normalized token `run_process` reports rather than as a traceback.
- **The transcript is displayed** (plan 0016). A buffer that exists and is
  nowhere on screen is a feature the user cannot use: `C-c a` would send a
  prompt and nothing visible would happen. `DisplayBuffer` splits once if the
  frame holds a single window and the split gate permits, then shows the
  buffer in the window *after* the focused one. Focus never moves — 0004 D1's
  constraint — which is why it is a command of its own rather than part of
  `CreateAgentBuffer`: 0004 owns the buffer's identity, this owns where it is
  seen. A frame too small to split shows nothing and breaks nothing.
- **Unexpected exit** (`AgentExited`) mid-turn is a peer failure, not a crash:
  it is rendered into the transcript in the same shape as a `ProtocolError`,
  the machine returns to `DISCONNECTED`, and any pending permission prompt is
  swept as in D5. The editor stays usable. This is the same fail-visible
  discipline the codec and machine already follow for malformed input.

### D7. The pump owns the machine; the session stays machine-free

`EditorSession` never holds an `AcpMachine`. The existing seam already has
this shape — `apply_permission_decision(machine, …)` takes the machine and
returns it — and it is the right one: the machine is a value on the transport
side of the boundary, and giving the session a field for it would put protocol
phase into editor state, where replay would then have to reproduce it.

## Verification

Layered, so that almost nothing depends on a real agent or a real thread:

1. **Loop semantics** — a fake input source yielding a scripted `InputEvent`
   list. Fully deterministic; covers every interleaving worth naming
   (delivery during a prompt, key during a burst, cancel mid-turn, child exit
   mid-turn). This is where the interesting cases live.
2. **The adapter** — the reader thread proven in isolation: bytes written to a
   pipe appear as `AgentBytes` events in order, and the queue closes on child
   exit. Small, and the only test that touches concurrency.
3. **TermVerify** — a **fake ACP agent** (a small script speaking the pinned
   0.9.0 wire) driven through the shipped executable over ConPTY, so the whole
   path is proven end to end without `hermes` installed. Gated on nothing;
   this should run in the ordinary suite.
4. **Real `hermes acp`** — one smoke scenario behind an availability check,
   skipped when the binary is absent, mirroring the pinned-Emacs pattern.

**The gap below is now reached, and the concrete test exists.**
`tests/termverify/test_shipped_agent.py` drives a fake 0.9.0 agent through the
shipped executable, and it is the one scenario in the suite whose wait is
bounded by wall clock rather than by quiescence — because an agent delivery is
a redraw the verifier did not dispatch, so there is no marker to wait on. The
obligation `AGENTS.md` sets is discharged as far as Drei can discharge it: the
gap is reduced to that test, and what remains is to take a second marker kind
for non-input-driven quiescence to TermVerify as its own issue. Plan 0016
deliberately did **not** invent a private marker; one the verifier's epoch
counter does not know about would corrupt every epoch after it, which is the
failure slice 15 demonstrated from the opposite direction.

**One known evidence gap, recorded rather than glossed.** Readiness markers
(OSC 7791) currently mark quiescence after each *dispatched key*, and
TermVerify counts input epochs. A spontaneous agent delivery has no input
epoch to belong to. The decision is to keep markers bound to input: an agent
delivery redraws the frame **without** emitting a marker, so epoch counting
stays honest for keystrokes. Consequence: an end-to-end agent scenario cannot
wait on "quiescence after agent output" and must wait on frame content with an
explicit deadline. That is weaker evidence than Drei uses everywhere else. Per
`AGENTS.md`, the obligation is to reduce it to a concrete test first and then
take it to TermVerify as its own issue — a second marker kind for
non-input-driven quiescence is the shape to propose. Do not invent a private
marker in Drei.

**Sharpened by plan 0015 V4.** The rule is markers bound to each *dispatched
input*, not to each dispatched **key**. Plan 0015 initially read it the
narrower way and planned an unmarked resize redraw; TermVerify's
`ConptyAdapter.dispatch` accepts a `Resize` on the same ordered input stream
as a `KeyInput` and `_read_epoch_chunks` then reads until exactly one marker,
so a resize *is* an input epoch and an unmarked one would have swallowed the
next input's marker. The gap above is therefore narrower than it looked: it
covers only redraws the verifier did not dispatch at all, which means agent
output and nothing else in §C.1.

**The invariant is narrower still, and §C.2 must respect it.** The editor
marks every resize it *observes*, and the size watcher observes only
*changes*. Dispatching a resize to the geometry the terminal already has
therefore produces no event, no redraw, and no marker — the epoch waits for a
marker that never arrives and dies on the abort deadline. A resize scenario
must genuinely change the geometry. The general rule for §C.2: an event kind
may be filtered by its adapter, and every filtered event is a marker the
verifier is still waiting for.

## Consequences

- **`run_editor` is rewritten**, and its tests move from "feed bytes" to
  "feed events". The `KeyAssembler` work from review 0001 cluster B is
  unaffected: it still converts characters to symbolic keys; it just sits
  behind the `Key` event source.
- **A new port and a new adapter**, both outside the deterministic core, both
  injectable. The core's no-`subprocess`/no-`asyncio` rule is unchanged.
- **Three deferred findings close together** — 5 (via 0004), 9's keystroke
  race (a prompt can no longer open "between keystrokes": prompts arrive as
  events in the same ordered queue), and 10 (cancellation actually wired).
  TD-6 (resize) closes as a side effect of D2 — **paid in plan 0015 V4**,
  though not quite "as a side effect": a synchronous source would have
  observed the resize and acted on it only at the next keystroke, so the
  threaded source had to ship with it.
- **Drei gains a runtime dependency on a child process**, as 0003 consequence
  1 already accepted. Confined to the port; the core stays fake-testable.

## Amendments to earlier records

- **0003 §A.1** describes the subprocess port as "launch/monitor/terminate;
  deliver stdout/stderr lines and exit status". Plan 0008 deliberately shipped
  a narrower blocking `run` and deferred streaming, and the record was never
  updated — so 0003 has read as a description of shipped code for five slices.
  §A.1 is hereby split: the blocking port is what shipped (A.1), and the
  streaming port is this record's D1.
- **0003 consequence 2**'s atomicity claim is made true by D4 rather than
  weakened.

## Open questions

- **`C-g` overloading.** See D5. Blocking for the cancellation slice, not for
  the pump's first slice.
- **Which key sends a prompt.** No key binds an agent command today. The
  text-prompt variant of §A.4 is the prerequisite; the binding itself is a
  keymap decision the slice owns.
- **Backpressure toward the agent.** D3 bounds Drei's *rendering* cost, not
  the child's output rate. If a real agent can outrun the pump, the answer is
  ACP-level flow control, not a Drei-side buffer cap. *Trigger:* only if
  measured against a real `hermes acp` session.
- **Session persistence across restarts.** Unchanged from 0003; still
  undecided, still not a prerequisite.
