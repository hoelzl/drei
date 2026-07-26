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

## Paid and removed

*TD-2 (the pump calls nothing on a `KeyboardQuitEvent` — turn cancellation
wired to nothing) was paid by slice 20 (issue #56, plan
`agent/plans/0020-turn-cancellation-and-trailing-slash.md`), the last of
design 0005's five items: the pump reads `KeyboardQuitEvent` out of the
command outcome and, with the phase at `PROMPT_IN_FLIGHT`, calls
`AcpMachine.cancel()` and applies `AbortPendingPermissions`, in 0005 D5's
order. The ambiguity the entry carried is settled by composition rather than
new rules: at a permission prompt `C-g` is the shipped deny, so the turn is
the second `C-g`; an exit prompt peels first; one `C-g`, one layer. The
entry's earlier payments (slices 15–16) are named in its removed text.*

*TD-3 (trailing-slash find-file creates an unreachable `""` buffer) was paid
by slice 20 (same plan) on the entry's own suggested approach: an empty
basename is refused at the find-file boundary with
`OpenFailed(path, "empty-basename")` before the filesystem is asked —
deterministic on every platform, in the same deviation family as the
directory-path row of `knowledge/emacs-parity.md`.*

*TD-11 (`C-x C-c` discards unsaved work with no prompt) was paid by slice 18
(issue #48, plan `agent/plans/0018-save-buffers-on-exit.md`) and removed
rather than narrowed again — the entry's own instruction. What `C-x C-c` does
now, and the seven differences from `save-buffers-kill-terminal` that remain,
are recorded in `knowledge/emacs-parity.md`.*

*TD-4 (`OpenFailed` and every non-save failure is invisible) was paid by
slice 19 (issue #52, plan `agent/plans/0019-echo-message-mechanism.md`) on
the entry's own terms — not for the mechanism alone, but with every
registry row that deferred to it rewritten in the same slice: the eight
"silent no-op / no echo" rows now record what Drei says, with citations, in
`knowledge/emacs-parity.md`.*

*TD-5 and TD-6 were paid by slice 17 and TD-1 by an earlier slice; they are
listed nowhere else, which is the point — an entry is removed when the debt is
paid, and git history is where a removed entry lives.*
