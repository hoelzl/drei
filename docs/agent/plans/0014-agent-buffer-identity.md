# Fourteenth slice: agent buffer identity

**Status:** claimed (issue #34); plan merged (PR #35). **V1 landed** on
`feat/agent-buffer-identity` as `7a0bd3b` — see the D1 amendment below, which
corrects the plan as drafted. V2–V6 outstanding.

**Resuming here?** Read the D1 amendment first (the drafted rule was wrong),
then *Where V1 left off*, then continue at V2 in *Implementation order*.

## Where V1 left off

- `dispatch` resolves a target: `pinned_id = self._target_of(command)` at the
  top, `commit_id = pinned_id or self._current_id` after the match arm. The
  four bookkeeping blocks and the write-back use `commit_id`; the focused
  window's `WindowValue` is refreshed only when `commit_id` is the focused
  buffer. No `self._state` remains in `dispatch`'s own body — that grep is an
  acceptance check.
- Both delivery commands carry `buffer_id: BufferId`; both delivery events
  carry `buffer_id: str`. `apply_session_effects(effects, buffer_id=None)`
  defaults to the focused buffer so pre-0004 callers still work — V2 is where
  an unnamed or non-generated target becomes an error.
- `tests/test_agent_buffer_identity.py` is the new home for this slice's
  tests: four on targeting, five on the bookkeeping arm.
- Nothing yet creates an agent buffer, so every production path still targets
  the focused buffer. Behavior is unchanged; the full suite is the gate.

**Two facts V1 turned up that V2 needs:**

1. An unknown target currently raises `KeyError` from a plain dict lookup
   (`self._buffers[pinned_id]`). V2's `ValueError` replaces it; until then the
   property strategy in `test_session_properties.py` pins `buffer_id` to
   `scratch` rather than generating ids, with a comment saying why.
2. The *decisions* behind D3 (`kind` on the private `_BufferState`) and D4
   (`CreateAgentBuffer` as a command, not a plain method) were confirmed by
   the owner after the plan merged. They are not open questions.

**Architecture gate:** design `0004-agent-buffer-identity.md`, which this slice
implements in full. No new ports, no I/O, no protocol change, no new
concurrency. `BufferValue` stays the frozen per-edit value; the new per-buffer
fact (kind) joins the existing private `_BufferState`. The command boundary is
unchanged in shape — what changes is that two commands stop reading ambient
focus and name their target instead.

**Goal:** make "the agent buffer" a thing that exists. Today
`InsertAgentText` and `DeliverSessionEffects` append to whatever buffer is
focused when the delivery lands, which since A.2 splits one transcript across
buffers, writes agent text into the user's file buffer without marking it
modified, and steals point from a human mid-edit (review 0001 finding 5,
`docs/technical-debt.md` TD-1). After this slice a transcript binds to one
`*agent*` buffer per ACP session, deliveries carry their target, and only a
generated buffer can receive one.

## What exists today (the delta is nameable)

- `EditorSession.dispatch` opens with `current = self.buffer.current` and
  closes with `self.buffer.replace(new_value)` — **every** command reads and
  writes the focused buffer. There is no way to express "edit that other
  buffer".
- `self._state` (`session.py:465`) resolves to the *focused* buffer's
  `_BufferState`, so the undo/yank/kill-chain bookkeeping at the end of
  dispatch always lands on the focused buffer, including for deliveries.
- `self._agent_fold` is **one** `TranscriptFold` on the session
  (`session.py:435`) — a single fold for a single implied agent buffer.
- `InsertAgentText(text)` and `DeliverSessionEffects(effects)` carry no
  target; `AgentTextInserted(text, before, after)` and
  `AgentTranscriptUpdated(effects, rendered)` record none.
- `_create_buffer(name, value, events)` exists and already handles `<N>`
  collision suffixes; `_BufferState` already holds per-buffer file facts
  (`saved_text`, `eol`). Both are the right hooks.
- No buffer kind anywhere. Nothing distinguishes `scratch` from a file buffer
  except `file_path`.

## Design decisions (implementing 0004)

### D1. Dispatch resolves a target buffer; focus is the default, not the rule

`dispatch` gains one resolution step: the **target** is the focused buffer for
every command except `DeliverSessionEffects` and `InsertAgentText`, which name
theirs. `current`, `self._state`, and the write-back all follow the target.

> **Amended during V1 (commit `7a0bd3b`) — resolve *when*, not just *what*.**
> As drafted this paragraph said the target is resolved "at the top", once,
> for every command. That is wrong, and the existing suite caught it: a
> command may change the focused buffer **inside its own match arm**
> (`find-file` selects the buffer it just created; `C-x b` selects the one it
> switched to), and its new value belongs to the buffer it ended on. The
> pre-refactor code got this right by accident, re-resolving `self.buffer` at
> commit time. Freezing the target up front sent find-file's loaded text into
> the *previous* buffer — four tests failed, including the shipped ConPTY
> switch-buffer scenario.
>
> The rule is therefore narrower than "resolve once": a delivery **pins** its
> target at the top (it must not follow a focus change it did not cause);
> every other command resolves again **after** the arm runs, preserving the
> existing semantics exactly. In code: `pinned_id = self._target_of(command)`
> up front, `commit_id = pinned_id or self._current_id` after the match. Any
> future command that moves focus as part of its own effect stays correct
> under this rule; it would not under the original wording.

This is the structural core of the slice and it is deliberately a *general*
mechanism stated in one place, not a special case threaded through two match
arms. Three consequences fall out for free, and each is a defect today:

- The delivery edits the agent buffer's text without touching the focused
  buffer's value.
- The end-of-dispatch bookkeeping lands on the **agent buffer's**
  `_BufferState`, so a delivery no longer breaks the *user's* kill-append
  chain (see *Pins that change*).
- The focused window's `WindowValue` is refreshed only when the target **is**
  the focused buffer; otherwise the window keeps its point.

`CommandOutcome.observation` stays the focused buffer's view regardless of
target — it is the read model for what the user is looking at.

### D2. Target on the commands and in the events

```text
DeliverSessionEffects(effects, buffer_id: BufferId)
InsertAgentText(text, buffer_id: BufferId)
AgentTranscriptUpdated(effects, rendered, buffer_id: str)
AgentTextInserted(text, before, after, buffer_id: str)
```

Events carry `str`, matching `BufferCreated`/`SaveFailed`, which already
record names rather than ids.

Putting the target in the **event** is what makes the fold oracle survive: the
agent text of buffer B is the concatenation of `rendered` over every
`AgentTranscriptUpdated` targeting B, derivable from the transcript alone. A
binding resolved from session state at dispatch time would not be replayable
across a rebinding.

### D3. Buffer kind on `_BufferState`; only generated buffers accept deliveries

`_BufferState` gains `kind: Literal["ordinary", "generated"]`, defaulting to
`"ordinary"`. It lives there rather than on `Buffer` or `BufferValue` because
it is a per-buffer session fact that never varies per edit — the same category
as `saved_text` and `eol`, which are already there.

A delivery naming a buffer that does not exist, or that exists and is
`"ordinary"`, raises `ValueError` (the class `BufferValue.__post_init__`
already uses for a caller contract violation). **Not** a silent drop, which
would desync the fold, and **not** a write into a file buffer, which is the
hazard. The pump can only pass ids the session itself minted, so a violation
is a programming error, not peer input.

### D4. `CreateAgentBuffer(acp_session_id)` — a delivery-class command

The session keeps `_agent_buffers: dict[str, BufferId]`. `CreateAgentBuffer`
returns the existing binding if present (idempotent — a re-fold of
`SessionEstablished` must not mint a second buffer), else creates a
`"generated"` buffer named `*agent*` (`*agent*<2>` … via the existing
collision rule), records `BufferCreated`, and stores the binding. It does
**not** switch focus: the agent buffer appearing must not yank the user out of
their work.

Creation is a command rather than a plain method so that `BufferCreated` lands
in the transcript through the one boundary everything else crosses.

The binding is readable via `agent_buffer_id(acp_session_id) -> BufferId | None`
so a caller can build subsequent deliveries. There is no pump yet; this slice's
tests are the caller, exactly as B.7's were.

### D5. The fold cache becomes per target buffer

`self._agent_fold: TranscriptFold` becomes
`self._agent_folds: dict[BufferId, TranscriptFold]`. One agent buffer per ACP
session means one fold per agent buffer; a single shared fold would interleave
two sessions' transcripts into each other's rendering state.

### D6. Tail-follow point

On append to buffer B with old length `n`:

- If `B.point == n`, it becomes the new end; otherwise it is unchanged.
- For every **non-focused** `WindowValue` showing B, the same rule against its
  own stored point.
- Mark adjustment is unchanged (the existing insert rule).

The window arm is why 0004 states the rule over windows: a user scrolled back
in one window is not dragged to the end because output arrived in another
window on the same buffer. The focused window's live point is
`BufferValue.point`, so the two arms together cover every view.

### D7. No file, no modified

A generated buffer is created with `file_path=None`, so `C-x C-s` on it
already fails with the existing `no-file` token, and `modified` stays False
because agent appends do not set it. Nothing new is needed — the point is that
the *question* disappears rather than being answered.

### D8. What this slice does NOT do

- **No read-only enforcement.** A user can still type into an agent buffer and
  the fold then diverges from the live text. That is §A.3 and 0004 D6; the
  `kind` field added here is the hook it will use. The existing registry row
  owning that hazard stays, updated only to say the hook exists.
- **No pump.** Nothing spawns an agent or dispatches these commands in
  production. Design 0005 owns that; this slice is its prerequisite.
- **No `kill-buffer`**, so agent buffers accumulate (0004 D7). Recorded, not
  fixed.
- **No `write-file`**, so a transcript cannot be saved. Recorded.
- **No display of kind** in observations or the modeline. Deferred until
  something needs it.
- **No rename of `scratch` → `*scratch*`.** The naming inconsistency is
  recorded in 0004 D1; changing the default buffer's name would touch
  differential scenarios and belongs in its own change.

## Pins that change (each is itself a finding, or follows from one)

1. `test_fold_only_delivery_breaks_the_chain` — a fold-only delivery currently
   breaks the *focused* buffer's kill-append chain. After D1 it breaks the
   **agent buffer's** chain and leaves the user's intact. Rewrite to assert
   both halves. The old behavior was never intended; it is the same
   ambient-focus defect in a different bookkeeping block.
2. `test_append_delivery_indirectly_preserves_the_chain` — relies on the
   delivery moving the *focused* buffer's point to end-of-buffer. With the
   delivery targeting another buffer the mechanism vanishes. Replace with a
   direct pin of the same rule (a silent no-op does not intervene) that does
   not route through a delivery.
3. `TestInsertAgentText::test_point_tracks_the_new_end` — becomes tail-follow:
   point tracks the end **only if it was already at the end**. Add the
   negative arm (point mid-buffer stays put), which is the finding.
4. `test_fold_cache_reconstructible_from_events` — must dispatch a **buffer
   switch between deliveries**. Its silence about that case is precisely what
   let the defect ship, so the strengthened oracle is the regression test.
5. `TestUserEditsToAgentBufferNotRejected` — still passes, but must now target
   an actual agent buffer rather than the focused one.

## Owned deviations (parity-registry rows)

1. **Agent buffer is generated and visits no file.** Named `*agent*` per ACP
   session; `C-x C-s` reports `no-file`. Emacs's nearest analogue is a comint
   buffer, which also visits no file, so this is parity in spirit; the row
   exists to own the `*…*` naming convention and to record that Drei's default
   buffer is inconsistently named `scratch`.
2. **Deliveries follow the tail, never steal point.** Emacs comint moves point
   for output only when point was at the process mark; Drei's rule is the same
   shape stated over windows. Pinned by D6's tests.
3. **Existing rows updated, not replaced:** "Agent deliveries not undoable"
   and "User edits to the agent buffer not rejected" keep their hazards; the
   latter gains a pointer to the `kind` hook §A.3 will use.

## Implementation order (vertical slices, strict TDD)

1. **V1 — target resolution.** Add the target field to both delivery commands
   and both events; make `dispatch` resolve a target and route `current`,
   `_state`, write-back, and the window refresh through it. Target defaults to
   the focused buffer everywhere else, so this step is behavior-preserving for
   every non-delivery command — the existing suite is the regression test.
   Deliveries still target the focused buffer at this point (callers pass
   `session.buffer.buffer_id`); nothing else changes yet.
2. **V2 — buffer kind + creation.** `_BufferState.kind`, `CreateAgentBuffer`,
   `_agent_buffers`, `agent_buffer_id`, idempotence, `BufferCreated` recorded,
   focus untouched. A delivery to a missing or ordinary buffer raises.
3. **V3 — per-buffer fold.** `_agent_folds` keyed by target; two concurrent
   agent buffers keep separate rendering state (test: interleaved deliveries
   to two agent buffers render independently and each buffer's text equals its
   own fold).
4. **V4 — tail-follow point.** D6, both arms, plus the non-focused-window arm.
   Rewrite pins 1–3 above.
5. **V5 — the oracle and properties.** Strengthen
   `test_fold_cache_reconstructible_from_events` with a buffer switch; add a
   property over generated histories interleaving edits, buffer switches, and
   deliveries: for every buffer B, B's agent text equals the concatenation of
   `rendered` over `AgentTranscriptUpdated` events targeting B, and no
   non-generated buffer ever receives agent text. Registry rows.
6. **V6 — adversarial review → fix → code PR (`Closes #<issue>`) → merge.**

## Acceptance criteria

- A delivery targeting buffer B appends to B whatever buffer is focused; the
  focused buffer's text, point, mark, and modified flag are untouched.
- A delivery naming a non-existent or `"ordinary"` buffer raises; no partial
  state change, no event recorded.
- `CreateAgentBuffer` is idempotent per ACP session id, records
  `BufferCreated`, and does not change focus.
- Two concurrent ACP sessions produce two agent buffers with independent
  folds and independent text.
- Tail-follow holds in both arms, for the focused buffer's point and for
  non-focused windows over the same buffer.
- The per-buffer fold oracle holds as a property over interleaved
  edit/switch/delivery histories, including at least one buffer switch between
  two deliveries.
- An agent buffer has no `file_path`; `C-x C-s` on it emits
  `SaveFailed(name, "no-file")`.
- Every changed pin from *Pins that change* is rewritten with its rationale in
  the test, not silently deleted.
- Full quality gate green on 3.12–3.14 and both CI OSes; coverage floor held
  at 100%; `drei.acp` purity unchanged (all of this is session-side).
- `docs/technical-debt.md` TD-1 removed (the debt is paid, not re-scoped) and
  the code TODO with it.

## Risks / open questions

- **The target-resolution refactor is the risky step.** It touches the opening
  and closing of `dispatch`, which every command crosses. Mitigation: V1 is
  deliberately behavior-preserving — deliveries keep targeting the focused
  buffer until V2 — so the full existing suite gates the refactor before any
  semantics change rides on it.
- **`self._state` has ~25 call sites, and only some of them matter.** The
  helpers (`_undo`, `_kill_line`, `_yank`, `_save`, …) are reached **only**
  for non-delivery commands, so they are correct unchanged — the focused
  buffer *is* their target. The blocks that run for **every** command,
  including deliveries, are the four bookkeeping blocks at the end of dispatch
  (kill chain, yank active, undo group, undo descent). Those are the ones that
  must follow the target. Mitigation: resolve the target's state into a local
  once at the top of dispatch and use it in those blocks; missing one leaves
  it silently operating on the focused buffer, which is the current bug. An
  acceptance check greps `dispatch`'s own body for `self._state`.
- **Two agent buffers is currently untestable end-to-end.** Nothing creates
  two ACP sessions. V3 tests the mechanism directly, which is honest but
  weaker than a scenario; the end-to-end case arrives with the pump.
- **`*agent*` naming.** 0004 leaves multi-agent naming open (a `cwd`-derived
  name is the candidate). This slice ships the deterministic `<N>` suffix and
  does not pretend it is a good user-facing name.
- **Property-test cost.** The interleaved-history property adds a third
  generated-history suite. If runtime becomes a problem, prefer narrowing the
  strategy over lowering `max_examples` — review 0001 cluster A showed a
  50-example profile passing against buggy code.
