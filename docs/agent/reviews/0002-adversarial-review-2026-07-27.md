# Adversarial review 0002 — post-review-0001 range (2026-07-27)

**Status:** findings consolidated and re-verified; **triage proposed, awaiting
owner decision** (see § Triage). No remediation has started.
**Baseline:** commit `5d2fa1d` (slice 20 merged), working tree clean, full
suite green (787 passed, 5 SDK-gated skips; the ConPTY tier green separately).
**Range:** `cabb31a..5d2fa1d` — review-0001 remediation clusters A–E, design
records 0004/0005, and slices 14–20 (agent buffer identity; input events and
resize; the ACP pump; `C-g` keyboard-quit; save-buffers-on-exit; the
echo-message mechanism; turn cancellation; trailing-slash refusal).
**Method:** three independent adversarial reviewers — (1) implementation audit
against the project's own rules, (2) design/documentation audit, (3) executable
bug hunt (39 scratch probe tests, deleted afterwards) — coordinated by a
session that **independently re-verified every major finding by execution**
(probe outputs quoted below). Line numbers refer to `5d2fa1d`.

Each slice in the range had its own per-PR fresh-agent review; this pass
looked for what those could not see: cross-slice interactions, accumulated
drift, and global invariants. Known-paid debts (TD-1…TD-6, TD-11, TD-2, TD-3)
were out of scope; open debts TD-7…TD-10 were verified accurate, not
re-litigated.

A later session should resume from **§ Triage** below.

---

## Findings

### Critical — user-facing

**1. Non-user commands break the focused buffer's last-command bookkeeping: a
resize or an agent-side dispatch flips undo into redo and splits the
kill-append chain. CONFIRMED (repro executed by lane 1; independently
re-executed by the coordinator).**
`session.py:984` computes `intervened = any(not isinstance(e, Message) for e
in events)` and applies it to the *commit* buffer's state (`session.py:976-977`):
kill chain (`991-996`), yank-pop (`998-1006`), and undo descent (`1029-1030`).
Three in-range commands emit non-`Message` events while committing against the
*focused* buffer, and none of them is a user command:

- `ResizeFrame` (`session.py:786-794`, slice 15). Coordinator repro: type
  `abc`, `C-/` ×2 (descending, buffer `'a'`), `harness.resize(100, 30)`,
  `C-/` → buffer becomes `'ab'`. **The user pressed undo and the buffer moved
  forward** — review-0001 finding 2's exact failure class (rated critical
  there), now driven by terminal timing. And: `C-k`, resize, `C-k` on
  `"one\ntwo\n"` yields ring `('\n', 'one')` instead of `('one\n',)`;
  interactive Emacs appends (a resize runs no command and never touches
  `last-command`).
- `DisplayBuffer` (`session.py:770-772`, slice 16, pump-dispatched on session
  bind) — same undo flip; what the next `C-/` does depends on when the
  *agent's handshake* lands.
- `PromptPermission` (`session.py:948-955`) — pre-existing command, but slice
  16 made it reachable in production for the first time; same flip.

No plan, test, or registry row examines the interaction, so it is not an owned
deviation. AGENTS.md's non-negotiable ("editor semantics deterministic and
independent of terminal, clock, … network") is violated in effect: replay is
well-defined, but the user's editing semantics now depend on terminal and peer
timing. Deliveries were given exactly the right treatment (pinned target
state, `session.py:971-975`); these three commands were not.

### Major

