# Twenty-fifth slice: failure-atomic history and kill-ring updates (TD-8)

**Status:** implemented on the code branch (issue #82). Honesty record: the
planned four arms—undo descent, redo direction, kill-ring insertion, and
kill-ring append—were all RED on hidden-state disagreement and GREEN after
prepare-before-commit reordering. The required sibling-path audit found the
same class in region kill/copy, yank/yank-pop, and successful save, so the slice
fixed and pinned those five adjacent paths too rather than closing TD-8 around
known twins. All nine old-order mutations fail with verified source binding;
restored focused and wider state suites pass. The shared semantic snapshot moved to
`tests/conftest.py`. No successful editing semantics, parity rows, public APIs,
or TD-9 behavior changed. Full gates and exact-SHA review remain the delivery
gate.

**Architecture gate:** design 0002 decision 4 requires all changes to occur
inside the serialized command/session boundary and requires a failed grouped
command to restore semantic state before it emits an event. This slice closes
the two known helper paths that currently mutate session-owned state before a
replacement `BufferValue` has passed validation. It does not change the live
model architecture or add a new rollback framework.

**Goal:** make `Undo` (both undo and redo directions) and `KillLine` failure-
atomic if replacement-value construction raises. No reachable editing behavior
changes today: current inputs cannot violate `BufferValue` invariants. The
user-facing guarantee is that a future validation failure cannot silently
consume history or alter the kill ring while leaving the visible buffer and
transcript unchanged.

## 1. The acceptance scenario

The ordinary keystroke-level behavior remains exactly the same:

```text
type "ab"             → buffer shows "ab"
press C-/              → buffer is empty; one undo group moves to redo
press C-b              → undo descent is broken
press C-/              → buffer shows "ab" again; the group returns to history

type "ab\ncd"
press C-k              → buffer shows "\ncd"; ring head is "ab"
press C-k              → buffer shows "cd"; ring head is "ab\n"
```

The discriminating acceptance probe acts below the keystroke layer because the
failure is deliberately unreachable through valid user input:

```text
prepare one undo/redo or kill-line transition
snapshot buffer + window + transcript + history/redo + flags + kill ring
inject failure at replacement BufferValue construction
issue Undo or KillLine
                     → the injected exception propagates
                     → every snapped semantic component is unchanged
                     → no TextUndone/TextRedone/TextKilled event is recorded
restore construction and repeat the command
                     → the existing visible editing result still occurs once
```

The four required failure arms are undo descent, redo direction, new kill-ring
entry, and consecutive-kill append. The last two distinguish merely preserving
the ring object from preserving both ring contents and append-chain semantics.

## 2. What exists today

- `src/drei/session.py:657-1070` serializes command dispatch. It builds a local
  event list, selects a replacement value, performs command bookkeeping, commits
  the buffer/window, and only then extends the transcript.
- `src/drei/session.py:1050-1052` claims validation precedes any mutation and
  therefore makes command failure atomic. That statement is too broad for two
  helpers.
- `src/drei/session.py:1715-1781` handles both undo directions. Each arm pops one
  stack, appends to the other, and appends its event before calling `replace`.
  If construction raised, the stack transfer would survive while the old buffer
  and transcript remained.
- `src/drei/session.py:1814-1844` handles `KillLine`. It inserts/appends/truncates
  the session kill ring and appends `TextKilled` before calling `replace`. If
  construction raised, the ring mutation would survive without the text edit or
  event.
- `docs/technical-debt.md` records those orderings as TD-8 and calls the failure
  currently unreachable. The two `TODO: [tech-debt] TD-8` comments identify the
  exact source locations.
- Existing undo and kill-ring tests comprehensively pin successful semantics,
  including descent, redo direction, append chains, no-op behavior, capacity,
  mark restoration, modified state, and transcript-derived bookkeeping. They do
  not inject a replacement-construction failure or inspect state afterward.

## 3. Design decisions

### D1. Prepare the complete replacement value before mutating owned side state

Compute text, point, mark, and modified state, then call `replace(current, ...)`
to obtain a validated `BufferValue`. Only after that succeeds may the helper
transfer an undo group, update the kill ring, or append its semantic event.

Alternative rejected: snapshot and rollback mutated lists in `except`. That is
more code, is easier to make partial as state grows, and violates the existing
prepare-then-commit shape used by ordinary dispatch arms. Alternative rejected:
move all helper state into frozen `BufferValue`; design 0002 explicitly permits
controlled session-owned mutation, and this debt is ordering rather than
ownership.

**On screen:** nothing on successful commands; on an injected construction
failure, the prior screen remains authoritative rather than disagreeing with
hidden history/ring state.

### D2. Failure propagates; the slice guarantees atomicity, not recovery UI

The injected exception remains a command-programming failure. The helper must
not translate it into `Message`, silently no-op, or add a broad exception arm.
The contract is that no semantic state changes before the exception crosses the
command boundary.

Alternative rejected: normalize construction failure into a deterministic
user event. `BufferValue` validation failure means Drei constructed an invalid
internal value, not hostile platform input; hiding it would turn an invariant
violation into ordinary editor behavior.

**On screen:** the test harness observes the exception; production behavior is
unchanged because the failure remains unreachable from valid command state.

### D3. Pin every authoritative component, not only the named list

Failure tests snapshot the focused `BufferValue`, focused `WindowValue`, full
transcript, undo history, redo history, `undo_descending`, kill ring,
`last_was_kill`, and relevant yank state. The assertion compares the complete
snapshot after failure. This catches a refactor that fixes the list transfer but
still changes command bookkeeping or window evidence before construction.

Alternative rejected: assert only stack/ring equality. That would close the
literal TODO while leaving design 0002's command-boundary promise unproved.

**On screen:** nothing; this is executable evidence that visible and hidden
state remain coherent.

### D4. Use the existing constructor seam; add no production failure hook

Tests monkeypatch the module-local `replace` reference after arranging valid
state and fail only calls that construct a `BufferValue` for the command under
test. They verify the injected failure was reached. No dependency injection,
subclass, test flag, or new public API enters production.

Alternative rejected: make `BufferValue.__post_init__` reject a specially
crafted valid-looking value. That couples the regression to unrelated model
validation and risks making the setup itself invalid. Alternative rejected:
call private helpers directly; dispatch-level tests are required to include
bookkeeping, buffer/window commit, and transcript behavior.

**On screen:** nothing.

### D5. Preserve mutation order after successful construction

After the replacement exists, retain each arm's current externally observable
ordering: transfer exactly one history group, append exactly one event, or update
one kill-ring entry with capacity 60. Dispatch then performs its existing
bookkeeping and commit. No copy-on-write ring/history redesign is required.

Alternative rejected: replace lists wholesale with immutable tuples. That is a
broader live-state architecture change with no acceptance benefit for TD-8.

**On screen:** ordinary undo, redo, kill, yank, echo, and modeline behavior are
byte-for-byte unchanged.

## 4. What this slice does NOT do

- TD-9: startup file visiting and CLI error normalization remain separate.
- TD-7: arbitrary ACP JSON values are not deep-frozen.
- No new undo groups, redo semantics, history capacity, kill-ring capacity,
  append-chain rule, yank behavior, mark adjustment, modified-state rule, or
  parity deviation.
- No general transaction/rollback abstraction for `EditorSession.dispatch`.
  Other dispatch arms are not refactored without a demonstrated ordering defect.
- No swallowing or normalization of internal `BufferValue` invariant failures.
- No terminal/TermVerify scenario: the successful visible paths already have
  shipped terminal and differential evidence, while the failure seam is an
  internal invariant that cannot be driven through valid terminal input.

## 5. Pins that change

No successful-behavior assertion changes.

New focused pins:

1. undo-descent construction failure preserves the complete semantic snapshot;
2. redo-direction construction failure preserves the complete semantic snapshot;
3. first-entry kill construction failure preserves an empty ring and all state;
4. append-chain kill construction failure preserves the existing ring head and
   append flag;
5. each restored command succeeds exactly once with its existing event and
   visible result.

Mutation evidence is mandatory. Move the side-state/event mutation back before
`replace` independently in undo, redo, ring-insert, and ring-append paths; each
relevant focused pin must fail for state disagreement rather than merely because
the injected exception did not run.

## 6. Owned deviations (parity-registry rows)

None. Successful GNU Emacs-facing semantics are unchanged. Internal failure
atomicity is a Drei architecture invariant, not an observable parity decision.
No parity-registry row is added or modified.

## 7. Implementation order (vertical slices, strict TDD)

1. **V1 — undo descent RED.** Arrange one undo group through public dispatch,
   snapshot complete semantic state, inject replacement failure, dispatch
   `Undo`, and observe the history→redo transfer on the old implementation.
   Reorder only the descent arm to construct first; focused GREEN; restore and
   verify the same command succeeds once.
2. **V2 — redo RED.** Arrange redo direction through public commands, inject at
   the redo replacement, and observe redo→history mutation on the old arm.
   Reorder the redo arm; focused GREEN; successful redo remains unchanged.
3. **V3 — kill entry and append RED.** Prove both a new ring insertion and a
   consecutive append survive failed construction on the old code. Construct
   the replacement first, then mutate ring and append `TextKilled`; focused
   GREEN. Pin capacity and append-chain success regressions.
4. **V4 — adversarial mutations and wider regressions.** Run the four
   mutation-order reversions with source binding. Run complete undo, kill-ring,
   mark-region, yank-pop, last-command bookkeeping, agent-buffer identity, and
   session property suites.
5. **V5 — records and debt.** Remove TD-8 and both code TODOs only after all
   failure and successful-path evidence passes. Update issue #74's TD-8 checkbox
   while leaving TD-9 and later entries open. Amend this plan's status/honesty
   block with any implementation correction.
6. **V6 — full gates → draft code PR (`Closes #82`) → fresh exact-SHA
   adversarial review → fixes and re-gate → ready/merge.**

## 8. Risks / open questions

- **Resolved recommendation: dispatch-level injected failure, not private-helper
  testing.** It costs a broader snapshot but proves the actual architecture
  boundary. The injection must be installed after setup and assert that the
  intended `BufferValue` replacement call was reached.
- **Event order:** prepare replacement before appending `TextUndone`,
  `TextRedone`, or `TextKilled`. Local events are not yet transcript state, but
  appending after successful construction makes the helper's prepare/commit
  phases explicit and prevents future callers from observing a phantom event.
- **Snapshot scope:** private state inspection is justified here because the
  debt concerns hidden authoritative state that has no public failure path.
  Tests should centralize a semantic snapshot helper rather than repeat a
  brittle dump of the entire session object.
- **No broad atomicity audit:** design 0002's promise is wider than TD-8, but the
  slice fixes the two recorded counterexamples. If review identifies another
  pre-construction mutation in the touched command paths, fix the class; do not
  expand into unrelated commands without an executable failure.

## 9. Acceptance criteria

- Undo descent and redo direction construct a validated replacement
  `BufferValue` before transferring any history group or appending an event.
- `KillLine` constructs a validated replacement before inserting, truncating, or
  appending to the kill ring or appending `TextKilled`.
- Injected construction failure propagates and leaves buffer, focused window,
  transcript, undo history, redo history, undo direction, kill ring,
  kill-append state, and yank state unchanged in all four arms.
- Restored commands retain their exact existing text, point, mark, modified,
  event, history/ring, capacity, and chain behavior.
- The old mutation-before-construction ordering is discriminated by focused
  tests with verified source binding.
- TD-8 and both `TODO: [tech-debt] TD-8` comments are removed only after the
  evidence passes; issue #74 marks TD-8 paid. TD-9 remains untouched and open.
- No public API, parity row, terminal behavior, dependency, or architecture
  ownership boundary changes.
- Full local gates from `AGENTS.md` are green, coverage remains 100%, and GitHub
  CI passes on Python 3.12–3.14 across Windows and Linux.
