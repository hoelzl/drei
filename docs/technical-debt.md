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
that review's. TD-10 and TD-11 name the slice that found them. TD-12 through
TD-17 were found by [adversarial review
0002](agent/reviews/0002-adversarial-review-2026-07-27.md) (2026-07-27) and
deferred in that review's triage; their finding numbers are that review's.

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

---

## TD-12 (review 0002 finding 5) — the startup path still mints the unreachable `""` buffer

**Location:** `src/drei/cli.py` — the `FileNotFoundError` arm of the startup
read.
**Severity:** minor; the buffer TD-3 named verbatim, reachable only from the
command line.

TD-3 was paid at the find-file boundary (slice 20): an empty basename is
refused with `OpenFailed(path, "empty-basename")` before the filesystem is
asked. The *startup* boundary never got the refusal: `drei missing-dir/`
raises `FileNotFoundError` out of the port read, lands in the missing-file
arm, and `harness.py`'s basename derivation mints a buffer literally named
`""` — unaddressable by any typed `C-x b` name, edits stranded, exactly the
hazard TD-3 recorded. Review 0002 caught the paid note overclaiming the class.

**Why deferred:** the fix is small (apply the empty-basename check before the
startup read, or route startup through the session's visit path — TD-9's
fix), but TD-9 already owns "startup bypasses the command boundary", and
doing the refusal twice invites divergence; the two should be paid together.
**Suggested approach:** fold into the TD-9 fix; refuse with the same
`empty-basename` vocabulary (exit 2 with the token on stderr — the startup
path has no echo row).

## TD-18 (slice-21 review) — a resize during an open prompt is swallowed, and the harness doesn't notice

**Location:** `src/drei/harness.py` `resize` (docstring carries the TODO
marker); the swallowing gate at `src/drei/session.py` (the minibuffer gate —
`ResizeFrame` is not on the exempt list).
**Severity:** minor; transient presentation divergence, self-healing on the
next resize or prompt close.

`harness.resize`'s docstring promises "the minibuffer gate never sees it" —
routing around the *harness's own* key gate. But the session-level gate still
swallows the `ResizeFrame` dispatch while any prompt is open (the command
emits nothing; slice-21 review probed: gate swallowed, session frame size
unchanged). Worse, `resize` then assigns the new `self._width`/`self._height`
unconditionally, so the harness renders against a size the session does not
hold until the prompt closes — exactly the stale-frame hazard the docstring
says the routing avoids, plus a harness/session disagreement. Slice 15/16
vintage; slice 21's classification makes the *bookkeeping* side safe either
way (a swallowed or landed resize intervenes in nothing), but the frame
divergence is real.

**Why deferred:** pre-existing and out of the slice-21 diff; the fix touches
the gate's exempt list, which the prompt-gating invariants pin heavily, so it
deserves its own slice-sized look rather than a review-week drive-by.
**Suggested approach:** exempt `ResizeFrame` from the session gate (it acts on
frame state, not buffer state, and post-slice-21 its events intervene in
nothing), or have `resize` read the applied size back from the session instead
of assuming. The first matches the docstring's intent.

## TD-14 (review 0002 finding 7) — the initial frame size is not in the event record

**Location:** `src/drei/session.py` — the `frame_size` constructor argument.
**Severity:** minor; a replay gap, pre-dating the range (plan 0012) but
sharpened by two in-range consumers.

`ResizeFrame`'s own docstring states the derivability rule: a transcript that
omitted a resize could not reproduce a later split-or-no-op decision on
replay. The *initial* size is exactly such an input — consumed by the `C-x 2`
gate and by `DisplayBuffer`'s split — and it enters the state at construction
with no event. A replay from the event record starts not knowing the frame
geometry.

**Why deferred:** the fix shape (a `FrameSized` initial event, or making the
initial size part of the session's genesis record) touches the replay
contract and deserves the verification-model treatment, not a drive-by.
**Suggested approach:** record the initial geometry as the transcript's first
event; pin a replay-reproduces-split-decision property.

## TD-15 (review 0002 finding 16) — parked codec frames die with the child, and effects render out of order

**Location:** `src/drei/pump.py` `_drain`; `src/drei/acp/codec.py`.
**Severity:** nit-to-minor; requires a malformed line from the peer in the
same chunk as valid frames.

Frames parsed before a malformed line in one chunk are parked until the
*next* `receive` — so a protocol error can render in the transcript *before*
the turn completion that preceded it — and `exited()` → `_reset()` discards
parked frames, so a turn completion in the child's final output is never
recorded. The codec's no-loss docstring is true only across calls, not across
child death.

**Why deferred:** only reachable with a misbehaving peer, and the wedge class
(cluster C) is the same area of the machine; one slice should take both.
**Suggested approach:** flush parked frames through the fold before reporting
the decode error, and drain the park on `exited()` before `_reset()`.

## TD-16 (review 0002 finding 17) — out-of-turn permission requests are accepted

**Location:** `src/drei/acp/machine.py` — the `session/request_permission`
phase gate.
**Severity:** nit; fail-closed and survivable, flagged for a decision.

The gate admits a permission request in `SESSION_ACTIVE`, so a request
arriving *after* its turn completed opens a live choice prompt for a dead
turn — and resolves normally. ACP ties permission requests to a turn ("during
a turn"); Drei's docstring says "only meaningful inside a live session",
which is broader. Neither answer is obviously wrong (refusing risks hanging
an agent that legitimately asks late), so the gate needs a decision and a
pin, not a drive-by tightening.

**Why deferred:** a protocol-semantics decision for the cluster-C slice.
**Suggested approach:** decide with the liveness work; if refused, the
machine answers `cancelled` so the agent never hangs.

## TD-17 (review 0002 finding 12) — registry row numbers are unstable citations

**Location:** `docs/knowledge/emacs-parity.md` (the rows); every "row N"
citation in prose.
**Severity:** nit; process debt, no behavior.

Rows have no stable IDs, so every inserted row renumbers its downstream
neighbours and silently rots every positional citation — slice 20's two
inserts rotted four. This sweep converted the live citations (in
`tests/test_harness.py`, `tests/test_exit_prompt.py`) to title-based
references, but nothing stops the next "row N" from appearing.

**Why deferred:** the durable fix — stable row anchors the machine check can
resolve, or a lint rule banning positional citations — is tooling work, not
a docs edit.
**Suggested approach:** give each row an explicit anchor (`<a id="row-quit">`
or a `#`-slug convention) and extend `tests/test_parity_registry.py` to
resolve title/anchor citations; until then, cite rows by title.

## Paid and removed

*TD-13 (`DisplayBuffer`'s "silent no-op" recorded
`Message("too-small-for-splitting")` into the transcript although the user
issued no command) was paid by slice 21 (issue #63, plan
`agent/plans/0021-last-command-bookkeeping.md` D3), exactly as its entry
suggested: `_split_window` gained `speak: bool`, and `_display_buffer` —
pump-dispatched on the peer's schedule — passes `False`. The user's own
`C-x 2` refusal still says `too-small-for-splitting`
(`test_windows.py::test_split_too_small_is_a_noop`, unchanged).*

*TD-2 (the pump calls nothing on a `KeyboardQuitEvent` — turn cancellation
wired to nothing) was paid by slice 20 (issue #56, plan
`agent/plans/0020-turn-cancellation-and-trailing-slash.md`), the last of
design 0005's seven decisions: the pump reads `KeyboardQuitEvent` out of the
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
