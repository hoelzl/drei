# 0002: Live editor state architecture spike

**Status:** accepted
**Revises:** `0001-foundation.md` and the immutable-live-state claim formerly made by `docs/knowledge/architecture.md`

## Decision

Drei adopts a **hybrid live-model ownership architecture**:

1. Extension-visible entities use stable runtime-owned identity shells, or stable IDs resolved through such an owner.
2. Commands, event records, observation records, explicit external deliveries, and replay transcripts are immutable values.
3. Identity owners replace immutable, structurally shared domain roots where history, branching, rollback, or snapshot reuse benefits. They may instead use controlled private mutation where measured needs justify it.
4. All changes occur inside a serialized command/session boundary. A failed grouped command restores semantic state and the architecture's promised identity boundary before it emits an event.
5. Canonical immutable observations, not implementation object graphs, define semantic equivalence and verification evidence.

This selects ownership and evidence boundaries, not a production text store. Piece trees, ropes, line tables, chunks, indexes, caches, and incremental parsers remain measured implementation choices behind owners.

## Vocabulary

The word *state* previously hid three distinct concepts:

- **live model** — authoritative runtime buffers, windows, markers, modes, processes, and extension-visible identities;
- **observation record** — immutable semantic projection captured for tests, TermVerify, reports, and replay comparison;
- **event record** — immutable ordered account of a completed command or delivered external input.

These concepts are orthogonal. Requiring immutable observation and event records does not require an immutable live model.

## Required stress cases

The comparison must cover large localized edits, shared buffers with independent window points, moving markers and overlays, grouped undo/redo, mode-local and extension-owned data, a Dired-like structured backing model, a mail/news-like model with structured message identity and asynchronous updates, deterministic delivery of process output, incremental redisplay/parsing invalidation, and extension code retaining references across edits.

Evaluate correctness, stable identity, replayability, extension ergonomics, implementation complexity, localized-edit cost, retained-history memory, and the ease of producing immutable observations.

## Decision gate

A recommendation is accepted only after the spike records executable evidence and explicit trade-offs. Re-evaluate any chosen model if later vertical slices require pervasive identity indirection, whole-model copying, ambient mutation outside the command boundary, or verification-only architecture in the production path.

## Evidence status

Three experiments are complete. The first compares large localized edits and retained history in naive persistent and controlled-mutable line tables. The second proves equal observations and event streams across fully persistent, controlled-mutable, and hybrid models for grouped branching history, overlay affinity, deterministic process delivery, mail-like refresh by stable message ID, atomic rollback, replay, and retained buffer/overlay references or handles.

The third puts the hybrid under the 10,000-line retained-history workload using a stable shell over a chunk-shared immutable root. It shares 78 of 79 chunks per localized edit, avoids the naive whole-table retention cost, and remains within a small constant factor of the controlled-mutable prototype for this architecture-specific workload. It also proves Dired-like provider refresh, stable entry selection and marks, generated views, deterministic deletion fallback, mode-local values, stable extension references, and undo/redo.

The evidence rejects whole-editor persistence as a prerequisite for replay and rejects unrestricted mutation as an acceptable ownership contract. Hybrid ownership supplies stable extension ergonomics, localized sharing, immutable history/evidence, and explicit rollback without fixing the product to the experiment's text representation.

## What survives from 0001

The deterministic command boundary, explicit effect ports, TDD, production-semantics direct and terminal profiles, immutable evidence, and human governance of baselines remain accepted. Only the unproven implication that determinism requires a fully immutable live model is withdrawn.
