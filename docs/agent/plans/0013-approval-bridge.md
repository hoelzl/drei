# Thirteenth slice: approval bridge (B.8)

**Status:** ready — architecture gate: design 0003 §B.8 and consequence 5 (approval prompts are a Drei UI responsibility). The answer path crosses the command boundary as a user decision (like a delivery, but human-initiated); the machine stays pure and transport-agnostic; no new port, no I/O.

**Goal:** per design 0003 §B.8 — *"When the agent requests `session/request_permission`, present a minibuffer prompt, return the user's `allow_once` / `allow_session` / `allow_always` / `deny`, and honour session-scoped auto-approval within the ACP session. Verify: each decision maps to the correct protocol response; session-scoped cache resets on new session."* Concretely: (1) the machine gains `resolve_permission(request_id, decision)` — it reads/clears `in_flight_incoming` and emits the exact 0.9.0 `RequestPermissionResponse` (`selected` with `optionId`, or `cancelled`); (2) the session gains a **choice minibuffer** (design 0003 §A.4's choice variant) — a prompt that lists the agent's `PermissionOption`s and resolves to one decision or an abort; (3) an **auto-approval cache** keyed on tool-call identity grants `allow_session`/`allow_always` scopes without re-prompting, reset on new session.

**Why this slice, and why now:** §B is otherwise complete (codec #20, machine #22, translation #27) and this is its last rung. Until it lands, `in_flight_incoming` is write-only (0010's deferred note) and a real agent that asks permission blocks forever — the 0010 review explicitly named this the B.8 deliverable. The §A.4 prerequisite (choice prompting) does not exist yet, so this slice lands the minimal choice variant it needs; the §A.4 text-prompt variant (entering a prompt to send) stays with §C.

## What exists today (the delta is nameable)

- The machine tracks an inbound `session/request_permission` in `in_flight_incoming: dict[RequestId, str]` and emits `PermissionRequested(request_id, params)` (`machine.py:379-384`) — but **never** answers. The dict is write-only; nothing reads or clears it.
- B.7 renders `PermissionRequested` as an audit line (`── permission requested (id N) ──`) and nothing more (0011's scope cut).
- The minibuffer is a **text** prompt only (`FindFile` → `MinibufferInput/Backspace/Accept/Abort`, `session.py:439-467`). No choice prompt. No way to map a keypress to a semantic decision.
- ACP 0.9.0 shapes (pinned, re-verified against the Hermes venv — still 0.9.0): `RequestPermissionRequest.options: list[PermissionOption]`, each `{kind: allow_once|allow_always|reject_once|reject_always, name, optionId}`; the response is `RequestPermissionResponse{outcome: selected{optionId} | cancelled}`.
- The dispatch gate (`session.py:332-344`) already exempts external deliveries; a *user's* approval decision is not a delivery — it is the minibuffer's own resolution and needs no new exemption (it resolves through `MinibufferAccept`, which already acts).

## Design decisions (Drei-specified, ACP-pinned)

### D1. `resolve_permission` on the machine (the answer path)

New pure API on `AcpMachine`:

- `resolve_permission(request_id, decision: PermissionDecision) -> (machine, out: list[Message], effects: list[SessionEffect])`.
- `PermissionDecision` is a new frozen value: `Selected(option_id: str) | Cancelled`. It is a **client-side** decision, not a wire shape; the machine maps it to the exact 0.9.0 response.
- `Selected(option_id)` → `Response(id=request_id, result={"outcome": {"outcome": "selected", "optionId": option_id}})`. `Cancelled` → `Response(id=request_id, result={"outcome": {"outcome": "cancelled"}})`. CamelCase `optionId`, snake_case discriminators — asserted as concrete dicts (the B.6 convention), not round-trip identity.
- The entry is **removed** from `in_flight_incoming` (read **and** clear — the 0010 deferred note). Resolving an id that is not in flight is a caller bug: raise `AcpStateError`, mirroring `prompt()`/`cancel()` phase strictness. (A stale/duplicate resolution would answer a request the agent no longer awaits — corrupt provenance, same class as answering twice.)
- A new effect `PermissionResolved(request_id, decision)` records the answer for the transcript (B.7's renderer gains an arm: `── permission granted: <optionId> ──` / `── permission denied ──`); the audit trail is symmetric with the request line.
- Phase gating (0010's last deferred note): an inbound `session/request_permission` is only valid in `SESSION_ACTIVE`/`PROMPT_IN_FLIGHT`; in any other phase it now yields a `ProtocolError` effect instead of being tracked. This tightens the ungated-inbound hole the B.6 review flagged, scoped to the one inbound request kind Drei answers.

### D2. Auto-approval cache (session-scoped, per tool-call identity)

- New frozen value on the machine: `auto_approvals: tuple[str, ...] = ()` — a set of **tool-call identity keys** the user has pre-approved for this session. Two scopes per 0.9.0's `PermissionOptionKind`: `allow_session` and `allow_always` both populate the cache; `allow_once`/`reject_*` do not. (`allow_always` is honoured within the ACP session only — Drei has no cross-session persistence, and design 0003's open question on session persistence is unresolved; recorded as an owned deviation.)
- Identity key: the permission request's `toolCall.toolCallId` when present (0.9.0 `RequestPermissionRequest.tool_call` is a `ToolCallUpdate`, which carries `toolCallId`); else the request `params` canonical-JSON (total, deterministic — malformed payloads still yield a stable key). Extracted by a pure helper; no new parse layer.
- When a `session/request_permission` arrives whose key is cached, the machine answers **immediately** (same `resolve_permission` path, `Selected` with the first `allow_*` option's `optionId`) and emits no `PermissionRequested` effect — the human is not re-prompted. The response is still recorded (`PermissionResolved`) so the transcript shows the auto-approval.
- **Reset on new session:** `new_session()` clears `auto_approvals` (design 0003 §B.8: "session-scoped cache resets on new session"). Verified by a test: approve-with-session-scope → new session → same request re-prompts.

### D3. Choice minibuffer (the §A.4 choice variant)

The current minibuffer is a text prompt. Approvals need a **choice** prompt: the agent's `PermissionOption`s presented, one keypress (or accept) resolving to a decision, abort mapping to `Cancelled`/`deny`. Minimal design, reusing the existing gate and transcript machinery:

- New command `PromptPermission(request: PermissionRequested)` — opens the choice minibuffer. It is a **delivery-class** command (agent-initiated), so it joins the gate exemption alongside `DeliverSessionEffects` (a swallowed permission prompt would hang the agent — same desync class as a dropped delivery; registry row extended).
- Minibuffer state gains a kind: text (existing) vs choice. Choice state carries the `request_id`, the rendered option list, and the selected index. The prompt line renders the tool-call summary + options (e.g. `Allow run-tests? [y]once [s]ession [a]lways [n]o`).
- `MinibufferInput(char)` in choice mode maps a key to an option (`y`/`s`/`a`/`n` by kind) rather than appending text; `MinibufferAccept` resolves the highlighted option; `MinibufferAbort` resolves `Cancelled`. Resolution emits a new event `PermissionDecided(request_id, decision)` and closes the minibuffer.
- The session's `apply_permission_decision(request_id, decision)` seam (mirroring `apply_session_effects`) feeds the decision back to the machine via `resolve_permission` and returns the outbound `Response` for the §C pump. In this slice the pump is not wired (§C), so the seam returns the message; tests assert it.
- The choice prompt is **one decision per request**: two concurrent `PermissionRequested`s queue (the minibuffer is single; the second opens after the first resolves). Queueing is in the session, not the machine (the machine answers whatever it is asked; ordering is a UI concern). Bounded by design: the queue is the set of in-flight permission requests, already bounded by the agent.

### D4. What this slice does NOT do

- **No §C pump wiring.** `apply_permission_decision` returns the `Response`; nothing sends it on a wire. The `hermes acp` launcher (§C.9) owns actually writing it.
- **No text-prompt variant** (entering a prompt to send — §A.4's other half). §C's end-to-end slice owns that; this slice's choice kind does not generalize it.
- **No `allow_always` persistence across Drei restarts** (owned deviation — see registry rows).
- **No fs/terminal capability change.** `clientCapabilities={}` stays; the 0010 note about permission-vs-capabilities consistency is unchanged (accepting permission while refusing fs/terminal remains consistent with the 0.9.0 pin).

## Owned deviations (parity-registry rows)

1. **`allow_always` honoured only within the ACP session.** ACP's `allow_always` implies persistence; Drei resets the auto-approval cache on `new_session()` and has no cross-session store. Hazard: a user who chose "always" is re-prompted next session. Owner: §C session-persistence open question; pinned by the reset test.
2. **Choice minibuffer keymap is Drei-owned, not Emacs-derived.** `y/s/a/n` by option kind is a Drei choice (Emacs has no ACP); not a parity deviation from GNU Emacs but recorded so the choice is deliberate. Pinned by the choice-prompt tests.
3. **Permission prompt exempt from the minibuffer gate (extension of the B.7 row).** A permission request arriving while a text prompt is open must queue, not be swallowed; the mechanism (delivery-class command) and hazard (agent hangs on a dropped prompt) are the B.7 row's, extended here. Pinned by a test that opens `find-file`, delivers a permission request, and asserts the prompt queues rather than vanishing.

## Implementation order (vertical slices, strict TDD)

1. **V1 — machine answer path.** `PermissionDecision`, `resolve_permission` (selected/cancelled → exact 0.9.0 dicts; read+clear `in_flight_incoming`; stale id → `AcpStateError`), `PermissionResolved` effect + B.7 renderer arm, inbound phase gating. Tests assert concrete response dicts and the cleared dict.
2. **V2 — auto-approval cache.** `auto_approvals` field, identity-key extraction, cached-request auto-answer (no `PermissionRequested`, still `PermissionResolved`), `allow_session`/`allow_always` populate, `allow_once`/`reject` don't, `new_session()` reset. Test: the design's "session-scoped cache resets on new session" verify line.
3. **V3 — choice minibuffer.** Choice-kind state, `PromptPermission` command + gate exemption, keymap (`y/s/a/n`), accept/abort resolution, `PermissionDecided` event, `apply_permission_decision` seam returning the `Response`. Command-level tests driving the prompt to each outcome (the §A.4 verify shape).
4. **V4 — queueing + properties.** Two concurrent requests queue; a request during an open text prompt queues. Property: over interleaved edit/permission histories, every answered request was in flight, no request answered twice, `in_flight_incoming` ⊆ prompted ∪ answered. Registry rows + hazard tests; full gate.
5. **V5 — adversarial review → fix → code PR** (`Closes #29`) → merge → cleanup.

## Acceptance criteria

- `resolve_permission` maps each decision to the exact 0.9.0 `RequestPermissionResponse` dict (camelCase `optionId`, snake_case `outcome`), asserted as concrete dicts.
- `in_flight_incoming` is read **and** cleared on resolution; resolving a non-in-flight id raises `AcpStateError`.
- Session-scoped auto-approval: a cached tool-call identity is answered without a prompt; the cache resets on `new_session()` (test proves the reset).
- Choice minibuffer: each key/accept/abort maps to the correct `PermissionDecision`; abort → `Cancelled`; the prompt closes on resolution; `PermissionDecided` recorded.
- Queueing: a permission request during an open prompt (text or choice) queues and is presented after; never swallowed (registry row 3 test).
- Inbound phase gating: `session/request_permission` outside `SESSION_ACTIVE`/`PROMPT_IN_FLIGHT` yields `ProtocolError`, not tracking.
- Full quality gate green; coverage ratchet held at 100%; `drei.acp.machine` purity guard unchanged (the choice UI is session-side, not machine-side).
- Adversarial review clean (or remediated) before merge.

## Risks / open questions

- **Choice-prompt scope creep.** The §A.4 feature is "a command that prompts and returns a choice"; this slice builds only the permission-shaped variant. Mitigation: the choice state is generic (option list + index), not permission-specific, so §C's other prompts reuse it without rework — but no speculative generality (no pluggable prompt framework).
- **Tool-call identity when `toolCallId` is absent.** 0.9.0 `ToolCallUpdate.toolCallId` is required in the schema, but Drei's totality rule treats payloads as opaque; the canonical-JSON fallback keeps auto-approval total over malformed requests. Risk: a fallback key over-matches (two different requests with identical malformed payloads share a key) — accepted: malformed payloads are already a protocol violation; the fallback only avoids a crash, it does not promise precision.
- **Answering before the human decides.** The auto-approval path answers synchronously inside `handle()`. This is correct (the decision is already made) but means `handle()` can now emit a `Response` for an inbound request — a new outbound shape on the notification path. Mitigation: the B.6 outbound contract already covers `Response`; the scripted-trace tests assert the exact message.
- **Two agents / concurrent sessions.** Unchanged from 0010: one machine per session; the queue is per-session. Flagged, not modeled.

## Deferred to §C (unchanged)

The `hermes acp` launcher (§C.9), the end-to-end scenario (§C.10), the text-prompt variant of §A.4, fs/terminal capability advertisement, and any `allow_always` persistence across Drei restarts.

## Deferred to §C (added by adversarial review)

- **`session/cancel` MUST sweep pending permissions.** ACP 0.9.0 requires the client to answer every pending `session/request_permission` with `cancelled` when the prompt turn is cancelled. This slice ships no cancellation/pump path (`cancel()` does not clear `in_flight_incoming`, and there is no editor-level sweep of `_permission_queue`/`_choice`), so a `session/cancel` while a permission prompt is open or queued leaves the agent hanging. The slice that wires cancellation (§C pump) must route a synthetic abort through the choice minibuffer and answer all pending requests `cancelled`. Latent until the pump exists.
- **`MinibufferClosed` event.** The event stream can show two consecutive `MinibufferOpened` with no close event (choice resolution opens the next queued prompt without an explicit close; text-prompt accept never had one either — pre-existing asymmetry B.8 amplifies). A future event-stream consumer that pairs open/close would need this; recorded, not added in this slice (no consumer pairs them today).
