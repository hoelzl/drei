# Drei

**Drei Resembles Emacs Intentionally.** Drei is an Emacs-like terminal editor built as a demanding example of agent-first software development and as a real-world test subject for [TermVerify](https://github.com/hoelzl/termverify).

Drei follows Eine ("Eine Is Not Emacs") and Zwei ("Zwei Was Eine Initially"), editors associated with the Lisp Machine tradition. The goal is not to clone all of GNU Emacs. It is to build a coherent, extensible editor whose semantics and terminal behavior agents can develop and verify autonomously.

## Status

Third vertical slice: kill ring on top of file-backed buffers. Supported commands: insert printable text, `C-f`/`C-b` horizontal movement, `C-k` kill-line (point→EOL text, the newline at EOL, or a silent no-op at buffer end), `C-y` yank (newest ring entry, point moves past the yanked text), `C-x C-s` save through an injected file port, and `C-g` clean exit. The session-owned kill ring (capacity 60) appends consecutive kills into one entry and breaks the chain on any non-kill command. `drei [FILE]` opens a file (missing files open an empty visiting buffer, per Emacs `find-file`); the modeline shows a `**`/`--` modified indicator covering every text-changing event (`TextInserted`/`TextKilled`/`TextYanked`). Immutable command, event, observation, and frame records cross the session boundary; all filesystem access sits behind the explicit `FilePort` effect port. The shipped executable is proven end to end by TermVerify ConPTY scenarios on Windows (save round-trip to disk, kill/yank frame evidence), and pinned GNU Emacs differential scenarios guard parity for insertion, movement, save-clears-modified, and the non-append kill/yank pieces. The append chain is an intentional, batch-unverifiable deviation (pinned by unit/property tests, recorded in the parity registry). `M-y`, region kill/copy, undo, minibuffer, multiple buffers/windows, modes, and extensions remain deferred.

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

Drei will expose deterministic command execution and immutable semantic evidence through an in-process harness and a terminal frontend. Stable runtime-owned identity shells will own immutable or controlled-private domain values behind a serialized, atomic command boundary; whole-model immutability is not required. Tests progress from unit/property contracts to replayable scenarios and TermVerify-driven end-to-end evidence. GNU Emacs differential tests are selective and explicit; intentional Drei behavior remains possible.

## License

Apache-2.0. Recursive://Neon is also Apache-2.0, but no source was copied during this bootstrap. Future reuse must follow the recorded reuse assessment and preserve attribution.
