# 0003: Hermes–Drei integration (Drei as an ACP client)

**Status:** proposed — direction committed, feature list drives future slices
**Builds on:** `0001-foundation.md`, `0002-live-editor-state-architecture-spike.md`
**Does not revise:** 0001/0002. This record applies their boundaries to a new direction; it changes none of their decisions.

## Decision

Drei will become a usable host for AI coding agents by acting as an **ACP client**: it spawns and drives an external agent process — initially `hermes acp` — over the Agent Client Protocol (JSON-RPC on stdio), rendering the agent's chat, tool activity, file diffs, terminal commands, and approval prompts into ordinary Drei buffers. Hermes remains the agent; Drei remains the editor. The integration is a new effect port and a set of buffers/commands layered on the existing deterministic core, not a change to editor semantics.

This commits to the **client** direction and to **Hermes-over-ACP** as the first concrete target. It deliberately does *not* pursue two alternatives:

- **Drei as an ACP server** (an agent inside Drei driven by another editor) — rejected as the first move: it inverts the value. The goal is a human/agent pair working *in* Drei, and Drei's near-term differentiator is being the editor an agent can drive *and* a human enjoys.
- **A bespoke Hermes↔Drei protocol** — rejected: ACP already exists, is implemented by `hermes acp`, and is editor-agnostic. Adopting it buys VS Code/Zed/Emacs-client parity for free and keeps the protocol contract in its owner's repo, per the provenance rule.

**Re-evaluation trigger:** if ACP's capability surface cannot express a Drei capability we require (e.g. a semantic observation richer than ACP's session/update stream) after a concrete slice attempts it, revisit the protocol choice in a new design record rather than weakening ACP or forking it silently.

## Why

The user-facing value is running Hermes Agent — with its persistent memory, automated skill improvement (curator), session store, and provider resolution — *inside* an editor the human actually edits in, instead of treating the agent as a separate terminal program. ACP is the load-bearing fact that makes this cheap: `hermes acp` already exposes exactly the stream an editor needs (chat messages, tool calls, diffs, terminal commands, approval prompts, streamed chunks) and already reuses the normal Hermes configuration and skills. Drei does not have to re-implement an agent, a skill system, or a memory system; it has to be a good ACP client and a good editor. That is a bounded, testable problem that fits Drei's existing port-and-adapter architecture.

This direction is also the natural continuation of Drei's own constraints. The architecture record already requires that effects enter through explicit ports and that semantic state be verifiable without screen scraping. An ACP subprocess is precisely such an effect port: launch `hermes acp`, pipe stdio, translate protocol notifications into Drei commands and buffer updates. The deterministic core stays pure and property-testable against a fake ACP peer; the real `hermes acp` wiring is a thin, separately verified adapter — the same discipline Drei already applies to its terminal frontend.

## Vocabulary

Two ordinary words each carry two senses in this integration; conflating them is the fastest route to confusion, so this record assigns each a distinct term on first use.

- **agent / client / server (process roles).** The *agent* is the program that does the reasoning and tool use — here, the `hermes acp` subprocess. ACP is asymmetric: one side is the **client** (the editor, which initiates sessions and displays results) and one side is the **server** (the agent, which accepts `session/new`, `session/prompt`, etc.). **Drei is the client; Hermes is the server.** "Client" in this record never means "the human's editor frontend to Drei"; Drei's own TUI is its *frontend*, not its ACP role.
- **buffer.** Keeps its existing Drei meaning (editable text plus buffer-local state). This record introduces one specialization, the **agent buffer** — a Drei buffer whose text is the rendered transcript of an ACP session and whose contents are produced by applying ACP `session/update` notifications through the command boundary. An agent buffer is a buffer; not every buffer is an agent buffer. Editing commands behave normally in it; inserting the agent's streamed text is an effect delivered through the port, not a user edit.
- **port.** Keeps its existing meaning (an explicit effect boundary). The **ACP port** is the narrow interface through which the live session launches the agent subprocess and exchanges JSON-RPC messages. It sits at the same architectural level as the filesystem/terminal ports in `docs/knowledge/architecture.md`; the live model never talks to the subprocess directly.
- **surface.** The *surface* is where a human interacts with Hermes (the Hermes CLI, the Ink TUI, the desktop app, a gateway platform, an ACP editor). This record makes Drei one more surface. Hermes' skill/memory/curator systems live in the agent core, not in any surface — which is why they carry over unchanged.

