---
name: "Slice claim: N — <slug>"
about: Claim a slice before drafting its plan PR (multi-agent coordination)
title: "Slice N: <slug>"
labels: ["slice"]
assignees: []
---

## Claim

- **Slice number:** N
- **Design-record section:** e.g. design 0003 §B.7
- **Base commit:** `<sha of origin/main at claim time>`

## Scope

One or two sentences: what this slice implements, and which neighbouring
slices it deliberately does NOT touch (name their owning issue if claimed).

## Links (fill in as the slice progresses)

- Plan PR: (user gate — user reviews/merges)
- Code PR: (adversarial review + CI → assistant merge)

<!-- Lifecycle: open this issue to claim the slice; labels move
claimed → in-progress when the plan PR opens; the code PR body carries
"Closes #<this issue>" so the claim auto-closes on merge. Run
scripts/sync-check.sh before claiming to confirm the number is free. -->
