# 001: Live editor state architecture

**Status:** in progress — preliminary experiment complete, no verdict yet

## Question

Given Drei's intended scope, when realistic editor identity and update patterns are exercised, which live-model architecture provides deterministic replay and immutable evidence without imposing unacceptable complexity, indirection, edit cost, or extension ergonomics?

## Compared models

| Model | Working hypothesis | Principal risk |
| --- | --- | --- |
| Fully persistent | Every command returns a new live-model root; stable IDs replace object references | Whole-graph/path copying and pervasive handle resolution leak into editor and extension design |
| Controlled mutable | Stable live objects own mutable text/index/history containers; localized mutation occurs only inside a serialized command boundary; evidence values remain immutable | Hidden mutation or poorly recorded external delivery breaks replay |
| Hybrid | Stable identity shells transactionally replace persistent text/history/mode-data roots; commands, events, and observations are immutable | Two ownership models or pervasive value replacement add complexity without useful sharing |

Text storage is treated as a separate dimension. The preliminary experiment implements only fully persistent and controlled-mutable line-table models; the distinct hybrid model remains pending. It tests copying the persistent line table versus replacing one line in place, not all three alternatives.

## Evidence informing the cases

GNU Emacs exposes buffers, markers, windows, overlays, and processes as objects with identity. Markers and overlay boundaries move with edits; windows form trees and can retain identity across splits; each buffer carries a major mode and buffer-local variables; asynchronous processes deliver output through process objects. Relevant manual sections:

- https://www.gnu.org/software/emacs/manual/html_node/elisp/Buffer-Basics.html
- https://www.gnu.org/software/emacs/manual/html_node/elisp/Markers.html
- https://www.gnu.org/software/emacs/manual/html_node/elisp/Windows-and-Frames.html
- https://www.gnu.org/software/emacs/manual/html_node/elisp/Text-Properties.html
- https://www.gnu.org/software/emacs/manual/html_node/elisp/Overlays.html
- https://www.gnu.org/software/emacs/manual/html_node/elisp/Processes.html
- https://www.gnu.org/software/emacs/manual/html_node/elisp/Major-Modes.html

Recursive://Neon's living implementation supplies additional candidate cases: mutable tracked marks, windows retaining buffer references, immutable undo-entry values around a mutable buffer, Dired text backed by structured provider entries, and an in-process editor harness. Its custom PTY transport is not an architectural target.

## Stress matrix

| # | Given / When / Then | Status |
| --- | --- | --- |
| 1 | Given a large line table and retained history, when localized edits run, then report elapsed, current traced, and peak traced allocations without treating them as a portable storage verdict | Implemented preliminarily for persistent and controlled mutable |
| 2 | Given two windows sharing one buffer, when edits cross both points, then both points track correctly and independently | Implemented preliminarily |
| 3 | Given extension-held references, when the buffer changes, then characterize direct references versus stable-handle resolution | Implemented preliminarily |
| 4 | Given markers and overlays with left/right insertion affinity, when edits hit boundaries, then all ranges remain valid | Markers partial; overlays pending |
| 5 | Given grouped edits and undo/redo, when history branches, then semantics and memory remain tractable | Pending |
| 6 | Given a Dired-like buffer backed by entries, when the provider changes, then model identity and generated text stay coherent | Pending |
| 7 | Given timestamped process output, when delivery is replayed, then live behavior and immutable observations agree | Pending |
| 8 | Given mode-local data and incremental invalidation, when extensions retain references, then updates remain localized and deterministic | Pending |
| 9 | Given a mail/news-like model with stable message identity and asynchronous delivery, when generated views refresh, then selection, unread state, and extension references remain coherent | Pending |
| 10 | Given stable identity shells over persistent text/history roots, when equivalent edits run, then compare the distinct hybrid ownership model | Pending |

## Running the preliminary experiment

```bash
uv --no-config run python spikes/001-editor-state-architecture/experiment.py
```

The script emits JSON with semantic agreement, explicit marker/point expectations, real handle resolution, elapsed time, current traced allocations after each run, peak traced allocations during each run, and immutable observation equality. Tracing starts before model construction, so both memory figures include construction; peak additionally includes transient edit allocations. They are neither baseline-subtracted nor portable performance promises.

## Preliminary result

On CPython 3.13.2 on the initial Windows host, the corrected 10,000-line, 1,200-edit workload produced equal immutable semantic observations:

| Model | Elapsed | Current traced after run | Peak traced allocations | Extension reference |
| --- | ---: | ---: | ---: | --- |
| Fully persistent line-table roots | 0.216 s | 99,720,408 bytes | 99,847,792 bytes | Original object became an old version; an implemented stable handle resolved the current version and rejected an unknown ID |
| Controlled mutable line table + immutable edit records | 0.047 s | 1,504,592 bytes | 1,514,484 bytes | Original object remained current |

For this run, the simple persistent model took 4.64× the time, had 66.28× the current traced allocations after the run, and reached 65.93× the traced allocation high-water mark. These describe this unmatched-history prototype, not general persistent-versus-mutable costs. Both models maintain 202 markers, explicitly edit across both window points, assert left/right insertion affinity at both locations, and emit exactly equal frozen observations.

This result is deliberately **not** a verdict. The persistent prototype copies a Python tuple of line references for each edit and retains every root; the controlled-mutable prototype stores compact edit records, so their histories are not matched representations. A production persistent vector, rope, piece tree, or edit-log history could materially reduce those costs. Conversely, the mutable prototype has not yet faced branching undo, overlays, asynchronous delivery, extension callbacks, or accidental out-of-boundary mutation. Current and peak traced allocations include model construction and have no no-history baseline. The experiment establishes pressure points and a repeatable workload, not architectural closure.

## Next experiment

Implement grouped branching undo plus overlay boundaries in both models, add the distinct hybrid identity-shell/persistent-root model, then replay a deterministic process-output schedule and a mail-like delivery/refresh schedule through the same command boundary. This tests whether controlled mutation preserves replay discipline and whether structural sharing avoids the preliminary cost without pushing pervasive handle resolution into extension code.

## Verdict: PENDING

No production live-model architecture may be selected from this first benchmark alone.
