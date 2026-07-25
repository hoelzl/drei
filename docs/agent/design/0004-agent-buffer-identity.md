# 0004: Agent buffer identity

**Status:** accepted — implemented in full by plan
`0014-agent-buffer-identity.md`, which paid TD-1. D6 (editability) is
deliberately still open and belongs to §A.3.
**Builds on:** `0003-hermes-drei-integration.md`
**Does not revise:** 0001/0002/0003. It supplies a binding 0003 named but never
defined.
**Occasioned by:** adversarial review 0001 finding 5 (`docs/technical-debt.md`
TD-1) — "the agent buffer" is vocabulary with no referent in the code.

## Problem

Design 0003 §Vocabulary defines an **agent buffer** as "a Drei buffer whose
text is the rendered transcript of an ACP session", and parity-registry rows
speak of it as an entity. No such entity exists. `InsertAgentText` and
`apply_session_effects` append to `self.buffer` — whichever buffer is
*focused at the moment the delivery arrives*. Before A.2 that was
indistinguishable from a binding, because there was only one buffer. A.2
shipped `C-x b` and multiple buffers without deciding what a transcript binds
to, which breaks three things at once:

1. **The transcript splits.** Delivery, `C-x b`, delivery → half the
   transcript in one buffer, half in another. The documented fold oracle
   ("a buffer's agent text is the concatenation of every
   `AgentTranscriptUpdated.rendered`") then matches **no** buffer.
2. **Agent text lands in the user's file.** A delivery into a file-visiting
   buffer appends without setting `modified`: the modeline reads clean and a
   later `C-x C-s` writes the agent's transcript into the user's file.
   Cluster A of review 0001 made this *more* visible, not less — the modified
   flag is now derived from the buffer's last-saved text, so an undo after a
   delivery reports the divergence the append itself hid.
3. **Point is stolen.** Every delivery moves point to end-of-buffer, so a
   burst of agent output relocates the cursor of a human mid-edit.

All three are consequences of one missing decision, which is why this is a
design record and not a bug fix: "append to the focused buffer" is a
*semantics* choice, and the alternatives differ in what a transcript *is*.

## Decision

**A transcript binds to an agent buffer whose identity is derived from the
ACP session that produced it, and never from focus.** Concretely:

### D1. One agent buffer per ACP session, created when the session is established

The session owns a binding from ACP session id to `BufferId`. The buffer is
created when the `SessionEstablished` effect is folded — *before* any text
arrives — so it exists, is switchable to, and is displayable while the agent
is still thinking. Name: `*agent*`, and `*agent*<2>`, `*agent*<3>` for
further concurrent ACP sessions, reusing the collision-suffix rule A.2
already ships.

The `*…*` bracketing is Emacs's convention for a buffer that does not visit a
file, and it is introduced here because this is Drei's first such buffer that
a user will actually see. The existing default buffer is named `scratch`
rather than `*scratch*` — a slice-1 shortcut. Renaming it is out of scope
here; it is recorded so the inconsistency is owned, not accidental.

Rejected alternatives:

- **One agent buffer, session-global.** Simpler, but a second ACP session
  would append into the first one's transcript, and the fold oracle would
  again describe no buffer. The binding must be as fine-grained as the thing
  being folded.
- **One buffer per turn.** A transcript spanning a conversation is the
  artifact a user wants to scroll; per-turn buffers would fragment it and
  multiply the no-kill-buffer problem below.
- **Lazy creation at first text.** Costs nothing at first, but means the
  buffer's existence depends on whether the agent said anything, so
  `C-x b *agent*` works or does not depending on timing. Existence should
  depend on the session, which is an event, not on output, which is a race.

### D2. Deliveries name their target; the transcript records it

`DeliverSessionEffects` and `InsertAgentText` gain a target `BufferId` field,
and the events they emit (`AgentTranscriptUpdated`, `AgentTextInserted`)
carry it too. Dispatch never consults focus.

This is the load-bearing half of the decision. Putting the target *in the
command* keeps deliveries in the same shape as every other immutable command
— all inputs explicit — and putting it *in the event* keeps the fold oracle
reconstructible from the transcript alone: the agent text of buffer B is the
concatenation of every `AgentTranscriptUpdated.rendered` whose target is B.
An implicit binding resolved from session state at dispatch time would make
the transcript un-replayable across a rebinding, which is exactly the class
of defect this record exists to remove.

### D3. An agent buffer is a distinct kind, and only agent buffers accept deliveries

Buffers gain a kind: ordinary (file-visiting or scratch) versus **generated**
(produced by an effect, visiting no file). A delivery naming a non-generated
buffer is a **caller bug** and raises, in the same discipline as
`resolve_permission`'s `AcpStateError` — never a silent drop (which would
desync the fold) and never a write into a file buffer (which is hazard 2).

