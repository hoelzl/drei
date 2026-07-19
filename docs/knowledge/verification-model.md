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

Approved snapshots and expected divergences are governed baselines. Unexpected differences fail; stale allowances are surfaced; baseline changes require a readable report and explicit review. If TermVerify cannot express required evidence, reduce the gap to the smallest reproducible Drei test and address TermVerify under its own contribution and protocol rules.

Coverage combines line and branch coverage. The committed floor is a no-regression ratchet derived from observed results, not a substitute for meaningful tests.
