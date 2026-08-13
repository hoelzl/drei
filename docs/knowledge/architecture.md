---
type: concept
title: Drei architecture
description: Hybrid live-model ownership and deterministic editor boundaries.
tags: [architecture, deterministic-core, tui]
---

# Architecture

The intended dependency direction is:

```text
terminal frontend / TermVerify adapter
ACP client adapter  ------------------> application session and command boundary
              -> hybrid live editor model
              -> explicit effect ports (file, process, terminal, ACP)

completed command
              -> ordered immutable event records
              -> immutable semantic observation + rendered frame
```

The ACP client adapter is frontend-adjacent, not a second core: an agent's
streamed updates reach the model only as commands crossing the same boundary
every keystroke crosses ([design record
0003](../agent/design/0003-hermes-drei-integration.md)). The live model never
talks to a subprocess.

Three terms are deliberately distinct:

- the **live model** is the authoritative runtime object graph;
- an **observation record** is an immutable semantic projection for verification;
- an **event record** is an immutable account of an accepted command or delivered external input.

Determinism requires controlled ownership, explicit inputs/effects, atomic commands, and reproducible observations. It does not require the entire live model to be immutable. [Design record 0002](../agent/design/0002-live-editor-state-architecture-spike.md) selects hybrid ownership: extension-visible entities retain stable shells or owner-resolved IDs while immutable, structurally shared domain values are used where history, rollback, and snapshot reuse benefit.

An owner may use controlled private mutation where measured needs justify it, but no ambient component may mutate editor semantics directly. A failed grouped command restores both semantics and the owner's promised identity boundary before any event is emitted. Storage strategy remains separate: strings, line tables, piece tables, ropes, chunks, and indexes must be chosen from measured requirements rather than inferred from the ownership decision.

Direct/in-process and terminal profiles must exercise the same production command path. Structured observation records are authoritative for semantic assertions; terminal frames prove presentation and integration.

## Effect ports

Native filesystem, process, and terminal access is mediated by narrow explicit
ports — Protocol definitions in the core, `System*` implementations at the
edge, injected by the harness or `run_editor`. Four have shipped:

- **`FilePort`** (`src/drei/files.py`) — `read`/`write` over text. It
  translates nothing: newline handling is the session's, per buffer, so a
  save cannot silently rewrite a file's line endings.
- **`ProcessPort`** (`src/drei/process.py`) — blocking run-to-completion
  (`run(argv) -> ProcessResult`), with launch failures normalized to tokens
  rather than raised. Deliberately not streaming: a conversation with a
  long-lived child is a different port, not a wider version of this one.
- **`StreamingProcessPort`** (`src/drei/streaming.py`) — `spawn(argv) ->
  AgentProcess`, with blocking `read`/`read_stderr`, `write`, `poll` and a
  cooperative-then-hard `terminate`. Bytes in and bytes out; framing belongs
  to the codec, which is chunk-safe by construction.
- **`TerminalPort`** (`src/drei/terminal.py`) — raw mode, one input unit,
  writes, size, restore.

Errors cross a port as **normalized tokens** (`not-found`,
`permission-denied`, `io-error`, `no-file`), never as a locale-dependent OS
message: the same failure must read the same on every host.

## The ACP subsystem

Drei speaks the [Agent Client Protocol](../agent/design/0003-hermes-drei-integration.md)
as the *client*; `hermes acp` is the server. The subsystem is layered so that
everything below the adapter is pure and replayable without an agent:

```text
drei.acp.codec      NDJSON framing over bytes
drei.acp.messages   JSON-RPC envelope model (Request/Response/Notification)
drei.acp.machine    session lifecycle as an immutable value folded over
                    inbound messages; emits outbound messages + SessionEffects
drei.acp.transcript pure fold: SessionEffect* -> rendered transcript text
session adapters    SessionEffect -> Command -> dispatch
```

No module in `drei.acp` imports `subprocess`, `asyncio`, or does I/O. A
`SessionEffect` becomes editor state only by being translated into a `Command`
and dispatched, so the transcript of events stays the oracle for agent-produced
text exactly as it is for typed text.

