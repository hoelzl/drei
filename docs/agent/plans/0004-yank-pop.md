# Fourth editor slice: yank-pop

**Status:** ready — architecture gate inherited from design 0002 (the yank-pop rotation cursor is session-owned transient state, no new ports)

**Goal:** Make the kill ring's depth observable: `M-y` immediately after a yank (or another yank-pop) replaces the just-yanked text with the next-older ring entry, cycling through the ring.

## Feasibility evidence (probed against pinned GNU Emacs 29.3)

- Batch `yank-pop` without a preceding yank reads from stdin (`read-from-kill-ring`) — an error path, not the replacement behavior.
- With `last-command` forced to `'yank` around a single `yank-pop`, batch Emacs **does** perform the replacement (verified: yank "two", pop → "one", point after the replaced text). So the **first** pop's replacement semantics are batch-verifiable.
- A **second** consecutive pop falls back to stdin-reading in batch (`read-from-kill-ring` again) because batch does not propagate `last-command = 'yank-pop'` between top-level calls. The **cycle** (pop-pop wrapping) is therefore batch-unverifiable — same class of deviation as the slice-3 append chain.

## Scope

In scope:

- `M-y` (`YankPop`): valid only when the immediately preceding command was a successful `Yank` or `YankPop`. It deletes the just-yanked text (between the recorded yank bounds) and inserts the next-older ring entry in its place, leaving point after the new text (same point rule as yank).
- Ring rotation cursor: session-owned transient state — the index of the entry most recently yanked/popped (0 = newest). `Yank` resets it to 0; each `YankPop` advances it by 1 modulo ring size (wrap-around). Any command other than `Yank`/`YankPop` clears the "yank active" flag, making `M-y` a no-op.
- `M-y` with no active yank, or on an empty ring, is a **silent no-op** (no event) — deliberate deviation from Emacs's "Previous command was not a yank" error signal, consistent with the slice-3 empty-kill no-op deviation (Drei has no echo-error mechanism yet). Recorded in the registry.
- Ring entries themselves are unchanged by pops (the rotation cursor moves; the ring is not rotated — Emacs rotates `kill-ring-yank-pointer`; Drei keeps the ring immutable-per-entry and moves only the cursor, which is behaviorally equivalent for yank-newest + pop and keeps `kill_ring` stable as evidence).
- Events: `TextYankPopped(old_text, new_text, before, after)` — `before` = start of the replaced region (the recorded yank start), `after` = before + len(new_text). `CommandOutcome.events` union widens. `TextYankPopped` is a text-changing event: it sets `modified` (the widened rule covers it; registry updated).
- Keys: `M-y` (`\x1by`, ESC then `y`) → `YankPop`. decode_key gains minimal two-byte meta-chord decoding for this one chord (ESC-prefixed single letter); the resolver maps `"M-y"`. No general prefix system (the existing `C-x` pending mechanism is command-prefix, not byte-prefix; meta decoding lives in the terminal/harness byte layer).

Explicitly out of scope (deferred): `C-u M-y` / numeric arguments, pop direction reversal (negative arg), the minibuffer, region commands, `yank-pop` on a yank that was itself appended to (chain head) — pops from the chain head behave like any other entry (the whole appended entry is one ring entry, replaced as a unit).

## Sequence

1. `YankPop` command + `TextYankPopped` event (TDD); union widens.
2. Session transient state: `_yank_active: bool` and `_yank_cursor: int` plus recorded `_last_yank_bounds: tuple[int, int]`. All private, mutated only in dispatch. `Yank` sets active+cursor 0+bounds; `YankPop` requires active, replaces bounds content with `ring[(cursor+1) % len]`, advances cursor, updates bounds; any other event-emitting command clears active (silent no-ops preserve it, mirroring the chain rule — the transcript stays the oracle: a `TextYankPopped` always follows a `TextYanked`/`TextYankPopped` in the evidence).
3. Semantics tests: pop replaces with next-older entry; pop-pop cycles and wraps (2-entry ring: yank A, pop → B, pop → A); pop after non-yank command is a no-op; pop on empty ring no-op; point-after-pop; modified set by pop; bounds bookkeeping when a pop changes text length (the replaced region shrinks/grows and subsequent pops still target the right span).
4. Keys: `M-y` decoding (ESC `y`) in decode_key + terminal byte loop; resolver maps `"M-y"` → `YankPop`.
5. Property tests: strategy gains `YankPop`; replay determinism (cursor is part of replayed evidence via outcomes); new invariant — a `YankPop` immediately after a `Yank` replaces the yanked span with `ring[1 % len]` (when ring ≥ 2) and text length changes by `len(new)-len(old)`; modified invariant widened to `TextYankPopped`.
6. TermVerify scenario: file-backed buffer, `C-k` ×2 (two entries), `C-y`, `M-y` — frame shows the older entry replacing the newer, end to end through ConPTY (ESC-y chord through the byte layer).
7. Emacs differential: single-pop batch eval (force `last-command='yank`, yank newest of a 2-entry ring, `yank-pop`, message resulting text/point) — parity required on first-pop replacement (older entry replaces, point after). The cycle (second pop) is an intentional batch-unverifiable deviation, pinned by unit/property tests, recorded in the registry.
8. Docs: README status, `development.md` if commands change, plan status; registry deviations updated (pop-cycle batch-unverifiable; pop-no-op vs Emacs error signal).

## Acceptance

- Full quality gate green on 3.12–3.14 and both CI OSes; coverage ratchet at 100%.
- First-pop replacement pinned by the pinned-Emacs differential; cycle/wrap pinned by unit + property tests.
- Pop is a no-op without an active yank and on an empty ring; both deviations recorded in the registry.
- Pop bounds bookkeeping: after a length-changing pop, a second pop replaces exactly the new span (focused test + property).
- Transcript coherence: every `TextYankPopped` is preceded by a `TextYanked`/`TextYankPopped` with no intervening event (property test folds the transcript).
- No ambient I/O in the command path (acceptance grep: `session.py commands.py model.py keys.py render.py` clean).

## Risks and decisions

- **Cursor vs ring rotation:** Drei moves a cursor rather than Emacs's `kill-ring-yank-pointer` list rotation. Behaviorally identical for the supported surface (yank newest, pop cycles older); the ring as immutable evidence stays stable, which replay and the transcript-derivability rule depend on. If a later slice needs `kill-ring` order to reflect pops (it shouldn't — Emacs's `kill-ring` variable order is also stable; only the pointer rotates), revisit then.
- **`M-y` decoding:** the smallest possible meta-chord support (ESC + one byte) rather than a general meta layer — general prefix/meta architecture is deferred to the minibuffer slice, which will need it properly.
- **Pop replaces the recorded bounds, not a re-derivation:** the yank bounds are recorded at yank time and updated by each pop, so a length-changing pop still targets the right span on the next pop. This is the piece batch Emacs can't verify (cycle), so it's the property test's job.
