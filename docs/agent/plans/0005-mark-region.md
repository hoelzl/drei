# Fifth editor slice: mark and region commands

**Status:** ready — architecture gate inherited from design 0002 (the mark is buffer-scoped value state on `BufferValue`; no new ports)

**Goal:** Region kill/copy on top of the kill ring: `C-@` (`set-mark-command`) records the mark, `C-w` (`kill-region`) kills point↔mark into the ring, `M-w` (`copy-region-as-kill`) copies without deleting, `C-x C-x` (`exchange-point-and-mark`) swaps point and mark.

**Why this slice:** the last "deferred" row in the registry that touches the ring; the slice-4 plan flagged it as the slice that must reconcile yank bounds `(start, end)` with Emacs's `(mark, point)`. It also completes the classic kill/copy/yank editing triad.

**Feasibility probe (pinned `ubuntu:24.04`, emacs-nox 29.3, this session):**

- Batch `set-mark`/`set-mark-command` makes the region active (`mark-active=t`); "Mark set" is echoed.
- `kill-region` removes point↔mark (either direction), pushes the removed text as one ring entry, point stays at the kill position. Backward kill (point < mark) verified: `REG="abcd"`, point 1, text "ef".
- `copy-region-as-kill` leaves text and point alone, pushes the region text into the ring.
- `exchange-point-and-mark` swaps point and mark.
- **Batch caveats:** mark deactivation on edit is an interactive `last-command` behavior — batch `mark-active` survives `insert` (verified). `keyboard-quit` signals out of batch evals (C-g deactivates mark — batch-unverifiable). Mark ring (`C-u C-@`) is out of scope.
- **ConPTY/TermVerify `C-@` probe (this session):** the raw NUL byte (`TextInput("\x00")`) survives ConPTY and reaches the child. But `termverify.key/v1` has NO chord for `C-@`/`C-SPC` — `("Control", "@")` is not canonical and `("Control", "Space")` fails closed in `key-encoding/v1` (adapter refuses to send). The TermVerify scenario therefore delivers `C-@` via `TextInput("\x00")` — honest input, frame evidence still end-to-end. A chord amendment would need a new registry version (v1 frozen); not filed (TextInput is sufficient).

**Design decision (Drei-specified, Emacs-informed):** the mark is **a single optional position on `BufferValue`** (`mark: int | None`, default `None`); a set mark is always active (no separate transient flag). Design 0002's invariant holds: `BufferValue` stays frozen, mutation only via `dispatch`. This is the first non-`None`-default field addition to `BufferValue` since slice 1 — the spike's value-object approach accommodates it (like `modified`/`file_path`).

**Marker adjustment (probed against pinned 29.3, this session):** the mark is an Emacs-style marker — text edits adjust it. Insert `n` chars at `p`: `p < mark` → `mark += n`; `p == mark` → mark stays (Emacs default insertion type keeps it before the inserted text — verified: mark 4, insert "XY" at 4 → mark 4, text "abcXYdef"). Delete `[s, e)`: `mark < s` → unchanged; `mark >= e` → `mark -= e - s`; `s <= mark < e` → `mark = s` (verified: kill-region [1,4) with mark 6 → mark 3). This applies to EVERY text-changing command (InsertText, KillLine, Yank, YankPop, KillRegion) — implemented as one pure helper `_adjust_mark(mark, edit)` in the session, so `__post_init__` validation can never reject an out-of-range mark. `BufferValue.__post_init__` validates `mark is None or 0 <= mark <= len(text)`.

