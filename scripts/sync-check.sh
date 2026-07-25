#!/usr/bin/env bash
# sync-check.sh — one-shot multi-agent coordination scan for drei.
#
# Read-only. Prints every signal an agent should check before claiming a
# slice: live worktrees, remote branches, open PRs, claimed slice issues,
# committed plans, and stale remote branches whose PR already merged.
#
# Usage: scripts/sync-check.sh
# Requires: git, gh (authenticated). Without a usable gh the scan cannot see
# claims or open PRs, so it FAILS (exit 1) rather than passing blind; set
# DREI_SYNC_CHECK_OFFLINE=1 to proceed deliberately without that visibility.

set -u
cd "$(git rev-parse --show-toplevel)" || exit 1

section() { printf '\n== %s ==\n' "$1"; }

# Preflight (review 0001 finding 17). A missing or unauthenticated gh used to
# print "skipped" and exit 0 — a clean run then read as "no one has claimed
# this slice" when it actually meant "nothing was checked". Claim visibility
# is the whole point of the scan, so its absence is a failure. Runs before any
# network call so the failure is immediate.
gh_usable=1
if ! command -v gh >/dev/null 2>&1; then
    gh_reason="gh is not installed or not on PATH"
    gh_usable=0
elif ! gh auth status >/dev/null 2>&1; then
    gh_reason="gh is installed but not authenticated (run: gh auth login)"
    gh_usable=0
fi

if [ "$gh_usable" -eq 0 ]; then
    if [ "${DREI_SYNC_CHECK_OFFLINE:-}" = "1" ]; then
        printf '!! DREI_SYNC_CHECK_OFFLINE=1: %s.\n' "$gh_reason"
        printf '!! Running with no claim visibility — open PRs and claimed\n'
        printf '!! slice issues are NOT checked. Do not claim a slice on the\n'
        printf '!! strength of this run.\n'
    else
        printf 'sync-check FAILED: %s.\n' "$gh_reason" >&2
        printf 'Claimed slices and open PRs cannot be read, so this scan\n' >&2
        printf 'cannot tell you whether a slice is free. Authenticate gh, or\n' >&2
        printf 're-run with DREI_SYNC_CHECK_OFFLINE=1 to accept that risk\n' >&2
        printf 'deliberately.\n' >&2
        exit 1
    fi
fi

section "Worktrees (live, possibly uncommitted work)"
git worktree list

section "Remote branches"
git ls-remote --heads origin | sed 's|refs/heads/|  |'

section "Open PRs"
if [ "$gh_usable" -eq 1 ]; then
    gh pr list --state open --limit 20 \
        --json number,title,headRefName \
        --template '{{range .}}  #{{.number}}  {{.headRefName}}  {{.title}}{{"\n"}}{{end}}'
else
    echo "  !! NOT CHECKED ($gh_reason)"
fi

section "Claimed slices (issues labeled 'slice', open)"
if [ "$gh_usable" -eq 1 ]; then
    gh issue list --label slice --state open --limit 30 \
        --json number,title,labels \
        --template '{{range .}}  #{{.number}}  {{.title}}{{"\n"}}{{end}}' \
        || echo "  (no 'slice' label yet, or query failed)"
else
    echo "  !! NOT CHECKED ($gh_reason)"
fi

section "Committed slice plans (docs/agent/plans/)"
git log --oneline -15 -- docs/agent/plans/ | sed 's/^/  /'

# Each plan's own Status, not just its filename (review 0001 finding 12: six
# plans still said "ready"/"PR pending" after merging, and AGENTS.md points
# agents at plan status to find current work — a stale one invites a
# re-claim of a shipped slice). Only the first clause is shown; the rest of
# a Status line is architecture-gate rationale.
for plan in docs/agent/plans/*.md; do
    [ -e "$plan" ] || continue
    status=$(sed -n 's/^\*\*Status:\*\* *//p' "$plan" | head -1 \
        | sed -e 's/ *—.*$//' -e 's/[[:space:]]*$//')
    if [ -z "$status" ]; then
        status="!! NO STATUS LINE"
    elif [ "${#status}" -gt 48 ]; then
        status="${status:0:47}…"
    fi
    printf '  %-44s %s\n' "$(basename "$plan")" "$status"
done

section "Stale remote branches (PR merged, branch not deleted)"
if [ "$gh_usable" -eq 1 ]; then
    merged_branches=$(gh pr list --state merged --limit 30 \
        --json headRefName --jq '.[].headRefName')
    stale=0
    while IFS= read -r branch; do
        if git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
            echo "  $branch (merged PR, branch still on origin)"
            stale=1
        fi
    done <<<"$merged_branches"
    [ "$stale" -eq 0 ] && echo "  (none)"
else
    echo "  !! NOT CHECKED ($gh_reason)"
fi

printf '\nClaim rule: no plan PR without a slice issue first (see\n'
printf '.github/ISSUE_TEMPLATE/slice-claim.md). git/GitHub state is\n'
printf 'authoritative for what shipped; issues are authoritative for intent.\n'