**2. An agent launch failure is invisible to the user. CONFIRMED (repro
executed by lane 1; independently re-executed by the coordinator).**
`pump.py:260-263` — `submit()` returns early on spawn failure, silently
dropping the prompt text; `pump.py:376-387` logs the normalized token to
`*agent-log*`; `pump.py:490-495` (`_log`) mints that buffer but never displays
it (only `_bind` calls `DisplayBuffer`, for the transcript). Coordinator repro
with a `FileNotFoundError` port through the loop's real wiring (`C-c a`,
`hello`, `RET`, `pump.after_command`): buffers `('scratch', '*agent-log*')`,
focused window still `scratch`, echo row blank — the only place that knows why
nothing happened is a buffer the user was never shown. This is precisely the
fail-visible class review 0001 finding 19 / TD-4 was paid to close,
reintroduced at the launch boundary; design 0005 D6 ("Drei must remain a
usable editor on a machine with no `hermes` installed") makes this the
*first-run* path, not a corner. `tests/test_pump.py:688-694` pins the
diagnostics landing and nothing about visibility — the silence is unexamined,
not deliberate.

**3. The machine has no liveness/recovery path once the peer misbehaves: three
malformed-response arms wedge the phase forever, and the held-prompt queue
hides the wedge from the user. CONFIRMED (three repros executed by lane 3;
arms 1–2 independently re-executed by the coordinator).**
One defect class, three arms:

- *Malformed `stopReason` in a successful `session/prompt` response.*
  `machine.py:536-545` clears the in-flight entry (`490-491`) but the
  bad-`stopReason` arm returns without restoring the phase, unlike the
  `ResponseError` branch above it which has explicit phase recovery
  (`497-502`, review-0001 B1). Coordinator repro: after
  `{id: prompt_id, result: {stopReason: "not-a-stop-reason"}}` →
  `phase == "PROMPT_IN_FLIGHT"` with `in_flight_outgoing == {}`. Nothing can
  advance the phase (a later response for the same id is "unknown/duplicate");
  pump-level `submit("second")` is silently held in `_pending_prompts`
  forever. Forward-compat hazard, not just hostility: a newer agent emitting
  a stop reason outside 0.9.0's five-value literal (`machine.py:55-57`) wedges
  every drei client it talks to.
- *`session/new` success missing `sessionId`.* `machine.py:521-530` returns a
  `ProtocolError` but leaves the phase `READY`; the pump sends `session/new`
  exactly once (on `Initialized`, `pump.py:418-422`) and never retries.
  Coordinator repro: `{id: new_id, result: {}}` → `phase == "READY"`,
  `ProtocolError`; the pump's held prompts stay held forever.
- *Wrong/duplicate response id.* `machine.py:483-489` records the protocol
  error and moves on — correct in isolation — but composes with any agent
  that answers `session/prompt` under a mismatched id (or never answers after
  a `session/cancel`) into the same wedge: repeated `C-g` re-sends
  `session/cancel` into the void.

The editor offers no kill-agent/respawn command, and the user's held prompts
produce zero visible signal in all three arms.

**4. The records still declare turn cancellation unwired in the two reference
documents a future slice is most likely to trust. CONFIRMED (lane 2;
coordinator spot-checked).**
Slice 20 amended the stale sentence in design 0005 D5's body (plan 0020
honesty entry 2) but missed two more sites of the same falsification:

- `docs/knowledge/emacs-parity.md:116` — the row "Turn cancellation answers
  every pending permission" still ends "*Nothing calls the sweep yet — the §C
  pump owns driving a cancel (design 0005 D5)*", contradicted two rows down by
  slice 20's own new row (`:126`) and by `pump.py:343-357`.
- `docs/knowledge/architecture.md:106-112` — "What remains unwired is turn
  **cancellation** … (`docs/technical-debt.md` TD-2)" — TD-2 no longer exists
  (its paid-and-removed note is `technical-debt.md:131-140`, so the pointer
  dangles), and the choice the paragraph says the cancellation slice "owns"
  was made and is pinned (`0005-acp-pump.md:164-177`, `TestTurnCancellation`).

### Minor

**5. TD-3's payment is incomplete: the startup path still mints the
unreachable `""` buffer. CONFIRMED (repro executed, lane 1).**
`cli.py:46-52` treats a `FileNotFoundError` on the startup path as "missing
file → empty buffer", and `harness.py:64-66` derives the buffer id from the
basename: `drei missing-dir/` yields a buffer literally named `""` — the
hazard TD-3 named verbatim. Slice 20 refused the empty basename at the
find-file boundary only (`session.py:1584-1587`); `technical-debt.md:142-147`
now records the class as *paid*, which is falsified by the second boundary.

