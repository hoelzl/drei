# Slice plan template

Copy this to `docs/agent/plans/NNNN-<slug>.md` and fill it in. Delete the
guidance in each section; keep the headings.

**Do not put this file in `docs/agent/plans/`.** `scripts/sync-check.sh` globs
that directory and reports each file's `**Status:**` line, so a template there
would show up as a statusless plan in every claim scan.

## What a plan is for

The plan PR is a **user gate**, and it is the only review that can catch a
feature that is *missing*. The adversarial review at the end of a slice reads
the code that exists; it cannot tell you that nothing puts the result on
screen. So the plan is aimed at scope and judgment calls, not at defects.

Two failures are worth naming because both have happened:

- **Plan 0016** specified the transport in detail and the user-visible outcome
  barely at all. The agent transcript was built, and nothing displayed it — a
  gap found only when the end-to-end scenario could not observe its own
  subject, long after the code was written.
- **Plan 0015** reached the gate with two open judgment calls, and the reviewer
  supplied a *third* question it had not asked (what happens to an
  already-split frame that shrinks). That became the slice's D7.

Both times the gate's value was in user-visible behavior. Sections 1 and 3
below exist because of the first failure; section 8 exists because of the
second.

Write for a reader who has **not** read the design record. Prefer a short plan
that answers hard questions to a long one that restates decided ones: material
already settled in `docs/agent/design/` belongs there, cited, not copied.

---

# Nth slice: <title> (<design section>)

**Status:** ready (issue #N).

> Parsed by `scripts/sync-check.sh` — keep `**Status:**` as the first bold
> field and lead with one word: `ready`, `implemented`, or `merged`. Update it
> in the first commit of the slice that changes it; a plan still saying `ready`
> after its slice merged invites a re-claim of shipped work.
>
> When the slice lands, amend this block with what the plan got **wrong**.
> That is the record's main long-term value: every plan so far has been wrong
> about something, and the corrections are how the next one gets better.

**Architecture gate:** which design record and which decisions in it. If this
slice needs a decision no record has taken, say so — that is a signal the
design record should come first.

**Goal:** one paragraph. What can a user do after this slice that they could
not do before? If the honest answer is "nothing yet", say that explicitly and
justify it — a slice with no user-visible outcome needs a reason, because
`AGENTS.md` asks for vertical behavior slices rather than framework layers.

## 1. The acceptance scenario

**Required, and written first.** A keystroke-level script of the thing the
slice delivers, ending in an assertion someone could fail:

```text
press C-c a          → echo row shows "Agent: "
type "ping"          → echo row shows "Agent: ping"
press RET            → ...
                     → then WHAT appears, and WHERE?
```

Write the last line before writing anything else in this document. Plan 0016
could not have been written past that arrow without noticing it had no answer.
"The answer is rendered into an agent buffer" is not an answer — it is true of
a buffer nobody can see.

This is the same discipline the code follows (failing test first), applied one
level up. It should be recognisable as the end-to-end test the slice will
ship.

## 2. What exists today

The delta, nameable. Cite files and line numbers. This is what makes the plan
reviewable by someone who has not read the subsystem, and it is where a wrong
premise gets caught cheaply — plan 0015 asserted `C-x 1` was unbound when it
was bound, and the mistake survived into a decision's rationale.

## 3. Design decisions

One `### Dn.` per decision, each with:

- **The alternatives**, and why this one. A decision with no alternative is a
  fact — put it in *What exists today* instead.
- **On screen:** what this changes for the user, or the single word `nothing`.
  **Required on every decision**, including the transport ones where the
  honest answer is `nothing`. The signal to watch for is a plan where *every*
  decision says `nothing`: that is a plan with no user-facing outcome, which
  is usually a framework layer wearing a slice's clothes.

## 4. What this slice does NOT do

Name the neighbouring work and why it is deferred — especially anything a
reader would reasonably expect to arrive with this. If a deferral is blocked on
an open question, say which.

## 5. Pins that change

Existing tests whose assertions this slice invalidates, and what each should
assert instead. A pin that changes silently is a regression nobody noticed;
listing them here makes the change a decision rather than an accident.

## 6. Owned deviations (parity-registry rows)

Every intentional divergence from GNU Emacs this slice introduces, in the form
it will take in `docs/knowledge/emacs-parity.md`. Deviations discovered during
implementation get added there too — this section is the ones known in advance.

## 7. Implementation order (vertical slices, strict TDD)

Numbered V-steps, each independently testable.

**Order them so the user-visible thing comes first.** Not the transport, not
the port, not the queue: the step that puts something on screen, faked below
the seam if it has to be. Plan 0016 built port → queue → pump → key → screen,
and the missing display decision stayed hidden until the last step; had V1
been "`C-c a` shows something", it would have surfaced immediately. Building
bottom-up also *is* the speculative-framework-layer failure `AGENTS.md` names,
even when every layer turns out to be needed.

The last step is always: adversarial review → fix → code PR (`Closes #N`) →
merge.

## 8. Risks / open questions

The section the gate exists for. State each open question with the options and
your recommendation, so the answer is one word rather than an essay. Resolve
them in place when they are settled — strike the question, keep the answer,
and say who decided.

If a question would change what gets built, it belongs here even when you
think you know the answer.

## 9. Acceptance criteria

Falsifiable statements, including the standing ones: full gate green on
3.12–3.14 and both CI OSes, coverage floor held, and any technical-debt entry
this slice pays, re-scopes, or adds.