Drei simplification vs Emacs (recorded as intentional deviation where observable): Emacs distinguishes "mark set" from "region active" (transient-mark-mode subtleties, `C-u C-@` mark ring, deactivation on many commands). Drei: `mark is None` = no mark; a set mark is always active; the mark is cleared (set to `None`) by `C-g` and by any successful `kill-region`/`copy-region-as-kill` (Emacs deactivates after both), and by `exchange-point-and-mark`? No — XPM keeps the mark (swaps only). The mark is NOT cleared by motion/insert in this slice (batch can't verify interactive deactivation; simplest deterministic rule; deviation recorded).

## Scope

In scope:

- `SetMark` command: records point as the mark. Emacs echoes "Mark set" — Drei has no echo area yet; the event is the evidence. Event: `MarkSet(position)`. Re-setting replaces the mark (no mark ring this slice).
- `KillRegion`: requires a set mark; kills `text[min(point,mark):max(point,mark)]` as ONE ring entry (chain head push, not append — Emacs pushes region kills as new entries; kill-line append chain is broken first). Point moves to `min(point, mark)` (the kill position). Mark cleared. Event: **`RegionKilled(text, before, after, direction)`** — a NEW event distinct from `TextKilled`, because the mark-fold must distinguish region kills (clear the mark) from line kills (don't); a reused `TextKilled(..., "forward")` is indistinguishable from `KillLine`'s and would make mark state underivable from the transcript (mirrors the `RegionCopied`-not-`TextYanked` precedent). `direction` = `"forward"` (mark > point) or `"backward"` (mark < point). No mark or mark == point → silent no-op (deviation from Emacs's no-mark error / empty-region behavior — same no-echo-error rationale).
- `CopyRegionAsKill`: same region; pushes text into ring; buffer text and point unchanged; mark cleared. Event: `RegionCopied(text)` (new event — NOT text-changing, so it must NOT set `modified`; verified against pinned 29.3: copy leaves `buffer-modified-p` nil).
- `ExchangePointAndMark`: requires a set mark; swaps point and mark; mark stays set. New event `MarkExchanged(point_before, mark_before)`. No-op without a mark (silent, deviation).
- Keys: `C-@` is `0x00` (NUL byte) — `decode_key` maps `\x00` → `C-@`; `C-SPC` sends the same byte on terminals. `C-w` = `0x17`, `C-x C-x` via the existing prefix table, `M-w` = ESC+w via `assemble_meta`/`_META_KEYS` (already built).
- Kill chain interplay: `SetMark`, `CopyRegionAsKill`, `KillRegion`, `ExchangePointAndMark` are all event-emitting (on success) → they break the kill-append chain per the slice-3 rule. KillRegion pushes a NEW entry (never appends to the chain head, even if the previous command was a kill-line — Emacs: `last-command` is `kill-region` ≠ `kill-line`).
- Yank bounds caveat (from the slice-4 plan Risks): Emacs yank pushes the mark at yank start. Drei has no mark-on-yank yet. This slice keeps yank bounds `(start, end)` as-is — adding mark-push-on-yank would couple slices; recorded as a deviation row (Emacs: yank sets mark at insertion start; Drei: yank does not touch the mark). Revisited when the mark ring lands.
- Property tests: strategy gains the four commands; replay determinism; modified invariant: `KillRegion`/`CopyRegionAsKill`/`SetMark`/`ExchangePointAndMark` — which set modified? Only KillRegion (text change). The property's "any events → modified" arms must NOT cover the new non-text events — widen carefully.
- Events must keep the transcript the oracle: mark state is derivable from `MarkSet`/`RegionKilled`/`RegionCopied`/`MarkExchanged`/`KeyboardQuitEvent` — a set mark survives until a `RegionKilled`/`RegionCopied`/`KeyboardQuitEvent` clears it (no separate clear event needed; positional, and now unambiguous because region kills have their own event type). The ring fold gains `RegionKilled` and `RegionCopied` as push producers. Document both folds (mark, ring) together in the knowledge index. `BufferObservation` gains `mark: int | None` — the renderer ignores it this slice (no region face), but observations stay the complete state snapshot the harness/TermVerify evidence needs.
- `RegionKilled` is a text-changing event (sets `modified`, widens the union and the modified rule again); `RegionCopied`/`MarkSet`/`MarkExchanged` are not.

Out of scope: mark ring (`C-u C-@`), transient-mark-mode highlighting (no region face in the renderer — a render slice), `delete-region` without ring push, rectangle commands, `C-y` pushing the mark.

## Implementation order

1. `BufferValue.mark: int | None = None` + validation (`mark is None or 0 <= mark <= len(text)`); `_adjust_mark` helper covering insert/delete per the probed rule; every text-changing dispatch path applies it; model tests incl. boundary cases (insert at mark, delete spanning mark, delete after mark).
2. Commands + events (TDD): `SetMark`/`MarkSet`; `KillRegion` (region empty → no-op; pushes NEW ring entry; point to kill start; mark cleared; clears the kill-append chain — a following `C-k` opens a new entry, matching Emacs `last-command` semantics); `CopyRegionAsKill`/`RegionCopied`; `ExchangePointAndMark`/`MarkExchanged`.
3. Keys: `\x00` → `C-@`, `\x17` → `C-w` in `decode_key`/`_CONTROL_KEYS`; `C-x C-x` in `_PREFIX_COMMANDS`; `M-w` in `_META_KEYS`.
4. Property tests: strategy, replay (now also comparing `mark`), modified arms (`KillRegion`/`RegionKilled`-with-events → modified; `CopyRegionAsKill`/`SetMark`/`MarkExchanged` do NOT), mark-bounds invariant (`mark is None or 0 <= mark <= len(text)` over random histories — would have caught the unadjusted-mark crash), mark-fold coherence (mark derivable from transcript per the fold rule).
5. TermVerify scenario: `C-@` (via `TextInput("\x00")` — no canonical chord exists; probe recorded above) `C-f C-f C-w` kills a region end-to-end through ConPTY. `M-w` copy — ESC-swallow caveat applies (in-process proof via the byte loop).
6. Emacs differential: batch eval — insert "hello world", set-mark at 1, forward 5, kill-region → text " world", ring ("hello"), point 1; backward kill pinned by a second eval (mark at 6, point at 1, kill-region → same ring text, point 1); marker adjustment pinned (kill before mark → mark shifts; insert before mark → mark shifts; insert AT mark → mark stays); copy-region-as-kill round: text unchanged, ring gains entry. Parity required on region text/point/ring-head/mark-position. Deviations: mark deactivation-on-edit (batch-unverifiable), no-mark/empty-region errors vs Drei no-ops, mark-ring absence, yank-not-pushing-mark, copy-region-as-kill modified-flag (verified clean: `copy-region-as-kill` and `set-mark` leave `buffer-modified-p` nil on a clean buffer — Drei matches).
7. Docs: README status, registry rows, plan status.

## Acceptance

- Full quality gate green; coverage ratchet 100%.
- Region kill/copy/exchange pinned by the pinned-Emacs differential (forward AND backward direction, marker adjustment); deactivation/no-op deviations in the registry.
- Kill chain interplay: `C-k C-k C-@ C-w` — the region kill opens a NEW entry even after chained line kills; `C-k C-w C-k` — the `C-k` after a region kill does NOT append to the region entry (focused tests).
- Mark-bounds invariant + transcript mark-fold coherence properties.
- `BufferObservation.mark` exposed; renderer unchanged (no region face this slice).
- ConPTY NUL-byte delivery probed and the result recorded (scenario or deviation).
- No ambient I/O in the command path.

## Risks and decisions

- The NUL byte (`C-@`) delivery is resolved (probe above: survives ConPTY via TextInput; no key chord). Remaining evidence risk: `M-w` has the known ESC swallow — in-process proof, same as `M-y`.
- `RegionCopied` is the first event that mutates session state (ring) without touching the buffer — the modified-flag property must handle it explicitly, and the transcript-fold derivation of the ring gains a new producer.
- Adding a field to `BufferValue`: dataclass default keeps non-text `replace(...)` call sites unchanged (save, motion carry the mark forward — correct); text-changing sites adjust the mark per the rule above.
