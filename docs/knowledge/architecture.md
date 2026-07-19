---
type: concept
title: Drei architecture
description: Hybrid live-model ownership and deterministic editor boundaries.
tags: [architecture, deterministic-core, tui]
---

# Architecture

The intended dependency direction is:

```text
terminal frontend / TermVerify adapter
              -> application session and command boundary
              -> hybrid live editor model
              -> explicit effect ports

completed command
              -> ordered immutable event records
              -> immutable semantic observation + rendered frame
```

Three terms are deliberately distinct:

- the **live model** is the authoritative runtime object graph;
- an **observation record** is an immutable semantic projection for verification;
- an **event record** is an immutable account of an accepted command or delivered external input.

Determinism requires controlled ownership, explicit inputs/effects, atomic commands, and reproducible observations. It does not require the entire live model to be immutable. [Design record 0002](../agent/design/0002-live-editor-state-architecture-spike.md) selects hybrid ownership: extension-visible entities retain stable shells or owner-resolved IDs while immutable, structurally shared domain values are used where history, rollback, and snapshot reuse benefit.

An owner may use controlled private mutation where measured needs justify it, but no ambient component may mutate editor semantics directly. A failed grouped command restores both semantics and the owner's promised identity boundary before any event is emitted. Storage strategy remains separate: strings, line tables, piece tables, ropes, chunks, and indexes must be chosen from measured requirements rather than inferred from the ownership decision.

Native filesystem and process access will be mediated by narrow explicit ports. Direct/in-process and terminal profiles must exercise the same production command path. Structured observation records are authoritative for semantic assertions; terminal frames prove presentation and integration.
