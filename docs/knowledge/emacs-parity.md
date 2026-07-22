---
type: concept
title: GNU Emacs parity policy
description: Pinned differential scenarios, normalization rules, and divergence governance.
tags: [verification, differential, emacs, parity]
---

# GNU Emacs parity policy

GNU Emacs is a behavioral reference, not an unquestioned specification. Each
differential scenario states whether parity is required or a Drei deviation is
intentional, and every normalization is explicit.

## Pinned reference

- **Version:** GNU Emacs 29.x (validated against 29.3).
- **CI:** dedicated `parity` job on the pinned `ubuntu-24.04` runner image
  with `apt-get install -y emacs-nox` (mirrors the Recursive://Neon pattern).
  The runner pin keeps the distro Emacs at a known version; bump it only
  deliberately and re-validate all scenarios.
- **Local:** `DREI_PARITY=1 uv --no-config run pytest tests/differential -q`
  runs the same scenario in the `ubuntu:24.04` container (installing
  `emacs-nox`) or against a host `emacs` that reports the pinned 29.x series.
  Without the opt-in, or without Docker/host Emacs, the scenario skips — it
  never fabricates a baseline.

## Scenario registry

| Scenario | Reference behavior | Normalization | Verdict |
| --- | --- | --- | --- |
| startup, insert, backward-char, forward-char (`tests/differential/test_emacs_parity.py`) | empty scratch-like buffer; `insert`, `backward-char`, `forward-char` | Emacs point is 1-based; Drei point is 0-based (`point_emacs - 1 == point_drei`) | parity required |
| find-file (new), insert, save-buffer (`tests/differential/test_emacs_parity.py`) | `buffer-modified-p` is `t` after `insert`, `nil` after `save-buffer`; file content on disk matches buffer text | same point normalization; Drei drives `SaveBuffer` through the production dispatch path with a fake `FilePort` and asserts content in the fake | parity required |
| kill-line ×2, yank (`tests/differential/test_emacs_parity.py`) | first `kill-line` kills text to EOL, second kills the newline, `yank` inserts the newest entry leaving point after it | same point normalization; the append chain is an intentional deviation (see below) so Drei's yank restores the full original text while batch Emacs yanks only the newest (unappended) entry | parity required on the non-append pieces (kill-to-EOL text, newline kill, yank-inserts-newest-with-point-after) |
| yank, yank-pop (`tests/differential/test_emacs_parity.py`) | `yank` inserts the newest of a 2-entry ring, then `yank-pop` (with `last-command` forced — batch cannot propagate it) replaces the yanked span with the next-older entry; entries have different lengths so point placement is pinned (`point = start + len(new)`) | same point normalization; batch `kill-line` point handling leaves point after the leftover newline, and Drei drives the same positions | parity required on the first pop; the pop cycle is an intentional deviation (see below) |
| region kill/copy (`tests/differential/test_emacs_parity.py`) | `kill-region` forward (mark behind point) and backward (point behind mark) remove point↔mark as one ring entry, point at the kill start; `copy-region-as-kill` pushes without deleting; a clean-buffer copy leaves `buffer-modified-p` nil | same point/mark normalization (1-based → 0-based); Drei clears the mark on kill/copy (batch keeps the numeric mark — deactivation is interactive) | parity required on region text/point/ring-head and the clean-copy modified flag |
| marker adjustment (`tests/differential/test_emacs_parity.py`) | kill-line before a surviving mark shifts it left; insert before shifts right; insert AT the mark keeps it before the inserted text | same normalization; Drei drives the identical command sequence | parity required on the resulting mark position |
| find-file (`tests/differential/test_emacs_parity.py`) | `find-file` on an existing file loads contents with point at start and MOD nil; a missing file yields an empty unmodified buffer with no error; a missing DIRECTORY likewise yields an empty buffer (probed twice) | same point normalization; batch minibuffer reads stdin, so only find-file semantics are compared — the prompt interaction is proven by the TermVerify scenario | parity required on all three arms |

Drei's modified-flag rule is deliberately narrower than Emacs's: any
text-changing event (`TextInserted`, `TextKilled`, `TextYanked`,
`TextYankPopped`, `RegionKilled`) sets modified; a successful save
clears it; `TextUndone`/`TextRedone` RESTORE the flag from their undo
group (undoing back to a saved state clears modified, matching Emacs's
`(t . 0)` undo entries). `SetMark`, `RegionCopied`, and `MarkExchanged`
never set it (probed: `set-mark` and `copy-region-as-kill` leave
`buffer-modified-p` nil on a clean buffer). Emacs also sets the flag on
some non-text operations; if a future scenario observes drift there,
record it as an intentional deviation with rationale.

## Intentional deviations

| Behavior | Drei | Emacs | Rationale |
| --- | --- | --- | --- |
| Append-on-consecutive-kill | consecutive `C-k` appends into one ring entry | batch `--eval` does not append (verified against 29.3: `("\n" "ab")`, two entries; even an explicit `(setq last-command 'kill-line)` does not append in batch) | matches *interactive* Emacs `kill-line`; batch-unverifiable, so pinned by unit/property tests instead of the differential |
| Empty kill at buffer end | silent no-op, no event | signals "End of buffer" (an error) | Drei has no echo-error mechanism yet; recorded here until one exists |
| Yank-pop cycle (second and later pops) | cursor advances modulo ring size, wrapping | batch `yank-pop` falls back to `read-from-kill-ring` (reads stdin) once `last-command` propagation ends; verified against 29.3 | batch-unverifiable, so pinned by unit/property tests (2-entry wrap) instead of the differential |
| Yank-pop without an active yank | silent no-op, no event | signals "Previous command was not a yank" (an error) | same no-echo-error rationale as the empty kill |
| Yank-pop on a one-entry ring | silent no-op, no event | replaces the entry with itself (sets modified) | a self-replacement event would set `modified` on unchanged text, contradicting the modified-flag invariant; no-opping keeps the flag honest |
| Kill-ring capacity | fixed 60 | `kill-ring-max` (default 60, configurable) | configuration is deferred |
| Mark deactivation on edit | set mark survives insert/motion (adjusted marker-style, parity-pinned) | deactivates on many commands interactively | interactive `last-command` behavior — batch-unverifiable (batch `mark-active` survives `insert`, verified); simplest deterministic rule |
| Region kill/copy without a mark, empty region | silent no-op, no event | signals an error (no mark) / kills nothing | same no-echo-error rationale as the empty kill |
| Yank pushing the mark | yank does not touch the mark | `yank` sets the mark at the insertion start | deferred to the mark-ring slice; yank bounds stay `(start, end)` |
| Mark ring (`C-u C-@`) | absent (single mark, re-set replaces) | ring of 16 marks per buffer | deferred; single mark covers the region commands |
| `C-@`/`C-SPC` on the Windows console | undeliverable — msvcrt treats NUL as an extended-key prefix and swallows the following byte (verified live: NUL+`Z` consumes `Z`); commands reachable via harness/POSIX | real Windows Emacs uses different input plumbing (w32 events) | platform console-API constraint, not semantics; TermVerify scenario is a documented skip, byte-loop proof is in-process |
| Undo grouping | one group per command | groups are command-loop driven; batch `undo` amalgamates across explicit `undo-boundary` calls (probed) | batch-unverifiable; one-group-per-command is the interactive equivalent |
| Nothing to undo | silent no-op, no event | signals "No further undo information" (probed in batch) | same no-echo-error rationale as the empty kill |
| Fresh edit after undo | truncates the redo tail (later undos cannot resurrect) | keeps redo reachable — the fresh edit flips direction and the next undo REDOES the buried undo (probed: insert, undo, insert, undo → text restored, MOD t) | deliberate simplification; stock redo reachability is a follow-up slice |
| Descent gating | any event-emitting non-undo command breaks the descent; silent no-ops (empty-ring yank, no-mark kill-region) do not | any command flips direction (`last-command` gating), including silent no-ops | batch-unverifiable interactive behavior; event-emitting gating keeps the walk derivable from the transcript |
| Undo capacity | fixed 100 groups, oldest dropped silently | `undo-limit`/`undo-strong-limit` (80k/120k bytes) | configuration is deferred; mid-descent eviction shortens the deepest history only |
| Undo of the kill ring | ring untouched by undo | ring untouched (global, not buffer state) | parity — no deviation |
| Undo restoring mark/modified | restored from the group | markers and `(t . 0)` modified-flag entries are restored (probed: undo to clean state → MOD nil) | parity |
| find-file on a missing file or missing directory | empty unmodified buffer, point at start, `BufferOpened` — no error | `New file` in the echo area; empty buffer, MOD nil, no error (probed) | parity on buffer state; the echo message differs (Drei has no echo-error/message mechanism yet) |
| find-file on a directory path | `OpenFailed` (read fails with an OS error) | opens dired | dired is out of scope; recorded until a directory mode exists |
| find-file with an already-open file | selects the existing buffer (create-or-select on `file_path`, no `<2>` duplicate) — **resolved by A.2** (differential `test_emacs_differential_find_file_reuses_buffer_name`) | revisits the same buffer | parity |
| find-file opening a new file | creates a new per-file buffer and selects it — the previous buffer (undo history, unsaved edits) survives — **resolved by A.2** (the slice-7 wholesale-replace/discard hazard is gone) | visits the file in a new buffer | parity on the visit; killing a modified buffer still has no guard (no kill-buffer command yet — deferred) |
| find-file with empty input | silent no-op close (no event, buffer untouched) | accepts the default (current buffer's directory) | defaults/completion are deferred; the no-op keeps the accept-always-closes invariant |
| Minibuffer abort (`C-g`) | closes the prompt, buffer untouched, never emits a quit | `quit` — aborts the minibuffer without touching the buffer | parity; the quit signal itself is event-driven in Drei (`KeyboardQuitEvent`) so routing `C-g` to abort while the minibuffer is open is safe |
| Recursive minibuffer (`C-x C-f` while open) | ignored (silent no-op) | allowed when `enable-recursive-minibuffers` is set (default nil signals an error) | single-slot minibuffer; recursion deferred |
| Minibuffer keymap | none — control/meta keys are ignored; only printable input, RET, DEL, C-g act | full minibuffer local map (completion, history, next-line…) | completion/history deferred to a later slice |
| Minibuffer rendering of over-wide input | clipped at the frame width; cursor parks at the right edge (no horizontal scroll) | minibuffer scrolls horizontally | presentation-only deviation; scroll deferred with display refinements |
| Backspace at empty minibuffer | silent no-op | delete-backward-char fails silently at prompt start | parity on observable state |
| Buffer names / switching | buffer set with basename ids; `C-x b` prompts (empty input = MRU other buffer, parity with `(switch-to-buffer nil)` — differential `test_emacs_differential_switch_buffer_mru_default`) — **resolved by A.2** | per-file buffer names, `switch-to-buffer` | parity on switching; name collisions resolve with a `<N>` numeric suffix vs Emacs's `<dirname>` uniquify suffix (deviation: uniquify is display-naming, deferred) |
| `C-x 2` below minimum height | silent no-op (frame keeps one window) when `frame_size` is known and too small | `split-window-below` errors "too small for splitting" | deviation: Drei has no error-signal mechanism; the no-op keeps the layout valid. Pinned by unit tests |
| Windows | vertical stacks only (`C-x 2`/`C-x o`/`C-x 1`); per-window points (differential `test_emacs_differential_window_focus_keeps_window_points`) | horizontal splits, window trees, window configurations | deviation: one layout axis covers the transcript-beside-work case; horizontal split and window configurations deferred |
| kill-buffer | absent | `C-x k` kills a buffer (prompts when modified) | deferred; buffers are created and selected but never killed |
| Kill ring scope | session-global — a kill in buffer A is yankable in buffer B, and B's yank changes what A sees at its ring head (the isolation oracle in `test_interleaved_buffers_match_solo_replays` routes the other buffer's ring-touching edits to scratch precisely because the ring mediates cross-buffer interference) | global kill ring (same cross-buffer visibility) | parity — the ring is Emacs-global by design |
| Agent deliveries not undoable | `InsertAgentText` creates no undo group; `Undo` skips agent text and reaches the newest user edit (pinned by `TestAgentDeliveriesNotUndoable`) | n/a — Emacs has no agent buffer; comint output is ordinary (undoable) text | undo of an external stream is incoherent with the fold-of-effects invariant: the next delivery appends to a text the fold no longer recognizes. Hazard owned: a user who interleaves edits into the agent buffer cannot undo across an agent-delivery boundary; §A.3 (read-only regions) shrinks it by discouraging interleaved edits |
| User edits to the agent buffer not rejected | `InsertText` in the agent buffer succeeds; the next delivery appends after the edit (pinned by `TestUserEditsToAgentBufferNotRejected`) | n/a — Emacs would use `buffer-read-only` / comint field protection | §A.3 owns the enforcement mechanism (read-only/generated buffers). **Hazard owned explicitly: after a user edit, the live buffer text diverges from the pure fold of `AgentTranscriptUpdated` events — the two oracles disagree until the buffer is re-created.** Not benign: any consumer folding the transcript events sees a different text than the screen shows |
| Agent file edits rendered, not applied | `diff` tool-call content lands in the transcript as text (path + old/new); no target buffer is modified and the filesystem port is not involved (pinned by `test_diff_renders_old_and_new_verbatim` and the §B.7 golden trace) | n/a — no Emacs equivalent; ACP peers with buffer support apply edits | applying diffs requires multiple buffers (§A.2) and a conflict policy for modified user buffers — a separate slice. B.6 refuses `fs/*` requests (Drei advertises no fs capability), so diffs only ever arrive as transcript content |
| External deliveries bypass the minibuffer gate | `DeliverProcessOutput` / `DeliverSessionEffects` / `InsertAgentText` act while the minibuffer is open; every other non-minibuffer command is a silent no-op (pinned by `TestMinibufferDoesNotSwallowDeliveries`). `PromptPermission` (B.8) is delivery-class too: a permission request arriving while a prompt is open **queues** and is presented after the prompt resolves (pinned by `test_is_gate_exempt_like_a_delivery`, `test_request_during_text_prompt_presented_after`) | n/a — Emacs has no delivery commands; async process output inserts regardless of minibuffer state | a dropped delivery would desync the agent-buffer fold from the transcript (the fold advances but the event is lost) — deliveries are not user input and must not be gated on it. A swallowed permission request is worse: the agent blocks indefinitely awaiting its answer, so queue-and-present replaces drop |
| `allow_always` honoured only within the ACP session | the auto-approval cache is cleared on `new_session()`; Drei has no cross-session persistence (pinned by `test_cache_resets_on_new_session`) | n/a — no Emacs equivalent; ACP `allow_always` implies durable approval | design 0003's session-persistence open question is unresolved; honouring `always` across restarts would require a store Drei does not have. Hazard owned: a user who chose "always" is re-prompted next session — safe (fail-closed), recorded here until persistence lands |
|| Auto-approval scoped to tool identity + arguments, never the per-call `toolCallId` | the identity key is `toolCall.kind`/`title` + canonical-JSON of the params (including arguments and options); any change re-prompts (pinned by `test_different_arguments_re_prompts`, `test_different_title_re_prompts`, `test_changed_options_re_prompts`) | n/a — no Emacs equivalent | ACP `allow_session`/`allow_always` are scope grants over a *tool*; keying on the per-invocation `toolCallId` would both miss the cache and fail open if an agent reused an id. Fail-closed: changed arguments/options always re-prompt rather than inherit a grant |
|| Malformed permission payloads never auto-approve | option kinds matched by explicit 0.9.0 enum, never `startswith`; duplicate `optionId`s resolve to the LAST (shadowing) match; a cached identity offering no allow option re-prompts (pinned by `test_invented_allow_kind_*`, `test_duplicate_option_id_last_match_wins_no_cache_poison`, `test_select_auto_option_prefers_scope_then_once`) | n/a — no Emacs equivalent | a hostile agent can name an option `allow_evil` or order a duplicate `allow_always` before a reject; startswith/first-match would auto-approve it. Fail-closed: anything not exactly an enum kind, or ambiguous, re-prompts the human |

## Rules

1. A scenario compares **semantic** observations (text, point) only. Terminal
   presentation differences are out of scope for parity.
2. Normalization rules are part of the scenario and change only with a
   readable diff and explicit review.
3. Unexpected differences fail; intentional Drei deviations are recorded in
   this registry with a rationale, never silenced in test code.
