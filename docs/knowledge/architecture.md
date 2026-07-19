---
type: concept
title: Drei architecture
description: Provisional layering and deterministic boundaries for the editor.
tags: [architecture, deterministic-core, tui]
---

# Architecture

The intended dependency direction is:

```text
terminal frontend / TermVerify adapter
              -> application session and command boundary
              -> live editor model (architecture under spike)
              -> explicit effect ports

completed command
              -> ordered immutable event records
              -> immutable semantic observation + rendered frame
```

Three terms are deliberately distinct:

- the **live model** is the authoritative runtime object graph;
- an **observation record** is an immutable semantic projection for verification;
- an **event record** is an immutable account of an accepted command or delivered external input.

Determinism requires controlled mutation, explicit inputs/effects, and reproducible observations. It does not by itself require the entire live model to be immutable. [Design record 0002](../agent/design/0002-live-editor-state-architecture-spike.md) suspends that earlier assumption while persistent, controlled-mutable, and hybrid models are stress-tested.

The selected live model must support stable buffer/window/marker identities, localized edits, undo, overlays and text properties, mode-local state, asynchronous process delivery, structured modes such as Dired, and extension-held references. Storage strategy is a separate decision: strings, line tables, piece tables, ropes, or other structures must be chosen from measured requirements rather than from the live-model mutability decision.

Native filesystem and process access will be mediated by narrow explicit ports. Direct/in-process and terminal profiles must exercise the same production command path. Structured observation records are authoritative for semantic assertions; terminal frames prove presentation and integration.
