# Technical debt

Known defects and shortcuts that were found, understood, and **deliberately
not fixed yet**. Each entry names where the problem lives, why it was
deferred, and what fixing it would take — so the next agent inherits a
decision, not a surprise.

Rules for this file:

- An entry is added only with an owner decision to defer. "I did not get to
  it" is not a deferral; finish it or leave the code untouched.
- Every entry has a `TODO: [tech-debt]` comment at the code location naming
  its entry, so a reader of the code finds the record.
- Entries are removed when the debt is paid, not when it is re-scoped. If the
  scope changes, edit the entry and say so.
- This file is not a backlog. Work that is merely *not built yet* belongs in a
  plan or a design record; this is for behavior that is wrong, misleading, or
  unowned in code that already ships.

TD-2 through TD-9 were found by [adversarial review
0001](agent/reviews/0001-adversarial-review-2026-07-24.md) (2026-07-24) and
deferred in that review's triage on the same date; their finding numbers are
that review's. Later entries name the slice that found them instead.

---

## TD-2 (finding 11) — turn cancellation is wired to nothing

**Deferred to:** the cancellation slice. Design record:
`agent/design/0005-acp-pump.md` D5.
**Location:** `src/drei/acp/machine.py` (`cancel`), `src/drei/session.py`
(`AbortPendingPermissions`), `src/drei/keys.py` (`C-g` is bound to
`KeyboardQuit`, which exits the editor).
**Severity:** medium — a turn in flight cannot be stopped except by quitting.

**Most of this entry is paid.** Plan 0016 shipped the pump: a
`StreamingProcessPort` holds a long-lived child, agent bytes arrive as events
on the loop's ordered input stream, `C-c a` sends a prompt, the streamed
answer folds into a displayed agent buffer, and a permission request is both
presented *and answered* — the response that "nobody sends" now goes on the
wire. Plan 0015 had already paid the injection point and single-dispatch
delivery. What is left is the fifth item design 0005 lists.

`AcpMachine.cancel()` answers every pending `session/request_permission` with
the `cancelled` outcome, and the session's `AbortPendingPermissions` closes an
open choice prompt and drains the queue. The pump calls the second (on child
exit) but never the first, and nothing at all triggers a turn cancel.

**Why deferred:** the trigger is a keymap decision, not a wiring one. 0005 D5
wants `C-g` *while a turn is in flight* to cancel the turn — but `C-g`
currently **exits the editor**, a slice-1 shortcut Emacs does not share
(`C-g` is `keyboard-quit`; `C-x C-c` exits). Overloading an exit key with turn
cancellation by accident is the bad end state 0005 names, and fixing the
binding falsifies a parity registry row. That is its own change.

**Suggested approach:** decide the `C-g`/`C-x C-c` binding first, with its
registry row; then have the pump call `cancel()` and dispatch
`AbortPendingPermissions`, in that order — answer the agent, which is blocked,
before clearing the UI.

## TD-3 (finding 18) — trailing-slash find-file creates an unreachable `""` buffer

**Location:** `src/drei/session.py` — `_visit`'s basename derivation
(`path.rsplit("/", 1)[-1]`), and the `switch-buffer` MRU default which treats
`""` as absent.
**Severity:** low frequency, bad outcome — unsaved edits become unreachable.

`C-x C-f notes/ RET` yields a buffer named `""`. Text typed into it is real
and editable, but after switching away nothing addresses it: `C-x b` with
empty input takes the MRU default, and no typed name matches `""`. With no
kill-buffer command there is also no way to discard it.

**Why deferred:** scope control — it sits at the intersection of path
handling and buffer naming, both of which have their own deferred work
(`file-truename` canonicalization, uniquify-style names, kill-buffer).

**Suggested approach:** reject a path whose basename is empty at the
find-file boundary (an `OpenFailed`-class outcome, consistent with the
directory-path arm), rather than special-casing `""` downstream.

## TD-4 (finding 19) — `OpenFailed` and every non-save failure is invisible

**Location:** `src/drei/harness.py` — `_echo_for`.
**Severity:** medium — a failure is indistinguishable from success.

The echo row renders only `KeyboardQuitEvent`, `BufferSaved`, and
`SaveFailed`. A `C-x C-f` that fails on a permission error closes the
minibuffer with a blank echo row, which looks exactly like a successful
no-op. The parity registry covers only the missing-file arm, where an empty
buffer is the correct outcome.

