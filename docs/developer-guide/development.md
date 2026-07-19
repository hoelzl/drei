# Development environment

Drei uses uv for interpreter, environment, dependency, lock, and command management. `uv.toml` declares the reviewed uv executable version for ordinary project discovery, and CI pins the same version explicitly; documented `--no-config` commands remain isolated from user- and project-level uv configuration. Python 3.12 is the minimum; CI continuously tests 3.12 through 3.14 on Windows and Linux.

Run the commands in `AGENTS.md`. The pre-commit stage stays fast (Ruff); pre-push adds the full coverage suite, strict mypy, and package build. CI repeats the gates independently. A separate least-privilege workflow scans action configuration and uses the pinned uv executable to audit the locked dependency graph; Dependabot proposes weekly action and uv updates. Hooks are convenience adapters, never the only enforcement.

## Test tiers

- unit: command-boundary model transitions, immutable event/observation values, key resolution, rendering, and normalization; live-model mutation is permitted only as selected by the active architecture decision;
- property/state-machine: generated command histories and invariants;
- integration: session or process boundaries;
- `termverify`: shipped TUI, explicit constraints, readiness, replay evidence;
- differential: selected pinned-reference scenarios with reviewed divergence policy.

## External evidence tools

- **TermVerify** is not yet a dev dependency; its publishing workflow is still being set up. Terminal evidence is currently limited to the fake-port tests in `tests/test_terminal.py` plus the `DREI:READY` readiness marker in `drei.terminal.run_editor`, which is the seam TermVerify scenarios will drive. Once TermVerify is published, add it to the dev group, author scenarios under `tests/termverify/`, and record the verified invocation here.
- **GNU Emacs** differential scenarios are pinned to a known version via a pinned CI runner image (`ubuntu-24.04` + `emacs-nox`, GNU Emacs 29.x) or an equivalent container locally; they never rely on an arbitrary host installation. Locally without Emacs, differential tests skip rather than fail. Run the differential tier explicitly with:

```bash
DREI_PARITY=1 uv --no-config run pytest tests/differential -q
```

## Verified commands

- Direct semantic evidence: `uv --no-config run pytest --cov --cov-report=term-missing`
- Shipped-terminal evidence: `uv --no-config run drei` in a real TTY (writes `DREI:READY`, exits cleanly on `C-g`); automated TermVerify scenarios pending the TermVerify dependency.
- Differential evidence: `DREI_PARITY=1 uv --no-config run pytest tests/differential -q` (requires Docker or a pinned 29.x host `emacs`).

## Coverage ratchet

`fail_under` is the integer floor of reviewed observed combined line-and-branch coverage. `precision = 2` prevents rounding grace. Raise only with durable headroom; lowering, exclusions, or tests written merely to inflate coverage require owner review.

## Dependencies and donor code

Add dependencies only at a demonstrated boundary and with tests. Record copied source provenance and Apache-2.0 attribution in the reuse assessment and `NOTICE` when required. Prefer reimplementing a small contract from behavioral tests over transplanting a subsystem.
