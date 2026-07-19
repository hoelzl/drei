# First editor vertical slice

**Status:** implemented — semantic path, harness, terminal frontend, and pinned Emacs differential complete; TermVerify terminal evidence deferred until TermVerify's publishing workflow lands (see `../../developer-guide/development.md` → External evidence tools)

**Goal:** Edit and render one in-memory buffer through the same semantic path used by a terminal entry point and an in-process verification harness.

## Architecture gate

Accepted. The three experiments under `spikes/001-editor-state-architecture/` select hybrid ownership: stable live identities around owner-controlled values, immutable command/event/observation evidence, explicit external delivery, and atomic command rollback.

Do not name a one-buffer observation `EditorState`; use the vocabulary and ownership invariants from design 0002. Text storage remains a slice-local measured decision rather than being copied from the disposable chunk experiment.

## Sequence

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

Files, kill ring, minibuffer, multiple buffers/windows, modes, syntax highlighting, and extensions remain later production slices. The completed architecture spike represents their state and identity demands; each production slice must still prove its own concrete behavior.
