# Tenth slice: ACP client core (B.6) — pure session state machine

**Status:** ready — architecture gate: builds on the B.5 codec/envelope layer (`drei.acp.codec`, `drei.acp.messages`). Still **pure**: no I/O, no `subprocess`, no `asyncio`, no editor change. The state machine is an immutable value folded over inbound messages, emitting outbound messages — nothing flows across a port yet.

**Goal:** model the client side of the ACP session lifecycle — `initialize` → `session/new` → `session/prompt` → stream of `session/update` → completion/cancel — as a pure, replayable state machine that is verifiable *without* a real agent, per design 0003 §B.6. This is the heart of the ACP client: the §C launcher will drive it, and B.7/B.8 translate its outputs into editor commands and approval prompts.

## Pinned protocol revision (design 0003 §open-question, resolved here)

Design 0003 requires the codec and state machine to be **pinned to a specific ACP schema and a specific `hermes` version** — never "current ACP" as a floating reference. Pin for the whole §B/§C arc:

- **ACP schema:** the official Python SDK **`agent-client-protocol 0.9.0`** (the version bundled with the real `hermes acp` peer, `acp/schema.py`). JSON-RPC 2.0 over NDJSON (B.5). Object keys `camelCase`; discriminator strings `snake_case`.
- **Peer:** `hermes acp` from the installed Hermes distribution. The exact `hermes` version is recorded at §C (launcher) time when the subprocess is actually spawned; B.6 speaks the *protocol*, not a specific binary.
- **Change policy:** bumping the pinned SDK is a deliberate change — update the pin, re-run the gated SDK cross-check (B.5) and the new B.6 scripted-trace tests, and record the bump in the commit.

## The lifecycle and schema shapes (from `acp/schema.py` 0.9.0)

Client→Agent (Drei sends):
- `initialize` — params `{protocolVersion, clientCapabilities, clientInfo}`; response `{protocolVersion, agentCapabilities, agentInfo, authMethods}`.
- `session/new` — params `{cwd (absolute), mcpServers}`; response `{sessionId, ...}`.
- `session/prompt` — params `{sessionId, prompt: [ContentBlock]}` (B.6 uses text blocks only); response `{stopReason}` where `stopReason ∈ {end_turn, max_tokens, max_turn_requests, refusal, cancelled}`.
- `session/cancel` — **notification**, params `{sessionId}`.

Agent→Client (Drei receives/handles):
- `session/update` — notification, params `{sessionId, update}` where `update.sessionUpdate` discriminates: `user_message_chunk` / `agent_message_chunk` / `agent_thought_chunk` (streamed content), `tool_call` / `tool_call_update`, `plan`, `available_commands_update`, `current_mode_update`, `session_info_update`, `usage_update`, `config_option_update`.
- `session/request_permission` — **request** (agent→client), expects a response carrying an outcome. B.6 tracks it as an in-flight *incoming* request but does **not** produce the user-facing approval (B.8).
- `fs/read_text_file` / `fs/write_text_file`, `terminal/*` — capability-gated; B.6 tracks them as in-flight incoming requests; the effect wiring is §C/B.7.

## What this slice (B.6) adds

- **New module `src/drei/acp/machine.py`** — the pure client state machine:
  - `AcpMachine` (frozen dataclass): `phase` (`DISCONNECTED`/`INITIALIZING`/`READY`/`SESSION_ACTIVE`/`PROMPT_IN_FLIGHT`/`CLOSED`), negotiated `agent_capabilities`, `session_id`, monotonically increasing `next_request_id`, and `in_flight` (map of request id → the `Request` sent, for matching responses; covers both outbound client→agent requests awaiting a response and inbound agent→client requests Drei must answer).
  - Pure transition functions:
    - `start() -> (AcpMachine, Request)` — emit the `initialize` request.
    - `new_session(machine, cwd) -> (AcpMachine, Request)` — emit `session/new` (only from `READY`).
    - `prompt(machine, text) -> (AcpMachine, Request)` — emit `session/prompt` with a single text content block (only from `SESSION_ACTIVE`).
    - `cancel(machine) -> (AcpMachine, Notification)` — emit `session/cancel` (only when a prompt is in flight).
    - `handle(machine, message) -> (AcpMachine, list[Message], list[SessionEffect])` — the core fold: given an inbound `Request`/`Notification`/`Response`/`ResponseError` (B.5 types), return the new machine, any outbound messages to send (responses to agent→client requests), and a list of **typed `SessionEffect` values** describing what the session observed (see below).
  - `SessionEffect` — a frozen value describing a semantic observation of the session, so B.7 can translate effects → editor commands without re-parsing ACP: e.g. `Initialized(agent_capabilities)`, `SessionEstablished(session_id)`, `AgentTextChunk(text)`, `ThoughtChunk(text)`, `ToolCallStarted(...)`, `PlanUpdated(...)`, `PromptCompleted(stop_reason)`, `PermissionRequested(request_id, ...)`, `FsReadRequested(...)` / `FsWriteRequested(...)` / `TerminalRequested(...)`, `ProtocolError(...)` / `Cancelled()`.
  - Guards: out-of-phase calls (e.g. `prompt` before `session/new`, `new_session` before `initialize` completes) raise a Drei-owned `AcpStateError`; a response with an unknown/duplicate id → `ProtocolError` effect (never a crash); a `session/update` for the wrong session → ignored-with-`ProtocolError` effect.

### What this slice does NOT add (deferred)

