# 0001: Agent-first deterministic editor foundation

**Status:** accepted

## Decision

Drei uses Python 3.12+, uv, a `src/` package, strict typing, Ruff, pytest/Hypothesis, branch coverage, two-stage hooks, and Windows/Linux CI. Product code begins as a deterministic editor core with explicit commands, state, outcomes, and effect ports. A direct harness and a TermVerify-driven terminal path will share production semantics.

GNU Emacs and Recursive://Neon provide behavior and evaluation ideas, but neither is an implicit specification. Parity is selected scenario by scenario. Framework and storage complexity are introduced only by a tested vertical slice or measurement.

## Why

Long-running autonomous work needs cheap local feedback, stable sources of truth, replayable evidence, and boundaries that do not require agents to infer state from pixels. A pure semantic core gives fast TDD and property tests; the shipped terminal path ensures architectural cleanliness does not hide integration failures.

## Consequences

Initial progress may look narrower than porting Recursive://Neon, but each slice is independently reviewable and proves a reusable path. Snapshot and divergence changes remain human-governed. TermVerify limitations discovered by real Drei scenarios are addressed in TermVerify without weakening either project's contracts.
