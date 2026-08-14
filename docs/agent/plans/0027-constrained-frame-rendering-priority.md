# Twenty-seventh slice: constrained-frame rendering priority (TD-10)

**Status:** ready (issue #89).

**Architecture gate:** design 0002 decisions 2 and 5 (immutable semantic observations
are the verification boundary; presentation does not redefine the live model), plan
0015 D7 (resize never deletes semantic windows), and issue #74's constrained-frame
policy gate. The owner selected the complete policy before this plan: prompt/message
priority at one row, a reserved shared echo row from two rows upward, a useful focused
pane before any non-focused pane, complete non-focused panes only, a contiguous
focus-centered visible subset, and the existing even-height/bottom-remainder rule.

**Goal:** shrinking an already-split Drei frame must keep the active prompt or message
visible, keep the focused pane useful whenever rows permit, and hide only complete
non-focused panes without changing semantic window state. Growing the frame restores
the same windows, focus, points, and marks. This pays TD-10 without adopting Emacs's
destructive window deletion or adding presentation state to the session.

## 1. The acceptance scenario

The shipped path, followed by the exact constrained-height progression:

```text
start Drei at 40x8 and press C-x 2
press C-x o                    -> the lower pane is focused
press C-x C-f                  -> bottom echo row shows `Find file: `
resize the physical terminal
so Drei has one editor row     -> that only row shows `Find file: `
                                 and the prompt cursor remains on it
press C-g                      -> that only row shows `Quit`
press C-f                      -> the message clears; that row shows the
                                 focused lower pane's modeline
resize to two editor rows      -> row 0 is the focused lower modeline
                                 row 1 is the shared echo row
resize to three editor rows    -> focused lower body, its modeline, echo
resize to four editor rows     -> two focused lower body rows, modeline, echo;
                                 the upper pane is still hidden
resize to five editor rows     -> upper body/modeline, focused lower
                                 body/modeline, then echo
resize back to eight rows      -> both original panes return in stack order
                                 with the same focus, points, and marks
```

The discriminating two-row real-terminal scenario uses a three-row cooperating
ConPTY: one physical bottom row belongs to the TermVerify readiness marker, leaving
two editor rows. With two semantic windows and `Find file: ` open, the frame must show
the focused modeline followed by the prompt. Before this slice the renderer emits two
modelines and truncates the prompt.

Three-window subset behavior is pinned directly:

```text
semantic stack A / B / C; room for two complete panes
focus A                         -> visible A / B
focus B                         -> visible B / C
focus C                         -> visible B / C
```

The selection is a presentation projection only. No resize or render operation changes
the semantic stack.

## 2. What exists today

- `src/drei/render.py:54-126` renders every semantic window, appends the shared echo
  row last, and then applies `rows[:height]`. At two rows with two windows this keeps
  both modelines and drops the prompt/message row. The cursor is subsequently clamped
  onto whichever surviving row happens to be last.
- `_window_heights` at `src/drei/render.py:144-158` gives every window at least one
  modeline row even when the frame cannot hold them. This creates modeline-only
  non-focused panes and relies on final truncation to satisfy the `Frame` row cap.
- `render` already defines the single-window baseline: height zero is empty, height
  one is modeline-only, height two is modeline plus shared echo, and ordinary heights
  are body + modeline + echo (`tests/test_render.py`). `render_session` promises the
  same single-window shape (`tests/test_render_windows.py:58-69`) but does not define
  prompt/message priority at height one.
- `tests/test_render_windows.py:180-208` deliberately pins the TD-10 defect: at height
  two both modelines survive and the echo row is absent; at height one only the first
  semantic pane's modeline survives, even when focus moved elsewhere.
- Plan 0015 D7 and `tests/test_windows.py:283-310` require resize to preserve all
  semantic windows and grow-back restoration. `tests/test_harness.py:340-362` records
  the current hazard that `C-x o` can focus and edit a completely invisible pane.
  This slice narrows that hazard by making the focused pane the presentation anchor;
  it does not weaken the non-destructive semantic rule.
- `docs/knowledge/emacs-parity.md` rows "Shrinking below the split minimum keeps every
  window" and "A frame too short for its panes drops the echo row first" separate the
  intentional semantic deviation from the unintended rendering defect. The second
  row must be replaced when TD-10 is paid; the first remains.
- TermVerify cooperation reserves the physical bottom row
  (`src/drei/terminal.py:315-380`). A three-row cooperating ConPTY therefore gives
  the renderer two editor rows and still leaves a full-width readiness row. The
  direct renderer/harness tier remains authoritative for zero/one-row semantics and
  the complete row-budget matrix: TermVerify 0.1.1 cannot observe a marker when the
  complete screen has too few cells for it (TermVerify #287), so terminal evidence
  must use a geometry that can carry the marker rather than treating timeout as
  product behavior.
- A plan-time real-ConPTY probe against base `5c4c63c` opened two windows, focused the
  lower one, opened `Find file: `, and resized to 40x3 physical cells. The resize epoch
  completed and the physical frame was exactly two `Drei: scratch --` modelines plus
  `<<termverify.ready:7>>`. This proves the proposed three-physical/two-editor-row
  oracle is executable and discriminates the pre-slice defect; it does not rely on a
  hypothetical tiny-screen capability.

## 3. Design decisions

### D1. Constrained rendering is a projection; semantic windows never change

`render_session` chooses a visible subset from the immutable `SessionObservation`.
It does not dispatch a command, mutate the session, delete or reorder windows, change
focus, or add a persistent presentation viewport. A later grow recomputes the
projection from the same semantic stack and therefore restores hidden panes.

- **Alternatives.** Delete windows as Emacs does: rejected by plan 0015 D7 because a
  resize is accidental and must be reversible. Add viewport state to `EditorSession`:
  rejected because rendering can derive the subset deterministically from window
  order, focus, and row budget; presentation bookkeeping would become replay state
  without changing editor semantics.
- **On screen:** panes may disappear while the frame is constrained, but the focused
  pane remains represented whenever any pane row exists, and hidden panes return on
  growth.

### D2. The shared echo row and one-row priority are explicit

The row contract is closed:

1. Height zero: `rows == ()`, cursor `(0, 0)`.
2. Height one with an active minibuffer: the sole row is prompt + input, cursor on it.
3. Height one without a minibuffer but with a non-empty transient echo message: the
   sole row is the message, cursor `(0, 0)`.
4. Height one otherwise: the sole row is the focused pane's modeline, cursor `(0, 0)`.
5. Height two or greater: the bottom row is always the shared echo/minibuffer row,
   even when empty; the remaining rows are the pane budget.

A prompt outranks a transient message because the minibuffer owns the echo row under
the existing contract. A non-empty message outranks a modeline at one row because it
is the only visible result of the command the user just issued. An empty echo row does
not outrank the focused modeline when there is only one row.

- **Alternatives.** Always reserve the sole row as echo: rejected because an empty
  terminal would hide all editor identity. Always show the modeline when no prompt is
  active: rejected because one-row failures and `Quit` would become invisible. Allow
  final truncation to arbitrate: rejected because row construction order would remain
  accidental policy.
- **On screen:** questions and command feedback remain visible at the smallest usable
  height; an idle one-row editor still identifies the focused buffer.

### D3. A useful pane is body plus modeline; focus is admitted first

For pane budget `P`:

- `P == 0`: no pane is visible.
- `P == 1`: the focused pane has an emergency modeline-only representation.
- `P >= 2`: the focused pane is admitted with at least one body row and its modeline.
- Every non-focused pane costs two rows: at least one body row plus its modeline.
- A leftover row that cannot admit another complete pane enlarges already visible
  body content; it never creates a modeline-only non-focused pane.

Thus, with the reserved echo row, total editor heights 0 through 5 produce:

| Height | Visible presentation without active content |
| ---: | --- |
| 0 | empty |
| 1 | focused modeline |
| 2 | focused modeline + echo |
| 3 | focused body + modeline + echo |
| 4 | two focused body rows + modeline + echo |
| 5 | two complete panes (when they exist) + echo |

- **Alternatives.** Admit every modeline before any body: rejected because typing can
  alter a focused buffer whose text is completely invisible. Permit modeline-only
  non-focused panes: rejected because a visible pane should expose editable content;
  the one-row focused exception exists only because no useful pane can fit. Leave an
  unmatched row blank: rejected because it can show more focused content at no cost.
- **On screen:** scarce rows show the work being edited, not a stack of status strips.

### D4. The visible subset is contiguous and focus-centered

When `K` complete panes fit but fewer than the semantic window count, select one
contiguous slice of the existing top-to-bottom stack that contains the focused index.
Center it on focus as far as the stack boundaries permit. For even `K`, put focus in
the upper-middle slot when possible, favoring the following semantic pane. Preserve
stack order in the rendered result.

Examples for A/B/C and `K == 2`: focus A -> A/B; focus B -> B/C; focus C -> B/C.
The selector is a small pure function over `(window_count, focused, K)` and has no
memory of the prior frame.

- **Alternatives.** Rotate the stack so focus always renders first: rejected because
  constrained rendering would reorder panes. Always combine focus with the earliest
  windows: rejected because it can hide an adjacent pane while showing a distant one.
  Persist a scrolling viewport: rejected by D1.
- **On screen:** changing focus can move the visible contiguous region, but panes never
  appear out of semantic order and the focused pane is never outside it.

### D5. Ordinary pane-height distribution remains unchanged

After admission gives every visible pane body + modeline, distribute surplus body rows
evenly and assign the remainder to the bottom visible pane. This is the existing
Emacs-style normal-layout rule. Focus priority controls which panes qualify and ensures
the focused minimum; it does not permanently make the focused pane larger.

- **Alternatives.** Give the focused pane first or all surplus rows: rejected because
  it would change ordinary uneven layouts beyond the constrained-frame defect. Keep
  the old `_window_heights` over all semantic windows and then select rows: rejected
  because hidden panes would still consume budget and truncation would still decide
  policy.
- **On screen:** once all panes fit, normal-sized frames retain their current row
  distribution and single-window frames retain their existing shape.

### D6. Cursor ownership follows the selected presentation

With an active minibuffer, the cursor is on the shared echo row (or the sole prompt
row). With visible focused body, use the existing sanitized/clipped body-point mapping
plus the selected pane's row offset. In the emergency modeline-only focused state,
place the cursor at column zero on that modeline. In message-only or empty output use
`(0, 0)`. No post-hoc clamp may move a focused-body cursor into another pane.

- **Alternatives.** Keep computing over all semantic windows then clamp: rejected
  because the cursor can land on a non-focused modeline or echo row after its pane was
  hidden. Hide the focused pane and clamp to a visible one: rejected by D3/D4.
- **On screen:** the cursor is always inside the row that owns the active interaction.

### D7. Direct evidence owns the full matrix; ConPTY owns one discriminating boundary

Focused renderer tests pin the complete height/content/focus matrix, pure selection,
height distribution, cursor placement, and single-window compatibility. Harness tests
prove real `ResizeFrame` shrink/grow changes no semantic windows, focus, points, or
marks and that `C-x o` reprojects around the newly focused pane. One TermVerify scenario
shrinks a real split editor with `Find file: ` open to two editor rows (three physical
rows including cooperation), requiring focused modeline + prompt and a completed
readiness epoch.

- **Alternatives.** Drive zero and one editor rows only through TermVerify: rejected
  because marker capacity rather than rendering would become the oracle at geometries
  near TermVerify #287. No real-terminal evidence: rejected because TD-10 is reachable
  through the shipped resize path and its user-visible consequence is precisely which
  row appears.
- **On screen:** the shipped two-row editor visibly retains the active question instead
  of showing two modelines.

## 4. What this slice does NOT do

- It does not delete semantic windows on resize or adopt Emacs's destructive behavior.
- It does not add a scrollable/persistent presentation viewport, window tree,
  horizontal split, `C-x 0`, or a window-deletion command.
- It does not change `ResizeFrame`, resize polling, readiness epochs, split-admission
  thresholds, or initial geometry/genesis evidence.
- It does not add horizontal scrolling for body or minibuffer text.
- It does not pay TD-17 or introduce stable parity-row IDs; current implementation
  citations remain title-based until that separately tracked tooling slice.
- It does not change TD-16 ACP permission phase semantics or TD-7 JSON immutability.
- It does not adopt issue #72's OKF metadata proposal.
- It does not claim TermVerify can prove zero/one-row behavior when its printable marker
  cannot fit; direct structured evidence owns those cases.

## 5. Pins that change

1. `tests/test_render_windows.py::test_shrink_below_the_split_minimum_degrades_in_stages`
   changes at height two from two modelines/no echo to focused modeline + echo, and at
   height one requires the focused modeline rather than the first semantic window.
2. `_window_heights` modeline-only overflow pins are replaced by tests over admitted
   complete panes and the unchanged even/bottom-remainder distribution.
3. The row-cap test stops accepting arbitrary truncation as sufficient. It requires the
   closed row-budget matrix, focused inclusion, and no overflow.
4. New one-row pins cover active text prompt, choice/permission prompt, exit prompt,
   transient `Quit`/failure messages, idle modeline, width zero, and cursor ownership.
5. New three-window pins cover A/B, B/C, B/C subset selection and preserve stack order.
6. Harness shrink/grow pins additionally require the visible focused pane at every
   nonzero pane budget while preserving all semantic windows and their points/marks.
7. `tests/test_render.py` remains the single-window byte-shape oracle at ordinary
   heights and height two. Its height-one prompt/message gap gains equivalent coverage
   or `render_session` delegates to one shared policy so the two public renderers cannot
   diverge.
8. The shipped prompt-resize scenario gains a constrained two-editor-row arm. It must
   assert subject-owned row contents, not only TermVerify's resized screen geometry.

## 6. Owned deviations (parity-registry rows)

No new semantic deviation. The existing intentional row "Shrinking below the split
minimum keeps every window" remains and is strengthened: Drei still differs from Emacs
by preserving hidden semantic windows, but the focused window now anchors the visible
projection. Its hazard narrows from "focus can cycle into a window the frame cannot
show" to "non-focused semantic windows can be temporarily hidden and return on growth."

The unintended row "A frame too short for its panes drops the echo row first" is
replaced with the accepted constrained-frame policy and exact focused/direct/terminal
test citations. The reference side remains: Emacs gives up windows before its echo
area. Drei now matches the user-visible priority while deliberately differing in how
hidden windows survive.

No differential batch scenario is added: terminal window fitting and echo-area
presentation are outside the registry's semantic batch-comparison tier. The pinned
reference statement and intentional semantic deviation remain governed in the registry.

## 7. Implementation order (vertical slices, strict TDD)

1. **V1 - prompt/message priority and the 0-5 row matrix.** Write renderer REDs first
   for the genuinely missing cases: active prompt at one/two rows, transient message at
   one row, the lower-focused idle modeline, and exact cursor ownership. Observe the
   current two-modeline/no-prompt and first-window failures. Retain the already-green
   height-zero, width-zero, and generic cursor-bound pins as regression gates rather
   than reporting them as RED evidence. Implement explicit echo reservation and focused
   emergency/useful minima without final row truncation.
2. **V2 - complete pane admission and focus-centered selection.** RED the three-window
   A/B/C matrix and modeline-only non-focused rejection. Add a pure contiguous selector
   and compute complete visible panes before height distribution. Mutation-check that
   selecting earliest windows or admitting a one-row non-focused pane fails.
3. **V3 - distribution, cursor, and single-window compatibility.** RED the genuinely
   new constrained cases: selected-pane cursor offsets, prompt cursor ownership after
   selection, and uneven surplus after some semantic panes are hidden. Preserve the
   already-green all-fit bottom-remainder, sanitized-column, and byte-identical ordinary
   single-window pins as characterization/regression gates; they must stay green through
   helper extraction and are not TDD RED evidence. Reuse/extract policy helpers rather
   than keeping independent tiny-height rules in `render` and `render_session`.
4. **V4 - semantic shrink/focus/grow scenario.** Through `EditorHarness.resize`, create
   two and three windows with distinct points/marks, shrink through the matrix, cycle
   focus so the visible subset reprojects, edit the focused window, and grow back.
   Require unchanged semantic windows except for the explicit focus/edit commands;
   rendering alone emits no semantic event.
5. **V5 - shipped constrained resize.** Extend the ConPTY prompt-resize evidence with a
   genuine geometry change to three physical rows/two editor rows. Require one completed
   resize epoch and exact focused-modeline/prompt placement. Keep zero/one-row claims in
   direct tests unless a separate probe proves the marker fits reliably; do not loosen
   abort deadlines or treat marker failure as Drei behavior.
6. **V6 - records and debt removal.** Replace the TD-10 registry row, narrow the hidden
   window hazard row, and update current renderer comments/docstrings. Advance README's
   status from twenty-six to twenty-seven merged slices and add the slice-27 constrained
   rendering summary whether or not another README behavior paragraph changes. Remove
   TD-10 and its code TODO after evidence passes, mark only the policy gate/TD-10 boxes
   in issue #74, and amend this status with the plan honesty record.
7. **V7 - full gates -> draft code PR (`Closes #89`) -> fresh exact-candidate adversarial
   review -> fixes/re-gate -> ready and assistant merge.**

## 8. Risks / open questions

- **No open product decision.** The owner selected D1-D7 before the plan was drafted.
  The plan PR remains the durable review gate for whether the policy is complete and
  implementable against the current renderer.
- **Single-window compatibility risk.** `render` and one-window `render_session` are
  promised byte-identical at ordinary sizes. A multi-window-only helper that changes
  one path can split them at height one. V1/V3 must compare both public paths for each
  shared state.
- **Focus-centered tie risk.** Even visible counts need a literal expected matrix; prose
  such as "centered" is insufficient. D4 fixes the tie toward the following pane so a
  reviewer can reject an off-by-one selector.
- **False-green terminal risk.** ConPTY resizing changes TermVerify's screen even if Drei
  draws the wrong rows. The scenario must assert `Find file:` and the focused modeline
  in the two editor-owned rows and a completed marker epoch.
- **Tiny-screen cooperation boundary.** The plan-time probe proved 40x3 physical cells
  carry both the two-row editor defect and the marker on current TermVerify 0.1.1.
  Zero/one-row product semantics remain direct evidence because narrower or shorter
  screens can cross TermVerify #287's total-cell limit. Do not weaken the measured
  three-row oracle or invent a Drei-private marker merely to move those cases upward.
- **Cursor false-clamp risk.** `cursor_row < len(rows)` is necessary but not sufficient;
  every constrained case must assert that the cursor row belongs to the active prompt
  or focused pane.
- **Over-scoping risk.** This is row allocation, not semantic window management. Any
  need to mutate `EditorSession.windows`, record a render event, or add viewport state
  is a stop-and-revisit signal rather than implementation latitude.

## 9. Acceptance criteria

- Height zero, one, and two obey D2 exactly for active prompt, transient message, idle
  focus, width zero, and cursor placement.
- With at least two pane rows, the focused pane has body + modeline before any
  non-focused pane is visible; every visible non-focused pane has body + modeline.
- Partial visibility is one contiguous focus-centered slice preserving stack order,
  including the exact A/B/C tie matrix in D4.
- Once panes are admitted, body rows distribute evenly with remainder to the bottom
  visible pane; ordinary single-window and all-fit layouts retain their current shape.
- Rendering never changes semantic windows, focus, points, marks, transcript, or events.
  Explicit `C-x o`/edit commands while constrained act on the visible focused pane.
- Shrink then grow restores every hidden semantic pane with its prior point and mark.
- A real cooperating ConPTY with two editor rows shows focused modeline + `Find file:`
  after a resize and completes the resize readiness epoch.
- The parity registry retains the non-destructive resize deviation, replaces the
  unintended echo-first row with the accepted policy, and cites tests whose assertions
  directly prove each claim.
- TD-10 and its `TODO: [tech-debt]` marker are removed only with the implementation;
  issue #74 marks the rendering policy and TD-10 paid while TD-17, TD-16, and TD-7
  remain unchanged.
- The slice-26 plan status accurately records merged PR #88 and current-head CI in the
  same documentation PR that introduces this plan.
- After the implementation merges, README reports twenty-seven merged slices and
  summarizes slice 27's constrained-frame rendering behavior.
- Full local gate from `AGENTS.md` is green, coverage remains 100%, and GitHub CI passes
  on Python 3.12-3.14 across Windows and Linux before the code PR merges.
