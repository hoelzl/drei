---
type: reference
title: Drei terminology
description: Stable vocabulary used by code, tests, and design documents.
tags: [terminology]
---

# Terminology

- **buffer** — editable text plus buffer-local state; not necessarily a file.
- **point** — insertion position in a buffer.
- **command** — semantic editor action independent of key encoding.
- **key sequence** — symbolic user input resolved by a keymap to a command.
- **session** — command execution boundary and owner of the authoritative live model shared by frontends.
- **live model** — authoritative runtime buffers, windows, markers, modes, processes, and extension-visible identities; its mutability model is an explicit architecture decision.
- **observation record** — immutable semantic projection emitted for verification without screen scraping; never the authoritative live model.
- **event record** — immutable ordered account of an accepted command or delivered external input.
- **terminal observation** — normalized frame, cursor, attributes, and process evidence.
- **parity contract** — an explicit scenario where matching a pinned GNU Emacs behavior is required.
- **intentional divergence** — reviewed behavior that differs from the selected reference.
