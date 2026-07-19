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
- **session** — editor state and command execution boundary shared by frontends.
- **structured observation** — semantic state emitted for verification without screen scraping.
- **terminal observation** — normalized frame, cursor, attributes, and process evidence.
- **parity contract** — an explicit scenario where matching a pinned GNU Emacs behavior is required.
- **intentional divergence** — reviewed behavior that differs from the selected reference.
