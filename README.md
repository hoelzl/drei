# Drei

**Drei Resembles Emacs Intentionally.** Drei is an Emacs-like terminal editor built as a demanding example of agent-first software development and as a real-world test subject for [TermVerify](https://github.com/hoelzl/termverify).

Drei follows Eine ("Eine Is Not Emacs") and Zwei ("Zwei Was Eine Initially"), editors associated with the Lisp Machine tradition. The goal is not to clone all of GNU Emacs. It is to build a coherent, extensible editor whose semantics and terminal behavior agents can develop and verify autonomously.

## Status

Sixth vertical slice: undo. Supported commands: insert printable text, `C-f`/`C-b` horizontal movement, `C-k` kill-line, `C-y` yank, `M-y` yank-pop, `C-@` set-mark, `C-w` kill-region (point↔mark as one ring entry, point at the kill start, forward/backward direction recorded), `M-w` copy-region-as-kill (ring push without deleting, never sets modified), `C-x C-x` exchange-point-and-mark, `C-/`/`C-x u`/`C-_` undo (session-side stack of inverse patches, capacity 100; one group per text-changing command; consecutive undos descend; any event-emitting non-undo command breaks the descent and a following undo redoes; a fresh edit truncates the redo tail — deviation from stock Emacs's redo reachability, registry; undo restores point, mark, and the modified flag from the group; nothing-to-undo is a silent no-op), `C-x C-s` save through an injected file port, and `C-g` clean exit (also clears the mark). The mark lives on the frozen `BufferValue` and follows Emacs marker adjustment on every edit (insert before shifts, insert AT stays before, delete shifts/clamps — parity-pinned); region kills get their own `RegionKilled` event so mark state stays derivable from the transcript. The session-owned kill ring (capacity 60) appends consecutive kill-lines, opens a fresh entry for region kills, and rotates a cursor for yank-pop; undo does not touch it. `drei [FILE]` opens a file; the modeline shows the `**`/`--` modified indicator covering every text-changing event (`TextInserted`/`TextKilled`/`TextYanked`/`TextYankPopped`/`RegionKilled`) and undo/redo restoration. Immutable records cross the session boundary; `BufferObservation` exposes the mark; all filesystem access sits behind the `FilePort`. The shipped executable is proven by TermVerify ConPTY scenarios (save, kill/yank, yank-pop prefix, undo via `C-x u` with a live `\x1f`/`C-/` byte arm); `M-y`/`M-w` assemble ESC+letter in the byte loop (ConPTY swallows bare ESC — termverify#169) and `C-@` is undeliverable on the Windows console (msvcrt treats NUL as an extended-key prefix — verified live, registry deviation), both proven in-process through the same `run_editor` loop. Pinned GNU Emacs differential scenarios guard parity for insertion, movement, save, non-append kill/yank, first yank-pop, forward/backward region kill, copy with clean modified flag, marker adjustment, and single undo (text/point/modified). The append chain, pop cycle, mark-deactivation-on-edit, no-mark no-ops, yank-not-pushing-mark, mark-ring absence, undo grouping, redo truncation, descent gating, and undo capacity are intentional deviations (registry). Minibuffer, multiple buffers/windows, modes, and extensions remain deferred.

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
