---
type: concept
title: GNU Emacs parity policy
description: Pinned differential scenarios, normalization rules, and divergence governance.
tags: [verification, differential, emacs, parity]
---

# GNU Emacs parity policy

GNU Emacs is a behavioral reference, not an unquestioned specification. Each
differential scenario states whether parity is required or a Drei deviation is
intentional, and every normalization is explicit.

## Pinned reference

- **Version:** GNU Emacs 29.x (validated against 29.3).
- **CI:** dedicated `parity` job on the pinned `ubuntu-24.04` runner image
  with `apt-get install -y emacs-nox` (mirrors the Recursive://Neon pattern).
  The runner pin keeps the distro Emacs at a known version; bump it only
  deliberately and re-validate all scenarios.
- **Local:** `DREI_PARITY=1 uv --no-config run pytest tests/differential -q`
  runs the same scenario in the `ubuntu:24.04` container (installing
  `emacs-nox`) or against a host `emacs` that reports the pinned 29.x series.
  Without the opt-in, or without Docker/host Emacs, the scenario skips — it
  never fabricates a baseline.

## Scenario registry

| Scenario | Reference behavior | Normalization | Verdict |
| --- | --- | --- | --- |
| startup, insert, backward-char, forward-char (`tests/differential/test_emacs_parity.py`) | empty scratch-like buffer; `insert`, `backward-char`, `forward-char` | Emacs point is 1-based; Drei point is 0-based (`point_emacs - 1 == point_drei`) | parity required |
| find-file (new), insert, save-buffer (`tests/differential/test_emacs_parity.py`) | `buffer-modified-p` is `t` after `insert`, `nil` after `save-buffer`; file content on disk matches buffer text | same point normalization; Drei drives `SaveBuffer` through the production dispatch path with a fake `FilePort` and asserts content in the fake | parity required |
| kill-line ×2, yank (`tests/differential/test_emacs_parity.py`) | first `kill-line` kills text to EOL, second kills the newline, `yank` inserts the newest entry leaving point after it | same point normalization; the append chain is an intentional deviation (see below) so Drei's yank restores the full original text while batch Emacs yanks only the newest (unappended) entry | parity required on the non-append pieces (kill-to-EOL text, newline kill, yank-inserts-newest-with-point-after) |
| yank, yank-pop (`tests/differential/test_emacs_parity.py`) | `yank` inserts the newest of a 2-entry ring, then `yank-pop` (with `last-command` forced — batch cannot propagate it) replaces the yanked span with the next-older entry; entries have different lengths so point placement is pinned (`point = start + len(new)`) | same point normalization; batch `kill-line` point handling leaves point after the leftover newline, and Drei drives the same positions | parity required on the first pop; the pop cycle is an intentional deviation (see below) |

Drei's modified-flag rule is deliberately narrower than Emacs's: any
text-changing event (`TextInserted`, `TextKilled`, `TextYanked`,
`TextYankPopped`) sets modified; a successful save clears it. Emacs also
sets the flag on some non-text operations; if a future scenario observes
drift there, record it as an intentional deviation with rationale.

## Intentional deviations

| Behavior | Drei | Emacs | Rationale |
| --- | --- | --- | --- |
| Append-on-consecutive-kill | consecutive `C-k` appends into one ring entry | batch `--eval` does not append (verified against 29.3: `("\n" "ab")`, two entries; even an explicit `(setq last-command 'kill-line)` does not append in batch) | matches *interactive* Emacs `kill-line`; batch-unverifiable, so pinned by unit/property tests instead of the differential |
| Empty kill at buffer end | silent no-op, no event | signals "End of buffer" (an error) | Drei has no echo-error mechanism yet; recorded here until one exists |
| Yank-pop cycle (second and later pops) | cursor advances modulo ring size, wrapping | batch `yank-pop` falls back to `read-from-kill-ring` (reads stdin) once `last-command` propagation ends; verified against 29.3 | batch-unverifiable, so pinned by unit/property tests (2-entry wrap) instead of the differential |
| Yank-pop without an active yank | silent no-op, no event | signals "Previous command was not a yank" (an error) | same no-echo-error rationale as the empty kill |
| Yank-pop on a one-entry ring | silent no-op, no event | replaces the entry with itself (sets modified) | a self-replacement event would set `modified` on unchanged text, contradicting the modified-flag invariant; no-opping keeps the flag honest |
| Kill-ring capacity | fixed 60 | `kill-ring-max` (default 60, configurable) | configuration is deferred |

## Rules

1. A scenario compares **semantic** observations (text, point) only. Terminal
   presentation differences are out of scope for parity.
2. Normalization rules are part of the scenario and change only with a
   readable diff and explicit review.
3. Unexpected differences fail; intentional Drei deviations are recorded in
   this registry with a rationale, never silenced in test code.
