# Twelfth slice: multiple buffers and windows (A.2)

**Status:** ready — architecture gate: design 0002's hybrid ownership is exercised, not revised. `Buffer` identity shells already exist; this slice makes the session own **several** of them and introduces windows as layout views. The command boundary, immutable commands/events/observations, and the transcript oracle are unchanged. No new ports, no I/O, no minibuffer contract change beyond find-file gaining real buffer creation.

**Goal:** per design 0003 §A.2 — *"At least two windows over distinct buffers with independent points, so an agent transcript can sit beside the buffer being edited. Verify: observation records of window/point layout; existing window stress cases in 0002 already cover shared-buffer independent points."* Concretely: the session owns a **buffer set**; `find-file` creates (or reuses) a real buffer instead of replacing the single one; `C-x b` switches buffers; `C-x 2` splits the focused window horizontally; `C-x o` cycles window focus; `C-x 1` collapses to one window. Two windows over distinct buffers display side-by-side (stacked vertically) in the frame, each with its own modeline; two windows over the **same** buffer keep independent points (0002's stress case, now in the product).

**Why this slice, and why now:** B.7 (issue #26 / PR #27) explicitly deferred two things to A.2 — multi-buffer display of the agent transcript beside the work buffer, and any honest treatment of agent file edits (B.7 deviation 3 renders diffs into the transcript precisely because there is no second buffer to edit). Slice 7 likewise recorded single-buffer wholesale replacement as "resolves naturally when the multi-buffer slice lands." This slice is the shared prerequisite for both, and for §A.3 (read-only/generated buffers) after it.

## What exists today (the delta is small and nameable)

- `EditorSession(buffer, ...)` owns exactly one `Buffer` (`src/drei/session.py:250`); `dispatch` computes on `self.buffer.current` and the observation projects that one buffer.
- Session fields that Emacs scopes **per buffer** and this slice must move: `_undo_history`, `_undo_redo`, `_undo_descending` (undo is per-buffer in Emacs — probed below), `_yank_active`, `_yank_cursor`, `_yank_bounds`, `_last_was_kill` (kill-append chaining is last-command state; switching buffers breaks the chain in Emacs — probed below).
- Session fields that Emacs scopes **globally** and stay: `_kill_ring` (slice 7 pinned: kill in one buffer, yank in another), `_transcript`, `_process_log`, `_minibuffer`/prompt, both ports.
- `BufferObservation` (`src/drei/commands.py:285`) is single-buffer; `render` draws body + one modeline + echo (`src/drei/render.py:16`). No window concept exists anywhere.

## Design decisions (Drei-specified, Emacs-informed)

### D1. Buffers: identity, naming, lifecycle

- `EditorSession` gains `self._buffers: dict[BufferId, Buffer]` plus `self._current_id: BufferId`. `self.buffer` becomes a **property** resolving the current buffer (the harness's `session.buffer.current` reads keep working). Buffer construction moves inside the session: the session, not the harness, owns buffer creation (`harness.py:40` constructs one today — it will pass the initial value/id through and the session wraps it).
- **Buffer names** follow Emacs: a file buffer is named by its file's basename, suffixed `<2>`, `<3>`, … on collision — a recorded deviation from Emacs 29.3, which suffixes the **parent directory name** (`probe.txt<bbb>` — evidence item 1); Drei's numeric suffixes are deterministic without directory context. The initial buffer keeps its existing id (harness passes `BufferId` as today — tests construct `BufferId("test")`-style ids and must not churn). `BufferId.value` IS the name; no separate name field.
- Find-file on an **already-open path** (string equality on `file_path`) selects that buffer instead of re-reading — Emacs behavior, and the agent-workflow case (prompt file open in one window, agent edits landing in another) depends on it. Re-reading a file the user may have edited would be data loss.
- No buffer killing in this slice (`C-x k` deferred): the dict only grows. Buffers are cheap (frozen values); a `kill-buffer` command is its own slice with its own modified-buffer discipline.

### D2. Per-buffer state: undo, yank, kill-chain

A private per-buffer record (plain mutable class in `session.py`, mirroring `_UndoGroup`'s module-private status — NOT on `BufferValue`, which stays the frozen per-edit value): undo history/redo/descending, yank active/cursor/bounds, last-was-kill. The existing session fields become accessors into the current buffer's record; all eleven dispatch arms and the post-dispatch bookkeeping blocks (`session.py:426-464`) read/write through them.

- **Emacs-pinned:** undo is per-buffer (undo in buffer A never touches buffer B; probe below); yank-pop chaining and kill-append chaining are `last-command` state — a buffer switch intervenes and breaks both (probe below). The per-buffer record's chain flags are therefore **cleared on switch-away**, matching Emacs's semantics through Drei's simpler model (the flags live per record but only the current buffer's are ever live; clearing on switch makes a stale chain unreachable — exactly what Emacs's last-command does).
- Kill ring stays global (slice 7's pin, re-asserted by a cross-buffer yank test).
- The replay property's oracle gains the switch/clear rule so transcript evidence remains the oracle for chain state.

### D3. Windows: layout views, not editor state

- New frozen value `WindowValue(buffer_id: BufferId, point: int, mark: int | None)` — **window-point is NOT `BufferValue.point`**. Emacs: point is per-window; the buffer's `point` is the current window's (probe below). Drei: `BufferValue.point` remains the *editing* point of the current window; each window additionally stores its own point so `C-x o` away and back restores where that window was. On window switch-away, the departing window's `WindowValue` is updated from the buffer; on switch-to, the buffer's point/mark are set from the arriving window. Two windows over one buffer therefore hold independent points — the 0002 stress case, executable in-product.
- Session holds `self._windows: tuple[WindowValue, ...]` (ordered top-to-bottom) + `self._focused: int`. `C-x 2` splits the focused window into two stacked halves showing the **same** buffer (independent points, both initialized to the buffer's current point — Emacs shows the same point in both after split; probe). `C-x o` moves focus cyclically. `C-x 1` collapses to the focused window only. **Minimum frame height per window: 3** (body ≥1 + modeline + shared echo row); a split that would violate this is a silent no-op (frame height is a render-time input — see D4 for how the session learns it).
- No horizontal splits (`C-x 3`), no window resize (`C-x ^`), no `other-buffer` heuristics beyond "most recently used" for `C-x b` default — deferred; the layout tuple generalizes without schema change.

### D4. How the session knows the frame size (no port, no I/O)

`C-x 2`'s minimum-height rule needs the frame height, which today is a pure `render(...)` argument the session never sees. The harness already receives resize-free fixed dimensions at construction; the session gains an optional `frame_size: tuple[int, int] | None = None` constructor value (injected like ports, default None = unconstrained). The harness passes its width/height through. Direct-session tests pass explicit sizes. This is configuration injection, not an effect port — deterministic, constructor-fixed, recorded here so it can't be mistaken for a terminal read. (Terminal-resize handling stays out of scope exactly as it is today.)

### D5. Events and observation

New events (all immutable, all in the transcript):

- `BufferCreated(buffer_id, file_path | None)` — find-file creating a new buffer.
- `BufferSelected(buffer_id)` — find-file reuse, `C-x b`, and window-switch landing on a different buffer. (Window focus changes within the same buffer emit `WindowFocusChanged` instead — see below.)
- `WindowSplit(count)`, `WindowFocusChanged(index, buffer_id)`, `WindowsCollapsed()` — layout evidence.

`BufferObservation` is **unchanged in shape** (it describes the current buffer + minibuffer — every existing consumer and property keeps compiling). A new `SessionObservation` value adds: `buffers: tuple[BufferId, ...]`, `windows: tuple[WindowValue, ...]`, `focused: int`. `CommandOutcome.observation` stays `BufferObservation`; the session gains a `session_observation()` read-only accessor (mirrors `kill_ring`/`process_log` derived views). Layout property tests assert against the session observation; the transcript remains the event oracle.

### D6. Rendering

`render` gains an optional `windows: tuple[WindowRenderInput, ...]` parameter (None = today's single-pane path, byte-identical). When present: the body height is divided equally among windows (remainder to the topmost), each pane is body rows + its own modeline (`Drei: <id> <indicators>`), and the echo/minibuffer row is shared at the bottom. Cursor sits in the focused window at its point. No borders/separators between stacked panes beyond the modeline (Emacs separates stacked windows by their modelines too). TermVerify proves the shipped frames.

### D7. Command/key surface

- `C-x b` → minibuffer prompt `Switch to buffer: `, accepting a buffer **name**; unknown name creates a new empty buffer of that name (Emacs behavior — probed). RET on empty input selects the most-recently-used other buffer (Emacs default; Drei's first minibuffer **default** — the accept arm gains an optional `default` on `MinibufferOpened`-adjacent state: `_minibuffer_default: str | None`, session-side only, no event change). Reuses the slice-7 minibuffer machinery wholesale — no new prompt infrastructure.
- `C-x 2`, `C-x o`, `C-x 1` → direct commands (`SplitWindow`, `OtherWindow`, `DeleteOtherWindows`) in `_PREFIX_COMMANDS`.
- `find-file` (`C-x C-f`) semantics change per D1: create-or-select. The slice-7 wholesale-replacement path and its "undo history dropped" deviation are **removed** — the registry row is resolved, not carried forward.
- `InsertAgentText`/`DeliverSessionEffects` (B.7, PR #27): buffer-targeted delivery to a named agent buffer is **not** in this slice — it lands on whichever side merges second as a follow-up (B.7's deviation 3 covers the interim). This slice must not import or depend on the ACP layer.

## Emacs evidence (pinned `ubuntu:24.04`, emacs-nox 29.3, batch — **probed this session**, script `scratch/a2-probe.el`)

1. **Naming on basename collision:** two files named `probe.txt` in different directories → buffers `probe.txt` and `probe.txt<bbb>` — Emacs 29.3 appends the **parent directory name** in angle brackets (uniquify), not `<2>`. Drei adopts `<N>` numeric suffixes as a recorded deviation (simpler, deterministic without directory context).
2. **Per-buffer undo:** edit `undo-a` ("AAA"), edit `undo-b` ("BBB"), switch to `undo-a`, `undo` → `undo-a` reverts to `""`, `undo-b` still `"BBB"`. Undo never crosses buffers. ✓ as planned.
3. **Kill-chain breaks on buffer switch:** kill in `chain-a`, switch, kill in `chain-b` → ring head `"three four"`, second entry `"one two"`, **not appended** (`P3-appended=nil`). Control in one buffer with an interleaved insert also breaks the chain (head `"zz"`, second `"xx yy"`) — Drei's slice-3 rule "any event-emitting command breaks the chain" already matches both. ✓ as planned: switch breaks the chain.
4. **Independent window points:** one buffer, `C-x 2`, move top window to line 3 (point 13), `C-x o` → bottom window's point starts at **1** (the buffer's pre-split point, not the top window's moved point — each window tracks its own from split time), move bottom to end, `C-x o` back → top window's point **preserved at 13**. ✓ as planned (D3's window-point model), with the refinement: after split, both windows start at the buffer's current point.
5. **`C-x b` unknown name** → new empty buffer of that name (`P5-new-buffer=brand-new-name`, empty content). `(other-buffer)` returns the most recently used other buffer (`probe.txt<bbb>`) — the MRU default. ✓ as planned.
6. **Split too small** → Emacs **errors**: `"Window ... too small for splitting"`. Drei's silent no-op is a recorded deviation (no error-echo channel yet). ✓ as planned.

## Implementation order (thin verticals, strict TDD each)

1. **Buffer set + per-buffer record**: `_buffers`/`_current_id`, `self.buffer` property, per-buffer undo/yank/kill record with switch-clear rule. No new commands yet — all existing tests must pass unmodified (the strongest possible regression gate: one-buffer behavior is invariant). **Tests:** accessor equivalence; property suite green unchanged.
2. **find-file create-or-select** + `BufferCreated`/`BufferSelected` + basename naming with `<N>` suffix + already-open reuse + kill-ring-global cross-buffer yank test. **Tests:** open two files, edit both, switch via re-find-file; per-buffer undo isolation (evidence 2); registry-row removal test for the old deviation.
3. **`C-x b`** with minibuffer name input, MRU default, unknown-name creation. **Tests:** all three arms; abort leaves buffer untouched; the MRU default on empty RET.
4. **Windows**: `WindowValue`, `_windows`/`_focused`, `SplitWindow`/`OtherWindow`/`DeleteOtherWindows`, window-point save/restore on focus change, min-height no-op rule, `SessionObservation` + events. **Tests:** split same-buffer independent points (evidence 4); focus cycle; collapse; too-small split no-op; layout events in transcript.
5. **Render panes** + harness `frame_size` pass-through. **Tests:** pure render golden frames (two panes, modelines, cursor in focused pane); existing single-pane goldens byte-identical.
6. **Property tests**: replay strategy gains buffer/window command sub-sequences (FakeFilePort already exists); new invariants — (a) per-buffer undo never crosses buffers, (b) switch breaks kill/yank chains, (c) window points are independent per window over one buffer, (d) every `BufferSelected` target exists in `buffers`, (e) transcript-fold equivalence unchanged for single-buffer traces.
7. **TermVerify scenario** (Windows ConPTY): open file A, `C-x 2`, `C-x b` to file B → frame shows two stacked panes with distinct modelines; edit in B; `C-x o` → cursor/point back in A's pane at A's point. Keys are ordinary bytes (no delivery risk of the slice-5 class).
8. **Emacs differential**: batch script driving evidence 1–5, Drei driven through the session with a real-fixture FilePort; parity on names, contents, point, modified, undo isolation, chain breaks, window-point preservation.
9. **Docs**: README status, registry rows (new: `C-x 2` too-small no-op vs Emacs error, `<N>` numeric naming vs Emacs's `<dirname>` uniquify suffix, no kill-buffer, no horizontal split, minibuffer default is new-but-parity; **resolved**: slice-7's wholesale-replacement + undo-dropped rows), plan status, design 0003 §A.2 checkbox.

## Acceptance criteria

- Full quality gate green (`pytest --cov`, `ruff check`, `ruff format --check`, `mypy src tests`, `pre-commit run --all-files`); coverage ratchet held at 100%.
- Step-1 invariant demonstrated: the entire pre-slice test suite passes against the multi-buffer session with zero modifications.
- Every Emacs evidence item (1–5) has a focused test; deviations recorded as registry rows with tests naming the rows.
- `C-x b`/`C-x 2`/`C-x o`/`C-x 1` proven through the harness byte loop and TermVerify frames; render single-pane path byte-identical.
- Buffer/window state fully derivable from or consistent with transcript evidence per the replay property; `SessionObservation` is a derived view, never a second oracle.
- No dependency on or import of `drei.acp.*` (B.7 integration is a follow-up on whichever side merges second).

## Risks / open questions

- **Merge race with PR #27 (B.7):** both touch `session.py` and `commands.py`. Mitigation: this slice adds new match arms and new observation accessors without changing existing arm signatures; whoever merges second rebases. The `InsertAgentText`-targets-agent-buffer unification is explicitly a follow-up, not this slice.
- **Per-buffer mark**: `BufferValue.mark` already exists per buffer (slice 5) — windows store their own mark snapshot (D3); Emacs's mark-ring subtleties stay out of scope exactly as slice 5 scoped them.
- **MRU list**: a simple recency list of buffer ids maintained on `BufferSelected`; eviction never needed (no kill-buffer). If a later slice adds buffer killing, the list gains removal in the same slice.
