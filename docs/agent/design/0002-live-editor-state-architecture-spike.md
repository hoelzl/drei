# 0002: Live editor state architecture spike

**Status:** active investigation
**Revises:** `0001-foundation.md` and the immutable-live-state claim formerly made by `docs/knowledge/architecture.md`

## Decision

Drei does **not** yet commit to a fully immutable live editor model. Before production editor behavior is implemented, a disposable architecture spike must compare:

1. a fully persistent live model;
2. a controlled mutable object graph;
3. a hybrid with identity-preserving live objects and immutable commands, events, observations, and replay records.

The spike is a prerequisite gate for the first editor slice. No option is the default winner. The selected design must earn adoption against realistic editor behavior, not only insertion into a single string.

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

## What survives from 0001

The deterministic command boundary, explicit effect ports, TDD, production-semantics direct and terminal profiles, immutable evidence, and human governance of baselines remain accepted. Only the unproven implication that determinism requires a fully immutable live model is withdrawn.