**Why deferred:** it is the first case of a general gap — Drei has no
error/message mechanism at all, which is also the recorded rationale for a
half-dozen registry deviations ("silent no-op where Emacs signals an error").
Fixing it one event at a time entrenches the ad-hoc shape.

**Suggested approach:** a small echo-message slice: a `Message`/`Error`
event class the session emits, one rendering site, and a pass over the
registry rows that currently say "Drei has no echo-error mechanism yet".

## TD-5 (finding 20) — `C-g` after a `C-x` prefix is swallowed

**Location:** `src/drei/keys.py` — `resolve`, the pending-prefix branch.
**Severity:** low — recoverable by pressing `C-g` again.

Any non-completing key after a prefix becomes one `UnresolvedKey("C-x C-g")`.
Emacs cancels the prefix *and* quits: the mark is deactivated and `Quit` is
echoed. In Drei nothing happens — the prefix is dropped silently and the mark
survives. Unregistered deviation.

**Why deferred:** minor, and the honest fix touches prefix semantics
generally (which keys abort a prefix, what the echo shows) rather than
special-casing `C-g`.

**Suggested approach:** handle `C-g` in the pending branch as
`KeyboardQuit()`, and register the resulting behavior. Then decide whether
any *other* non-completing key should echo something rather than vanish.

## TD-7 (finding 22) — frozen dataclasses over aliased mutable dicts

**Location:** `src/drei/acp/machine.py` — `in_flight_outgoing`,
`in_flight_incoming`, `request_params`; and the raw payload dicts carried by
`PermissionRequested.params`, `ToolCallStarted.update`, and siblings.
**Severity:** theoretical today; real if a consumer ever mutates a payload.

The machine is a frozen dataclass whose fields are plain dicts, and inbound
payload dicts are **aliased** across the machine, the session's `_choice` and
`_permission_queue`, and the transcript effects. Nothing copies or freezes
them. One consumer mutating a payload in place would retroactively rewrite
both the transcript oracle and the auto-approval identity key, since
`_permission_identity` canonicalizes the same dict at match time.

**Why deferred:** no consumer mutates today, and the discipline is uniform;
the cost of the fix (deep-freezing arbitrary JSON at the boundary) is real
and the benefit is currently zero.

**Suggested approach:** freeze at the parse boundary — convert inbound JSON
into an immutable mapping type once in the codec, so every downstream alias
is safe by construction, rather than copying at each hand-off.

## TD-8 (finding 23) — `_undo` and `_kill_line` mutate session state before validating the value

**Location:** `src/drei/session.py` — `_undo` (history pop / redo append
before the `replace(...)` that constructs the new `BufferValue`), `_kill_line`
(kill-ring mutation before construction).
**Severity:** currently unreachable; the ordering is the debt.

Both mutate session-owned state *before* the new `BufferValue` is
constructed. If `BufferValue.__post_init__` ever raised there, the undo
stacks or the kill ring would be mutated with no event recorded — live state
and transcript would silently disagree. Today the inputs cannot violate the
invariants, so the failure is unreachable; the comment claiming these paths
are "atomic by construction" overstates what the code guarantees.

**Why deferred:** no reachable failure, and the reordering is mechanical but
touches two hot paths that are heavily pinned.

**Suggested approach:** construct the new value first, then mutate — the same
shape the other dispatch arms already use. No behavior change, so the
existing pins are the regression test; then the comment becomes true.

## TD-9 (finding 29) — `drei FILE` bypasses the command boundary

**Location:** `src/drei/cli.py` — the `OSError` arm prints
`error.strerror` and exits 2.
**Severity:** low, but it is a rule violation, not just a wart.

Opening a file from the command line and opening the same file with
`C-x C-f` fail differently: the CLI prints a raw, locale-dependent
`strerror` and exits 2, while `C-x C-f` produces a normalized `OpenFailed`
token. `process.py` states the normalized-token rule explicitly; the CLI
predates its enforcement and is recorded nowhere.

**Why deferred:** hardening, and the tidy fix is to make startup go through
the same visit path the editor uses — which is a small refactor of
`run_editor`'s initial-buffer construction rather than a change to the error
arm.

**Suggested approach:** have the CLI hand the path to the session and let
`_visit` produce the outcome, so there is exactly one open path and one
vocabulary of failures. The CLI then reports the normalized token.

