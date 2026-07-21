# Drei

**Drei Resembles Emacs Intentionally.** Drei is an Emacs-like terminal editor built as a demanding example of agent-first software development and as a real-world test subject for [TermVerify](https://github.com/hoelzl/termverify).

Drei follows Eine ("Eine Is Not Emacs") and Zwei ("Zwei Was Eine Initially"), editors associated with the Lisp Machine tradition. The goal is not to clone all of GNU Emacs. It is to build a coherent, extensible editor whose semantics and terminal behavior agents can develop and verify autonomously.

## Status

Twelfth vertical slice: multiple buffers and windows. Supported commands: insert printable text, `C-f`/`C-b` horizontal movement, `C-k` kill-line, `C-y` yank, `M-y` yank-pop, `C-@` set-mark, `C-w` kill-region (point↔mark as one ring entry, point at the kill start, forward/backward direction recorded), `M-w` copy-region-as-kill (ring push without deleting, never sets modified), `C-x C-x` exchange-point-and-mark, `C-/`/`C-x u`/`C-_` undo (session-side stack of inverse patches, capacity 100; one group per text-changing command; consecutive undos descend; any event-emitting non-undo command breaks the descent and a following undo redoes; a fresh edit truncates the redo tail — deviation from stock Emacs's redo reachability, registry; undo restores point, mark, and the modified flag from the group; nothing-to-undo is a silent no-op), `C-x C-f` find-file through the minibuffer (prompt on the echo row; printable input, `DEL` backspace, `RET` accept, `C-g` abort; an already-open file selects its buffer, a new file creates one — basename id, `<N>` suffix on collision; a missing file or missing directory yields an empty unmodified buffer, other read errors leave the buffer untouched with `OpenFailed`; abort closes the prompt without quitting and without touching the buffer or mark), `C-x C-s` save through an injected file port, `C-x b` switch-to-buffer (empty input selects the MRU other buffer), `C-x 2`/`C-x o`/`C-x 1` split/cycle/collapse stacked windows with per-window points (each pane renders its buffer with its own modeline; the echo row is shared), and `C-g` clean exit (also clears the mark). The mark lives on the frozen `BufferValue` and follows Emacs marker adjustment on every edit (insert before shifts, insert AT stays before, delete shifts/clamps — parity-pinned); region kills get their own `RegionKilled` event so mark state stays derivable from the transcript. Undo history, yank state, and the kill-chain flag live in a per-buffer record (undoing an interleaved history replays exactly that buffer's edits — property-pinned); the session-owned kill ring (capacity 60) appends consecutive kill-lines, opens a fresh entry for region kills, and rotates a cursor for yank-pop; undo does not touch it. While the minibuffer is open only its four commands act (everything else is a silent no-op, incl. nested `C-x C-f` — no recursive minibuffer); the harness routes keys at a single site. `drei [FILE]` opens a file; the modeline shows the `**`/`--` modified indicator covering every text-changing event (`TextInserted`/`TextKilled`/`TextYanked`/`TextYankPopped`/`RegionKilled`) and undo/redo restoration. Immutable records cross the session boundary; `BufferObservation` exposes the mark and the minibuffer state and `SessionObservation` adds the whole-session read model (buffer names, one `WindowObservation` per pane, focused index); all filesystem access sits behind the `FilePort`. The shipped executable is proven by TermVerify ConPTY scenarios (save, kill/yank, yank-pop prefix, undo via `C-x u` with a live `\x1f`/`C-/` byte arm, find-file accept with live `\x0d` RET and `\x7f` DEL arms, find-file abort, window split/focus/collapse, and buffer switching); `M-y`/`M-w` assemble ESC+letter in the byte loop (ConPTY swallows bare ESC — termverify#169) and `C-@` is undeliverable on the Windows console (msvcrt treats NUL as an extended-key prefix — verified live, registry deviation), both proven in-process through the same `run_editor` loop. Pinned GNU Emacs differential scenarios guard parity for insertion, movement, save, non-append kill/yank, first yank-pop, forward/backward region kill, copy with clean modified flag, marker adjustment, single undo (text/point/modified), find-file semantics (existing/missing/missing-directory), MRU switch-to-buffer, find-file buffer reuse, and per-window points across focus round-trips. The append chain, pop cycle, mark-deactivation-on-edit, no-mark no-ops, yank-not-pushing-mark, mark-ring absence, undo grouping, redo truncation, descent gating, undo capacity, empty-input no-op accept, no recursive minibuffer, no minibuffer keymap, directory-path rejection, `<N>` numeric collision suffix vs uniquify, `C-x 2` too-small no-op, vertical-only window stacks, no kill-buffer, and minibuffer default naming are intentional deviations (registry). The slice-7 wholesale-replace/discard hazard is resolved (find-file now creates per-file buffers). Modes, kill-buffer, horizontal splits, and extensions remain deferred.

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
