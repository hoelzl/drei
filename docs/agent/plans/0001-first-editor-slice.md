# First editor vertical slice

**Status:** gated by the live-state architecture spike in `../design/0002-live-editor-state-architecture-spike.md`

**Goal:** After the architecture gate is accepted, edit and render one in-memory buffer through the same semantic path used by a terminal entry point and an in-process verification harness.

## Architecture gate

Before production editor types are introduced, complete `spikes/001-editor-state-architecture/` and record the selected live-model design in an accepted design record. The spike must exercise identity, shared buffers/windows, moving markers, undo, structured modes, asynchronous input, extension references, immutable observations, replay, and realistic edit/history costs.

Do not name a one-buffer observation `EditorState`; use vocabulary from design 0002. The implementation plan below remains provisional until the spike determines whether the production live model is persistent, controlled-mutable, or hybrid.

## Provisional sequence after the gate

1. Define the smallest production buffer and point contract justified by the spike.
2. Define insertion and horizontal movement one behavior at a time; commands produce explicit immutable event and observation records regardless of live-model mutability.
3. Add Hypothesis properties for point bounds, deterministic replay, edit preservation, marker behavior, and the selected model's ownership invariants.
4. Render a fixed-size frame with body, modeline, echo area, and cursor as structured data; test clipping and narrow/short dimensions.
5. Resolve printable keys plus `C-f`, `C-b`, and `C-g` into semantic commands.
6. Add an in-process session harness that sends keys and exposes observations plus frames from the production command path.
7. Add the minimal raw terminal frontend with explicit startup/input readiness markers and clean exit.
8. Drive the shipped frontend through TermVerify on Windows and Linux. Preserve transcript/replay evidence and reduce any unsupported observation to a minimal TermVerify issue or fix.
9. Add one pinned GNU Emacs differential scenario for startup, insertion, and horizontal movement; explicitly classify every difference.

## Acceptance

The architecture gate is accepted; the full quality gate passes on Python 3.12-3.14 and both CI operating systems; one scenario is proven by direct semantic assertions and TermVerify terminal evidence. No ambient I/O enters the command path, and no parity baseline changes without human review.

## Deferred

Files, kill ring, minibuffer, multiple buffers/windows, modes, syntax highlighting, and extensions remain later production slices, but the architecture spike must represent their state/identity demands before the first slice commits the core model.
