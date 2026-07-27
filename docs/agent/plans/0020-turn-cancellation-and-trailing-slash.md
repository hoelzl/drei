# Twentieth slice: C-g turn cancellation and trailing-slash find-file (TD-2, TD-3)

**Status:** implemented (issue #56).

What the plan got wrong (the honesty record):

1. **V2's "queued permission answered before the UI clears" premise is
   unreachable.** Caught RED by its own test: *every* prompt close drains
   the permission queue — accept included (`session.py:942–945`), not only
   abandons — so a top-level `C-g` never meets an unpresented request, and
   the `AbortPendingPermissions` half of the post-cancel sweep is defensive,
   not load-bearing. The test was rewritten to pin that routing invariant
   (`test_a_request_queued_behind_a_prompt_is_presented_on_close`); design
   0005 D5 carries the same amendment. The plan (and 0005) had assumed the
   queue could stand at cancel time.

2. **The peeling order had two more claimers than the plan discussed.**
   Round-1 review found both unpinned: `C-g` at a *text prompt* mid-turn
   aborts the prompt alone (the turn waits — `MinibufferAborted` carries no
   `KeyboardQuitEvent`), and a *pending prefix* peels *with* the turn —
   `C-x C-g` resolves to the same `KeyboardQuit()` as a bare `C-g`, so the
   event carries no prefix provenance and the pump cannot distinguish
   (pinned as intended: Emacs's own `C-g` also quits from a prefix). Both
   are now pinned in `TestTurnCancellation` and recorded in the registry
   row and 0005 D5's banner. Also in that review's wash: a stale "Nothing
   calls either." in 0005 D5's body, amended.

Everything else held: the V-order, the trigger-composition D1–D4, the D5
token choice, "no pins change" (§5), and the ConPTY settle witness
(`Drei: *agent*` ⟹ prompt in flight) all survived contact.

**Architecture gate:** design `0005-acp-pump.md` **D5** (cancellation — the
one decision in that record still `proposed`; its blocker, `C-g` meaning
exit, was removed by slice 17). TD-3 has no design record — it is a
tech-debt entry with a suggested approach this slice adopts. The pump's
event seam (0005 **D7**: the session never holds a machine) is load-bearing
and unchanged.

**Goal:** two small debt payments, both user-visible. (1) A turn in flight
can be stopped: `C-g` cancels it — the agent gets `session/cancel`, every
pending permission is answered `cancelled`, and the transcript shows the
turn end. Today the only way to stop a turn is quitting the editor.
(2) `C-x C-f notes/ RET` is refused with a message instead of creating a
buffer named `""` that typed text can never be reached from again.

## 1. The acceptance scenario

Written first, per the template. Two scenarios; the slice ships both.

```text
C-c a                  → echo row shows "Agent: "
type "hold"            → echo row shows "Agent: hold"
RET                    → the agent buffer appears (split) and shows the
                         prompt; the turn stays in flight (the fake agent
                         holds its answer)
C-g                    → echo row shows "Quit"; the agent receives
                         session/cancel; the agent buffer gains
                         "── end turn (cancelled) ──"
C-c a "ping" RET       → a fresh turn runs and answers — the editor and
                         the agent both survived the cancel
```

```text
C-x C-f                → echo row shows "Find file: "
type "notes/"          → echo row shows "Find file: notes/"
RET                    → echo row shows "notes/: empty-basename"; NO new
                         buffer is created; the buffer list is unchanged
```

The first is the ConPTY scenario (`test_shipped_agent.py`, fake ACP agent
over the real wire); the second is pinned in-process at the harness (the
keystroke path is identical to the already-ConPTY-proven find-file accept
— no new ConPTY scenario for it).

## 2. What exists today

- `AcpMachine.cancel()` (`src/drei/acp/machine.py:285`) emits
  `session/cancel` and answers every pending `session/request_permission`
  with `Cancelled`, reusing `resolve_permission`. Fully unit-tested
  (`tests/acp/test_machine.py:411–459`, incl. the phase guard:
  non-`PROMPT_IN_FLIGHT` raises). **Nothing calls it** — its own TODO
  comment says so (TD-2).
- `AbortPendingPermissions` (`src/drei/session.py:956`) closes an open
  choice prompt and drains the permission queue. The pump calls it only on
  child exit (`src/drei/pump.py:302`).
- The pump sees every dispatched command's outcome in `after_command`
  (`src/drei/pump.py:309`) and already acts on two event kinds
  (`AgentPromptSubmitted`, `PermissionDecided`); a `KeyboardQuitEvent`
  falls into `case _: pass` (`pump.py:341`). `pump.phase` (`pump.py:252`)
  exposes the machine phase for the guard.
- `C-g` at a **permission (choice) prompt already denies the request**:
  `MinibufferAbort` with `self._choice` emits
  `PermissionDecided(request_id, Cancelled())` (`session.py:859–865`).
- `C-g` at an **exit prompt** abandons the exit, unconditionally
  (`session.py:866–876`, plan 0018 D5); a permission request queued behind
  the exit is presented when it abandons (plan 0018 D7).
- `C-g` at top level deactivates the mark and echoes `Quit`
  (`session.py:795–799`; row: `C-g` is `keyboard-quit`).
- The transcript already renders the cancel outcome:
  `PromptCompleted("cancelled")` → `── end turn (cancelled) ──`
  (`src/drei/acp/transcript.py:85–89`), and each swept permission renders
  `── permission denied ──` (`transcript.py:93–99`).
- TD-3: `_open_file` derives the buffer name with
  `path.replace("\\", "/").rsplit("/", 1)[-1]` (`session.py:1592`) —
  `""` for a trailing slash — and carries the TD-3 TODO at
  `session.py:1588–1591`. Error tokens are a closed Drei-owned vocabulary
  (`normalize_os_error`, `src/drei/files.py:61`) rendered raw by
  `_message_text` (`src/drei/harness.py:38`): `path: token`.
- The fake ACP agent (`tests/fake_acp_agent.py`) answers every
  `session/prompt` immediately with `stopReason: "end_turn"`; it has no
  held-turn mode and no `session/cancel` handling.

## 3. Design decisions

### D1. The pump reads the trigger out of the outcome — no keymap change

`after_command` gains a third case: on `KeyboardQuitEvent`, if
`self._machine.phase == "PROMPT_IN_FLIGHT"`, call `cancel()`, write the
resulting messages, then `harness.apply(AbortPendingPermissions())` — in
that order (0005 D5: answer the blocked agent first, then clear the UI).
The session's `KeyboardQuit` handling is untouched: the mark still
deactivates and `Quit` still echoes. The phase guard makes every other
`C-g` (no agent, handshake, idle) exactly today's keyboard-quit.

Alternatives: (a) a session-side `CancelTurn` command routed by a new
keymap entry — violates 0005 D7 (the session would have to know what a
turn *is*); (b) the pump intercepting the key before dispatch — breaks
the single ordered input stream and would duplicate the minibuffer gate's
routing. The outcome seam is the one plan 0016 already established for
`PermissionDecided`.

**On screen:** `C-g` during a turn shows `Quit` on the echo row; the agent
buffer gains `── end turn (cancelled) ──` when the agent answers (ACP
0.9.0 requires it to). No new key to learn.

### D2. `C-g` at a permission prompt is already the deny — the second `C-g` cancels

The ambiguity TD-2 names resolves with no new session behavior: the first
`C-g` denies the open request (shipped, `session.py:859–865`); the prompt
closes; the turn is still in flight; the second `C-g` is a top-level
keyboard-quit and D1's wiring cancels the turn. This slice pins the
sequence rather than inventing a chord.

**On screen:** first `C-g` — the prompt closes, `── permission denied ──`
appears; second `C-g` — `Quit` and the turn ends.

### D3. An exit prompt outranks the turn: one `C-g` peels one layer

`C-g` at an exit prompt with a turn in flight abandons the exit **only**;
the turn keeps running and can be cancelled by a `C-g` after the prompt
closes. Slice 18 settled the keystroke's meaning there ("abandons the
whole exit") and layering a turn-cancel onto it would make one key do two
unrelated things — refuse a question about losing work AND kill agent
work. The peeling order is uniform: permission prompt → exit prompt →
turn.

Alternative: exit-prompt `C-g` also cancels — rejected; the user asked
about the exit, not the agent, and the turn remains one `C-g` away.

**On screen:** nothing new — the exit prompt closes as today; the turn is
visibly still running (the agent buffer keeps streaming).

### D4. Re-cancel is idempotent; no "cancel sent" tracking

The phase stays `PROMPT_IN_FLIGHT` until the agent's response arrives, so
a second top-level `C-g` in that window calls `cancel()` again: a
duplicate `session/cancel` notification (benign — it is a notification,
and the permission sweep is already drained) and no state corruption. A
"cancel requested" flag would exist to suppress a harmless duplicate and
would be one more piece of transport state to get wrong.

**On screen:** nothing.

### D5. An empty basename is refused at the boundary, before the filesystem

`_open_file` rejects a path whose normalized basename is `""` with
`OpenFailed(path, "empty-basename")` — before the already-open check and
before `files.read`, so the refusal is deterministic and OS-independent
(a trailing slash is a *name* judgment, not a read result). The echo is
the raw-token form the other `OpenFailed`s use: `notes/: empty-basename`.
No buffer is created; the minibuffer closes as on every accept; buffer
list, MRU, and current buffer are untouched.

Alternatives: (a) reuse `"io-error"` (the directory arm's token) —
dishonest: nothing was read, and on some inputs the read would have
succeeded; (b) a new English `_MESSAGE_TEXT` entry — the OpenFailed
vocabulary renders raw tokens by design (V1 of slice 19), and
`empty-basename` reads clearly as itself; (c) strip the slash and open
`notes` — silently opening a different file than named is worse than
refusing.

**On screen:** `notes/: empty-basename` on the echo row, where today a
`""` buffer silently appears.

## 4. What this slice does NOT do

- **A `New file` echo** for missing-file opens (registry follow-up noted
  in slice 19) — informational, not a failure row; separately tracked.
- **Dired / directory modes** — the deviation family stands; TD-5's
  row only narrows which paths hit the refusal.
- **TermVerify's non-input quiescence marker** — the agent-delivery
  marker gap stays recorded in `test_shipped_agent.py`'s docstring and
  belongs to TermVerify, not drei.
- **TD-7 through TD-10** — separately tracked hardening.
- **Review 0002's owed adversarial pass** — follows this slice, per the
  claim.

## 5. Pins that change

None expected. Evidence: `tests/test_pump.py` has no `cancel`/
`KeyboardQuit` assertion (grep, 2026-07-27); `cancel()`'s machine-level
tests assert the machine, not the trigger; no test opens a trailing-slash
path (the TD-3 hazard is precisely that none did). The fake agent's
default behavior is unchanged — the two existing agent scenarios keep
their fixtures. If V1/V2 find a pin that moves, it lands in this list in
the plan's status amendment, as slice 19 did.

## 6. Owned deviations (parity-registry rows)

- **Widen `find-file on a directory path`:** a trailing slash (empty
  basename) is refused deterministically at the boundary —
  `OpenFailed("empty-basename")`, no filesystem access — where Emacs
  opens dired. Same deviation family (dired out of scope), now
  OS-independent.
- **New row — `C-g` while an agent turn is in flight:** cancels the turn
  (`session/cancel`; pending permissions answered `cancelled`; the
  transcript shows `── end turn (cancelled) ──`). No Emacs equivalent —
  n/a row, like the other agent rows. The peeling order (permission
  prompt deny → exit prompt → turn) is recorded in the same row.
- **Widen the `C-g` is `keyboard-quit` row:** one sentence — during an
  agent turn `C-g` additionally cancels the turn; its editor meanings
  (mark off, `Quit`, prompt abort) are unchanged.

## 7. Implementation order (vertical slices, strict TDD)

1. **V1 — TD-3, the small visible one.** RED: `C-x C-f notes/ RET` →
   `OpenFailed("notes/", "empty-basename")`, no `BufferCreated`, state
   untouched (session) and the echo row shows the refusal (harness).
   Implement D5 (delete the TD-3 TODO with it). Sweep for trailing-slash
   pins (expected: none).
2. **V2 — the pump wiring (D1–D4).** Scripted-loop tests at the pump's
   own level (the design's verification layer 1): `C-g` mid-turn writes
   `session/cancel` and applies `AbortPendingPermissions`; `C-g` with no
   agent / during handshake / idle is a plain keyboard-quit (phase
   guard); first-`C-g`-denies-second-`C-g`-cancels (D2); exit-prompt
   `C-g` does not cancel (D3); a second mid-flight `C-g` re-cancels
   without raising (D4).
3. **V3 — in-process end to end.** `tests/test_agent_end_to_end.py`
   style, fake agent with a held-turn mode: prompt `hold` stays
   unanswered until `session/cancel` arrives, then answers
   `stopReason: "cancelled"`. Assert the wire order (cancel before any
   further prompt) and the transcript line.
4. **V4 — the ConPTY acceptance scenario.** §1's first script in
   `test_shipped_agent.py`: `C-g` mid-held-turn, then read
   `── end turn (cancelled) ──` off the shipped frame (the
   `_settle_until` crank — agent deliveries carry no marker), then a
   fresh `ping` turn proves recovery.
5. **V5 — the records.** TD-2 and TD-3 entries removed from
   `docs/technical-debt.md` (history notes, the TD-4 pattern); the three
   registry rows; README's `C-g`/agent paragraph; design 0005's status
   line (D5 → implemented, closing the record).
6. **V6 — gate.** Full validation suite, both ConPTY agent scenarios,
   coverage ratchet.
7. **V7 — adversarial review → fix → code PR (`Closes #56`) → merge.**

## 8. Risks / open questions

- **Should the cancel deliver a local transcript line immediately**
  ("cancel requested") rather than waiting for the agent's answer?
  Recommendation: **no.** ACP 0.9.0 requires the agent to end the turn
  with `stopReason: "cancelled"`; the transcript renders that; the `Quit`
  echo confirms the keystroke landed. A local line would double-render
  against every compliant agent to cover a misbehaving one. An agent
  that never answers leaves the phase at `PROMPT_IN_FLIGHT` — the editor
  stays usable, the next prompt queues (pump `_send_pending`), and D4's
  re-cancel remains available. Settle at the gate if the reviewer
  disagrees.
- **The fake agent's held-turn mode is test equipment touching two
  suites** (`test_agent_end_to_end.py` and the ConPTY scenario). Kept
  opt-in by prompt text (`hold`) so existing fixtures are byte-identical.
  Not a question — a note for the reviewer.
- **`session/cancel` for a turn whose permission prompt is open** is the
  normal case, not an edge: `cancel()` answers the tracked request, and
  `AbortPendingPermissions` closes the presentation — both already exist
  and are order-pinned by 0005 D5 (answer first, clear second).

## 9. Acceptance criteria

- Both §1 scenarios pass as executable tests: the first as a ConPTY
  scenario against the shipped executable (Windows CI leg), the second
  in-process at session and harness level.
- `AcpMachine.cancel()` is called by shipped code (the pump), proven by
  the wire-order assertion in V3 — the TD-2 gap was precisely that it
  was reachable only from tests.
- TD-2 and TD-3 are removed from `docs/technical-debt.md` with history
  notes; design 0005's status line moves D5 to implemented; the three
  registry rows land with citations that `tests/test_parity_registry.py`
  resolves.
- Full gate green on 3.12–3.14 and both CI OSes; coverage floor held;
  `uv --no-config build` clean.
- Fresh-agent adversarial review before merge; findings fixed or carried
  into the plan status block, per the standing rule.