## TD-10 (plan 0015 V2) — the frame cap drops the echo row before a window pane

**Location:** `src/drei/render.py` — `rows = rows[:height]` at the end of
`render_session`.
**Severity:** low; reachable only at absurd frame heights, but newly
reachable *in production* rather than only from a hand-built observation.

`render_session` builds every pane, appends the shared echo row last, then
truncates the row list to the frame height. Truncation therefore cuts from
the bottom, and the echo row is the bottom. With two windows in a
two-row frame the result is two modelines and **no echo row**: the minibuffer
prompt is invisible while the minibuffer is open, and the cursor — placed at
`height - 1` for an open minibuffer — lands on a modeline instead of on the
prompt it is supposed to be editing.

Emacs prioritizes the other way round: the echo area is the last thing it
gives up, and windows are deleted to make room.

This was found while implementing plan 0015 D7. Before `ResizeFrame` existed,
the truncation branch was reachable only by constructing a `SessionObservation`
by hand, because the `C-x 2` gate prevented a real session from over-
subscribing its frame. A resize can now shrink a live split frame to any
height, so the path ships.

**Why deferred:** fixing it is a *rendering priority* decision (which row
wins when rows are scarce: echo, modeline, or body), not a resize decision.
It deserves its own reasoning and a parity row of its own, and slice 15's
subject is the input boundary. Pinning the wrong-but-current behavior is
deliberate: `test_shrink_below_the_split_minimum_degrades_in_stages` asserts
the echo row is the first casualty, so the fix will show up as a failing
test rather than as a silent change.

**Suggested approach:** reserve the echo row before distributing body rows —
compute pane heights against `height - 1` and let the *panes* absorb the
shortfall, dropping whole panes from the bottom while the echo row is
retained. Then decide, with a parity row, whether a frame too short for even
one pane keeps the echo row or renders empty.

## TD-11 (2026-07-26) — quitting discards unsaved work, on one keystroke, with no prompt

**Location:** `src/drei/keys.py` — `C-g` is bound to `KeyboardQuit`;
`src/drei/terminal.py` — the loop returns on `KeyboardQuitEvent`, ending the
run.
**Severity:** high — silent, unrecoverable data loss reachable by habit.

`C-g` exits the editor, and exiting drops every modified buffer without
asking. There is no confirmation, no prompt, and no way back.

This is worse than an ordinary missing feature, because of *which* key it is.
In GNU Emacs `C-g` is `keyboard-quit` — the key you press when you do not know
what is happening, and the one key guaranteed to destroy nothing. Exiting is
`C-x C-c`, which offers to save each modified buffer first. Drei inverted the
safest key in the reference editor into its most destructive one, so muscle
memory carried over from Emacs actively causes data loss. It shipped in slice
1 as a shortcut and went unregistered in the parity registry for sixteen
slices (now recorded — see `knowledge/emacs-parity.md`).

**Why deferred:** it is two behavior changes, not one, and they have different
risk profiles.

1. *The binding.* `C-g` becomes `keyboard-quit` (clears the mark, echoes
   `Quit`, changes nothing else) and `C-x C-c` exits. The loop needs its own
   exit event so `KeyboardQuitEvent` can go back to meaning "the user aborted
   something". Roughly 113 `C-g` references across 15 test files plus every
   TermVerify scenario end with "C-g quits" and have to be rewritten — wide,
   mechanical, and exactly the kind of sweep where an assertion quietly stops
   asserting what it used to.
2. *The prompt.* `C-x C-c` with modified buffers should offer to save them.

Doing both at once puts two behavior changes and a 15-file test sweep in one
slice. Doing (1) first is a large safety win on its own — quitting stops being
a single keystroke and becomes a sequence the user has to mean — but it does
**not** close this entry, and taking (1) as licence to close it is the failure
this entry exists to prevent.

**Suggested approach:** the choice minibuffer built for the approval bridge
(B.8) already presents options and maps a key to a decision, so the
save-buffers prompt is a use of existing machinery rather than new machinery.
Per-buffer `y`/`n`, plus an escape that quits without saving, matching
`save-buffers-kill-terminal`.

**This entry is edited, not removed, by the binding slice.** After (1) it
reads "`C-x C-c` discards unsaved work with no prompt"; it is removed when the
prompt ships.
