# Twenty-first slice: last-command bookkeeping keys on user commands (review 0002 cluster A)

**Status:** ready (issue #63).

**Architecture gate:** review 0002
(`agent/reviews/0002-adversarial-review-2026-07-27.md`) **cluster A, finding
1** — the range's one critical finding — with TD-13 (finding 6) riding on the
same classification. No design record owns this; the fold's bookkeeping
contract lives in `session.py` and the parity registry.

**Goal:** a terminal event or an agent-side dispatch can no longer change
what the user's next editing command does. Today a resize landing between
two undos flips the third into a *redo* — the buffer moves forward when the
user pressed undo — and a resize between two `C-k`s splits the kill-append
chain. After this slice, last-command bookkeeping (kill chain, yank-pop,
undo descent) keys on **user-issued commands only**, matching Emacs, where a
resize runs no command and peer output is not a command. User-visible: undo
and kill-append stop depending on terminal and agent timing.

## 1. The acceptance scenario

In-process (harness level — the bug is timing, and the harness delivers the
timing deterministically):

```text
type "abc"                  → buffer "abc"
C-/ C-/                     → buffer "a"      (two undos, descending)
resize frame to 100x30      → nothing visible (a terminal event, not a command)
C-/                         → buffer ""       — the descent continues
                              (pre-slice: buffer "ab" — the undo became a REDO)
```

Second arm — the kill chain:

```text
visit a file with "one\ntwo\n", point at 0
C-k                         → ring ("one\n",)
resize frame                → nothing visible
C-k                         → ring ("one\n",) — the chain appended ("\n" joins "one\n")
                              (pre-slice: ring ("\n", "one\n") — chain split)
```

Third arm — the peer's schedule, pump level: an agent handshake completing
(the pump's `DisplayBuffer`) between two undos leaves the third undo
descending. Pre-slice it flips to redo.

## 2. What exists today

- The fold's bookkeeping, `session.py:976-1030`: `commit_id` resolves to the
  pinned target or the focused buffer; then
  `intervened = any(not isinstance(e, Message) for e in events)` (`:984`)
  gates the kill chain (`:986-996`), yank-pop state (`:998-1006`), and undo
  descent (`:1013-1030`) for the commit buffer.
- The classification hole: `intervened` keys off **event shapes**, so *any*
  command emitting a semantic event intervenes — including commands the user
  never issued. Three such commands commit against the **focused** buffer:
  - `ResizeFrame` (`:786-794`) — emits `FrameResized`. Slice 15.
  - `DisplayBuffer` (`:770-772`) — emits window-layout events on a successful
    split, `Message("too-small-for-splitting")` on a refused one. Slice 16,
    pump-dispatched on session bind.
  - `PromptPermission` (`:948-955`) — emits the minibuffer-open event when
    presenting a peer's request. Pre-existing, first reachable in production
    in slice 16.
  - `AbortPendingPermissions` (`:956-967`) — emits `MinibufferAborted` when a
    choice prompt is open. Harmless today (the user's own `C-g` already
    intervened) but the same class.
- Already safe, verified while planning: deliveries
  (`DeliverProcessOutput`/`DeliverSessionEffects`/`InsertAgentText`) commit
  against their **pinned target's** state (`:971-975`), and
  `CreateAgentBuffer`/`CreateGeneratedBuffer` land the bookkeeping on the
  fresh buffer's state (`:773-778`, which names the invariant in its
  comment). Neither can break the focused buffer's chains.
- Emacs's model: the command loop sets `last-command` only for commands;
  a resize runs no command; subprocess output runs no command. `keyboard-quit`
  and prompt answers in drei ARE user commands and intervene — which matches
  Emacs (`C-g` sets `last-command` to `keyboard-quit` and breaks
  kill-append).
- Review 0002 finding 1's repros (lane 1 + coordinator): the undo flip via
  `harness.resize`, the kill-chain split, the flip via
  `harness.apply(DisplayBuffer(...))` and via `PromptPermission`.
- TD-13 (finding 6): `DisplayBuffer` on a too-small frame records
  `Message("too-small-for-splitting")` although the user issued no command —
  invisible only because `harness.apply` doesn't recompute the echo
  (`commands.py` `DisplayBuffer` docstring claims "silent no-op").

## 3. Design decisions

### D1. The classification lives on the command type, not on event shapes

A `ClassVar[bool]` on each command dataclass — `user_issued`, default `True`
— and the fold computes
`intervened = command.user_issued and any(not isinstance(e, Message) for e in events)`.

- **Alternatives.** (a) A `HOUSEKEEPING` frozenset in `session.py` —
  rejected: a future command defaults to whatever the set forgets to list,
  and the knowledge sits away from the command's definition. (b) Bookkeeping
  in the harness's key path — rejected: the session owns semantics; replay
  and the semantic suites drive `session.dispatch` directly, and a harness
  key path can't see pump-dispatched commands anyway. (c) Keep keying on
  event shapes and make housekeeping commands emit nothing — rejected:
  `FrameResized` and the split events are load-bearing records (replay
  derivability is why `ResizeFrame` is a command at all, plan 0015).
- **On screen:** nothing directly — it is the mechanism under D2's behavior.

### D2. The peer/housekeeping set: four commands today, plus the already-safe

Marked `user_issued = False`: `ResizeFrame`, `DisplayBuffer`,
`PromptPermission`, `AbortPendingPermissions` (the focused-buffer claimers),
and — for a total rule, "commands the user cannot issue never touch
last-command bookkeeping" — the delivery trio and `CreateAgentBuffer`, whose
target pinning already keeps them safe. `CreateGeneratedBuffer` stays
user-issued: it is the `C-x b new-name` creation path, a user command whose
bookkeeping harmlessly lands on the fresh buffer.

- **Alternatives.** Mark only the three repro'd claimers — rejected: it
  leaves the class open for the next pump-dispatched command, and the total
  rule is easier to audit than a repro-driven list.
- **On screen:** the acceptance scenario — undo descent and kill-append no
  longer answer to terminal resizes, agent handshakes, or permission
  presentations.

### D3. TD-13: the split-refusal message speaks only for the user's `C-x 2`

`_split_window` gains `speak: bool`; `SplitWindow` (user-issued) passes
`True`, `_display_buffer` passes `False`. The refused `C-x 2` keeps saying
`too-small-for-splitting`; the pump's `DisplayBuffer` on a short frame
becomes the genuine silent no-op its docstring claims.

- **Alternatives.** Suppress by checking `user_issued` inside the fold —
  rejected: the message is emitted in `_split_window`, below the fold, and
  threading the flag through is longer than naming the two call sites.
  Amend the contract to admit the message instead — rejected: the message
  describes a command the user never issued; the transcript should not
  claim it happened.
- **On screen:** nothing (the message never reached the echo row); the
  transcript stops recording a user-facing message for a peer-timed event.

### D4. User commands keep intervening — including prompt answers and `C-g`

The user's answer to a permission prompt, a minibuffer abort, and
`keyboard-quit` all stay `user_issued = True` and keep breaking chains and
descents — Emacs's `last-command` model, where `C-g` is a real command.
The peer's *presentation* of the prompt is what this slice exempts.

- **On screen:** nothing — this pins the status quo for user commands.

## 4. What this slice does NOT do

- **TD-14** (initial frame size not in the event record) — replay-contract
  work with its own entry; this slice changes which commands intervene, not
  what the transcript records about geometry.
- **The permission-answer-as-command parity question** (Emacs reads prompt
  answers inside the prompting command; drei answers are commands) — an open
  question below, recommendation: keep. If settled against, it is minibuffer
  semantics, not bookkeeping gating.
- **Review 0002 clusters B and C** — own slices.
- **Delivery bookkeeping** — already safe (target-pinned); only the
  classification label changes.

## 5. Pins that change

Predicted: **none.** Review 0002 (lane 1) found no test, plan, or registry
row examining the interaction, and the bookkeeping pins
(`test_kill_ring.py`, `test_yank_pop.py`, undo suites) drive user commands.
Two sweep obligations:

1. V1: grep for tests dispatching `ResizeFrame`/`DisplayBuffer`/
   `PromptPermission` adjacent to undo/kill/yank assertions.
2. V2: if any test pins `Message("too-small-for-splitting")` from a
   `DisplayBuffer` dispatch (rather than from `C-x 2`), it flips to
   asserting silence. (`test_shrink_below_the_split_minimum_degrades_in_stages`
   drives the user's `C-x 2` and must keep passing unchanged.)

## 6. Owned deviations (parity-registry rows)

No new deviations — the slice removes an unowned one. One row updated: the
undo-descent/kill-append bookkeeping rows gain the classification clause —
last-command bookkeeping keys on **user-issued commands**; a resize, an
agent-side dispatch, or a permission presentation is not a command and never
intervenes (Emacs's command loop runs no command for either). TD-13's
removal note lands in `docs/technical-debt.md`.

## 7. Implementation order (vertical slices, strict TDD)

1. **V1 — the classification.** RED: the acceptance scenario's three arms
   (undo descent across a resize; kill chain across a resize; descent across
   a pump-level `DisplayBuffer`), plus a `PromptPermission` arm, written at
   the level that repro'd the bug (harness/pump). Observe each fail for the
   finding's reason. Implement D1 + D2. GREEN; sweep per §5; full unit
   suite.
2. **V2 — TD-13.** RED: `DisplayBuffer` on a too-small frame records no
   message; `C-x 2` on the same frame still says
   `too-small-for-splitting`. Implement D3. GREEN; TD-13 marked paid.
3. **V3 — records.** Registry row clause (§6), TD-13 removal with a paid
   note, plan status. Gate.
4. **V4 — review → fix → code PR (`Closes #63`) → merge.**

## 8. Risks / open questions

- **Q1 — does a permission *answer* breaking the undo descent need a parity
  row?** Drei's answers are commands and intervene (D4); Emacs reads answers
  inside the prompting command, so `last-command` survives. Options: (a)
  keep + no row (the answer's command-ness is drei's established minibuffer
  model, already deviation-adjacent); (b) keep + a registry row naming the
  difference; (c) re-scope answers as non-intervening (minibuffer
  semantics — a bigger slice). **Recommendation: (b)** — cheap honesty, no
  behavior risk.
- **Q2 — `ClassVar` on frozen dataclasses.** The command dataclasses are
  frozen with slots; a `ClassVar[bool]` is class-level, unaffected. No
  question, noted for the reviewer.

## 9. Acceptance criteria

- The acceptance scenario's three arms pass (plus the `PromptPermission`
  arm); pre-slice each fails for the finding's reason.
- A resize, agent handshake, or permission presentation between user
  commands changes nothing about kill-append, yank-pop, or undo descent —
  pinned at session and pump level.
- `C-x 2`'s refusal still speaks; `DisplayBuffer`'s refusal is silent in
  the transcript; TD-13 paid and removed with a note.
- Registry row updated per §6; the Q1 row added if the gate picks (b).
- Full gate green (3.12–3.14, both CI OSes), coverage floor held.
