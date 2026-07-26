# Nineteenth slice: the echo-message mechanism (TD-4)

**Status:** implemented (issue #52; the code PR closes #52 and #51). The
sweep count §5 refused to estimate, measured at landing: **16 test files, 26
`events == ()` silence assertions** — 21 becoming explicit `Message(...)`
assertions — plus **two `while …dispatch(Undo()).events:` loops** that went
infinite the moment an exhausted undo spoke, and one property-invariant arm.
The fresh-agent adversarial review (verdict NOT CLEAN on arrival) found one
major and four smaller defects, all fixed in the same branch: three
property-tier folds still keyed on event truthiness / transcript adjacency —
the "fourth site" D2 exists for, and a latent hypothesis flake — registry
row 126's stale "silent no-op" wording, §9's answer-set overclaim (below), a
README overclaim scoped to printable keys, and an implicit `_note` invariant
made explicit.

What the plan got wrong or did not foresee:

- **Row 68 moved from V5 to V2.** §7 assigned yank-pop to V5, but the D2
  hazard test needed a second speaking no-op as its demonstrator, so row 68
  landed with the exclusion.
- **Emission before exclusion, deliberately.** §7 said "exclude Message from
  the three bookkeeping sites, then let the exhausted undo speak." V2 did it
  the other way round inside the step: the no-ops spoke first and the suite
  went RED in exactly the two predicted ways (descent flipped to redo, kill
  chain split) — the only reason the exclusion is known to be load-bearing
  rather than precautionary.
- **V5 found a fourth casualty class.** Beyond §5's `== ()` pins and loops,
  the refused-split *behavioral* tests asserted silence, and the resize
  transcript now records the Message.
- **D7 needed a frame re-render.** The unresolved-key branch set the echo,
  but frames were only rebuilt after dispatches.
- **§9 overclaimed the clipping guarantee.** "The prompt's question and
  answer set survive `_clip` at 40 columns" is unmeetable: the gate's parity
  string is 46 characters, so `(y or n)` clips at 40 even with no note. What
  the tests pin is the recorded D3 decision — the question is the half that
  must survive. The criterion is narrowed here to the question; shortening
  the prompt was rejected because the string is the parity text.
- **The citation audit found exactly one hollow citation** (row 92, fixed in
  V4) — at §8's file-it threshold, so none was filed. It also found the
  end-to-end gap V6 closed: `_echo_for`'s Message branch had no key-to-frame
  pin.
- **The sweep stopped one file early.** §8 warned that transcript-folding
  property tests must ignore messages; V2 migrated two folds and the review
  found three more in the same file. The warning was right; the counting
  was incomplete.
- **And one tier short.** The differential tier had a silence pin of its
  own (`test_undo_parity`'s exhausted-undo `== ()`), invisible locally
  because `DREI_PARITY=1` needs the pinned Emacs — CI's parity job caught
  it on the PR. The sweep count above is the unit tier; the honest total
  is 27 silence assertions across 17 files.

**Architecture gate:** none — no design record owns the echo area, and this
slice does not need one. It is `docs/technical-debt.md` **TD-4**, the parity
rows that defer to it, and issue **#51**. It touches design 0003 §B.8's choice
minibuffer and plan 0018's exit prompts only as *consumers*: both own the echo
row while they are open, which is what D3 is about.

**Goal:** after this slice the editor stops failing silently. A `C-x C-f` that
hits a permission error says so instead of closing the prompt on a blank row
that looks exactly like success; an exhausted `C-/`, a `C-k` at end of buffer,
a `C-w` with no mark, and a mistyped answer at an exit prompt all say what
happened. The session says it as a **normalized token** and one adapter site
turns tokens into text — the seam `SaveFailed` has used since review 0001
finding 26 — so this is one mechanism rather than a ninth `isinstance` branch.
TD-4 closes and #51 closes with it.

## 1. The acceptance scenario

TD-4's own example, because it is the one where silence is actively
misleading — the failure and the success render identically today:

```text
drei                  → scratch buffer, echo row blank
press C-x C-f         → echo row shows "Find file: "
type "/etc/shadow"    → echo row shows "Find file: /etc/shadow"
press RET             → the port raises PermissionError
                      → the prompt CLOSES, and the echo row of the next frame
                        shows "/etc/shadow: permission-denied"
                      → the buffer is untouched and the editor is running
press a               → echo row is blank again; "a" is in the buffer
```

Today the fourth step leaves the echo row **blank**. `OpenFailed` is emitted
and recorded in the transcript; nothing renders it. That is the whole of TD-4's
headline case, and it is one rendering site away.

The second scenario is the one that decides D3, because the echo row is already
occupied when the message is raised:

```text
(a modified file buffer, /tmp/notes.txt)
press C-x C-c         → echo row: "Save file /tmp/notes.txt? (y or n) "
press z               → echo row: "Save file /tmp/notes.txt? (y or n) [Please answer y or n]"
                      → the prompt is STILL UP and its answer set is still readable
press y               → the file is written, the run ends
```

And the third, which is why #51 is a *prerequisite* here rather than a rider:

```text
(a modified pathless buffer)
press C-x C-c         → "Modified buffers exist; exit anyway? (y or n) "
press n               → prompt closes, editor still running, echo row BLANK
                      → and the transcript carries ExitRefused, not MinibufferAborted
press C-x C-c         → the same prompt
press C-g             → prompt closes, editor still running,
                      → echo row shows "Quit"
```

The two closes must echo differently, and today they emit the *same event*.

## 2. What exists today

- `src/drei/harness.py:149` — `_echo_for`, and it is the whole mechanism: a
  three-branch `isinstance` chain over `KeyboardQuitEvent`, `BufferSaved`,
  `SaveFailed`, returning `""` for everything else. TD-4's `TODO:
  [tech-debt]` marker sits on it.
- `src/drei/harness.py:68,81` — the echo is recomputed from the outcome on
  every key, so a message lives exactly until the next command. `resize`
  (`:98`) and `apply` (`:118`) deliberately leave it alone: neither is a user
  action, so agent output arriving must not wipe an unread message.
- `src/drei/render.py:47,89` — the echo row is the last row, `_clip`ped to
  width. **While the minibuffer is open the prompt owns that row instead**
  (`:36,83`), so a message raised by a command that leaves a prompt open is
  drawn over before it can be read. Slice 18 hit this and solved it locally;
  see D3.
- `src/drei/session.py:1572,1575` — `OpenFailed(path, token)` is emitted
  already. Nothing renders it. This is the acceptance scenario, and V1 is a
  harness change with no session change at all.
- `src/drei/session.py:967-1011` — **the hazard.** Three bookkeeping sites key
  off `if events:` / `elif events:`: the kill-append chain, `yank_active`, and
  `undo_descending`. The rule is "only event-emitting commands intervene", and
  it is pinned by parity registry row 82 and by review 0001 finding 2 — the
  bug where a held `C-/` on an exhausted history oscillated the buffer
  forever. A silent no-op that starts emitting a message becomes an
  event-emitting command. D2.
- `src/drei/session.py:865,871,954,1131` — `MinibufferAborted()` has **four
  emitters and means at least three different things**: `C-g` at an exit
  prompt, `C-g` at a text prompt, a turn cancellation sweeping a choice
  prompt, and `n` at the exit gate (a deliberate answer, not an abort). D4.
- `src/drei/session.py:1055,1153` — plan 0018's `_advance_exit(events, note)`
  and `_save_buffer`'s return value: a failed save is carried to the user as a
  `[<path>: <token>]` **suffix on the next prompt**, because the prompt owns
  the echo row. This is a one-case message mechanism built three days ago
  under review pressure; D3 generalizes and retires it.
- `src/drei/keys.py:69` / `src/drei/harness.py:77` — an unbound key becomes an
  `UnresolvedKey` recorded on the harness. It never reaches the session, and
  nothing renders it. D7.
- `src/drei/process.py` states the normalized-token rule, and `SaveFailed`
  follows it: the session emits `permission-denied`, the harness formats
  `f"{event.path}: {event.error}"`. **The seam this slice needs already
  exists**; nothing about it is new work.

Registry rows whose recorded rationale is exactly "Drei has no echo-error
mechanism yet": **66** (empty kill at buffer end), **68** (yank-pop with no
active yank), **72** (region kill/copy with no mark), **80** (nothing to
undo), **92** (`C-g` echoes nothing), **98** (`C-x 2` too small), **130**
(unrecognized key at an exit prompt), **134** (unbound key after a prefix).

## 3. Design decisions

### D1. The session emits a token; one adapter site turns tokens into text

A `Message(token, subject)` event — `token` a Drei-owned identifier
(`end-of-buffer`, `no-further-undo`, `answer-y-or-n`, …), `subject` the
optional thing it is about (a path, a buffer name, a key name). The harness
owns the token→English table and the formatting, exactly as it already does
for `SaveFailed`.

Alternatives:

- **The session emits English.** Rejected: it couples editor semantics to
  locale and presentation, which `AGENTS.md` forbids ("keep editor semantics
  deterministic and independent of terminal…"), and it would make every
  message assertion in the suite a string-equality test against prose.
- **The harness derives messages from the existing events, adding branches.**
  Rejected — this is the ad-hoc shape TD-4 exists to prevent, and it cannot
  reach the cases that emit *no event at all* (every silent no-op), which is
  most of the registry rows.

**On screen:** nothing by itself; this is the shape decision, and it is why the
diff is one table plus one `isinstance` branch rather than nine.

### D2. A message is not an intervening event

The three bookkeeping sites at `session.py:967-1011` must exclude `Message`
when they ask "did this command intervene". A no-op that now speaks is still a
no-op.

Without this the slice silently breaks the kill-append chain, `yank_active`,
and undo descent — the last of which is review 0001 finding 2's exact bug.
Concretely: undo three times (descending), press `M-y` on an empty ring (a
no-op that now emits `Message("previous-command-not-a-yank")`), press `C-/` —
and the descent has been broken, so the undo *redoes*. Registry row 82 pins the
opposite.

Alternatives:

- **Carry the message outside the event stream** (a second return channel on
  `CommandOutcome`). Rejected: it puts messages outside the transcript, which
  is what #51 needs them inside of, and it adds a parallel path where the
  event stream already is the evidence.
- **Let messages intervene, and re-pin row 82.** Rejected: the rule is not
  arbitrary, it is what keeps the chains derivable from the transcript, and a
  message is precisely the kind of event that describes rather than acts.

**On screen:** an exhausted `C-/` says "No further undo information" *and* the
next `C-/` still undoes rather than redoing.

### D3. A message raised while a prompt is open rides the prompt

The minibuffer owns the echo row, so a message raised by a command that leaves
a prompt standing is invisible. It is appended to the prompt as
`<prompt> [<message>]`, and it is a **suffix** because the row is hard-clipped
(no wrap, no scroll) at a shipped width of 40 columns: one half is going to be
sacrificed and it must not be the question.

This is not a new idea — it is plan 0018's `_save_buffer` note, which was
built for exactly one case under review pressure and hard-codes its own
formatting at `session.py:1055`. That code is deleted here and re-expressed
through the general mechanism.

Alternative: **the message replaces the prompt for one frame**, as Emacs does
(message, then redraw the prompt). Rejected: Drei has no timer and no
redisplay loop — the frame is written once per input — so "for one frame"
would mean "until the next keystroke", i.e. the prompt would be *gone* while
the user is being asked to answer it.

**On screen:** `Save file /tmp/notes.txt? (y or n) [Please answer y or n]`,
and slice 18's `[…: permission-denied]` keeps working through a mechanism that
is no longer specific to it.

### D4. `n` at the exit gate is a refusal, not an abort

A new `ExitRefused` event replaces the `MinibufferAborted()` at
`session.py:1131`, and a `SaveDeclined(buffer_name)` is emitted where `n` at a
stage-1 offer currently emits nothing. This closes **#51**.

It is a prerequisite, not a nicety. Registry row 92 wants `C-g` at a prompt to
echo `Quit`; `C-g` and `n`-at-the-gate emit the *same event today*, so making
the abort speak would make a deliberate answer echo `Quit` as well — the user
did not abort anything, they answered a question. The two cannot be told apart
until they are different events.

Alternative: **give `MinibufferAborted` a `reason` field.** Reasonable, and
cheaper. Rejected on the same grounds slice 17 separated `KeyboardQuit` from
`ExitEditor`: one event that means four things is what let `C-g` mean "exit"
for sixteen slices. Four emitters is already too many.

**On screen:** `C-g` at any prompt echoes `Quit`; `n` at the exit gate echoes
nothing, which is correct — Emacs's `save-some-buffers` says nothing per
declined buffer either.

### D5. Rows convert only where the mechanism was the only blocker

Rows 66, 68, 72, 80, 92, 98, 130 and 134 become messages. Rows that stay
deviations for reasons of their own are stated as such and keep their
rationale: **row 69** (yank-pop on a one-entry ring — Emacs replaces the entry
with itself and sets `modified`, which would contradict the modified-flag
invariant; the no-op is deliberate and unrelated to messages), **row 96**
(backspace at an empty minibuffer, where Emacs is silent too — already
parity), and **row 91** (find-file with empty input, deferred on defaults and
completion, not on messages).

**On screen:** eight behaviours that were indistinguishable from success now
say what happened.

### D6. A message lives until the next command

Already the behaviour (`harness.py:68,81` recompute the echo per key), and it
is Emacs's: a message sits in the echo area until something displaces it.
Stated as a decision because it is load-bearing and invisible — slice 17's
`test_editor_esc_consumed_as_chord_start_then_keyboard_quit` asserts that
`Quit` is on the `C-g` frame and *not* on the frame after it, and that
assertion is the pin.

Alternative: **messages persist until explicitly cleared.** Rejected: it needs
a clearing rule Drei has no place for, and it would leave a stale error on
screen through an arbitrary number of later commands.

**On screen:** the message vanishes on the next keystroke, as it does in Emacs.

### D7. Unbound keys speak from the harness, not the session

`C-x z is undefined` and its siblings are composed where `UnresolvedKey` is
already recorded (`harness.py:77`). The session never sees an unbound key and
should not start to: there is no editor semantics in "that key does nothing",
and routing it through a command would mean inventing one whose only job is to
be refused.

Alternative: **a `ReportUnboundKey` command.** Rejected for the above, and
because the harness already owns the `_unresolved` list that this reads.

**On screen:** `C-x z` echoes `C-x z is undefined` where it echoed nothing.

## 4. What this slice does NOT do

- **A `*Messages*` buffer.** Emacs logs every message to one. Drei has no
  buffer-appending mechanism that is not the agent path, and a log buffer is
  its own slice with its own naming and read-only questions. The echo row is
  the whole surface here.
- **`minibuffer-message` / `sit-for` timing.** Emacs's "message, pause, restore
  the prompt" needs a timer and a redisplay loop; Drei writes one frame per
  input. D3 takes the static answer instead.
- **Errors as control flow.** Nothing starts raising, and no command changes
  what it *does* — only what it says. A silent no-op stays a no-op (D2).
- **Turn cancellation** (TD-2, unclaimed) and **`write-file`** for pathless
  buffers. Both would add message sites; neither is blocked on this slice and
  this slice is not blocked on them.
- **`save-some-buffers`' `!`/`q`/`.`/`C-r`** (plan 0018 §4, registry row 126).
  Adding `Please answer y or n` does not widen the answer set it names.

## 5. Pins that change

Every one of these is a test that currently asserts *silence* and must assert a
message instead. The suite is the regression net for the ones that must stay
silent.

| Where | What changes |
| --- | --- |
| `tests/test_exit_prompt.py` — `test_an_unrecognized_key_leaves_the_offer_standing` | asserts `outcome.events == ()` for seven keys; each now carries a `Message`. Becomes "no *semantic* event, and the prompt gains the note" |
| `tests/test_exit_prompt.py` — `test_del_at_an_exit_prompt_leaves_it_standing`, `test_ret_is_not_a_default_yes`, `…_at_the_gate` | same `outcome.events == ()` shape, same change |
| `tests/test_exit_prompt.py` — the three `[<path>: <token>]` suffix assertions and `test_the_question_survives_clipping_when_a_note_is_present` | the suffix is produced by the general mechanism now; the *rendered* strings should not change, which is the point — these are the tests that prove D3 retired plan 0018's local version without changing behaviour |
| `tests/test_exit_prompt.py` — `test_n_at_the_gate_leaves_the_editor_running_with_the_text_intact` | asserts `MinibufferAborted() in outcome.events`; becomes `ExitRefused` (D4) |
| `tests/test_harness.py` — `test_harness_routes_minibuffer_keys` | gains an echo assertion it does not have. Registry row 92 cites it for "echoes nothing", but the test only asserts `MinibufferAborted` is emitted and `KeyboardQuitEvent` is not — **it never reads the echo row**. The row's echo claim is unpinned today, which is a smaller version of the failure `test_every_registry_test_citation_exists` guards: the citation exists, it just does not check the thing. Fix the citation in V4 whichever way the behaviour lands |
| `tests/test_minibuffer.py` — `test_open_failed_on_read_error_leaves_buffer_untouched`, `test_binary_file_open_fails_without_crash` | the buffer stays untouched *and* the echo row now names the failure |
| `tests/test_undo.py`, `tests/test_kill_ring.py`, `tests/test_yank_pop.py`, `tests/test_mark_region.py` | the exhausted/no-mark/no-active-yank no-ops gain a `Message` in their outcomes; the assertions that they changed **no buffer state** stay exactly as they are, and are what proves D2 |
| `tests/test_session_properties.py` | any invariant of the form "a no-op emits no events" becomes "emits no *semantic* event" — to be counted during V2, not estimated here |
| `tests/test_terminal.py` — `test_editor_esc_consumed_as_chord_start_then_keyboard_quit` | already asserts `Quit` on one frame and not the next; D6's pin, expected to survive unchanged |
| `tests/test_parity_registry.py` | citations for the eight converted rows |

**The count is deliberately not estimated.** Plan 0017 guessed and was wrong by
a factor of two; plan 0018 guessed and was wrong in both directions. V2 counts
them by running the suite, and the number goes in this plan's status block when
the slice lands.

## 6. Owned deviations (parity-registry rows)

1. **Message text is Drei's own, not byte-identical to Emacs's.** The tokens
   are Drei identifiers and the English is a Drei table; where Emacs says
   `End of buffer` Drei may say the same words, but nothing pins them
   together and the differential tier does not compare echo text.
2. **A message raised behind a prompt is appended to it** (D3), where Emacs
   shows the message and restores the prompt. Deviation: no timer, no
   redisplay loop.
3. **No `*Messages*` buffer** (§4). Emacs logs everything; Drei's message
   exists only until the next command.
4. **Errors do not signal.** Emacs's `C-k` at end of buffer, `C-w` with no
   mark and exhausted `C-/` raise real errors that abort a keyboard macro and
   enter the debugger under `debug-on-error`. Drei says the same sentence and
   carries on. Deviation, deliberate: Drei has no macros, no condition system,
   and no debugger, so "signal" would mean nothing but the message.
5. **`C-g` at a prompt echoes `Quit` without emitting `KeyboardQuitEvent`**
   (row 92's other half stands): aborting a prompt still must not deactivate
   the main buffer's mark, so the echo comes from the abort event, not from a
   top-level quit.

## 7. Implementation order (vertical slices, strict TDD)

1. **V1 — the acceptance scenario, end to end, no session change.**
   `C-x C-f` on a permission error shows `/etc/shadow: permission-denied`.
   `OpenFailed` already exists, so this is the `Message` type, the token
   table, one `_echo_for` branch, and the harness test — the whole mechanism
   proven on an event that is already emitted, with none of D2's risk.
2. **V2 — D2 first, before any no-op speaks.** Write the failing test that
   proves the hazard: undo twice, `M-y` on an empty ring, `C-/` — assert it
   *undoes*. Then exclude `Message` from the three bookkeeping sites, then let
   the exhausted undo speak. Count the sweep here and record it.
3. **V3 — D3.** Messages behind a prompt, and delete plan 0018's
   `_save_buffer` note plumbing in the same step, keeping its rendered output
   identical. `Please answer y or n` at an exit prompt is the new case.
4. **V4 — D4, closing #51.** `SaveDeclined` and `ExitRefused`, then `C-g`
   echoes `Quit` — in that order, because the second is wrong until the first
   lands.
5. **V5 — the remaining rows.** Empty kill, no-mark region, no-active-yank
   pop, `C-x 2` too small, and D7's unbound keys.
6. **V6 — records.** TD-4 removed (not re-scoped), the eight rows rewritten
   with citations, the deviation rows in §6 added, README's command paragraph,
   and #51 closed by the code PR.
7. **V7 — adversarial review → fix → code PR (`Closes #52`, `Closes #51`) →
   merge.**

## 8. Risks / open questions

- **Do messages go in the transcript?** Recommend **yes**: #51's whole subject
  is transcript evidence, `KeyboardQuitEvent` is already an event whose main
  job is to be rendered, and D2 makes their presence harmless to the
  bookkeeping. The cost is that the transcript grows records that describe
  rather than act, and property tests that fold the transcript must ignore
  them.
- **How wide is the registry pass?** The user flagged this as the bulk of the
  work at the scoping gate. Recommend **all eight rows in this slice**: TD-4
  can only be *removed* if nothing is left deferring to it, and an entry that
  gets re-scoped instead of paid is how TD-11 stayed open for seventeen
  slices — the sibling case, in this same file, with the same shape. If the
  slice has to shrink, shrink §5's sweep by splitting V5, not by leaving rows
  pointing at a debt entry that no longer exists.
- **Does `C-g` at top level change at all?** Recommend **no**.
  `KeyboardQuitEvent` is semantic — it deactivates the mark — and already
  echoes `Quit`. It stays as it is; only the *minibuffer abort*, which
  deliberately does not emit it, gains a voice. Worth stating because
  "unify them" is the obvious refactor and it would re-break slice 17's
  separation.
- **Is `subject` on `Message` one field or a payload?** A path, a buffer name
  and a key name are all strings today, but `C-x z is undefined` composes a
  key *sequence*. Recommend one optional `str`, composed by the caller, until
  a second field is actually needed.
- **Will D3 make any message unreadable at 40 columns?** The exit prompts plus
  a note already clip at that width; the slice-18 round-2 review found the
  same defect the other way round. V3 should assert the *question* survives
  clipping for every prompt kind that can carry a note, not only the two that
  do today.
- **How many other registry citations point at tests that do not check the
  claim?** Row 92 does (§5), and it was found by opening one file. The
  citation test only proves the name resolves. Recommend: **do not widen this
  slice to audit all of them** — check the eight rows this slice touches, and
  if more than one is hollow, file it rather than absorbing it. Recorded
  because "the pin exists" and "the pin pins" have quietly diverged at least
  once.

## 9. Acceptance criteria

- `C-x C-f` on a path the port refuses closes the prompt and shows
  `<path>: <token>` on the echo row of the next frame; the buffer is
  untouched.
- Every no-op that gains a message changes **no** buffer state, breaks **no**
  kill/yank chain, and does not flip undo descent — asserted directly, not
  assumed (D2).
- `C-g` at a minibuffer prompt echoes `Quit` and still does not deactivate the
  main buffer's mark; `n` at the exit gate echoes nothing and is distinguishable
  from `C-g` in the transcript.
- A message raised while a prompt is open is visible, and the prompt's question
  and answer set survive `_clip` at 40 columns.
- Plan 0018's `_save_buffer` note plumbing is **deleted**, and the strings it
  produced are unchanged.
- **TD-4 is removed** from `docs/technical-debt.md`, its code marker is gone,
  and no registry row still says "Drei has no echo-error mechanism yet".
- **#51 is closed** by the code PR.
- Proven through the shipped executable on a real ConPTY: at least one scenario
  reads a message off the frame.
- Full gate green on 3.12–3.14 and both CI OSes; coverage floor held at 100%.
