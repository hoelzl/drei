# Fifteenth slice: input events and resize (§C.1)

**Status:** claimed (issue #37); plan not yet merged. No code written.

**Architecture gate:** design `0005-acp-pump.md` D2 (the injection point) and
D4 (atomic delivery). This slice places the boundary and proves it with the
one input source that already exists; it adds **no** agent-facing code. No new
protocol, no ACP change, no change to the deterministic core's rules — the
core still sees nothing but a serialized sequence of commands. One new effect
port-shaped thing (an input *source*) and one adapter that owns threads,
both outside the core, both injectable.

**Goal:** make asynchrony expressible before anything asynchronous arrives.
`run_editor` today blocks in `port.read_key()`, which is why five merged ACP
slices are unreachable (TD-2) and why a terminal resize is never observed
(TD-6). Both are the same missing boundary. After this slice the loop consumes
one totally ordered stream of input events, a resize is an event on that
stream, and adding `AgentBytes` in §C.2 is a new event kind rather than a
second rewrite of the loop.

## What exists today (the delta is nameable)

- `run_editor` (`terminal.py:145`) reads `port.get_size()` **once**, builds
  the harness, then loops on `assembler.feed(port.read_key())` forever. There
  is no second input source and no way to introduce one.
- `EditorHarness.__init__` takes `width`/`height`, passes them to
  `EditorSession(frame_size=…)`, and stores its own copies for
  `render_session`. Nothing can change either afterwards.
- `frame_size` is **semantic**, not presentational: `_split_window`
  (`session.py`) refuses to split when the frame is shorter than
  `(len(windows) + 1) * MIN_WINDOW_ROWS + 1`. So the size a resize would
  change is an input to a command's outcome.
- `apply_session_effects` is two dispatches with an observable seam — the
  fold advances in the first whether or not the second runs. Its own docstring
  and design 0003 consequence 2 both say "atomic"; both are currently wrong,
  and TD-2 records it.
- 45 tests in `tests/test_terminal.py` and the two TermVerify suites drive
  the loop through `read_key()`. They are the regression gate for the rewrite.

## Design decisions (implementing 0005 D2/D4)

### D1. `InputEvent` carries exactly the kinds that have a producer

```text
InputEvent = Key(str) | Resize(width: int, height: int)
```

`AgentBytes` and `AgentExited` are 0005's D2 list and are deliberately
**absent** here: nothing in this slice produces them, and a union member with
no producer is the speculative framework layer the rules forbid. §C.2 adds
them, which is an addition to a union, not a change to the loop's shape.

`Key` carries the raw input unit, not a symbolic key: `KeyAssembler` stays
exactly where it is, behind the event source, so the escape-sequence work from
review 0001 cluster B is untouched.

### D2. `InputSource` is the seam; the loop never blocks on a port again

```text
InputSource.next_event() -> InputEvent     blocks until one is available
InputSource.close() -> None
```

`run_editor` takes an optional source and defaults to the threaded terminal
source built from its `TerminalPort`. Tests pass a scripted source — a list of
events — and touch no thread, which is 0005's verification layer 1 and where
every interleaving worth naming will live in §C.2.

### D3. Resize crosses the command boundary

A resize dispatches `ResizeFrame(width, height)` and records
`FrameResized(width, height)`. It is **not** a harness-local mutation, because
frame size is an input to `SplitWindow`'s outcome (see *What exists today*):
if a resize did not enter the transcript, replaying a transcript containing a
`C-x 2` would not reproduce the split-or-no-op decision. That is precisely the
"deterministic and replayable" rule, and it is what makes this a command
rather than a setter.

`ResizeFrame` is not a user command and binds to no key.

### D4. The threaded terminal source, and what it owns

The default source runs two reader threads pushing onto one
`queue.Queue`: one blocking in `port.read_key()`, one watching
`port.get_size()`. Threads live **entirely** in the adapter (0005 D2); the
loop pops one event at a time and the session sees only commands.

The size watcher polls rather than using SIGWINCH / `WINDOW_BUFFER_SIZE_EVENT`
because those have no common shape across POSIX and the Windows console, and
this slice is not the place to grow two platform paths. The poll interval is
an adapter concern — a clock dependency **outside** the deterministic core,
which is where the rules allow it, and no test depends on it (layer-1 tests
feed scripted events; the layer-2 test drives the watcher through a fake port
and asserts ordering, not timing).

### D5. Delivery becomes one dispatch (0005 D4)

`DeliverSessionEffects` emits both `AgentTranscriptUpdated` **and**
`AgentTextInserted`; `apply_session_effects` becomes one dispatch.
`InsertAgentText` survives as a command in its own right — it is still how a
caller appends agent text — but the delivery path stops depending on two
dispatches landing in sequence. TD-2's atomicity paragraph and 0003
consequence 2 become true as written, and the `TODO: [tech-debt]` in
`apply_session_effects` goes with them.

Tail-follow (0004 D6) and the target rules (0004 D2/D3) are unchanged; this
moves *where* the append happens, not *what* it does.

### D6. What this slice does NOT do

- **No streaming process port** (0005 D1) and no agent event kinds. §C.2.
- **No cancellation** (0005 D5). Blocked on the `C-g`-overload question, which
  0005 records as needing its own change.
- **No agent keymap binding and no launcher** (0005 D6). Nothing spawns a
  child; `drei` on a machine without `hermes` is unaffected.
- **No readiness marker for non-input-driven quiescence.** 0005's recorded
  evidence gap belongs to the slice that first produces a spontaneous redraw.
  A `Resize` *is* such a redraw — see *Owned deviations* — so this slice names
  the gap concretely rather than deferring it abstractly.

## Pins that change

1. Every test constructing `EditorHarness(width=…, height=…)` keeps working —
   the constructor is unchanged. Only `run_editor`'s loop is rewritten, so
   `tests/test_terminal.py` and both TermVerify suites are the regression
   gate, unmodified except where they stub `read_key`.
2. `tests/test_agent_delivery.py::TestApplySessionEffects::test_one_delivery_one_append`
   asserts `[AgentTranscriptUpdated, AgentTextInserted]` across **two**
   outcomes. After D5 both come from one dispatch; the event order is
   unchanged, the outcome count is not. Rewrite to assert the single outcome,
   which is the point of the change.
3. `TestDispatchRejectsCorruptDelivery::test_deliver_sessioneffects_through_dispatch`
   pins that a raw `DeliverSessionEffects` dispatch does **not** append text.
   D5 deletes that property deliberately. Rewrite to pin the new one: one
   dispatch, both events, text appended.
4. `tests/test_session_properties.py`'s `_Delivery` helper exists precisely
   because a raw dispatch did not append (its docstring says so). After D5 the
   helper collapses to a plain command in the history — a simplification the
   slice should take, not leave behind.

## Owned deviations (parity-registry rows)

1. **Resize is observed only as an event on the input queue.** Emacs reflows
   on `SIGWINCH` immediately. Drei's watcher polls, so a resize is observed
   within one poll interval rather than instantly. Presentation-only lag; the
   semantic frame size is correct from the next command onward.
2. **A resize redraws without a readiness marker.** Markers stay bound to
   input epochs (0005's recorded decision), so a TermVerify resize scenario
   must wait on frame content with an explicit deadline rather than on
   quiescence. This is weaker evidence than Drei uses elsewhere and is the
   first concrete instance of 0005's recorded gap: the row exists so that the
   TermVerify issue for a second marker kind has something to cite.

## Implementation order (vertical slices, strict TDD)

1. **V1 — the seam, keys only.** `InputEvent`/`Key`, `InputSource`,
   `run_editor` consuming events; a scripted source in tests. The default
   source is a thin synchronous wrapper over `read_key` at this step (no
   threads yet), so the entire existing terminal suite and TermVerify are the
   regression gate for a behavior-preserving rewrite.
2. **V2 — `ResizeFrame` command.** `FrameResized` event, session
   `_frame_size` update, harness width/height update, the `C-x 2` gate
   re-evaluated at the new size. Dispatch-level tests; no source involvement.
3. **V3 — `Resize` event end to end.** The loop turns a `Resize` event into
   the V2 command and redraws. Scripted-source tests, including resize
   *while the minibuffer is open* (delivery-class? no — it is not user input;
   pin the answer either way) and a resize that makes a split illegal.
4. **V4 — the threaded source.** Two reader threads, one queue, `close()`.
   The one test that touches concurrency: bytes/sizes pushed through a fake
   port appear as events in order, and the queue closes cleanly. Plus a
   TermVerify resize scenario per deviation 2.
5. **V5 — atomic delivery (0005 D4).** One dispatch, both events; rewrite
   pins 2–4; drop the `_Delivery` helper; remove TD-2's atomicity paragraph
   and the `apply_session_effects` TODO.
6. **V6 — adversarial review → fix → code PR (`Closes #37`) → merge.**

## Acceptance criteria

- `run_editor` contains no `port.read_key()` call; the loop's only input is
  `source.next_event()`.
- Every existing `tests/test_terminal.py` and TermVerify scenario passes
  unmodified except for source injection — the rewrite is behavior-preserving
  for keys.
- A `Resize` event changes the rendered frame width **and** the `C-x 2`
  minimum-height decision, and `FrameResized` appears in the transcript, so a
  replay of split-after-resize reproduces the same outcome.
- The threaded source is exercised by exactly one test that starts a thread;
  every other test feeds scripted events.
- One `apply_session_effects` call produces exactly **one** `CommandOutcome`
  carrying both delivery events, and no code path can observe a state between
  the fold and the append.
- The child-process story is untouched: no spawn, no new dependency, `drei`
  runs on a machine with no `hermes`.
- Full quality gate green on 3.12–3.14 and both CI OSes; coverage floor held
  at 100%; `drei.acp` purity unchanged.
- TD-6 is **reduced and re-scoped, not removed** (see risks), and TD-2's
  atomicity paragraph is removed.

## Risks / open questions

- **Rewriting the loop is the risky step**, exactly as V1 of slice 14 was.
  Mitigation: V1 is deliberately behavior-preserving and keeps the default
  source synchronous, so the entire shipped terminal suite gates it before any
  new event kind exists.
- **TD-6 is not fully paid by this slice.** The size watcher observes a
  resize, but with a *synchronous* default source (V1) a resize is only acted
  on when the next event arrives. V4's threaded source removes that, which is
  why V4 is in scope. If V4 slips, TD-6 must be re-scoped in
  `docs/technical-debt.md` rather than marked done — the rules say entries are
  removed when paid, not when re-scoped.
- **Is the threaded source in the right slice?** The alternative is to defer
  every thread to §C.2 and ship a synchronous source here, accepting that
  resize lags a keystroke. That is a smaller, safer slice with a visibly worse
  outcome, and it moves the only concurrency test next to the only agent test
  — two risky things in one slice instead of one each. This plan takes the
  threads now; **this is the decision most worth overriding at the plan
  gate.**
- **`Resize` while the minibuffer is open** has no obviously right answer. It
  is not user input, so the gate arguably should not swallow it; but it is
  also not a delivery. V3 must pin one answer with a reason.
- **The readiness-marker gap becomes concrete here**, one slice earlier than
  0005 anticipated, because a resize is the first spontaneous redraw. The
  obligation under `AGENTS.md` is to reduce it to a concrete test and file it
  with TermVerify, not to invent a private marker in Drei.
