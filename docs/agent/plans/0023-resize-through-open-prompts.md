# Twenty-third slice: resize through open prompts (TD-18)

**Status:** ready (issue #75).

**Architecture gate:** design 0002 decision 4 (serialized command/session boundary), plan 0015 D3 (`ResizeFrame` records semantic geometry), and plan 0021 D1/D2 (resize is external and `user_issued = False`). Issue #74 has already selected the missing classification: terminal geometry is an external semantic input, not a user command and not input-focus state.

**Goal:** resizing the terminal while any prompt is open immediately changes the geometry owned by the editor session, records `FrameResized`, redraws the same prompt at the new size, and governs the next geometry-dependent command. Today the harness changes its rendering dimensions even though the session gate silently rejects the resize; this slice removes that split ownership and pays TD-18.

## 1. The acceptance scenario

Text prompt, through the shipped key and resize path:

```text
start at 20x6
press C-x C-f                 → echo row shows `Find file: `
resize to 40x24               → the same prompt remains open on a 40-column echo row
                               → `FrameResized(40, 24)` is recorded
press C-g                     → the prompt closes
press C-x 2                   → the frame splits into two windows
                               (pre-slice: the session still owns height 6 and refuses)
```

The same geometry transition while the other shipped prompt classes are open:

```text
agent presents permission     → choice prompt is visible
resize                         → `FrameResized` is recorded; same request/options remain visible

modify a file; press C-x C-c  → save/exit prompt is visible
resize                         → `FrameResized` is recorded; same save/exit stage remains visible
```

The TermVerify scenario resizes a real ConPTY while `Find file: ` is visible and reads the same prompt from the reflowed frame. Structured in-process assertions remain the oracle for the event, prompt identity, and later split decision.

## 2. What exists today

- `EditorSession.dispatch` gates every non-minibuffer command while `_minibuffer` is active (`src/drei/session.py:661-685`). Its exemption union includes delivery and peer-housekeeping commands, but not `ResizeFrame`, so all text, choice/permission, and exit prompts swallow geometry changes.
- The `ResizeFrame` arm (`src/drei/session.py:791-799`) updates `_frame_size`, emits `FrameResized`, and leaves windows intact. It is already `user_issued = False`, so plan 0021 guarantees a successful resize changes no kill/yank/undo command-chain bookkeeping.
- `EditorHarness.resize` dispatches `ResizeFrame`, then unconditionally assigns its own `_width` and `_height` (`src/drei/harness.py:123-148`). With a prompt open, rendering uses the new harness dimensions while split decisions continue using the session's old dimensions. The TD-18 marker names this disagreement.
- `TestHarnessResize.test_resize_while_the_minibuffer_is_open_is_not_swallowed` currently checks only prompt survival and rendered width. Because those values come from the harness-local assignment, it passes while the session swallowed the command; it is a false pin for the behavior its name claims.
- Plan 0015 explicitly left “resize while the minibuffer is open” to be pinned, then its test failed to assert the semantic side. Issue #74 promotes the correction and requires text, permission, and exit prompt coverage plus a later geometry-dependent decision.
- The shipped TermVerify resize scenario proves ordinary resize/reflow with no prompt open. No shipped-terminal scenario composes resize with the prompt gate.

## 3. Design decisions

### D1. `ResizeFrame` joins the session gate's external-input exemption set

The session gate admits `ResizeFrame` alongside delivery/peer-housekeeping commands. The existing dispatch arm remains the sole mutation point: it validates through the command value, updates session geometry, and records `FrameResized` in order.

- **Alternatives.** (a) Let `EditorHarness.resize` bypass `dispatch` and set session internals: rejected because geometry determines command semantics and must remain replay evidence. (b) Keep swallowing and make the harness read back the applied size: internally consistent but wrong for a live terminal—the editor would render at stale geometry until the prompt closed. (c) Close/reopen the prompt around resize: rejected because terminal geometry is orthogonal to input focus and reopening risks changing queued permission/exit state.
- **On screen:** the open prompt immediately reflows to the terminal's new width and row; its text, choice, or exit stage is unchanged.

### D2. The session remains authoritative; harness dimensions follow the accepted event

`EditorHarness.resize` continues to dispatch exactly once and render exactly once. After dispatch, it derives its render dimensions from the accepted `FrameResized` event rather than blindly assuming the requested command landed. Since a valid `ResizeFrame` is gate-exempt after D1, ordinary and prompted resizes both carry exactly one such event.

- **Alternatives.** Leave the unconditional assignment after adding D1: behavior would work today but would retain two independently mutated geometry owners and allow a future validation/gate change to recreate TD-18. Expose `_frame_size` as a public session reader: unnecessary API growth when the immutable accepted event already carries the authoritative values.
- **On screen:** no difference from D1 in the success path; this prevents future harness/session disagreement.

### D3. Resize preserves every prompt-specific state and does not become a user command

Text input and prompt kind, the exact `PermissionRequested` value/queue position, and the exit sequence's pending buffers/stage all survive unchanged. `ResizeFrame.user_issued` remains `False`; `FrameResized` therefore does not alter kill append, yank-pop, or undo descent.

- **Alternatives.** Treat each prompt class separately in the resize arm: rejected because geometry has one meaning and prompt-specific branches invite omissions when a new prompt kind appears. Make resize user-issued because it is a `Command`: rejected by plan 0021 and Emacs's command-loop model.
- **On screen:** the same prompt remains visible; no answer is selected, no text is entered, and no `Quit`/message is introduced.

### D4. Terminal evidence covers one representative prompt; semantic tests cover the prompt-class cross-product

A TermVerify scenario composes the real resize watcher with the text prompt because that is sufficient to prove bytes → ConPTY resize → input queue → session → redraw. Focused in-process tests enumerate text, permission, and both exit stages, where prompt identity and `FrameResized` can be asserted directly and deterministically.

- **Alternatives.** Three or four ConPTY scenarios: rejected as expensive duplication of one adapter path with weaker semantic observability. No ConPTY scenario: rejected because the defect spans the real resize watcher and shipped prompt rendering, and the existing ordinary-resize scenario does not cross the gate.
- **On screen:** TermVerify reads `Find file: ` at the resized echo row; the other prompt classes have the same visible preservation contract proven beneath the adapter.

## 4. What this slice does NOT do

- TD-15: no ACP decoder or pump drain/error ordering changes. It is issue #74's next separately claimed slice.
- TD-8: no undo stack, redo stack, kill ring, or `BufferValue` construction ordering changes. It follows TD-15 as its own slice.
- TD-14: the initial frame size still is not represented in genesis/replay evidence; only accepted resize commands are recorded.
- TD-10: constrained-height rendering priority is unchanged. A sufficiently short resized frame may still drop the echo row first; issue #74 requires a rendering-policy gate before that debt is paid.
- No recursive minibuffer, prompt redesign, completion, or new geometry/session API.
- No change to resize polling/readiness semantics from plan 0015/0022.

## 5. Pins that change

1. `tests/test_harness.py::TestHarnessResize::test_resize_while_the_minibuffer_is_open_is_not_swallowed` stops accepting harness-local width as proof. It additionally requires exactly `FrameResized(new_width, new_height)` and proves the post-prompt `C-x 2` decision uses the new session height.
2. New focused pins cover choice/permission, save-offer, and final-exit prompts. Each changes from the pre-slice silent outcome `()` to one `FrameResized` while preserving its exact prompt-specific state.
3. A new TermVerify prompt-resize scenario extends the existing shipped resize evidence; existing readiness and ordinary-resize pins remain unchanged.
4. Existing prompt-gate properties that predict silent no-op from command type must classify `ResizeFrame` as exempt. A V1 sweep will locate every such oracle before implementation.

## 6. Owned deviations (parity-registry rows)

No new deviation. This pays an unintended implementation defect and extends the existing “A resize is observed on the input queue, not by a signal” row with the prompt-open case and its exact test citations. The “External deliveries bypass the minibuffer gate” row is narrowed in wording: the gate admits **external semantic inputs**—deliveries, peer housekeeping, and resize—while user editing commands remain blocked. Resize behavior matches Emacs's relevant observable: changing terminal geometry is independent of minibuffer input focus.

## 7. Implementation order (vertical slices, strict TDD)

1. **V1 — text prompt, event and semantic consequence.** Strengthen the existing harness test first: require `FrameResized`, prompt preservation, then close the prompt and prove a split allowed only by the new height succeeds. Observe RED against the swallowed resize. Add `ResizeFrame` to the session exemption and observe GREEN. Sweep all state-predicting prompt-gate properties and update their model, not their invariant.
2. **V2 — prompt-class cross-product.** One RED→GREEN cycle per missing prompt class: permission choice, save offer, final exit gate. Each requires the exact prompt state before/after and one `FrameResized`, with no decision/close event. No production branch per class is expected; if a class needs one, stop and revisit D3 rather than adding special cases.
3. **V3 — authoritative harness geometry.** Add a discriminating test double/session seam that returns no `FrameResized` for a requested resize and prove the harness keeps its prior render dimensions; then change `resize` to adopt dimensions only from the accepted event. Mutation-verify the test against the old unconditional assignment.
4. **V4 — shipped-terminal acceptance.** RED: open `Find file: ` over ConPTY, resize, and assert the same prompt occupies the resized echo row. It must fail against the pre-slice gate for the editor-owned modeline/echo geometry, not merely observe TermVerify's screen model. GREEN through the existing production code—no test-only cooperation.
5. **V5 — records and debt removal.** Update the two parity rows, remove TD-18 and its code TODO only after all acceptance evidence passes, update issue #74's TD-18 checkbox, and amend this status block with the honesty record.
6. **V6 — full gates → draft code PR (`Closes #75`) → fresh adversarial review → fixes and re-gate → ready/merge.**

## 8. Risks / open questions

- **No open product decision.** Issue #74 selected gate exemption and session authority before this plan; the alternatives are recorded in D1/D2 for review.
- **False-green risk in terminal evidence.** TermVerify's screen object resizes even if Drei ignores the event. The scenario must assert a Drei-owned landmark (echo/modeline placement and prompt text), following the existing ordinary-resize test, rather than `len(frame.lines)` alone.
- **Property-oracle risk.** Any command-type model saying “open prompt + non-minibuffer command ⇒ no events” must add the external-input exemption. Updating only focused examples would leave generated histories stale.
- **Constrained-height interaction.** Test geometries remain above all pane+echo minima so TD-10 cannot masquerade as a TD-18 failure.
- **Harness-seam scope.** V3 must not expose session internals merely for testing. Prefer an existing injectable/session subclass seam if one exists after source tracing; otherwise isolate the authoritative-event derivation in a small pure helper and mutation-test it.

## 9. Acceptance criteria

- A resize during a text, permission-choice, save-offer, or final-exit prompt records exactly one `FrameResized(width, height)` and preserves that prompt's complete state.
- After a prompted resize, a geometry-dependent `C-x 2` decision uses the new session size; the harness and session cannot disagree about accepted geometry.
- `EditorHarness.resize` does not adopt dimensions from a swallowed/rejected outcome.
- Resize remains `user_issued = False`; existing undo-descent, kill-append, and yank-pop bookkeeping pins remain green.
- A real ConPTY resize while `Find file: ` is open reflows the shipped editor and leaves the prompt usable, observed through a Drei-owned frame landmark.
- Prompt-gate generated-model/property tests include `ResizeFrame` in the exempt external-input class.
- TD-18 and its `TODO: [tech-debt]` marker are removed; issue #74 marks only TD-18 paid. TD-15 and TD-8 remain untouched and open.
- Relevant parity/architecture prose describes external semantic inputs accurately and cites tests that assert the claimed behavior.
- Full local gate from `AGENTS.md` is green, coverage remains 100%, and GitHub CI passes on Python 3.12–3.14 across Windows and Linux.