**6. `DisplayBuffer` on a too-small frame is not the "silent no-op" its
contract claims. CONFIRMED (repro executed, lane 1).**
`commands.py:273-275` ("A frame too small to split is a silent no-op") and
design 0005 D6 ("shows nothing and breaks nothing") vs `_display_buffer` →
`_split_window` (`session.py:1430-1434` → `1398-1401`): at frame height < 7
the dispatch records `Message("too-small-for-splitting")` — a message about a
command the user never issued, pinned into the transcript. Invisible on screen
only because `harness.apply` (`harness.py:143-159`) doesn't recompute the
echo.

**7. The initial frame size is not in the event record; replay cannot
reproduce a pre-resize split decision. CONFIRMED (traced, lane 1).**
`session.py:438-447` injects `frame_size` at construction with no event; the
first `FrameResized` only records *changes* (`session.py:792-793`).
`ResizeFrame`'s own docstring states the derivability rule it serves
(`commands.py:149-151`) — and the initial size is exactly such an input, now
consumed by two in-range decisions: the `C-x 2` gate (`session.py:1398-1401`)
and slice 16's `DisplayBuffer` split (`session.py:1430-1434`). Pre-range in
origin (plan 0012), sharpened in-range.

**8. Design 0004's status header still lists D1's trigger as open. CONFIRMED
(lane 2).** `0004-agent-buffer-identity.md:5-8` and `:77-78` say "nothing
folds `SessionEstablished` into a creation yet"; the pump has done so since
slice 16 (`pump.py:423-424`, `:440-441`). A status header is exactly what a
slice-planning agent scans for open work.

**9. Design 0005's open questions list a settled question as open. CONFIRMED
(lane 2).** `0005-acp-pump.md:327-329` — "Which key sends a prompt. No key
binds an agent command today." — `C-c a` has bound it since slice 16
(registry `:123`). The neighboring `C-g` bullet got a "Resolved by slice 17"
strike-through; this one got none.

**10. README's ConPTY scenario parenthetical omits the scenarios slices
15/16/19/20 added. CONFIRMED (lane 2).** `README.md:13` reads as the
exhaustive evidence list; resize (`test_shipped_terminal.py:228`), region kill
(`:374`), exhausted-undo (`:443`), inert navigation keys (`:635`), and the
three agent scenarios (`test_shipped_agent.py:155,199,249`) go unnamed.

**11. README's minibuffer-gate claim contradicts the delivery-class
exemption. CONFIRMED (lane 2).** `README.md:13` — "While the minibuffer is
open only its four commands act (everything else is a silent no-op)" — vs
`architecture.md:152-155` and registry `:110` (`TestMinibufferDoesNotSwallowDeliveries`).
Pre-slice-14 wording never updated.

### Nit

**12. Registry "row N" references rot: rows have no stable IDs, and slice
20's inserted row shifted every downstream number. CONFIRMED (systemic, lane
2).** `tests/test_harness.py:70` cites "row 134" (now `:136`);
`tests/test_harness.py:196` cites "rows 66/68/72/80/98" (98→99);
`tests/test_exit_prompt.py:183` cites "row 92" (now `:93`); plan 0019 `:12`'s
"registry row 126" likewise. The machine check covers only backticked test
names; positional row numbers in prose are unchecked and already drifting.

**13. TD-2's paid note misstates design 0005's decision count. CONFIRMED
(lane 2).** `technical-debt.md:134` — "the last of design 0005's five items" —
0005 has seven decisions D1–D7; "five" is a garbled inheritance from the
removed entry's "the fifth item design 0005 lists" (i.e., D5).