These senses are orthogonal: a process-role term (client/server), a buffer-kind term (agent buffer), a boundary term (port), and a product term (surface) never overlap in what they name.

## Consequences (accepted deliberately)

1. **Drei takes a hard external-process dependency at runtime, not in the core.** The editor must spawn, monitor, and cleanly shut down a subprocess and speak framed JSON-RPC over its pipes. Accepted: this is the cost of any real agent integration, and it is confined to the ACP port; the deterministic core never imports a process library. This is what keeps the core property-testable with an in-memory fake peer.
2. **Drei must tolerate an asynchronous, non-deterministic-by-design peer.** The agent streams partial chunks, takes unbounded time, and can be cancelled. Accepted: asynchrony enters only as *external deliveries* across the command boundary (the same shape as 0002's "deterministic delivery of process output"), never as ambient mutation. The live model applies each ACP `session/update` as one atomic command/event, so replay and observation stay well-defined even though the peer's timing is not.
3. **The deterministic core must not depend on Hermes, ACP, or network.** Accepted and required by the non-negotiable rules: the ACP *client logic* (translating protocol messages into commands and observations) is core-adjacent and pure; the ACP *transport* (subprocess + stdio framing) is a port. Verification uses a scripted fake ACP server; only a thin integration slice launches the real `hermes acp`, mirroring the direct/terminal two-profile rule already in 0001.
4. **Some Hermes features are out of scope at the client.** The ACP toolset intentionally excludes cronjob management and messaging delivery; Drei as an ACP client inherits that exclusion. Accepted: the goal is an editor workflow, and the curator (automated skill improvement) is a background Hermes process that keeps working regardless of surface. If a future need requires cron/messaging from inside Drei, that is a separate integration, not an ACP extension.
5. **Approval prompts become a Drei UI responsibility.** ACP routes dangerous-command approval back to the client (allow once / allow for session / allow always / deny). Accepted: Drei must render an approval prompt and return a decision. This is a feature Drei does not yet have (interactive prompting in the minibuffer) and is listed in the feature list rather than assumed.

## Feature list (what Drei must grow)

Each item names the capability, why the integration needs it, and the verification shape. Items are ordered so earlier ones are prerequisites of later ones; the first several are valuable editor features independent of ACP. No item is a speculative framework layer — each is introduced by a tested vertical slice, per 0001.

### A. Editor prerequisites (agent-independent, but required)

1. **Subprocess effect port.** Launch/monitor/terminate a child process; deliver its stdout/stderr lines and exit status into the session as immutable external-input events. *Why:* ACP is stdio JSON-RPC to a subprocess. *Verify:* in-process with a scripted fake child emitting known bytes; property: delivered events are ordered and replayable.
2. **Multiple buffers and windows.** At least two windows over distinct buffers with independent points, so an agent transcript can sit beside the buffer being edited. *Why:* the agent's activity must be visible alongside the work. *Verify:* observation records of window/point layout; existing window stress cases in 0002 already cover shared-buffer independent points.
3. **Read-only / generated buffers.** A buffer whose text is produced by the port and is not user-editable in place (or is editable only in a controlled region), for the streaming transcript. *Why:* the agent's output must not be corrupted by point motion/yank between updates. *Verify:* property that transcript text equals the fold of delivered `session/update` events.
4. **Minibuffer / interactive prompting.** A command that prompts the user and returns a choice (for approval decisions and for entering a prompt to send). *Why:* approvals and the human's own prompts both need it. *Verify:* command-level tests driving the prompt to each outcome.

### B. ACP client core (protocol-correct, transport-agnostic)

5. **JSON-RPC framing codec.** Encode/decode ACP's JSON-RPC messages (headers + length-prefixed or newline-delimited framing per the protocol) as pure functions over bytes. *Why:* transport-independent and property-testable. *Verify:* round-trip properties (encode∘decode = id) over generated messages, including partial/chunked delivery.
6. **ACP session state machine (pure).** Model the client side of the protocol: `initialize` → `session/new` → `session/prompt` → stream of `session/update` → completion/cancel, plus `fs/read_text_file`/`write_text_file` and `terminal` capability negotiation. Input messages in, output messages + editor effects out, all immutable values. *Why:* this is the heart of the client and must be verifiable without a real agent. *Verify:* a state-machine property test against a scripted ACP server trace; assert exact outbound messages, not just equality between two runs.
7. **Translation of `session/update` into commands.** Map each ACP notification (agent message chunk, tool call, tool call update, plan, thought) onto a Drei command that updates the agent buffer and, for file writes, routes through the filesystem port. *Why:* keeps the live model authoritative and replayable. *Verify:* transcript-fold property (3) plus per-notification command tests.
8. **Approval bridge.** When the agent requests `session/request_permission`, present a minibuffer prompt (4), return the user's `allow_once` / `allow_session` / `allow_always` / `deny`, and honour session-scoped auto-approval within the ACP session. *Why:* safety gate the human controls. *Verify:* each decision maps to the correct protocol response; session-scoped cache resets on new session.

### C. Integration slice (real agent, thin and separately gated)

9. **`hermes acp` launcher adapter.** Resolve the `hermes` executable, spawn `hermes acp` (or `python -m acp_adapter`), wire its stdio to the ACP port, and surface its stderr as a diagnostics buffer. *Why:* the only place a real subprocess is required. *Verify:* a smoke scenario launching the real binary behind an availability check (skip when `hermes` is absent), mirroring the pinned-reference pattern.
10. **End-to-end agent scenario.** A TermVerify-style scenario: open a file, send a prompt, observe the agent buffer accumulate the streamed response and the target buffer receive the agent's edit. *Why:* proves the shipped path, not just the fake peer. *Verify:* terminal frames plus semantic observation records; the real-agent scenario is gated on `hermes` availability and a configured provider.

### D. Explicit non-goals for this direction

- Emacs Lisp compatibility, an ACP *server* in Drei, cron/messaging surfaced in Drei, and any weakening of the deterministic core to accommodate the agent. Networking stays out of the core; the only process Drei speaks to is the local `hermes acp` child.

## Relationship to existing records

- **0001** — this record uses the deterministic-core-with-effect-ports foundation exactly as written; the ACP port is a new instance of the explicit-effect-port rule, and the direct-fake / real-agent two-profile verification mirrors the existing direct/terminal split.
- **0002** — the agent's streamed updates are delivered as *external inputs* across the serialized command boundary, the same shape 0002 established for "deterministic delivery of process output." Agent buffers are identity-owned entities in the live model; their rendered text is an observation/event fold, not ambient state. No ownership decision from 0002 is revisited.
- **`docs/knowledge/architecture.md`** — the dependency arrow gains one frontend-adjacent adapter (`ACP client adapter → ACP port`) feeding the same command/session boundary; the live model and observation model are unchanged.

## Open questions

- **Framing and exact capability set of the target ACP version.** The codec (5) and state machine (6) must be pinned to a specific ACP schema and a specific `hermes acp` version. *Trigger:* before slice 5/6, record the exact protocol revision and the `hermes` version pin, per the immutable-external-inputs rule; do not leave "current ACP" as a floating reference.
- **How much of the transcript is editable.** Whether the human may edit the agent buffer mid-session (and how that reconciles with subsequent `session/update` folds) is undecided. *Trigger:* slice 3 (read-only/generated buffers) should resolve the default; revisit only if a real workflow demands mid-stream edits.
- **Session persistence across Drei restarts.** ACP sessions are scoped to the running server process; whether Drei should be able to resume a prior Hermes session is undecided and depends on Hermes-side support. *Trigger:* only after the end-to-end slice (10) works; not a prerequisite.

## What this record does not decide

The text store, the concrete minibuffer implementation, the key bindings for agent commands, and the exact ACP revision pin are all deferred to the slices that own them. This record commits the *direction* (Drei as ACP client driving `hermes acp`), the *boundary placement* (transport in a port, protocol logic pure, effects across the command boundary), and the *feature set*; it leaves representation choices to measured slices, consistent with 0001 and 0002.
