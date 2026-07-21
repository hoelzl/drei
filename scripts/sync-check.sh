#!/usr/bin/env bash
# sync-check.sh — one-shot multi-agent coordination scan for drei.
#
# Read-only. Prints every signal an agent should check before claiming a
# slice: live worktrees, remote branches, open PRs, claimed slice issues,
# committed plans, and stale remote branches whose PR already merged.
#
# Usage: scripts/sync-check.sh
# Requires: git, gh (authenticated). Missing gh degrades that section.

set -u
cd "$(git rev-parse --show-toplevel)" || exit 1

section() { printf '\n== %s ==\n' "$1"; }

section "Worktrees (live, possibly uncommitted work)"
git worktree list

section "Remote branches"
git ls-remote --heads origin | sed 's|refs/heads/|  |'

section "Open PRs"
if command -v gh >/dev/null 2>&1; then
    gh pr list --state open --limit 20 \
        --json number,title,headRefName \
        --template '{{range .}}  #{{.number}}  {{.headRefName}}  {{.title}}{{"\n"}}{{end}}'
else
    echo "  (gh not available — skipped)"
fi

section "Claimed slices (issues labeled 'slice', open)"
if command -v gh >/dev/null 2>&1; then
    gh issue list --label slice --state open --limit 30 \
        --json number,title,labels \
        --template '{{range .}}  #{{.number}}  {{.title}}{{"\n"}}{{end}}' \
        || echo "  (no 'slice' label yet, or query failed)"
else
    echo "  (gh not available — skipped)"
fi

section "Committed slice plans (docs/agent/plans/)"
git log --oneline -15 -- docs/agent/plans/ | sed 's/^/  /'
ls docs/agent/plans/ | sed 's/^/  /'

section "Stale remote branches (PR merged, branch not deleted)"
if command -v gh >/dev/null 2>&1; then
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
    echo "  (gh not available — skipped)"
fi

printf '\nClaim rule: no plan PR without a slice issue first (see\n'
printf '.github/ISSUE_TEMPLATE/slice-claim.md). git/GitHub state is\n'
printf 'authoritative for what shipped; issues are authoritative for intent.\n'