This makes hazard 2 structurally unreachable rather than merely unlikely. It
is worth the extra concept precisely because the alternative — "the pump is
careful" — is the convention that already failed once.

### D4. An agent buffer visits no file

It has no `file_path`; `C-x C-s` on it fails with the `no-file` token that
already exists. The question "should an agent delivery set `modified`?"
therefore does not arise: there is nothing on disk for the flag to describe.
This is a deliberate dissolution rather than an answer — a modified flag on a
buffer with no file is meaningless in Drei's own definition ("clean means the
buffer matches the file"), and inventing a meaning for it would weaken that
definition everywhere else.

Writing a transcript to disk is a real want; it is a `write-file` command,
which Drei does not have. Recorded, not designed here.

### D5. Deliveries follow the tail; they do not steal point

On append, a window whose point was at end-of-buffer **before** the append
moves to the new end; every other window's point stays where it is. This is
`tail -f` semantics and matches what Emacs's comint does for the common case.

The rule is stated over *windows*, not over the buffer, because A.2 made
window point distinct from `BufferValue.point`: a user reading back through
the transcript in one window is not disturbed by output arriving in another
window showing the same buffer. Mark adjustment on the append is unchanged —
the existing insert-adjustment rule applies.

### D6. Editability is not decided here

The existing rules stand: agent deliveries create no undo group, and a user
*can* still type into an agent buffer, after which the live text diverges
from the pure fold of `AgentTranscriptUpdated` events. Both are recorded
parity-registry deviations with the hazard owned explicitly.

Making the agent buffer read-only is design 0003 §A.3 (read-only/generated
buffers) and is the natural follow-up: D3's buffer kind is the hook A.3 needs.
This record deliberately fixes **where** a transcript goes without also
deciding **who may edit it** — bundling them would make one slice carry two
independent decisions, and the binding is the one that is currently broken.

### D7. Lifecycle: transcripts outlive their session

When an ACP session ends the agent buffer survives; a transcript is a record,
and discarding it on completion would destroy the thing the user was reading.
A new ACP session creates a new agent buffer.

Consequence, accepted: with no `kill-buffer` command, agent buffers
accumulate for the lifetime of the editor process. That is a real cost of a
long session and it is the same gap A.2 already recorded (registry:
"kill-buffer — absent"). This record does not add kill-buffer; it notes that
generated buffers make the gap sting sooner.

## Consequences

- **Existing commands and events change shape.** Adding a target field to
  `DeliverSessionEffects` / `InsertAgentText` and to the two events they emit
  touches `tests/test_agent_delivery.py` and the B.7 golden trace. The
  changed pins are the point: `test_fold_cache_reconstructible_from_events`
  must gain a buffer switch between deliveries, since its silence about that
  case is what let the defect ship.
- **The fold oracle becomes per-buffer.** Stated as: for every buffer B, B's
  agent text equals the concatenation of `rendered` over every
  `AgentTranscriptUpdated` targeting B, in transcript order. Strictly
  stronger than today's oracle, and expressible as a property over generated
  multi-buffer histories.
- **A new parity-registry row.** "Agent buffer is generated, visits no file" —
  n/a for Emacs comparison in the usual sense, but the registry is where Drei
  records owned deviations and the `*…*` naming convention belongs there.
- **The §C pump gains a prerequisite it can rely on.** The pump
  (`0005-acp-pump.md`) resolves a target once per ACP session and passes it
  with every delivery; it never asks what is focused.

## What this record does not decide

The buffer-kind representation (a field on `Buffer`, a separate registry, or
a subtype), whether generated buffers appear in `C-x b` completion, the
`write-file` command that would let a transcript be saved, and the read-only
enforcement mechanism (§A.3). Consistent with 0001 and 0003, this record
commits the binding and the boundaries; the slice that implements it chooses
the representation from the tests it writes.

## Open questions

- **Naming with several agents.** `*agent*<2>` is deterministic but tells the
  user nothing about which agent or which working directory. *Trigger:*
  before a slice supports more than one concurrent ACP session; a name
  derived from the session's `cwd` is the obvious candidate and needs a
  collision rule of its own.
- **Diagnostics.** 0003 §C.9 wants the child's stderr surfaced as a
  diagnostics buffer. That is a second generated buffer, and whether it is one
  per session or one per editor is undecided. *Trigger:* the launcher slice
  (`0005-acp-pump.md` D6).
