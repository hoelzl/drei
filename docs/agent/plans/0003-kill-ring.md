# Third editor slice: kill ring

**Status:** ready — architecture gate inherited from design 0002 (the kill ring is session-owned state, not a new effect; no new ports)

**Goal:** Kill and yank text through the production command path with an Emacs-style kill ring, exercising multi-command composition (kill appends/prepend semantics across consecutive kills) entirely inside the deterministic core.

## Scope

In scope:

- `C-k` (`KillLine`): kills from point to end of line; if point is at end of line (or the line is empty), kills the newline itself. Killing the last line's end when the buffer has no trailing newline kills nothing (no-op, `KilledText` with empty text is NOT emitted — the command is a silent no-op like Emacs).
- `C-y` (`Yank`): inserts the most recent kill at point.
- Kill ring: session-owned, immutable-per-entry, bounded (capacity 60, Emacs `kill-ring-max` default). Consecutive `C-k` commands (a kill immediately following a kill, with no intervening command) **append** to the same ring entry. **Deviation from batch Emacs:** in batch `--eval`, consecutive `kill-line` calls produce separate ring entries (append requires `last-command == 'kill-line`, which batch doesn't propagate the way interactive repetition does). Drei specifies append-on-consecutive-kill as its own semantic — it matches *interactive* Emacs, which is the behavioral reference that matters for an editor — and the differential verifies the non-appended pieces (kill-to-EOL text, kill-at-EOL newline, yank-text) rather than the append chain, which is pinned by unit/property tests. Recorded as an intentional deviation in the parity registry. Any non-kill command breaks the chain.
- `M-y` (yank-pop) is **deferred** (needs ring cycling + transient state); only the most recent entry is yankable in this slice.
- Events: `TextKilled(text, before, after, direction)` where direction is `"forward"` (only direction in this slice; `"backward"` reserved); `TextYanked(text, point)`. Observation unchanged (kill/yank are text edits; the ring is not in the observation — it's session evidence via the transcript/events, and yankability is observable through behavior).

Explicitly out of scope (deferred): `M-y` yank-pop, `C-w`/`M-w` region kill/copy (needs mark/region), kill-ring rotation, `kill-ring-max` configuration, undo, inter-program clipboard.

## Sequence

1. `KillLine`/`Yank` commands and `TextKilled`/`TextYanked` events (TDD). `CommandOutcome.events` union widens.
2. Kill-ring state on `EditorSession` (a list of immutable string entries + a `_last_was_kill` flag, both private; the ring is exposed read-only via a `kill_ring: tuple[str, ...]` property for tests — newest first). Dispatch handles append-on-consecutive-kill and chain-breaking.
3. `KillLine` semantics: point→EOL text, or the newline at EOL, or no-op at buffer end. Tests pin all three cases plus the append chain (C-k C-k → one ring entry) and chain break (C-k C-f C-k → two entries).
4. `Yank` semantics: insert newest entry at point; empty ring → no-op. Tests pin point-after-yank (point moves past the yanked text, Emacs behavior) and multi-line yank.
5. Keys: `C-k` (`\x0b`) → `KillLine`, `C-y` (`\x19`) → `Yank`. decode_key + resolver + harness pass-through (single keys, no prefix).
6. Property tests: extend `command_history` with `KillLine`/`Yank`; new invariants — replay determinism still holds with the ring; ring size ≤ capacity; yank never changes text when ring is empty; kill+yank round-trip preserves text (kill then yank at same point restores the original text).
7. TermVerify scenario: insert two lines, `C-k C-k`, `C-y`, assert frame shows the joined/yanked text end to end through ConPTY.
8. Emacs differential: batch eval for kill-line/yank semantics — kill-to-EOL text, kill-at-EOL kills the newline, yank inserts the newest entry — with parity-required verdicts; the append chain is an intentional deviation (see Scope) verified by unit/property tests, not the differential. Registry updated with both the parity verdicts and the deviation rationale.
9. Docs: README status, `development.md`, plan status.

## Acceptance

- Full quality gate green on 3.12–3.14 and both CI OSes; coverage ratchet at 100%.
- Kill-append chain and chain-break pinned by focused tests and the Emacs differential.
- Ring state is session-owned and private; dispatch remains the sole mutation path; no new ambient I/O (acceptance grep still empty).
- Property tests cover kill/yank replay and round-trip invariants.
- Parity registry records the `M-y`/capacity deviations explicitly.

## Risks and decisions

- **Kill-at-EOL kills the newline** (Emacs behavior) is the only way `C-k C-k` on a one-line file with a trailing newline then `C-y` reproduces the text; pinned by the differential.
- **Ring in the observation?** No — observations stay the renderable projection (buffer_id/text/point/file_path/modified). The ring is semantic session state proven through behavior (yank output) and the event transcript, consistent with "observations never authoritative." If a later slice needs the ring in the UI (e.g. a ring browser), that's a new observation field then, not now.
- **Empty-kill no-op:** at buffer end, `C-k` emits no event and does not break/extend the append chain (Emacs signals "End of buffer"; Drei records it as a silent no-op for this slice — a deliberate deviation from Emacs's error signal, recorded in the registry, because Drei has no echo-error mechanism yet).
