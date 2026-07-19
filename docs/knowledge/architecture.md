---
type: concept
title: Drei architecture
description: Layering and deterministic boundaries for the editor.
tags: [architecture, deterministic-core, tui]
---

# Architecture

The intended dependency direction is:

```text
terminal frontend / TermVerify adapter
              -> application session
              -> deterministic editor transition core
              -> immutable state and ordered domain events
```

Core transitions receive explicit state, commands, and deterministic services and return new state plus ordered events/outcomes. The core never reads ambient terminal size, time, randomness, environment, filesystem, or network. Frontends translate input into semantic commands and render explicit state at explicit dimensions.

Start with immutable strings and cursor offsets rather than adopting a rope or gap buffer prematurely. Introduce a storage abstraction only after measurements demonstrate a need. Likewise, start with a small explicit command/key-dispatch table; extension machinery follows real commands.

Native filesystem access will be mediated by a narrow port rooted in an explicit sandbox. Direct/in-process and terminal profiles must exercise the same production semantics. Structured observations are authoritative for semantic assertions; terminal frames prove presentation and integration.
