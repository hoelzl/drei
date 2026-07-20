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

Drei's modified-flag rule is deliberately narrower than Emacs's: any
`TextInserted` event sets modified; a successful save clears it. Emacs also
sets the flag on some non-text operations; if a future scenario observes
drift there, record it as an intentional deviation with rationale.

## Rules

1. A scenario compares **semantic** observations (text, point) only. Terminal
   presentation differences are out of scope for parity.
2. Normalization rules are part of the scenario and change only with a
   readable diff and explicit review.
3. Unexpected differences fail; intentional Drei deviations are recorded in
   this registry with a rationale, never silenced in test code.