**14. `InsertAgentText` docstring still claims point always moves to
end-of-buffer. CONFIRMED (traced, lane 1).** `commands.py:228` predates
tail-follow; `_append_agent_text` (`session.py:1380-1383`) moves point only
from the tail. The sibling `DeliverSessionEffects` docstring was updated; this
one wasn't.

**15. Shipped code and the parity registry cite "review 0002 finding N" —
which, until this document, resolved nowhere. CONFIRMED (traced, lane 1).**
`session.py:481` ("review 0002 finding 10"), `emacs-parity.md:128` ("review
0002 finding 3"), `emacs-parity.md:133` ("review 0002 round 2 finding 2").
Slices 18–19's per-PR review rounds left no citable document, and the invented
numbering did not match this review's. See § The dangling citations below.

**16. Parked codec frames are lost if the child dies before the next
`receive`, and transcript effects render out of order. CONFIRMED (repro
executed, lane 3).** `pump.py:_drain` + `codec.py:60-74`: one
`receive(completion_frame + b"garbage\n")` delivers only the `ProtocolError`;
the parked completion flushes on the *next* `receive()` (even `b""`), so the
transcript renders "protocol error" *before* "── end turn ──"; if the garbage
was the child's last output, `exited()` → `_reset()` discards the parked
frames — a turn completion never recorded. The codec's no-loss docstring
(`codec.py:46-50`) is true only across calls, not across child death.

**17. Out-of-turn permission requests are accepted. CONFIRMED (repro executed,
lane 3; flagged as by-design).** `machine.py:631` admits
`session/request_permission` in `SESSION_ACTIVE`; a request arriving *after*
its turn completed opens a live choice prompt for a dead turn and resolves
normally. Fail-closed and survivable, but the phase gate's docstring ("only
meaningful inside a live session") is broader than ACP's "during a turn".

## Held under attack (coverage evidence)

- **Cancellation composition (TD-2/D5):** `KeyboardQuitEvent` is genuinely
  top-level-only (`harness.py:180-181`); the phase guard makes every non-turn
  `C-g` a plain quit; the peeling order holds; a cancelled turn's
  `PromptCompleted` frees the held-prompt queue; re-`C-g` re-sends the
  idempotent notification; dead child mid-cancel logs and does not raise;
  `close()` with a turn in flight and an unanswered permission terminates
  cleanly. Lane 3's semantics note — `C-g` at an open *permission* prompt
  only denies (the turn is the second `C-g`) — is slice 20's pinned and
  recorded design (registry `:126`, 0005 D5 banner), not a finding.
- **D7 seam:** the session holds no `AcpMachine`; all pump triggers are read
  off key-command outcomes at the single call site (`terminal.py:382`).
- **Determinism sweep:** no `os.environ`/`time`/`random`/`datetime` reads in
  `src/` outside ports/adapters; `test_process_purity.py` correctly widened
  to the streaming port.
- **Exit sequence vs permission queue vs message notes:** queue drains on
  every abandon/refuse path and only there (`session.py:1157-1167`);
  `AbortPendingPermissions` leaves exit/text prompts untouched; the
  `SaveFailed` note rides the next prompt and is cleared per command.
- **Codec/machine robustness (review-0001 cluster C holds):** garbage lines
  preserve valid frames before/after; chunk-split multibyte chars; invalid
  UTF-8 → `AcpDecodeError`; duplicate inbound permission id → one prompt,
  clean resolution; unknown/pre-session updates → `ProtocolError`.
- **Trailing slash:** `/`, `//`, `foo/`, `foo//`, `C:\`, `./`, `../`, `\` all
  refused as `empty-basename`, no buffer minted.
- **Records spot-checks:** hollow-citation checks across the slice 17–20
  registry rows found every cited test pinning its row's claim; plan honesty
  blocks 0019/0020 match the git history; TD-7…TD-10 markers, line refs, and
  mechanisms accurate; AGENTS.md's Validation block matches pre-commit and CI
  exactly.
- **Deliveries/undo/resize:** undo across a delivery into a non-focused
  buffer leaves the delivery intact; tail-follow in the focused agent buffer;
  turn completing underneath an open choice prompt releases the held prompt
  and the stale choice resolves by id; resize-below-split during deliveries
  caps rows non-destructively and restores on grow; 0×0/1×1 resizes render.

## The dangling citations (finding 15)

The three pre-existing citations anticipated this document with numbers from
slices 18/19's internal review rounds. Rather than renumber this review to
match invented numbers, the triage should re-point each citation at its
durable record:

- `emacs-parity.md:128` "review 0002 finding 3" → the answer-set row itself
  (`:131`) is the durable record; cite the row.
- `emacs-parity.md:133` "review 0002 round 2 finding 2" → the gate-shape row
  (`:132`); cite the row.
- `session.py:481` "review 0002 finding 10" → identify the slice-18/19 review
  observation it meant (the `intervened`/message-bookkeeping design note) and
  cite the design record or TD entry that owns it.

## Triage (proposed — owner decides)

**Cluster A — last-command bookkeeping (finding 1).** Behavior slice. The
`intervened` computation must key on *user command* boundaries, not on event
shapes: resize, peer dispatches, and prompt presentations run no command in
Emacs's sense and must not touch kill-append, yank-pop, or undo descent.
Design question to answer in the slice: how the dispatch path marks a command
as user-issued (a command classification, or moving the bookkeeping to the
key-command path only). Same failure class as review 0001 finding 2 — treat
with the same urgency.

**Cluster B — launch failure must be visible (finding 2).** Behavior slice
(or folded into cluster A's slice if scoped tight). Design 0005 D6's spirit:
the first-run path must not fail silently. Options: `DisplayBuffer` the
`*agent-log*` on spawn failure, or echo the normalized token. Needs a
decision, then pins.

**Cluster C — machine liveness (finding 3).** Behavior slice. Phase recovery
on malformed success responses (mirroring the `ResponseError` recovery at
`machine.py:497-502`), plus a design question: what un-wedges a peer that
never answers (kill-agent command? a reset effect? documented as
impossible?). Forward-compat arm (unknown `stopReason`) argues for failing
*open* — log the unknown reason, complete the turn — rather than wedging.

**Cluster D — records sweep (findings 4, 8, 9, 10, 11, 13, 14, 15).** Cheap,
mechanical, no behavior: amend the two falsified "unwired" sites (finding 4),
the two stale design-record status surfaces (8, 9), README's two clauses (10,
11), the paid note's count (13), the `InsertAgentText` docstring (14), and
re-point the three dangling citations (15, per the mapping above). Proposed as
one docs commit riding this review's own PR.

**Deferred — proposed tech-debt entries (owner confirms):**

- *TD-new-1 (finding 5):* the startup path's `""` buffer — TD-3's residue;
  either re-open TD-3 or a new entry pointing at `cli.py:46-52`.
- *TD-new-2 (finding 6):* `DisplayBuffer`'s false "silent no-op" contract —
  either suppress the message for pump-dispatched splits or amend the
  contract.
- *TD-new-3 (finding 7):* the initial frame size is not in the event record —
  replay gap predating the range, sharpened by two in-range consumers.
- *TD-new-4 (finding 16):* parked codec frames lost on child death; transcript
  effect reordering across a malformed line.
- *TD-new-5 (finding 17):* out-of-turn permission requests accepted — the
  phase gate is broader than ACP's "during a turn"; decide and pin.
- *Process note (finding 12):* registry row numbers are unstable references;
  a future slice should give rows stable anchors (or ban positional citations
  in prose) rather than renumbering after every insert.

**Remediation order:** cluster A first (critical, user-facing semantics), then
B and C (both are design-question slices; B is smaller), D with this PR.
The termverify 0.1.1 migration (issue #58) is unblocked and independent; it
can interleave anywhere after cluster A.
