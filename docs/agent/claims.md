# Slice claims (multi-agent coordination)

Drei is developed by parallel agents in separate worktrees. To keep two
agents from implementing the same slice, **every slice is claimed by a
GitHub issue before its plan PR exists.**

## Protocol

1. **Sync first** — run `scripts/sync-check.sh` (read-only). It prints
   every claim signal: worktrees, remote branches, open PRs, open `slice`
   issues, committed plans, stale branches.
2. **Claim** — open a slice issue from the *Slice claim* template. The
   issue is the atomic claim: first issue for a slice number wins. Do not
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
until its PR merges. Issues are atomic at creation, visible to agents and
humans in the same place PRs are reviewed, carry mid-flight scope
discussion, and never produce merge conflicts.
