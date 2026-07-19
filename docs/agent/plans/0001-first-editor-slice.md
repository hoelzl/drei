# First editor vertical slice

**Goal:** Edit and render one in-memory buffer through the same semantic path used by a terminal entry point and an in-process verification harness.

## Sequence

1. Define immutable `EditorState(text, point)` through failing constructor and mutation tests; reject invalid point offsets.
2. Define `InsertText` and `MovePoint` commands one behavior at a time; transitions return new state and explicit outcomes/events.
3. Add Hypothesis properties: point remains in range, source state is unchanged, replay is deterministic, insertion preserves surrounding text.
4. Render a fixed-size frame with body, modeline, echo area, and cursor as structured data; test clipping and narrow/short dimensions.
5. Resolve printable keys plus `C-f`, `C-b`, and `C-g` into commands using symbolic key sequences.
6. Add an in-process session harness that sends keys and exposes semantic plus frame observations.
7. Add the minimal raw terminal frontend with explicit startup/input readiness markers and clean exit.
8. Drive the shipped frontend through TermVerify on Windows and Linux. Preserve transcript/replay evidence and file a minimal TermVerify issue for any unsupported required observation.
9. Add one pinned GNU Emacs differential scenario for startup, insertion, and horizontal movement; explicitly classify every difference.

## Acceptance

The full quality gate passes on Python 3.12-3.14 and both CI operating systems. One scenario is proven by direct semantic assertions and TermVerify terminal evidence. No ambient I/O enters the core; no parity baseline changes without human review.

## Deferred

Files, marks/regions, undo, kill ring, minibuffer, multiple buffers/windows, modes, syntax highlighting, extensions, and optimized text storage follow later vertical slices.
