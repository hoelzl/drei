---
type: concept
title: Drei product vision
description: Product scope and success criteria for the editor and agent-first testbed.
tags: [product, editor, agents]
---

# Product vision

Drei is a usable Emacs-like terminal editor and an educational reference for long-running autonomous development. Its design must make behavior cheap to specify, execute, inspect, replay, and review from several agent harnesses.

The first useful milestone edits an in-memory buffer through Emacs-style keys and renders a deterministic terminal screen. Later milestones add files, multiple buffers/windows, commands/minibuffer, undo, kill ring, search/replace, modes, and extension facilities.

Non-goals for the first milestones are Emacs Lisp compatibility, byte-for-byte GNU Emacs cloning, GUI/browser frontends, networking, plugins, and broad host-filesystem access. Selective Emacs parity is evidence, not product identity.

Success means humans can use the editor, agents can drive the shipped interface through TermVerify, semantic state can be verified without terminal scraping, and failures yield replayable evidence suitable for review and teaching.
