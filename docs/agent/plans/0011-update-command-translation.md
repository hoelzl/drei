# Eleventh slice: ACP client core (B.7) — session/update → command translation

**Status:** ready — architecture gate: the live model stays authoritative and replayable. Agent-streamed text enters the editor **only** as typed `SessionEffect` values (B.6) mapped to `Command` values and applied through `EditorSession.dispatch` — the same command boundary every user edit crosses (design 0003 §consequence-2, 0002's external-delivery shape). No port, no I/O, no subprocess, no new editor-visible feature beyond the agent-buffer command/event surface: the transport and the §C pump are out of scope here.

**Goal:** translate each B.6 `SessionEffect` into a deterministic update of the **agent buffer** — the Drei buffer whose text is the rendered transcript of an ACP session (design 0003 §vocabulary) — per design 0003 §B.7: *"Map each ACP notification (agent message chunk, tool call, tool call update, plan, thought) onto a Drei command that updates the agent buffer … Verify: transcript-fold property (3) plus per-notification command tests."* B.7 lands the *session-local* translation: effect → commands → dispatch → rendered agent-buffer text. Multi-buffer display (§A.2) and any file-write routing through the filesystem port are explicitly deferred (see below).

## Why this slice, and why scoped this way

B.6 shipped the pure protocol machine: it folds inbound envelopes and emits typed `SessionEffect` values (`AgentTextChunk`, `ThoughtChunk`, `ToolCallStarted`, `ToolCallUpdated`, `PlanUpdated`, `PromptCompleted`, `PermissionRequested`, `ProtocolError`, `Initialized`, `SessionEstablished`). Nobody consumes them yet. B.7 is that consumer — the seam where the ACP layer first touches the editor.

The design's B.7 wording mentions routing file writes through the filesystem port. Concretely that means answering agent→client `fs/write_text_file` requests — which B.6 deliberately **refuses** (Drei advertises `clientCapabilities = {}`, and the machine answers `fs/*` with method-not-found). The only file-affecting signals that actually reach a B.7 client are `diff` tool-call content items *inside* the transcript. So the honest split is:

- **B.7 (this slice): the agent buffer transcript.** Effect → command → dispatch. Pure and replayable; the §C pump is just a driver that repeats `handle → translate → dispatch`.
- **fs-port routing of agent file edits: deferred to §A.2+.** Agent diffs target *other* buffers/files, which requires multiple buffers and a decision about how an agent edit meets a modified user buffer. Until then, `diff` tool-call content is **rendered into the transcript** (paths + unified-diff text), never applied — recorded as an explicit owned deviation below.

This keeps B.7 one reviewable vertical: new commands/events, one new pure module (`drei.acp.transcript`), one session-side adapter, and a transcript-fold property test.

## What this slice (B.7) adds

### New commands and events (`commands.py`, `session.py`)

Two command variants, dispatched like any other command; agent deliveries are **not user edits** (same discipline as `DeliverProcessOutput`):

- `DeliverSessionEffects(effects: tuple[SessionEffect, ...])` — one atomic delivery of the effect list from one `AcpMachine.handle(...)` call. The session maps it to one `AgentTranscriptUpdated(rendered: str, chars: int)` event; buffer, undo, and kill-ring state are untouched. Validated at construction: `effects` must be non-empty and every member must be a `SessionEffect` (a machine-generated delivery cannot record a corrupt transcript fold).
- `InsertAgentText(text: str)` — the agent-buffer edit proper: appends `text` at end-of-buffer regardless of point, marks the buffer **unmodified** (agent text is not a user edit; the agent buffer is never dirty), and moves point to the new end (a visible agent buffer tracks the stream). Emits `AgentTextInserted(text, before, after)` where `before` is the pre-insert buffer end. `_make_group` is **not** extended: `InsertAgentText` is not undoable — undo/redo of an agent delivery would corrupt the fold-of-effects invariant, since the next delivery appends to a text the fold no longer recognizes.

`CommandOutcome.events`, the `Command`/`Event` unions, and `BufferObservation` are unchanged in shape — no new buffer state appears (the fold cache is session state, not buffer state, mirroring the `process_log` precedent).

### New module `src/drei/acp/transcript.py` — the pure renderer

Owns the *text* the agent buffer displays. Two parts:

- **Formatting helpers** (pure functions): `format_tool_call_started(update) -> str`, `format_tool_call_updated(update) -> str`, `format_plan(update) -> str`, etc. These interpret the 0.9.0 `tool_call` / `tool_call_update` / `plan` payloads that B.6 keeps as opaque `JsonValue`, with total fallbacks: missing/ wrong-typed fields degrade to `?` placeholders, never an exception, never a crash (the peer is non-deterministic by design; the transcript must survive a malformed update).
  - `tool_call` → a header line: `[tool:<kind or ?>] <title or toolCallId> (<status or ?>)` plus one `  <path>:<line or 1>` line per `locations[]` entry, plus `diff` content rendered verbatim as fenced text (`newText` elided — it duplicates the diff).
  - `tool_call_update` → a compact delta line naming only the fields present (`title`, `status`, added locations, added diff content). Status vocabulary pinned from 0.9.0: `pending` / `in_progress` / `completed` / `failed`.
  - `plan` → a numbered list: `N. [<status or ?>] <content or ?>` per entry (`PlanEntry.content`, `PlanEntry.status` in 0.9.0); an empty entry list renders `Plan: (empty)`.
- **`TranscriptFold`** (frozen dataclass) — the interpreter state: `turn_open: bool`, `thought_open: bool`, `stop_reason: str | None`, plus `advance(fold, effect) -> tuple[TranscriptFold, str]` mapping each `SessionEffect` to rendered text:
  - `AgentTextChunk` — opens an agent turn if none is open (prefix `\n── agent ──\n`), closes an open thought block first; appends the chunk verbatim.
  - `ThoughtChunk` — opens a thought block (`\n  ┆ thought ┆\n`) inside the current turn; thought text is appended verbatim (line-prefixing would make the fold context-sensitive across chunk boundaries — banned).
  - `ToolCallStarted` / `ToolCallUpdated` / `PlanUpdated` — close any open thought block, then append the formatted block. They stay *inside* the turn: `session/prompt`'s response is the only turn boundary, so interleaved chunks and tool calls cannot split a header onto the wrong side of a completion.
  - `PromptCompleted(stop_reason)` — closes thought and turn; appends `\n── end turn (<stop_reason>) ──\n` (`refusal` / `cancelled` render the same way — the stop reason is printed, not branched on).
  - `PermissionRequested` — appends `\n── permission requested (id <request_id>) ──\n`. Answering it is B.8; the line is the audit trail that a request is pending.
  - `ProtocolError` — appended verbatim as `\n── protocol error: <detail> ──\n`. **Must not be dropped** (dropping silently misaligns the live text with any recomputed fold and hides agent misbehavior).
  - `Initialized` / `SessionEstablished` — transcript-silent (they carry no agent-visible text; the §C launcher surfaces them as status, not transcript).

The module imports only `drei.acp.machine` (effect types) + stdlib; the existing recursive purity guard already covers `drei.acp.*`.

### Session-side adapter (`session.py`)

- `EditorSession` gains a private `_agent_transcript: TranscriptFold` — a derived cache of the `AgentTranscriptUpdated` event stream, exactly as `_process_log` is a derived cache of `ProcessOutputRecorded`. It is **not** folded at dispatch time; it is folded lazily and memoized, so transcript-folding cost stays off the hot path of ordinary editing commands. `transcript` remains the authoritative event list; the cache is reconstructible by folding `AgentTranscriptUpdated.rendered` values (and independently by folding the effects through `TranscriptFold.advance`).
- `apply_session_effects(effects) -> CommandOutcome` — the single delivery entry point (mirrors `run_process`): validates, dispatches `DeliverSessionEffects`, recomputes the fold cache incrementally, extracts the newly rendered suffix, and dispatches `InsertAgentText(suffix)`. One `handle()` call's effects land as one delivery event + at most one append event, keeping the record atomic per design 0003 §consequence-2.
- Read-only enforcement is **delivery-only**: the new commands are never key-bound (the harness/keys layer is untouched), so no user key can produce them. Emacs-style `buffer-read-only` text properties are deferred to §A.3 — recorded as an owned deviation with its hazard.

### Owned deviations (parity registry rows)

1. **Agent deliveries are not undoable.** `InsertAgentText` emits no undo group; `Undo` skips agent insertions. Hazard: a user who interleaves edits into the agent buffer cannot undo across an agent delivery boundary. Owned: undo of an external stream is incoherent with the fold invariant; §A.3 (read-only regions) shrinks the hazard by discouraging interleaved edits.
2. **User edits to the agent buffer are not rejected.** Nothing stops `InsertText` in the agent buffer; the next delivery simply appends after the edit. Hazard: the live text can diverge from the pure fold of `AgentTranscriptUpdated` events (the fold cache and the buffer text disagree until the buffer is re-created). Owned explicitly — §A.3 owns the enforcement mechanism; this slice records the hazard so it cannot be read as benign.
3. **Agent file edits are rendered, not applied.** `diff` tool-call content lands in the transcript as text; no target buffer is modified and the filesystem port is not involved. Owned: applying diffs requires multiple buffers (§A.2) and a conflict policy for modified user buffers — a separate slice.

## Parity note

No Emacs-facing keybinding and no GNU Emacs behavior is referenced: the peer contract is the pinned `agent-client-protocol 0.9.0` schema (plan 0010's pin — re-verified against the installed Hermes venv: still 0.9.0, no re-pin needed). The three registry rows above are the parity surface. No minibuffer interaction: `PermissionRequested` renders an audit line only; the approval bridge is B.8.

## Implementation order (thin verticals, strict TDD each)

1. **`acp/transcript.py` scaffolding + text/thought rendering**: `TranscriptFold`, `advance` for `AgentTextChunk` / `ThoughtChunk` / `PromptCompleted`. **Tests** — turn/thought open-close sequencing; a second prompt opens a fresh turn; chunks never re-open a completed turn; `PromptCompleted` prints each pinned stop reason verbatim.
2. **Tool-call and plan formatting**: the three `format_*` helpers + `advance` arms. **Tests** — golden-rendered blocks for representative 0.9.0 payloads (full fields; minimal `toolCallId`-only; unknown `kind`/`status` strings; missing/mistyped fields → `?` placeholders, no exception); `diff` content renders verbatim; `PlanUpdated` inside an open thought closes it first.
3. **Commands/events + session dispatch**: `DeliverSessionEffects` (with construction validation), `InsertAgentText`, `AgentTranscriptUpdated`, `AgentTextInserted`, `apply_session_effects`, the lazy fold cache. **Tests** — append-at-end regardless of point; buffer stays `modified=False`; point tracks the new end; events carry exact spans; `InsertAgentText` pushes no undo group and `Undo` skips it; an interleaved user `InsertText` is preserved while the next delivery still appends at end; minibuffer-active gating does not swallow deliveries (mirrors the `DeliverProcessOutput` exemption); delivery validation rejects an empty/non-effect payload.
4. **Transcript-fold property test** (design 0003 §B.7 verify): drive `start → new_session → prompt → handle(trace)` with a scripted trace (streamed text + thoughts + tool calls + a plan + completion, and a cancel variant), threading every emitted effect through `apply_session_effects`. **Property**: the agent-buffer text equals the concatenation of every `AgentTranscriptUpdated.rendered` in the transcript — and, independently, equals folding the same effects through `TranscriptFold.advance` from the initial state. Two oracles, one invariant; not just equality between two runs.

## Acceptance criteria

- Full quality gate green (`pytest --cov`, `ruff check`, `ruff format --check`, `mypy src tests`, `pre-commit run --all-files`); coverage ratchet held at 100%.
- `drei.acp.transcript` imports **no** effect modules and only `drei.acp.machine` + stdlib; the recursive purity guard (already covering `drei.acp.*`) stays green.
- Every `SessionEffect` variant has an `advance` arm and a per-effect rendering test; formatting is total (no payload shape can raise).
- `InsertAgentText` never marks the buffer modified, never creates an undo group, and always appends at end-of-buffer.
- The fold property holds for both scripted traces (completion and cancel) against both oracles.
- The three deviation rows land in the parity registry with focused tests naming the rows (hazard-owning discipline).

## Risks / open questions

- **Lazy-fold invalidation.** The memoized `TranscriptFold` must track exactly the `AgentTranscriptUpdated` events folded so far; an `apply_session_effects` that raises mid-way must leave cache and transcript consistent. Mitigation: validation happens before any dispatch (construction-time, as with `DeliverProcessOutput`), so a dispatch cannot half-apply; the cache only advances after the delivery event is recorded.
- **Renderer surface drift.** The formatter interprets payloads B.6 keeps opaque; a future SDK bump could add fields the renderer ignores (safe) or rename ones it reads (silent `?`). Mitigation: bumping the pin re-runs this slice's golden tests (plan 0010's change policy), and unknown *values* are already rendered, not rejected.
- **Over-formatting.** Rendering every `tool_call_update` delta could flood the transcript on a chatty agent. Accepted for now: fidelity beats brevity while the end-to-end slice (§C.10) doesn't exist yet; if a real session proves noisy, compaction is a renderer-only change, not a protocol change.
- **Point motion on delivery.** `InsertAgentText` moves point to the end even when the user scrolled back. Accepted (Emacs comint default) until §A.2 gives the agent buffer its own window; then point-tracking becomes a per-window decision.

### Hardening deferred to B.8 / §C

- **B.8 owns the answer path** for `PermissionRequested` (minibuffer prompt, outcome response, session-scoped auto-approval cache); B.7 renders the request and nothing more. The 0010 deferred notes still stand: `in_flight_incoming` stays write-only until B.8 reads/clears it; capability advertisement is revisited only when §C wires the fs/terminal ports.
- **§C owns** the pump (`handle → apply_session_effects` driver), the decoder bound and EOF-tail surfacing (B.5 deferred notes), string-id-mismatch teardown policy, and phase gating on inbound agent→client requests (0010 deferred notes) — none of which B.7's session-local translation can observe.
