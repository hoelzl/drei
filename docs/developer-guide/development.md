# Development environment

Drei uses uv for interpreter, environment, dependency, lock, and command management. `uv.toml` declares the reviewed uv executable version for ordinary project discovery, and CI pins the same version explicitly; documented `--no-config` commands remain isolated from user- and project-level uv configuration. Python 3.12 is the minimum; CI continuously tests 3.12 through 3.14 on Windows and Linux.

Run the commands in `AGENTS.md`. The pre-commit stage stays fast (Ruff); pre-push adds the full coverage suite, strict mypy, and package build. CI repeats the gates independently. A separate least-privilege workflow scans action configuration and uses the pinned uv executable to audit the locked dependency graph; Dependabot proposes weekly action and uv updates. Hooks are convenience adapters, never the only enforcement.

## Test tiers

- unit: pure state transitions, key resolution, rendering, and normalization;
- property/state-machine: generated command histories and invariants;
- integration: session or process boundaries;
- `termverify`: shipped TUI, explicit constraints, readiness, replay evidence;
- differential: selected pinned-reference scenarios with reviewed divergence policy.

## Coverage ratchet

`fail_under` is the integer floor of reviewed observed combined line-and-branch coverage. `precision = 2` prevents rounding grace. Raise only with durable headroom; lowering, exclusions, or tests written merely to inflate coverage require owner review.

## Dependencies and donor code

Add dependencies only at a demonstrated boundary and with tests. Record copied source provenance and Apache-2.0 attribution in the reuse assessment and `NOTICE` when required. Prefer reimplementing a small contract from behavioral tests over transplanting a subsystem.
