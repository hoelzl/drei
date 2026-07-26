# Eighteenth slice: the save-buffers prompt on exit

**Status:** implemented (issue #48). Two places the code diverged from this
plan, both recorded rather than quietly absorbed:

- **§5's sweep was measured low.** 9 in-process sites in `test_terminal.py`
  (not 11 — several predicted sites end on a *clean* buffer and never reach a
  prompt) and **9** ConPTY scenarios, not 5: `navigation`, the two in
  `test_shipped_windows.py`, and `survives_an_agent_that_will_not_start` all
  type into `scratch` and so meet the stage-2 gate. The self-checking property
  §5 claims held exactly as described — every miss was a hard failure, none
  was a test that kept passing while asserting less.
- **V1 landed stage 1 and the direct exit; the gate arrived in V2**, as
  planned — but `_advance_exit` reached its final three-way shape in V2 rather
  than being extended, because the "after the last offer, exit" placeholder
  and the real gate are the same branch point.

Two ConPTY scenarios exit *through* stage 1 rather than around it
(`kill_yank` answers `y` and the file is written; `yank_pop` answers `n` and
then confirms), which is more than §9 asked for and is where the acceptance
criterion "proven through the shipped executable" is met.

**One behavior arrived that this plan did not specify.** The adversarial
review found that a stage-1 save which *fails* was completely silent: the
`SaveFailed` event is one of the three `_echo_for` renders, but the same
outcome opens the next exit prompt, and an open minibuffer owns the echo row —
so the message was drawn over unread. The user had asked to save, saw no
failure, and was then asked `Modified buffers exist; exit anyway?`, which
reads as being about some *other* buffer. D3's "On screen" line described the
control flow and mistook it for the message. The fix carries the failure in
the next prompt (`<path>: <token>. ` prefix, `_echo_for`'s own shape) rather
than building the message mechanism §4 defers, and it is registry row 8.

**Architecture gate:** none — no design record owns the keymap or the exit
path, and this slice does not need one. It is `docs/technical-debt.md` **TD-11
step 2**: the half slice 17 deliberately left open, plus the parity rows that
govern it. It touches design 0003 §B.8's choice minibuffer only as a
*neighbour* — see D4, which deliberately does not generalize it.

**Goal:** after this slice, no unsaved work leaves the editor without the user
having said so. `C-x C-c` offers to save each modified file-visiting buffer
(`Save file /tmp/notes.txt? (y or n)`), and if anything is still modified when
the offers are done — including a pathless buffer Drei cannot save at all — it
asks `Modified buffers exist; exit anyway? (y or n)` before ending the run.
`C-g` at any prompt abandons the exit and leaves the editor running. TD-11
closes.

## 1. The acceptance scenario

The file-backed case, which is the one that writes to disk:

```text
drei /tmp/notes.txt   → buffer "notes.txt" holds "saved", modeline "--"
type "x"              → buffer "xsaved", modeline "**"
press C-x             → prefix pending, frame unchanged
press C-c             → echo row shows "Save file /tmp/notes.txt? (y or n) "
                      → THE EDITOR IS STILL RUNNING, nothing written yet
press y               → /tmp/notes.txt on disk now holds "xsaved"
                      → echo row of the FINAL frame shows "Wrote /tmp/notes.txt"
                      → the process exits, status 0
```

The last two lines are the ones worth reading twice. `y` produces one outcome
carrying **both** `BufferSaved` and `EditorExited`, so `_echo_for` puts
`Wrote …` on the very frame the loop writes before returning — unlike slice
17's `Quit`, which the exit frame cleared. That is the on-screen evidence the
end-to-end test asserts.

And the pathless case, which is what the user chose Option B at the gate for
(§8) — the startup buffer, where a new user's first typing lands:

```text
type "hello"     → scratch shows "hello", modeline "**"
press C-x , C-c  → echo row shows "Modified buffers exist; exit anyway? (y or n) "
press n          → prompt gone, editor STILL RUNNING, "hello" intact, modeline "**"
press C-x , C-c  → the same prompt again
press y          → the process exits, status 0; "hello" is gone, deliberately
```

Two more, because they are the cases a prompt is *for*:

```text
(two modified file buffers, a.txt then b.txt)
press C-x , C-c  → "Save file /tmp/a.txt? (y or n) "
press n          → "Save file /tmp/b.txt? (y or n) "      <- sequence advances
press y          → b.txt written; a.txt still modified, so:
                 → "Modified buffers exist; exit anyway? (y or n) "
press C-g        → every prompt gone, editor running, b.txt stays written

(one modified file buffer, on a read-only file)
press C-x , C-c  → "Save file /tmp/ro.txt? (y or n) "
press y          → the write fails; the buffer is still modified, so:
                 → "Modified buffers exist; exit anyway? (y or n) "
```

## 2. What exists today

- `src/drei/commands.py:91` — `ExitEditor` exists (slice 17) and carries
  TD-11's `TODO: [tech-debt]` marker. `EditorExited` at `:464`.
- `src/drei/session.py:778` — the whole exit arm is two lines: append
  `EditorExited()`, change nothing. This is the site of the slice.
- `src/drei/terminal.py:378` — the loop keys off `EditorExited` in the
  outcome. It fires for **every** key, including minibuffer keys (`send`
  returns the minibuffer outcome too), so a `y` that resolves to
  `EditorExited` from inside a prompt already ends the run. **No change to
  `terminal.py` is expected in this slice.**
- `src/drei/session.py:1000-1096` — B.8's choice minibuffer: `_open_choice` /
  `_close_choice`, `_choice: PermissionRequested | None`, and a key→decision
  map. It is *permission*-shaped: `_choice` holds a `PermissionRequested` and
  every helper reads that request's `options`.
- `src/drei/session.py:799-916` — the three minibuffer arms (`MinibufferInput`,
  `MinibufferAbort`, `MinibufferAccept`) each branch `_choice is not None`
  first, then fall through to text mode. Text mode already keys its *accept*
  behavior off `_minibuffer_kind` (`"find-file"`, `"switch-buffer"`,
  `"agent-prompt"`), which is the extension point D4 uses.
- `src/drei/session.py:1559` — `_save` is **current-buffer-bound**: it reads
  `self._state.eol` and names `self._current_id` in the `no-file` event. D6
  addresses that.
- `src/drei/harness.py:136` — `_minibuffer_command` already routes a printable
  key to `MinibufferInput(key)` and `C-g` to `MinibufferAbort()`, so `y`, `n`
  and `C-g` reach the session with no harness change.
- `src/drei/render.py:81` — while the minibuffer is active it owns the echo
  row and the cursor. A prompt is visible for free; an echo message set by the
  same command is covered until the prompt closes.
- `tests/test_terminal.py:119` — `test_c_x_c_c_discards_a_modified_buffer_without_asking`,
  written by slice 17 *so that this slice would have something to turn red* on
  Windows. It is the pin, and it becomes the acceptance test.

Two facts that shape the design rather than the delta:

- Drei has no `write-file`, so a modified buffer with no `file_path` **cannot
  be saved at all** — `C-x C-s` there emits `SaveFailed(name, "no-file")`
  (registry row). Offering to save one would be offering something the editor
  cannot do.
- Agent buffers are generated and never carry `modified` (an agent append
  leaves the flag alone by design 0004), so agent output never triggers a
  prompt. Pinned in §7 V4 rather than assumed.

## 3. Design decisions

### D1. The session decides whether the run may end

`ExitEditor` stops being unconditional: the arm computes what is at risk and
either emits `EditorExited()` (nothing at risk — today's behavior) or opens
the first prompt and emits only `MinibufferOpened`. The loop and the harness
are untouched.

Alternatives:

- **The loop asks.** It would have to reach into buffer state and drive a
  prompt sequence, which is editor semantics in an adapter. Rejected on the
  architecture rule, and it would be untestable without a terminal.
- **The harness asks.** It "contains no edit, movement, or render logic of its
  own" (its own docstring). Rejected for the same reason.

**On screen:** `C-x C-c` with a modified buffer no longer ends the run — it
puts a question on the echo row.

### D2. Two stages: per-buffer offers, then one gate

Stage 1 offers to save each **file-visiting** modified buffer, one prompt at a
time, in buffer-creation order. Stage 2 — reached however stage 1 ended —
asks once more if **any** buffer is still modified, pathless ones included,
and only a `y` there ends the run.

This is the user's answer at the gate (§8): the alternatives were

- **Emacs parity (file-visiting buffers only, in both stages).** Emacs's
  `save-buffers-kill-emacs` checks `(and (buffer-file-name) (buffer-modified-p))`,
  so a modified `*scratch*` exits silently. Cheaper — the sweep in §5 would be
  a single test — but it leaves the *most reachable* data-loss path open: the
  startup buffer is where a new user's first keystrokes land, and Drei, unlike
  Emacs, has no `write-file` to rescue that text. Rejected by the user;
  recorded because it is the parity-shaped option.
- **No stage 2 at all** — `n` means "discard it, I said so". Rejected: a save
  that *fails* (read-only file, full disk) then loses the buffer with no
  second word, and one habitual `n` is one key from data loss.

**On screen:** the prompts above. A modified pathless buffer now blocks the
exit until confirmed, where today it dies with the process.

### D3. Stage 2's question is recomputed from buffer state, not tallied

The gate condition is "does any buffer currently report `modified`", asked
after stage 1 finishes — not "did the user answer `n` anywhere". A `y` whose
write failed leaves the buffer modified and therefore still blocks; a `y` that
succeeded stops blocking. Emacs re-checks `buffer-modified-p` the same way, and
a tally would have to model failure separately to get the read-only case right.

**On screen:** `y` on a file Drei cannot write leads to
`Modified buffers exist; exit anyway?` rather than to a silent exit.

### D4. A third minibuffer mode, keyed off `_minibuffer_kind` — not a generalized `_choice`

Two new `_minibuffer_kind` values, `"save-buffer"` and `"exit-anyway"`, plus
one new field: `_exit_pending: list[BufferId]`, the file-visiting modified
buffers still to ask about. `_choice` stays `None` throughout, so B.8's
permission path is not touched. The three minibuffer arms gain one branch each,
before the text-mode fall-through.

Alternative: **generalize `_choice` into a union of choice kinds** (permission
| save-buffer | exit-anyway) with a shared key→answer table. Rejected: it is a
refactor of the fail-closed approval path (review 0001 finding 9 lives there,
and `_choice_accept_decision`'s "never auto-approve an invented allow kind" is
pinned by a family of tests) bought with no behavior. The kind-based
dispatch is the extension point the text prompts already use.

**On screen:** nothing. This is the shape decision, and it is the reason the
diff is small enough to review.

### D5. `y`, `n`, `C-g` — and every other key is ignored

`y` saves (stage 1) or exits (stage 2); `n` skips or refuses; `C-g` abandons
the whole exit at any prompt, leaving already-saved buffers saved. `RET`,
`DEL`, and every other key are silent no-ops — the prompt stays up.

Alternatives: **`save-some-buffers`' full answer set** (`!` save all
remaining, `q` stop asking, `.` save just this one and exit, `C-r` view the
buffer) — deferred (§4); each is convenience on a list that is one or two
entries long in practice, and `C-r` needs a recursive view Drei has no
machinery for. **`RET` defaults to `y`** — rejected: the same reasoning as
B.8's finding 9, a habitual `RET` must not resolve a question about losing
work.

**On screen:** a mistyped key at an exit prompt does nothing at all, which is
where Emacs echoes `Please answer y or n` (deviation row — Drei has no message
mechanism, TD-4).

### D6. `_save` becomes buffer-addressed

Stage 1 saves buffers the user is not looking at, so `_save(current, events)`
becomes `_save(value, buffer_id, events)`: the line ending comes from
`self._states[buffer_id].eol`, the `no-file` event names that buffer, and the
new value is written into that buffer. The `SaveBuffer` arm passes
`self._current_id` and is otherwise unchanged.

The write-back needs care, and it is the one place a reviewer should look
twice: `dispatch` writes `new_value` into `commit_id` at the end, and for a
`MinibufferInput` that is the *focused* buffer. So the stage-1 helper replaces
the target buffer's value directly and the arm then sets
`new_value = self.buffer.current` — re-read, the way the `find-file` arm
already does — so a save of the focused buffer is not overwritten by a stale
`current` captured before the arm ran.

Alternative: **pin the target through `_pinned_target`.** Rejected: the target
depends on session state rather than on the command's fields, which is exactly
what that hook does not do.

**On screen:** the modeline of the saved buffer flips `**` → `--` even when it
is not the focused pane.

### D7. An abandoned exit drains the permission queue; a completed one does not

A `session/request_permission` arriving while an exit prompt is open is
delivery-class, so it queues (the existing `PromptPermission` arm — the gate
exempts it and `_minibuffer is not None` is true). If the exit is abandoned
(`n` at stage 2, or `C-g`), the queue must be drained and the request
presented, or the agent hangs for the rest of the run. If the exit proceeds,
nothing is presented: the process is ending and `pump.close()` terminates the
child.

So the exit prompts get their own close helper rather than reusing
`_close_choice` — which always drains — and the drain happens on exactly the
abandonment paths. `AbortPendingPermissions` arriving mid-sequence needs no
change: it clears the queue and closes a *choice* prompt, and an exit prompt is
not one, so the sequence survives a cancelled turn. Both are pinned in §7 V5.

**On screen:** after `C-g` abandons an exit, a permission prompt that arrived
during it appears instead of the run dying with an unanswered request.

## 4. What this slice does NOT do

- **`write-file`.** A pathless buffer still cannot be saved, so it is never
  *offered* — only counted by stage 2. The existing `no-file` registry row
  stands, and the path prompt is its own slice (it needs a text minibuffer
  that produces a `file_path`, which nothing does yet).
- **`save-some-buffers`' `!` / `q` / `.` / `C-r`, and `C-x s` itself.** D5.
  Emacs's own answer set is reachable from `C-x s` too, and neither exists
  here; a later slice can add both together.
- **Turn cancellation.** TD-2 / design 0005 D5, still unclaimed. `C-g` at an
  exit prompt abandons the exit and does not cancel a turn in flight; the
  reverse interaction (a `C-g` that has to choose between the two) only exists
  once cancellation is wired, and that slice owns the choice.
- **An echo/message mechanism.** TD-4 stands: a key the exit prompt does not
  recognize is silent, and `Modified buffers exist` is a *prompt*, not a
  message.
- **Confirming an exit for anything but modified buffers.** Emacs's
  `confirm-kill-emacs` and its running-process check are out of scope; the ACP
  child is terminated by `pump.close()` as it is today.

## 5. Pins that change

Measured by inspection of every exit site, not estimated (slice 17's plan was
wrong by a factor of two in the other direction). A test is affected only if a
buffer reports `modified` at the moment it exits.

| Where | Sites | What changes |
| --- | --- | --- |
| `tests/test_terminal.py` — 21 `"\x18", "\x03"` exit scripts | **11 affected** (lines 134, 145, 204, 226, 379, 394, 442, 847, 869, 873, 1031) | one more key: `"y"` after the exit pair |
| `tests/test_terminal.py` — the other 10 exits | 0 | clean at exit and must stay that way: no edit (85, 172, 1053), a no-op key (187 `M-y` on an empty ring, 336/346 arrows, 363/410 `C-g`), a file saved before exiting (306), or an **agent** append, which never sets `modified` (995) |
| `tests/termverify/test_shipped_terminal.py` | **5 affected** (`terminal`, `resize`, `kill_yank`, `yank_pop`, `find_file_abort`) | one more `KeyInput(("y",))`-shaped epoch; the `C-c` dispatch becomes an `EpochCompleted` and the `y` becomes the `TerminalResult` |
| `tests/termverify/…` — the other scenarios | 0 | `save` and `find_file` save before exiting; `undo` undoes back to the empty saved text, so `_modified_after_undo` reports **clean** (non-obvious, and worth an inline note); `navigation` is inert; `stop_is_clean` uses `Stop` |

Three specific hazards, each an assertion that would otherwise pass while
asserting less:

1. `test_every_key_outcome_is_offered_to_the_pump` (`test_pump.py`) asserts
   `after_command` ran exactly **2** times for `keys("a", C-x, C-c)`. It
   becomes **3**. This is slice 17's "exiting costs one marker more than
   quitting did" repeating in a different currency; it is listed here so it is
   a decision rather than a surprise.
2. The readiness-marker and `_CLEAR_SCREEN` counts at `test_terminal.py:880`
   and `:1022` are *relative* (script vs. baseline script). They survive
   **only if both** scripts gain the extra key — a one-sided edit shifts the
   difference and the test fails, which is the right failure.
3. `test_c_x_c_c_discards_a_modified_buffer_without_asking` is renamed and
   inverted (`…_offers_to_save_a_modified_buffer`): same fixture, `y` added,
   and the assertion becomes `files["/tmp/notes.txt"] == "xsaved"` plus
   `"Save file" in written`. The registry row citing it changes with it.

**The sweep is self-checking.** `scripted()` closes the queue behind its
events, so an affected test that does not gain its `y` raises `EndOfInput`
instead of passing; a ConPTY scenario that does not gain its epoch gets an
`EpochCompleted` where it asserted `TerminalResult`. There is no silent
"still green, no longer asserting" mode.

## 6. Owned deviations (parity-registry rows)

1. **`C-x C-c` offers to save each modified buffer** — the existing "exits
   without offering to save" row is **rewritten, not deleted**: what remains
   is the smaller set below. TD-11 is removed from `technical-debt.md`.
2. **The answer set is `y` / `n` / `C-g`.** Emacs offers
   `y, n, !, ., q, C-r, C-f, C-h`. Deviation, deferred by D5/§4.
3. **The final gate is a single-key `y`/`n`.** Emacs uses `yes-or-no-p` —
   typing `yes` and `RET` — precisely because it is the last guard before data
   loss. Deviation: Drei has no text-confirm mode, and adding a fourth
   minibuffer mode for one question is worse than the friction it buys.
4. **A pathless modified buffer blocks the exit but is never offered.** Emacs's
   kill-emacs condition counts only file-visiting buffers, so a modified
   `*scratch*` exits silently there. Deviation, deliberate (D2), and it links
   the existing `no-file` row: Drei cannot save such a buffer at all, so
   counting it is the only protection available.
5. **Buffers are offered in creation order.** Emacs walks `buffer-list`, which
   is recency-ordered. Deviation: deterministic and already the order
   `EditorSession.buffers` exposes; recency would make the prompt sequence
   depend on focus history.
6. **An unrecognized key at an exit prompt is silent.** Emacs echoes `Please
   answer y or n`. Deviation, TD-4, same reasoning as the other silent-error
   rows.
7. **A permission request queued behind an exit prompt is presented only if
   the exit is abandoned** (D7). No Emacs equivalent; recorded next to the
   turn-cancellation row for the same reason — a request left pending hangs
   the agent, and one presented after the process decided to die asks about
   work that is about to stop existing.

## 7. Implementation order (vertical slices, strict TDD)

1. **V1 — the user-visible thing, end to end, one buffer.** Rewrite the §5.3
   pin first and watch it fail: `C-x C-c` on a modified file buffer shows
   `Save file …?`, `y` writes the file and exits with `Wrote …` on the final
   frame. Needs D1, D6, the stage-1 prompt, and the direct-exit path when
   nothing is modified (which is every other exit test staying green). Stage 2
   is not built yet — after the last offer, exit.
2. **V2 — stage 2, and the pathless case.** `n` leads to
   `Modified buffers exist; exit anyway?`; `y` exits, `n` leaves the editor
   running with the buffer intact; a modified pathless buffer goes straight
   there. D3's read-only arm (`y` → `SaveFailed` → still blocked) belongs
   here, driven by a `FakeFilePort` that raises.
3. **V3 — `C-g` at both stages**, including "already-saved buffers stay
   saved", and the multi-buffer sequence in creation order.
4. **V4 — the sweep**, plus the two facts §2 asserts rather than assumes: an
   agent buffer's appends never trigger a prompt, and a clean buffer exits in
   one keystroke pair. The termverify epochs last, since they are the slow
   Windows-only leg.
5. **V5 — the ACP interactions** (D7): a permission request queued behind an
   exit prompt is presented after `C-g`, dropped on a completed exit, and
   `AbortPendingPermissions` mid-sequence leaves the exit prompt standing.
6. **V6 — records.** Registry rows 1-7, TD-11 removed, the `ExitEditor` and
   `KeyboardQuit` docstrings' TD-11 references, README's "**`C-x C-c` does not
   offer to save modified buffers yet**" sentence, and
   `docs/knowledge/architecture.md` if it names the exit path.
7. **V7 — adversarial review → fix → code PR (`Closes #48`) → merge.**

## 8. Risks / open questions

- **~~Which buffers does the prompt protect?~~ Resolved at the gate by the
  user (2026-07-26): Option B — file-visiting buffers are offered, and *any*
  still-modified buffer, pathless ones included, must be confirmed.** The
  cost the user accepted is the 16-site sweep in §5 rather than the 1-site
  sweep Emacs parity would have cost. Recorded here rather than struck out
  because the alternative is the shape of the *next* argument about this code.
- **Coverage floor is 100%, and this adds branches with no obvious caller.**
  Every arm above needs a test that fails without it — in particular "stage 1
  ends and nothing is modified" versus "…and something is", which are two
  paths through one predicate. If a branch turns out to be unreachable, delete
  it rather than pragma it.
- **The exit prompt is a new way for the minibuffer to be open, and three
  arms fall through to text mode.** A missed branch would treat `y` as text
  input into a prompt whose kind is `"save-buffer"` and silently append it to
  `_minibuffer` — visible as `Save file …? y` on the echo row rather than as a
  crash. V1's first red test should be read for exactly that.
- **Does anything else observe `EditorExited` or assume `C-x C-c` is
  terminal?** `terminal.py:378` is the only consumer I found, and the
  `exiting` flag also suppresses the final readiness marker — so a `y` that
  exits must produce the marker-free frame, and the `n` that does not exit must
  produce a marked one. V2 should confirm by marker count, not by inspection.
- **`_save`'s signature change touches a heavily pinned path.** No behavior
  change is intended for `C-x C-s`; the existing save tests (including the
  CRLF round-trip and the `no-file` naming) are the regression net, and they
  should be run before anything else in V1.

## 9. Acceptance criteria

- `C-x C-c` with a modified file-visiting buffer prompts per buffer; `y`
  writes through the `FilePort` and the run ends with `Wrote <path>` on the
  final frame; `n` leaves the file untouched.
- No unsaved buffer — file-visiting or not — is discarded without either a `y`
  at its own offer or a `y` at `Modified buffers exist; exit anyway?`.
- `C-g` at any exit prompt leaves the editor running with every buffer intact,
  and buffers already saved during that sequence stay saved.
- A save that fails leaves the buffer modified and the exit blocked behind the
  stage-2 gate.
- `C-x C-c` with nothing modified still exits in exactly two keystrokes, and
  the exit frame still carries no readiness marker.
- Proven through the shipped executable on a real ConPTY, not only in-process:
  at least one scenario exits through the prompt.
- **TD-11 is removed** from `docs/technical-debt.md`, its code markers are
  gone, and the parity registry describes the deviations that remain (§6).
- Full gate green on 3.12-3.14 and both CI OSes; coverage floor held at 100%.
