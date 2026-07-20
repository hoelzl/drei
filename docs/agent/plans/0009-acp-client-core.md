# Ninth slice: ACP client core (B) — framing codec first

**Status:** ready — architecture gate: a new **pure protocol layer** (`drei.acp`) with no effect imports, behind the same determinism discipline as the rest of the core (design 0001). No change to `BufferValue`, the existing `Command`/`Event` surface, the transcript fold, or any existing property. The codec and protocol model are **pure functions/values over bytes and dicts** — no `subprocess`, no `asyncio`, no I/O. Effects stay behind the ports; this slice only *describes* the messages that will later flow across the `ProcessPort` delivery seam (0008).

**Goal:** Drei can speak the Agent Client Protocol's wire format and model the client-side session lifecycle as pure, replayable values — **without** yet spawning `hermes acp` or touching the editor loop. This is design 0003 §B. The slice lands the transport-agnostic core first (framing codec + protocol message model), so every later slice (the state machine, the update→command translation, the approval bridge, and the real `hermes acp` launcher in §C) builds on a verified, property-tested foundation.

## Why this slice, and why scoped this way

Design 0003 §B lists four capabilities (B.5 framing codec → B.6 session state machine → B.7 `session/update`→command translation → B.8 approval bridge). They are ordered prerequisites, but they are **not** one PR-sized slice. The honest scoping question is how much to land before the first editor-visible behavior exists.

Two resolutions were possible:

- **(a) Land the whole §B core at once** (codec + state machine + translation + approval) as one large PR, then wire it to the editor in §C.
- **(b) Land §B incrementally**, smallest-first: the framing codec (B.5) and protocol message model as a self-contained pure module, then the state machine, then translation, then approval — each its own tested slice.

**Decision: (b), starting with B.5 + the message model.** The codec is the foundation everything else depends on, is pure and property-testable in isolation, and has a hard external contract (the ACP wire format) that must be pinned *exactly* before any state machine is built on it. Landing it separately keeps each PR reviewable and lets the state-machine slice assume a verified codec. This mirrors how 0008 landed the `ProcessPort` boundary before any consumer. The full §B arc is tracked here so the sequencing and the end-state are explicit, but **this PR implements only B.5 (codec + message model)**; B.6–B.8 follow as their own slices.

### The wire contract (pinned from the real peer)

The only agent Drei speaks to is `hermes acp`, which uses the official ACP Python SDK. The codec must match that SDK byte-for-byte on the wire. Pinned facts (verified against the installed SDK):

- **Framing:** newline-delimited JSON (NDJSON) — one JSON-RPC value per line, `\n`-terminated, **no** `Content-Length` headers. (`acp/stdio.py:56` `readline()`; `acp/connection.py` docstring "newline-delimited JSON frames".)
- **Encode:** `json.dumps(payload, separators=(",", ":")) + "\n"`, utf-8. (`acp/task/sender.py:33`.)
- **Decode:** read one line, `json.loads(line)`. (`acp/connection.py:151-155`.)
- **Envelope:** JSON-RPC 2.0 — requests carry `jsonrpc`/`id`/`method`/`params`; responses carry `jsonrpc`/`id` + `result` **or** `error{code,message}`; notifications carry `jsonrpc`/`method`/`params` and **no** `id`. ACP object keys are `camelCase`; discriminator strings are `snake_case`.

### The methods Drei's client must model

Client→Agent (Drei sends): `initialize`, `authenticate` (if required), `session/new`, `session/load` (optional), `session/prompt`, `session/cancel` (notification).
Agent→Client (Drei receives/handles): `session/update` (notification: message chunks, tool calls, plans, mode changes), `session/request_permission` (request), `fs/read_text_file`, `fs/write_text_file`, `terminal/*` (capability-gated).

## What this slice (B.5) adds

- **New module `src/drei/acp/__init__.py`** — package marker; re-exports the public surface.
- **New module `src/drei/acp/codec.py`** — the framing codec as pure functions:
  - `encode(message: JsonValue) -> bytes` — one value → one `\n`-terminated utf-8 NDJSON frame, compact separators, matching the SDK byte-for-byte.
  - `JsonRpcDecoder` — an incremental, chunk-safe decoder: `feed(data: bytes) -> None` buffers arbitrary byte fragments; `messages() -> list[JsonValue]` drains and returns each complete parsed frame. Handles partial lines across `feed` calls, multiple frames in one chunk, and a trailing partial line. This is the shape the later streaming pump (§C) needs: bytes arrive in arbitrary chunks from the child, not line-aligned.
  - Decode errors: a malformed line raises a Drei-owned `AcpDecodeError` carrying the offending bytes — never a bare `json.JSONDecodeError` leaking across the boundary (same normalized-error discipline as `files`/`process`).