- **No update→command translation** (B.7): `SessionEffect` values are *described*, not turned into editor commands. No `Command`/`Event`, no buffer/window work.
- **No approval bridge** (B.8): `session/request_permission` surfaces as a `PermissionRequested` effect; nothing answers it from a minibuffer prompt.
- **No I/O** (§C): no subprocess, no reader/writer pump, no editor-loop injection. The machine is fed messages by tests; the effect wiring of `fs/*`/`terminal/*` stays abstract (tracked as in-flight requests + effects, not executed).
- **No capability *negotiation* logic** beyond recording what the agent advertised and gating `fs/*`/`terminal/*` effects on it — Drei's *use* of negotiated capabilities lands with the slices that consume them.

## Parity note

No Emacs-facing behavior and no new user-visible command: an internal protocol layer. **No parity registry rows.**

## Implementation order (thin verticals)

1. **`machine.py` scaffolding + handshake** (`start`, `new_session`, `handle` for `initialize`/`session/new` responses): **tests** — happy-path handshake emits exact `initialize` then `session/new` requests with incrementing ids and correct `camelCase` params; `Initialized`/`SessionEstablished` effects; out-of-phase guards raise `AcpStateError`.
2. **Prompt lifecycle** (`prompt`, `cancel`, `handle` for `session/update` + `session/prompt` response): **tests** — exact `session/prompt` request (text content block); each `session/update` variant maps to the right `SessionEffect`; `PromptCompleted(stop_reason)` on the prompt response; `cancel` emits the notification; wrong-session update → `ProtocolError` effect.
3. **Inbound agent→client requests** (`session/request_permission`, `fs/*`, `terminal/*`): **tests** — tracked as in-flight, surface as effects, capability-gated (`fs/*` effect only when the negotiated `agentCapabilities`/`clientCapabilities` permit); the response the machine emits (when one is required by the protocol) matches the id.
4. **Scripted-trace property test** (design 0003 §B.6 verify): a recorded ACP server trace (a full handshake → prompt → streamed updates → completion, and a cancel variant) replayed through `start`/`new_session`/`prompt`/`handle`; assert the **exact sequence of outbound messages** (ids, methods, params) and the **exact sequence of `SessionEffect` values** — not just equality between two runs.

## Acceptance criteria

- Full quality gate green; coverage ratchet held at 100%.
- `drei.acp.machine` imports **no** effect modules and only `drei.acp.{codec,messages}` + stdlib; the purity guard (widened in B.5) covers it.
- Every lifecycle step emits the *exact* outbound message pinned to the 0.9.0 schema (camelCase keys, snake_case discriminators); verified by asserting concrete dicts, not round-trip identity.
- Out-of-phase transitions raise `AcpStateError`; unknown/duplicate response ids and wrong-session updates never crash — they yield `ProtocolError` effects.
- Scripted-trace test asserts exact outbound-message and `SessionEffect` sequences for handshake→prompt→completion and handshake→prompt→cancel traces.

## Risks / open questions

- **`SessionEffect` granularity.** The effect set must be rich enough for B.7 (transcript rendering) and B.8 (approval) without over-modeling. Mitigation: model the update variants 0.9.0 actually defines; keep opaque payloads as `JsonValue` where Drei doesn't yet interpret them (e.g. tool-call internals) so B.7 can refine without breaking the machine.
- **Where capabilities gate.** B.6 records negotiated capabilities and gates *incoming* `fs/*`/`terminal/*` requests on them, but Drei's own advertised `clientCapabilities` (which of fs/terminal/permission Drei claims) is a product decision. Default: advertise the minimum (no fs/terminal) until §C wires those ports, so the agent can't request what Drei can't serve. Recorded as a constant in `machine.py` for the §C slice to widen.
- **Multiple concurrent sessions.** The machine models one session per instance (the common case); a future multi-session buffer story would run multiple machines keyed by `session_id`. Not modeled now — flagged so the `EditorSession` wiring (§C) doesn't assume a singleton.

### Hardening deferred to B.8 / §C (surfaced by the B.6 adversarial review)

The B.6 review found two blocking defects (a `ResponseError` phase deadlock, and an unguarded pre-session `session/update`) — both fixed in-slice. It also flagged follow-ups that only matter once a live peer / approval path exists, deferred here:

- **Answering `session/request_permission` (B.8).** B.6 tracks an incoming permission request in `in_flight_incoming` and surfaces a `PermissionRequested` effect, but never emits the answering `Response` — a real agent will block indefinitely. B.8 (the approval bridge) owns the answer path: it must read/clear `in_flight_incoming` and emit the outcome response. The `in_flight_incoming` dict is currently write-only; that is intentional until B.8.
- **Permission accepted while `fs/*`/`terminal/*` refused.** 0.9.0 `ClientCapabilities` has no permission toggle, so `clientCapabilities={}` tells the agent nothing about permission support — accepting is consistent with the pin. But see above: until B.8 answers, a real agent hangs. Revisit Drei's advertised capabilities when §C wires the fs/terminal ports.
- **String-id echo leaves the request in flight.** An agent that echoes `"2"` for request `2` yields a `ProtocolError` but the int-keyed entry stays in `in_flight_outgoing`. Strictness is arguably correct; §C's pump should decide whether a repeated mismatch tears down the session.
- **No phase gating on inbound agent→client requests.** A `session/request_permission` in `DISCONNECTED` is currently tracked and surfaced. Low risk (fs/terminal/unknown are refused regardless); tighten if §C shows it matters.
- **Dead type pruning.** The never-constructed `FsReadRequested`/`FsWriteRequested`/`TerminalRequested`/`Cancelled` effects and the unreachable `CLOSED` phase were removed in-slice rather than shipped dead (the original plan text mentioned them; the refusal-instead-of-effect behavior for `fs/*`/`terminal/*` is the shipped contract). Reintroduce a close path / those effects only when a slice actually produces them.