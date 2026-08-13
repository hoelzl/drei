# Twenty-fourth slice: ordered ACP decode results (TD-15)

**Status:** implemented on the code branch (issue #79). Honesty record: the
machine-valid final-chunk acceptance trace is update → malformed line → prompt
completion → immediate exit, avoiding the plan's warned ambiguity around a
post-completion update. The codec now returns frozen `DecodedFrame` and
`DecodeFailure` values for every complete nonblank line, retains only a partial
trailing line, and the pump folds one ordered result sequence through one
delivery. `AcpDecodeError` was removed rather than retained as uncovered dead
exception control flow. Regroup-failures-first and stop-at-first-failure
mutations both fail the focused codec/pump order tests. Full gates and code-PR
review remain the delivery gate. Round-one review added package-level runtime
and strict-type exports for `DecodeResult` and the canonical `JsonValue` alias,
and placed both result records in the frozen/slotted record matrix; the exact
mutable-record mutation now fails that matrix.

**Architecture gate:** design 0003 consequences 2/3 and feature B.5 put ACP
framing in a pure transport-independent codec and require asynchronous peer
input to cross the serialized command boundary in order. Design 0005 D3 keeps
one ACP pipe read to one accumulated session-effects delivery/redraw. Issue #74
selects the missing contract: decoded frames and framing failures are one
ordered result stream, not values returned on one call plus exceptions and
parked values recovered on another.

**Goal:** when a peer writes valid ACP frames around one or more malformed lines,
Drei records every valid effect and every protocol error in wire order, including
the child's final read immediately before exit. The user sees the completion or
streamed text that arrived first, then the error, then any later valid effect;
no valid frame waits for another read or disappears when the process exits.

## 1. The acceptance scenario

A scripted ACP child is already in a live turn with its transcript visible:

```text
child writes one pipe chunk:
  valid session/prompt completion
  malformed NDJSON line
  valid session/update notification
child exits immediately                 → transcript shows the turn completion
                                        → then `protocol error: ...`
                                        → then the later valid update/error
                                        → then `agent exited (status 1)`
                                        → each complete line appears once
                                        → editor remains usable
```

The primary executable acceptance is deterministic at the pump seam rather than
ConPTY: feed that exact byte chunk to `AgentPump.receive`, then call
`AgentPump.exited` without another receive. Assert the ordered transcript suffix
and the machine/reset consequences. A second scenario uses
`valid → malformed → valid` and multiple consecutive malformed lines in one
`receive`, proving the behavior does not rely on process exit.

This slice has a user-visible result despite using a fake child: protocol
failures and child exit are already rendered in the visible `*agent*` transcript,
and the regression is the order and survival of those lines. A real subprocess
or provider would add scheduling variability without exercising another product
branch.

## 2. What exists today

- `JsonRpcDecoder.messages()` (`src/drei/acp/codec.py:60-74`) returns decoded
  values but raises `AcpDecodeError` at the first malformed line. Values parsed
  before that line remain in `_parsed`; bytes after it remain in `_buffer`.
  Therefore one wire chunk is split across later `messages()` calls.
- The codec claims no valid frame is lost, but guarantees that only across a
  future call. It cannot represent `value → failure → value` in one return value,
  and repeated malformed lines require repeated exceptional calls.
- `AgentPump.receive()` (`src/drei/pump.py:267-291`) intends to drain every
  complete frame, fold all machine effects, and dispatch them once. `_drain`
  catches one `AcpDecodeError`, appends its `ProtocolError`, returns no frames,
  and leaves earlier/later values for the next receive. Thus the displayed
  protocol error can precede a valid effect that came first on the wire.
- `AgentPump.exited()` reports exit and immediately `_reset()`s the decoder
  (`src/drei/pump.py:293-305`). If no later `AgentBytes` event arrives, parked
  complete frames disappear. This is reachable because `AgentIo._read_wire`
  queues each non-empty read and then queues `AgentExited`; the main loop handles
  those events in FIFO order, but `_drain` has postponed part of the first event.
- Existing codec tests explicitly pin recovery on a second `messages()` call,
  including `test_decoder_preserves_frames_parsed_before_malformed_line`. That
  test encodes the debt rather than the desired ordering.
- Existing pump tests cover a single malformed frame and batching of many valid
  frames, but not values around decode failures, repeated failures, or a final
  mixed chunk followed by exit.

## 3. Design decisions

### D1. One drain returns an ordered sequence of decoded values and decode failures

The codec exposes immutable discriminated results for each complete nonblank
line, for example `DecodedFrame(value)` and `DecodeFailure(line)`. Draining
consumes every complete line currently buffered and returns those results in
wire order. A malformed line is data at the untrusted framing boundary, not
exceptional control flow for the drain. The normalized failure carries only
stable offending bytes; raw `JSONDecodeError`/`UnicodeDecodeError` text does not
cross into deterministic transcript effects.

- **Alternatives.** Keep exceptions and repeatedly call `messages()` until it
  stops raising: rejected because it preserves a stateful hidden park and makes
  order recovery indirect. Return `(messages, errors)` as two lists: rejected
  because grouping by type destroys interleaving. Yield a generator that can
  raise: rejected because partial consumption recreates the ownership problem
  and makes reset/exit safety caller-dependent.
- **On screen:** valid effects and `protocol error` lines appear in the same
  order as their complete wire lines.

### D2. The decoder retains only an incomplete trailing line

After one drain, every newline-terminated line—valid, invalid UTF-8, malformed
JSON, or blank—is consumed. Blank lines remain tolerated and produce no result.
Only bytes after the final newline remain buffered for the next `feed`; `_parsed`
and its cross-call parking semantics disappear.

- **Alternatives.** Preserve a completed-result queue inside the decoder: rejected
  because the caller has already asked to drain and can own the returned values.
  Treat an incomplete line as a failure on each read or on exit: rejected because
  pipe chunk boundaries are arbitrary and current ACP framing requires newline
  completion; changing EOF semantics is outside TD-15.
- **On screen:** nothing for partial bytes or blank lines; complete lines no
  longer wait for an unrelated future read.

### D3. The pump folds ordered results in one receive and preserves D3 batching

`AgentPump.receive()` walks the ordered decode results exactly once. For a
decoded value it parses/handles the ACP message as today; for a decode failure it
appends a deterministic `ProtocolError` at that position. All resulting
`SessionEffect`s still go through one `_apply` call, so many valid frames—or
valid/error mixtures—in one pipe read cost one delivery/redraw. Outbound replies
remain emitted while their input frame is handled, in wire order.

- **Alternatives.** Call `_apply` at each error boundary: ordering would be
  visible but would regress design 0005 D3's one-read/one-redraw property. Build
  separate valid/error effect lists and concatenate: rejected because it repeats
  the defect. Put decode failures through `parse_message`: rejected because
  framing and JSON-RPC envelope failures are distinct boundaries even though
  both render as `ProtocolError`.
- **On screen:** one redraw presents the final ordered transcript suffix; there
  is no transient frame with the error ahead of an earlier completion.

### D4. Exit consumes no hidden completed work

The event queue remains authoritative: `AgentBytes(final_chunk)` is handled
before the following `AgentExited`. D1/D2 ensure `receive(final_chunk)` has no
hidden completed results left when `exited()` resets the child-local decoder.
An incomplete trailing line is discarded on reset as before; the exit line is
then delivered after every complete final-frame effect.

- **Alternatives.** Add an `exited()` drain of the codec park: rejected as a
  symptom patch that cannot restore interleaving and would be unnecessary once
  draining is total. Convert incomplete EOF bytes into a protocol error: a
  reasonable future policy, but not required to pay TD-15 and not specified by
  the existing transport contract.
- **On screen:** a final valid completion precedes the malformed-line error and
  exit status; no complete frame is lost when the child exits immediately.

### D5. Codec failures remain normalized and deterministic

`AcpDecodeError` may remain as a compatibility/exported value only if another
caller needs it; the shipped decoder drain does not embed `str(cause)` in its
result. The pump creates a stable detail from the offending bytes using the
project's existing protocol-error vocabulary. Tests assert ordering and stable
classification, not Python-version-specific parser diagnostics.

- **Alternatives.** Carry the exception object/cause in the immutable result:
  rejected because exception messages vary by Python version and should not
  enter deterministic events. Remove every error type immediately: acceptable
  only if source tracing proves there are no external/internal users; API
  cleanup is subordinate to the behavioral contract.
- **On screen:** the protocol-error detail remains useful and stable across
  Python 3.12–3.14 and both CI operating systems.

## 4. What this slice does NOT do

- TD-8: no undo/redo stack, kill-ring, or `BufferValue` construction-order
  changes. It remains the next separately claimed slice.
- TD-16: no decision on permission requests outside a live turn. A valid
  JSON-RPC request still reaches the existing machine phase gate unchanged.
- TD-7: no deep-freezing of arbitrary decoded JSON payloads.
- No ACP schema/version change, framing change, request/response semantics,
  subprocess threading redesign, event-queue ordering change, or transcript UI
  redesign.
- No new EOF rule for a non-newline-terminated trailing fragment.
- No ConPTY or real-provider scenario: the bug and acceptance oracle live at the
  pure codec + deterministic pump seam, and the visible transcript is asserted
  there.

## 5. Pins that change

1. `test_decoder_malformed_line_raises_acp_decode_error` changes from an
   exception assertion to one ordered decode-failure result carrying the bytes.
2. `test_decoder_recovers_after_malformed_line` and
   `test_decoder_preserves_frames_parsed_before_malformed_line` stop making a
   second drain. They require `failure → value` and
   `value → failure → value` respectively in one result sequence.
3. Add multiple-consecutive-failure and partial-trailing-line codec pins. The
   next feed completes only the trailing line; no previous completed result
   repeats.
4. `test_many_frames_in_one_read_are_one_delivery` remains unchanged and is a
   non-regression gate for D3 batching.
5. Pump-level ordered transcript tests add valid completion/error/later-valid
   mixtures and immediate exit. They assert exact relative order and
   no duplication/loss, not only substring presence.

## 6. Owned deviations (parity-registry rows)

No GNU Emacs parity row changes: ACP framing and transcript errors have no Emacs
equivalent and this slice corrects an unintended internal ordering defect. The
architecture/verification knowledge should mention ordered framing failures if
a current statement claims only valid frame order; no product deviation is
introduced.

## 7. Implementation order (vertical slices, strict TDD)

1. **V1 — visible final-chunk survival and order.** At the pump seam, construct
   a live turn and feed `valid completion → malformed line → valid notification`
   in one `receive`, immediately followed by `exited`. Assert the exact relative
   transcript order, each marker once, the exit line last, and editor recovery.
   Observe RED: completion/later frame are parked or lost and error appears
   first. This is the user-visible acceptance test and remains red until the
   codec/pump vertical path changes.
2. **V2 — ordered codec contract.** Add focused RED codec examples for
   `value → failure → value`, consecutive failures, invalid UTF-8, blank lines,
   and a trailing partial frame. Introduce immutable result variants and make
   one drain consume every complete line in order. Remove `_parsed` parking.
3. **V3 — one-pass pump fold.** Replace exception-driven `_drain` with one walk
   over ordered results. Keep exactly one `_apply` per receive and run the
   existing many-valid-frames delivery pin. Mutation-verify by regrouping errors
   before values and by stopping at the first failure; both must fail V1/V2.
4. **V4 — exit and no-duplication matrix.** Add pump pins for multiple malformed
   lines, valid/error/valid without exit, and final chunk + immediate exit.
   Require stable ordering, exactly-once effects, machine reset, permission
   cleanup, and successful next-child startup. Verify incomplete trailing bytes
   remain child-local and cannot contaminate the next decoder.
5. **V5 — records and debt removal.** Remove TD-15 and its code TODO only after
   all acceptance evidence passes; update issue #74's TD-15 checkbox while
   leaving TD-8 open; amend this status block with what implementation/review
   changed. Update relevant architecture prose only where the old park/exception
   contract is described.
6. **V6 — full gates → draft code PR (`Closes #79`) → fresh exact-SHA
   adversarial review → fixes and re-gate → ready/merge.**

## 8. Risks / open questions

- **Recommendation: ordered result variants, not repeated exceptions.** This is
  the only option that makes interleaving explicit and total in the type. The
  user gate should reject it only if preserving `messages() -> list[JsonValue]`
  is an external compatibility requirement; Drei is pre-1.0 and source tracing
  currently finds only tests and `AgentPump` as callers.
- **Batching versus observable order.** One `DeliverSessionEffects` can preserve
  effect order while producing one final frame. Tests must inspect the ordered
  transcript/event suffix, not expect intermediate redraws.
- **Stable error detail.** Current `AcpDecodeError.__str__` includes parser text.
  The implementation must choose and pin a version-independent detail before
  removing TD-15; raw parser diagnostics must not enter deterministic events.
- **Completion followed by update legality.** A post-completion update may be
  rejected by the ACP machine and render another protocol error. The acceptance
  oracle is wire-order survival, not that every syntactically valid frame is
  semantically accepted. If a clearer valid later effect is needed, choose a
  machine-valid trace rather than weakening order assertions.
- **Mutation discrimination.** A codec-only mutation may be hidden by one final
  folded transcript string. Tests must use unique visible markers and count/order
  assertions so regrouping, dropping, and duplication each fail for the intended
  reason.

## 9. Acceptance criteria

- One decoder drain returns an ordered immutable result for every complete
  nonblank line: decoded value or normalized decode failure.
- `valid → malformed → valid` and multiple malformed lines are represented in
  exact wire order in one drain; no second call is required.
- Partial trailing bytes remain buffered until completed, while all preceding
  complete results are returned exactly once.
- One `AgentPump.receive` preserves decode and machine-effect order through one
  accumulated `_apply`/delivery; existing one-read/one-redraw batching remains
  green.
- A final complete turn result before a malformed line survives an immediate
  child exit, appears before the protocol-error line, and the exit line appears
  last. No complete frame is duplicated or lost.
- Decoder failures produce stable deterministic protocol-error details; raw
  platform/Python parser exception text does not enter transcript events.
- Exit still aborts pending permission UI, resets machine/decoder/process state,
  and permits a fresh child/session; incomplete final fragments do not
  contaminate the next child.
- TD-15 and its `TODO: [tech-debt]` marker are removed; issue #74 marks TD-15
  paid. TD-8 remains untouched and open.
- Full local gate from `AGENTS.md` is green, coverage remains 100%, and GitHub CI
  passes on Python 3.12–3.14 across Windows and Linux.
