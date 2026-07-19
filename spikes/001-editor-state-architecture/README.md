# 001: Live editor state architecture

**Status:** in progress — two experiments complete, no verdict yet

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
| 4 | Given markers and overlays with left/right insertion affinity, when edits hit boundaries, then all ranges remain valid | Markers in experiment 1; overlay boundary affinity in experiment 2 |
| 5 | Given grouped edits and undo/redo, when history branches, then semantics remain equal and replayable | Implemented in experiment 2; large-history cost remains pending |
| 6 | Given a Dired-like buffer backed by entries, when the provider changes, then model identity and generated text stay coherent | Pending |
| 7 | Given sequence-stamped process output, when delivery is replayed, then live behavior, immutable events, and observations agree | Implemented in experiment 2 |
| 8 | Given mode-local data and incremental invalidation, when extensions retain references, then updates remain localized and deterministic | Invalidation implemented in experiment 2; mode-local extension data pending |
| 9 | Given a mail/news-like model with stable message identity and asynchronous delivery, when generated views refresh, then selection, unread state, and extension references remain coherent | Implemented minimally in experiment 2; realistic provider scale pending |
| 10 | Given stable identity shells over persistent text/history roots, when equivalent edits run, then compare the distinct hybrid ownership model | Implemented semantically in experiment 2; large-workload cost pending |

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

The planned second experiment is now implemented:

```bash
uv --no-config run python spikes/001-editor-state-architecture/experiment2.py
```

It executes the same nine accepted actions against fully persistent, controlled-mutable, and hybrid adapters. The scenario performs a grouped edit, moves an overlay with explicit boundary affinity, delivers process output, selects and marks a message read before refreshing that same stable message ID, creates two undo branches, selects both redo branches, rejects negative and out-of-range branch selectors, rolls back a valid insertion followed by a duplicate external sequence in the same transaction, and replays every accepted action from a fresh model.

All three adapters produced directly equal complete observation streams and event records at every accepted step; each adapter's fresh replay reproduced its original stream. The history contained five nodes and two children at the process-output branch point. The final alternative branch retained overlay range `(4, 6)`, process output, stable mail IDs, selection, and unread state. The failed mixed transaction restored semantic state, history position, event stream, and each architecture's intended identity boundary.

| Model | Live identity behavior | History/evidence behavior | Experiment-specific pressure |
| --- | --- | --- | --- |
| Fully persistent | Direct values became stale; buffer and overlay handles resolved current values | History nodes reused immutable roots directly | 22 whole-root replacements and handle indirection |
| Controlled mutable | Buffer and overlay object references remained current, including across restore | Each history/evidence boundary materialized an immutable snapshot | 19 in-place operations and 23 snapshot materializations; restore required identity-preserving reconciliation |
| Hybrid | Buffer identity shell remained current; overlay ID resolved within the current value root | History reused immutable domain roots while live shells retained identity | 13 text-root and 15 mail-root replacements; two ownership disciplines must remain explicit |

The sub-millisecond differences between adapters are not performance evidence: this scenario is deliberately tiny, tracemalloc and interpreter noise dominate, and all adapters share the same history driver. The useful result is semantic and architectural: deterministic replay and immutable evidence worked with controlled mutation, while the hybrid preserved stable top-level identity without requiring a fully persistent whole-editor root.

## Current finding

All three models remain feasible. Whole-editor persistence is no longer justified merely by replay or verification: the other models produced identical evidence. Controlled mutation offers the most direct extension references but makes atomic rollback and identity-preserving restoration explicit implementation obligations. The hybrid currently offers the strongest balance—stable live identities with immutable, shareable domain values—but this is a **leading hypothesis**, not a decision.

The next risk-reducing experiment should combine the large edit/history workload with a structurally shared hybrid text root, then add a Dired-like provider refresh and mode-local extension object. Until that work is complete, the architecture gate remains open.

## Verdict: PENDING

No production live-model architecture may be selected from this first benchmark alone.
