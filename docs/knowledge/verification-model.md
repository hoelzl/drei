---
type: concept
title: Drei verification model
description: Layered evidence strategy for editor development.
tags: [verification, termverify, tdd, parity]
---

# Verification model

Every behavior is developed by a vertical RED-GREEN-REFACTOR cycle. Verification layers accumulate:

1. focused examples for command semantics and rendering;
2. Hypothesis properties/state machines for cursor, text, undo, and replay invariants;
3. in-process scenario transcripts using production commands and structured observations;
4. TermVerify terminal scenarios at fixed dimensions, locale, seed, sandbox, and readiness epochs;
5. selective differential scenarios against a pinned GNU Emacs version.

Raw terminal frames are evidence but never the sole semantic oracle. A scenario records inputs, constraints, readiness, semantic observations, terminal observations, and outcome. Replaying the same initial state and inputs must produce equivalent semantic evidence.

Governed baselines are compared, never rubber-stamped: unexpected differences fail, stale allowances are surfaced, and a baseline change requires a readable diff and explicit review. Today there is exactly one such baseline — the GNU Emacs parity registry (`emacs-parity.md`), whose deviation rows are the reviewed allowances and whose test citations are checked mechanically. **No snapshot mechanism exists**: no test asserts against a stored frame or golden file, so the rule above is a standing policy for the first one, not a description of current machinery. If TermVerify cannot express required evidence, reduce the gap to the smallest reproducible Drei test and address TermVerify under its own contribution and protocol rules.

Coverage combines line and branch coverage. It is a **hard floor at 100%**, not a ratchet: the project has been at total coverage since the first slice, so every uncovered line is a new gap and fails the gate. (The "no-regression ratchet" framing describes how the floor would be managed if it were ever lowered — reviewed, observed, raised only with durable headroom. It is vestigial while the floor is 100%.) Coverage is a completeness check on the tests that exist, never evidence that they mean anything.
