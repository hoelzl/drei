# Drei

**Drei Resembles Emacs Intentionally.** Drei is an Emacs-like terminal editor built as a demanding example of agent-first software development and as a real-world test subject for [TermVerify](https://github.com/hoelzl/termverify).

Drei follows Eine ("Eine Is Not Emacs") and Zwei ("Zwei Was Eine Initially"), editors associated with the Lisp Machine tradition. The goal is not to clone all of GNU Emacs. It is to build a coherent, extensible editor whose semantics and terminal behavior agents can develop and verify autonomously.

## Status

Bootstrap only. The package identity is executable; editor behavior starts with the vertical slice in `docs/agent/plans/0001-first-editor-slice.md`.

## Setup

```bash
uv --no-config sync --all-groups --locked
uv --no-config run pytest --cov --cov-report=term-missing
uv --no-config run drei --version
```

Install both local hook stages once:

```bash
uv --no-config run pre-commit install --hook-type pre-commit --hook-type pre-push
```

See `CONTRIBUTING.md`, `AGENTS.md`, and `docs/knowledge/index.md` before changing behavior.

## Direction

The implementation starts from a pure, deterministic editing core and exposes the same behavior through an in-process harness and a terminal frontend. Tests progress from unit/property contracts to replayable scenarios and TermVerify-driven end-to-end evidence. GNU Emacs differential tests are selective and explicit; intentional Drei behavior remains possible.

## License

Apache-2.0. Recursive://Neon is also Apache-2.0, but no source was copied during this bootstrap. Future reuse must follow the recorded reuse assessment and preserve attribution.
