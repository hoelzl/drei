# Third editor slice: kill ring

**Status:** implemented — kill ring (KillLine/Yank, append chain, widened modified flag), TermVerify kill/yank scenario, and pinned Emacs kill/yank differential complete; append-chain and empty-kill deviations recorded in the parity registry

**Goal:** Kill and yank text through the production command path with an Emacs-style kill ring, exercising multi-command composition (kill appends/prepend semantics across consecutive kills) entirely inside the deterministic core.

## Scope

In scope:

- `C-k` (`KillLine`) decision table at point `p`: **if `p == len(text)` → no-op, no event, ring unchanged, chain not broken.** **Else if `text[p] == '\n'` → kill that one newline.** **Else → kill `text[p:eol)`** (up to but excluding the next `'\n'` or buffer end). All three cases pinned by focused tests on `'ab\ncd'`, `'ab\ncd\n'`, `''`, `'\n'`.
- `C-y` (`Yank`): inserts the most recent kill at point; empty ring → no-op.
- Kill ring: session-owned, immutable-per-entry, bounded (capacity 60, Emacs `kill-ring-max` default). Consecutive `C-k` commands (a kill immediately following a kill, with no intervening command — a no-op kill does NOT break the chain) **append** to the same ring entry. **Deviation from batch Emacs:** batch `--eval` does not kill-append consecutive `kill-line` (verified against GNU Emacs 29.3: `R2=("\n" "ab")`, two entries; append needs `last-command` propagation that batch doesn't perform, and even an explicit `(setq last-command 'kill-line)` between calls does not append in batch — also verified). Drei specifies append-on-consecutive-kill as its own semantic, *motivated by* interactive Emacs `kill-line` but **not verifiable by the batch differential**; it is pinned solely by unit/property tests. Recorded as an intentional deviation in the parity registry with this rationale. Any non-kill command breaks the chain.
- `M-y` (yank-pop) is **deferred** (needs ring cycling + transient state); only the most recent entry is yankable in this slice. The ring exists this slice to pin append semantics and the immutable-entry/capacity machinery `M-y` will need; its observable surface is exactly the append chain.
- Events: `TextKilled(text, before, after, direction)` where direction is `"forward"` (only direction in this slice; `"backward"` reserved — event records are immutable evidence, so the field exists now because adding it later would break replay comparisons of old transcripts; its consumer is deferred region/backward kill); `TextYanked(text, before, after)` mirroring `TextInserted` (before = point at dispatch, after = before + len(text)). Observation unchanged (kill/yank are text edits; the ring is not in the observation — it's session evidence via the transcript/events, and yankability is observable through behavior).
- **Modified-flag rule widened:** any text-changing event sets `modified` — `TextInserted`, `TextKilled` (non-empty), `TextYanked`. Kill and yank both change the buffer, so both set the flag; the parity registry rule and `test_modified_flag_consistent_with_history` are updated to match.

Explicitly out of scope (deferred): `M-y` yank-pop, `C-w`/`M-w` region kill/copy (needs mark/region), kill-ring rotation, `kill-ring-max` configuration, undo, inter-program clipboard.

## Sequence

1. `KillLine`/`Yank` commands and `TextKilled`/`TextYanked` events (TDD). `CommandOutcome.events` union widens.
2. Kill-ring state on `EditorSession` (a list of immutable string entries + a `_last_was_kill` flag, both private — session-scoped live state like `_transcript`, mutated only inside dispatch; it does NOT belong in the frozen per-buffer `BufferValue` because the Emacs kill ring is global, not per-buffer). The ring is derivable from the `TextKilled`/`TextYanked` event stream (modulo append-chain and capacity eviction, both visible in events); tests assert ring content through the transcript as the oracle, and a read-only `kill_ring: tuple[str, ...]` property (newest first) exists as a debugging convenience, never the assertion oracle. Dispatch handles append-on-consecutive-kill and chain-breaking.
3. `KillLine` semantics per the decision table in Scope. Tests pin all three branches plus the append chain (C-k C-k → one ring entry), chain break (C-k C-f C-k → two entries), and no-op kill not breaking the chain (C-k C-k@eob C-k → one appended entry).
4. `Yank` semantics: insert newest entry at point, point moves past the yanked text (`TextYanked(text, before, after)`, observation point = after, matching Emacs `yank` leaving point after the inserted text); empty ring → no-op. Tests pin point-after-yank and multi-line yank.
5. Keys: `C-k` (`\x0b`) → `KillLine`, `C-y` (`\x19`) → `Yank`. decode_key + resolver + harness pass-through (single keys, no prefix). Kill/yank are silent in the echo area (like Emacs) — `_echo_for` gains no branch.
6. Property tests: extend `command_history` with `KillLine`/`Yank`; widen the modified-flag expectation to the new text-changing events; new invariants — replay determinism still holds with the ring; **narrowed round-trip**: for any `KillLine` dispatch that emits `TextKilled` with non-empty text, an immediately following `Yank` restores the pre-kill text and point; a no-op kill followed by yank is NOT a round trip (yank inserts the prior ring head) and is pinned separately; yank never changes text when the ring is empty. Ring capacity bound (61st kill drops oldest) is deferred — unobservable this slice without `M-y`.
7. TermVerify scenario: insert two lines, `C-k C-k`, `C-y`, assert frame shows the joined/yanked text end to end through ConPTY.
8. Emacs differential: batch eval for kill-line/yank semantics — kill-to-EOL text, kill-at-EOL kills the newline, yank inserts the newest entry with point after — with parity-required verdicts; the append chain is an intentional deviation (see Scope) verified by unit/property tests, not the differential. Registry updated with both the parity verdicts and the deviation rationale.
9. Docs: README status, `development.md`, plan status.

## Acceptance

- Full quality gate green on 3.12–3.14 and both CI OSes; coverage ratchet at 100%.
- Kill-append chain and chain-break pinned by focused tests and property tests (intentional deviation from batch Emacs, recorded in the registry); the differential pins the non-append pieces (kill-to-EOL, kill-at-EOL newline, yank text/point).
- Ring state is session-owned and private; dispatch remains the sole mutation path; no new ambient I/O — `grep -nE "open\(|pathlib|os\.|sys\." src/drei/session.py src/drei/commands.py src/drei/model.py src/drei/keys.py src/drei/render.py` returns nothing.
- Property tests cover kill/yank replay, the narrowed round-trip, empty-ring yank no-op, and the widened modified-flag rule.
- Parity registry records the append-chain deviation, the empty-kill no-op deviation (Emacs signals an error), `M-y`, and capacity explicitly.
- `session.Command` and `CommandOutcome.events` unions both widen to include the new commands/events (typing must not silently break replay).

## Risks and decisions

- **Kill-at-EOL kills the newline** (Emacs behavior) is the only way `C-k C-k` on a one-line file with a trailing newline then `C-y` reproduces the text; pinned by the differential.
- **Ring in the observation?** No — observations stay the renderable projection (buffer_id/text/point/file_path/modified). The ring is semantic session state proven through behavior (yank output) and the event transcript, consistent with "observations never authoritative." If a later slice needs the ring in the UI (e.g. a ring browser), that's a new observation field then, not now.
- **Empty-kill no-op:** at buffer end, `C-k` emits no event and does not break/extend the append chain (Emacs signals "End of buffer"; Drei records it as a silent no-op for this slice — a deliberate deviation from Emacs's error signal, recorded in the registry, because Drei has no echo-error mechanism yet).