- **New module `src/drei/acp/messages.py`** — the JSON-RPC 2.0 envelope model as frozen dataclasses + builders/parsers, so the state machine never hand-builds dicts:
  - `Request(id, method, params)`, `Notification(method, params)`, `Response(id, result)` / `ResponseError(id, code, message, data)`.
  - `to_json(...) -> dict` / `parse_message(dict) -> Request | Notification | Response | ResponseError`, with a Drei-owned `AcpProtocolError` on a structurally invalid envelope (missing `jsonrpc: "2.0"`, both `result` and `error`, an `id` on a notification, etc.).
  - Method-name constants for the ACP method set above (no stringly-typed dispatch downstream).

### What this slice does NOT add (deferred)

- **No state machine** (B.6): no `initialize`→`session/new`→`session/prompt` lifecycle, no capability negotiation, no in-flight request tracking.
- **No update→command translation** (B.7): nothing maps `session/update` into editor commands yet.
- **No approval bridge** (B.8): `session/request_permission` is only a *parseable* message here, not a wired minibuffer prompt.
- **No I/O**: no subprocess, no `asyncio`, no reader/writer pump, no editor-loop injection point. The codec is fed bytes by tests; §C wires it to the `ProcessPort`.
- **No editor change**: no new `Command`/`Event`, no `EditorSession` change, no buffer/window work.

## Parity note

No Emacs-facing behavior and no new user-visible command: this is an internal protocol layer. **No parity registry rows.** The contract being honored is the ACP wire spec + the real `hermes acp` SDK's framing, not GNU Emacs.

## Implementation order (thin verticals)

1. **`acp/codec.py`**: `encode`, `JsonRpcDecoder`, `AcpDecodeError`. **Tests:** encode matches the SDK's exact bytes for representative payloads (compact separators, `\n` terminator, utf-8); round-trip `decode(encode(m)) == m`; **property** (hypothesis): for arbitrary generated JSON values and arbitrary chunk splits of the encoded bytes, the decoder yields exactly the original values in order (encode∘decode = id under chunking); multiple frames in one chunk; trailing partial frame across feeds; malformed line → `AcpDecodeError` carrying the bytes, decoder state recoverable.
2. **`acp/messages.py`**: envelope dataclasses, `to_json`/`parse_message`, method constants, `AcpProtocolError`. **Tests:** build each of the client→Agent methods and parse it back; parse each Agent→Client method shape; reject structurally invalid envelopes (both result+error, notification with id, wrong/missing `jsonrpc`) with `AcpProtocolError`; round-trip `parse_message(to_json(x)) == x` for every variant.
3. **Cross-check against the real SDK** (gated, availability-guarded like the parity probe): import the installed `acp` SDK's sender framing and assert Drei's `encode` produces identical bytes for a fixed payload — pins the wire contract to the real peer, skips when the SDK isn't importable. **No dependency added**: the check imports `acp` only if present (guarded), mirroring how `drei` already probes `docker` for parity.

## Acceptance criteria

- Full quality gate green (`pytest --cov`, `ruff check`, `ruff format --check`, `mypy src tests`, `pre-commit run --all-files`); coverage ratchet held at 100%.
- The new modules import **no** effect modules (`subprocess`/`os`-to-launch/`asyncio`/`socket`); extend the existing purity guard to cover `acp/` (it already scans `src/drei/*.py` — confirm `acp/*.py` is included or widen the glob).
- Codec pinned by property: encode∘decode = id under arbitrary chunking; malformed input → `AcpDecodeError` (never a raw JSON error across the boundary).
- Message model: every ACP method Drei must speak is representable and round-trips; invalid envelopes rejected with `AcpProtocolError`.
- Wire contract cross-checked byte-for-byte against the real ACP SDK when available (gated).

## Risks / open questions

- **Framing assumption.** This slice pins NDJSON because that is what the official SDK (and therefore `hermes acp`) uses. If a future ACP revision or a different agent adopts `Content-Length` framing, the codec's framing is isolated in `acp/codec.py` — the message model (`acp/messages.py`) is framing-agnostic and unaffected. Mitigation: keep framing and envelope strictly separate (they are).
- **Chunked decode shape.** `JsonRpcDecoder.feed/messages` is chosen for the §C pump, but no consumer exists yet. Risk of a speculative API. Mitigation: it is the *minimal* incremental-decoder shape (buffer + drain), directly required by "bytes arrive in arbitrary chunks," and is fully exercised by the chunking property today. If §C ends up wanting a `readline`-style pull instead, the change is confined to this module.
- **Purity guard scope.** The current guard globs `src/drei/*.py`; it must also cover `src/drei/acp/*.py` or the new package escapes the launch-check. Widening the glob is part of this slice.
- **`_meta`/extensibility.** ACP allows `_meta` fields and `_`-prefixed methods. This slice passes them through opaquely (params is an open dict); no Drei-specific extension is modeled yet.
