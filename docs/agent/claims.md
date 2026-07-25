# Slice claims (multi-agent coordination)

Drei is developed by parallel agents in separate worktrees. To keep two
agents from implementing the same slice, **every slice is claimed by a
GitHub issue before its plan PR exists.**

## Protocol

1. **Sync first** — run `scripts/sync-check.sh` (read-only). It prints
   every claim signal: worktrees, remote branches, open PRs, open `slice`
   issues, committed plans **with each plan's own Status line**, and stale
   branches. A plan whose Status still says `ready` while its slice has
   obviously merged is drift, not a free slice — check git before claiming
   it, and fix the Status in your first commit. It **exits 1** when `gh` is
   missing or unauthenticated: without it the claim signals cannot be read
   at all, and a scan that cannot see claims must not look like a clean
   one. `DREI_SYNC_CHECK_OFFLINE=1` runs the git-only sections anyway,
   loudly — use it to inspect local state, never as the basis for a claim.
2. **Claim** — open a slice issue from the *Slice claim* template. Do not
   start the plan PR without it.
3. **Progress** — the issue body links the plan PR (user gate) and then
   the code PR. The code PR body carries `Closes #<issue>` so the claim
   auto-closes on merge.
4. **Authority** — git/GitHub history is authoritative for what *shipped*;
   slice issues are authoritative for *intent* (claimed but not yet
   committed). When the two disagree (an abandoned claim), close the issue
   with a note rather than silently reclaiming.

## Why issues, not a repo file

A claims file in the repo serializes every parallel slice on one write
hotspot and can't provide atomic check-and-set — your claim isn't real
until its PR merges. Issues are visible to agents and humans in the same
place PRs are reviewed, carry mid-flight scope discussion, and never
produce merge conflicts.

## What the claim does not guarantee (review 0001 finding 17)

Issue creation is **not** check-and-set. Two agents that both see no claim
and open issues seconds apart both believe they hold the slice; "first
issue for a slice number wins" is a clock tie-break by convention, enforced
by nobody. The window is the gap between reading `sync-check` output and
the issue appearing. Practical consequences:

- Re-run `scripts/sync-check.sh` immediately *after* opening the issue. A
  second issue for the same slice number is the collision signal; the
  lower number keeps the claim and the other agent closes theirs with a
  note.
- Treat the plan PR (user-gated) as the real serialization point — no code
  PR precedes it, so a duplicate claim costs a plan, not a slice.
- Do not widen a claim silently. A claim covers the slice number it names.

A genuine check-and-set would need a single writer (a label mutation on a
pre-created issue, or a lock branch with a non-fast-forward push). Not
worth it at the current agent count; recorded so the convention is not
mistaken for a guarantee.
