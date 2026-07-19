# Contributing to Drei

Read `AGENTS.md` first. Use `uv`, Python 3.12+, strict TDD, and focused vertical slices.

## Workflow

1. Write one focused behavioral test and run it to observe the expected failure.
2. Implement only enough to pass it; refactor while green.
3. Run focused tests, then the full relevant gate from `AGENTS.md`.
4. Update user/developer/design documentation when its contract changes.
5. Review the full diff. Snapshot, parity-baseline, dependency, public API, and coverage-floor changes require explicit rationale.

The coverage floor in `pyproject.toml` is a no-regression ratchet: it is the integer floor of reviewed observed line-and-branch coverage. Raise it after durable improvement leaves at least one full point of headroom. Lowering it or adding exclusions requires owner review.

Use an external sibling worktree per concurrent agent. Do not share a writable checkout or place durable plans only in harness-private state.