**The pump (§C, `src/drei/pump.py`)** is the adapter that reaches all of it
from the shipped editor, along the boundaries
[design 0005](../agent/design/0005-acp-pump.md) placed:

- **One totally ordered `InputEvent` queue** (`src/drei/input.py`) is the
  injection point. Reader threads live in adapters — `TerminalReaders` for the
  keyboard and the size watcher, `AgentReaders` for the child's two pipes —
  and the loop pops one event at a time, so the core still sees nothing but an
  ordered command sequence. The queue has **two lanes**: `Key | Resize` ahead
  of everything else. That set is exactly the events a verifier dispatches,
  which is also exactly the set that carries a readiness marker, so fairness
  and evidence are one distinction.
- **The pump owns the machine**, and `EditorSession` never does. Protocol
  phase is transport state; putting it in the session would mean replay had to
  reproduce it.
- **Drain, then deliver once**: every frame a read completed is folded, and
  the accumulated effects become a single dispatch and a single redraw.
- **Lazy launch, visible failure**: the child spawns on the first `C-c a`, its
  stderr and any launch failure go to an `*agent-log*` buffer as normalized
  tokens, and `run_editor`'s `finally` terminates it.

Turn **cancellation** is wired (slice 20, TD-2 paid): with a turn in flight,
`C-g` writes `session/cancel`, answers every pending permission request
`cancelled`, and clears the presentation, in design 0005 D5's order — the
pump reads `KeyboardQuitEvent` out of the command outcome, so the session
still holds no machine. One `C-g` peels one layer: at a permission prompt it
is the shipped deny (slice 18 left one claim on that key: `C-g` at an exit
prompt abandons the exit), so the turn is the second `C-g`; an exit or text
prompt peels first; a pending prefix peels with the turn. Slice 18 also fixed
what an exit owes the agent — a `session/request_permission` that queues
behind an exit prompt is presented if the exit is abandoned and dropped if it
completes, because a request left pending hangs the agent for the rest of a
run that did not end after all.

Where a transcript lands is decided separately in
[design 0004](../agent/design/0004-agent-buffer-identity.md): one **agent
buffer** per ACP session, named `*agent*`, created when the session is
established. Deliveries carry their target buffer id in both command and
event — focus is never consulted — and only a *generated* buffer (one that
visits no file) may receive one. Where it is *shown* is a separate command,
`DisplayBuffer`: another window, never the focused one, splitting once if the
frame permits. A transcript nowhere on screen would be a feature the user
cannot use; a transcript that stole focus would be the interruption design
0004 forbids.

## Session-scoped vs buffer-scoped state

The session owns several buffers; which state is per buffer and which is
global follows Emacs, and the split is load-bearing for replay:

- **Per buffer** (`_BufferState`, `src/drei/session.py`): undo history, redo
  stack and descent direction; yank-pop chaining; the kill-append chain flag;
  the last-saved text (the modified flag is derived from it) and the file's
  line ending. Undo in one buffer never touches another. Chain flags are
  cleared on switch-away — Emacs reaches the same result through
  `last-command`.
- **Session-global**: the kill ring (a kill in buffer A is yankable in B), the
  transcript, the process log, the minibuffer, the agent-transcript fold
  cache, and the ports.

None of this lives on `BufferValue`, which stays the frozen per-edit value
(text, point, mark, file path, modified).

## Minibuffer and window models

- The **minibuffer** is a single slot of session state, not a buffer: a
  prompt label plus either text input or a *choice* (the permission prompt's
  option list). It is not recursive and has no keymap of its own. While it is
  open, only its own commands and **external semantic inputs** act — every
  user editing command is a silent no-op. Process output, agent effects,
  permission requests, and terminal resize are exempt: dropping a delivery
  would desync the transcript from the model, while dropping a resize would
  leave session geometry stale after the terminal changed.
- **Windows** are layout views over buffers, not editor state: an ordered
  tuple of `WindowValue(buffer_id, point, mark)` plus a focused index.
  Window point is distinct from `BufferValue.point`, so two windows over one
  buffer hold independent points. Vertical stacks only.
