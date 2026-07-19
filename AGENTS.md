# Drei — Agent Guide

Drei ("Drei Resembles Emacs Intentionally") is an Emacs-like terminal editor and a reference project for autonomous, multi-harness software development. The codebase is intentionally small today; planned behavior is not current behavior.

## Start here

1. Read `README.md` and `docs/knowledge/index.md`.
2. Read `docs/developer-guide/development.md` before changing code or gates.
3. Read the relevant design record or active plan under `docs/agent/`.
4. Treat executable tests, `pyproject.toml`, and live CLI help as authoritative over prose; fix stale prose in the same change.

## Commands and sources of truth

| Question | Authoritative source |
| --- | --- |
| Dependencies and Python support | `pyproject.toml`, `uv.lock`, CI matrix |
| Quality commands | `pyproject.toml`, `.pre-commit-config.yaml`, CI |
| Editor behavior | tests, then `src/drei/`; GNU Emacs only where a parity contract says so |
| Architecture and verification | `docs/knowledge/` and accepted records in `docs/agent/design/` |
| Current work | Git status and active plan/handover; never this file |

## Non-negotiable rules

- Use `uv`; never use `pip install` or edit `uv.lock` manually.
- Support Python 3.12 through 3.14 and use the `src/` layout.
- Use strict TDD for every behavior change: focused failing test, observed expected failure, minimum implementation, focused pass, then wider gates.
- Build vertical behavior slices; do not create speculative framework layers.
- Keep editor semantics deterministic and independent of terminal, clock, randomness, filesystem, environment, and network. Inject effects through explicit ports.
- Prefer immutable commands, event records, observation records, and configuration values. Do not assume the live editor model is immutable; follow the architecture decision and spike in `docs/agent/design/0002-live-editor-state-architecture-spike.md`.
- Treat terminal output as evidence, not the only oracle. Test semantic state directly and prove the shipped TUI separately.
- TermVerify is the preferred interactive verification boundary. If Drei needs evidence TermVerify cannot capture, first reduce it to a concrete test; then file or fix a TermVerify issue under that project's conventions.
- GNU Emacs is a behavioral reference, not an unquestioned specification. Each differential scenario must state whether parity is required or a Drei deviation is intentional.
- Never auto-approve changed snapshots or divergence baselines. Require a readable diff and explicit review.
- Do not copy donor code or add a dependency without recording provenance, rationale, and a test-first adoption plan.

## Validation

```bash
uv --no-config sync --all-groups --locked
uv --no-config run pytest --cov --cov-report=term-missing
uv --no-config run ruff check .
uv --no-config run ruff format --check .
uv --no-config run mypy src tests spikes/001-editor-state-architecture/experiment.py
uv --no-config run pre-commit run --all-files
uv --no-config run pre-commit run --hook-stage pre-push --all-files
uv --no-config build
```

During development run the narrowest relevant test first. Before completion, run the full applicable gate and review `git diff --check` plus the complete diff.

## Layout and documentation placement

| Content | Location |
| --- | --- |
| Product package | `src/drei/` |
| Executable contracts | `tests/` |
| Human introduction | `README.md` |
| Stable workflows | `docs/developer-guide/` |
| Durable product/architecture/verification knowledge | `docs/knowledge/` |
| Decisions, reuse assessments, plans, handovers | `docs/agent/` |
| Proven recurring agent procedures | `.agents/skills/` only after validation |

Keep this file compact. Harness-specific files are thin adapters and must not duplicate project knowledge. Durable facts belong in the repository, not private harness memory. Use isolated external worktrees for parallel writers and keep the primary checkout as integration point.
