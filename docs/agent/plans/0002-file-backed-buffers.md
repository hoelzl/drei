# Second editor slice: file-backed buffers

**Status:** ready — architecture gate inherited from design 0002 (no new state/identity demands beyond one buffer + one injected effect port)

**Goal:** Open a file into the scratch path's buffer, edit it through the existing production command path, and save it back — with filesystem effects behind an explicit port, never ambient I/O in the command path.

## Scope

In scope:

- CLI accepts an optional file path: `drei [FILE]` opens the file's content into the buffer (missing file → empty buffer, Emacs-visited-file semantics without the name yet: buffer name derives from the file basename; no file → `scratch` as today).
- `C-x C-s` (`SaveBuffer` command) writes the buffer text back to the visited file through an injected `FilePort` effect port. Echo area reports `Wrote <path>` on success and `<path>: <error>` on failure without crashing.
- Buffer tracks a `modified` flag: set by text-changing events, cleared by a successful save. Modeline shows `**` when modified, `--` otherwise (Emacs convention).
- File-backed observation: `BufferObservation` gains `file_path: str | None` and `modified: bool` (additive, frozen; no existing field changes).
- Harness/terminal/TermVerify all drive the same `SaveBuffer` path; the save port is faked in unit/harness tests and real only at the CLI boundary.

Explicitly out of scope (deferred): minibuffer / `C-x C-f` (prompted open), `C-x C-w` write-as, auto-save, backup files, encoding detection, line-ending conversion, multiple buffers, undo, kill ring. Key chord sequences (`C-x` prefix map) are introduced only as far as `C-x C-s` needs: a minimal two-key prefix resolver, not a general keymap framework.

## Sequence

1. Extend `BufferValue`/`BufferObservation` with `file_path` and `modified` (TDD: additive frozen fields, replay property still passes).
2. Define `FilePort` protocol (`read(path) -> str`, `write(path, text) -> None`) and record save outcomes as events (`BufferSaved` / `SaveFailed` with the error message, no exception escape).
3. Add the `SaveBuffer` command through `EditorSession.dispatch`; session owns the modified-flag transitions and calls the injected port; failure is atomic (buffer state unchanged, `SaveFailed` event recorded).
4. Extend the key resolver with a two-key prefix: `C-x` enters a pending-prefix state, `C-s` completes `SaveBuffer`, any other key cancels with an explicit `UnresolvedKey` (recorded, deterministic).
5. CLI: parse the optional `FILE` argument, load content via the real `FilePort` before entering raw mode; terminal loop unchanged otherwise.
6. Modeline: `Drei: <name> [**]` when modified (name = basename of file or `scratch`); render tests for both states.
7. Harness and TermVerify scenarios: open file → edit → `C-x C-s` → file content on disk matches buffer (TermVerify scenario uses the delivered filesystem sandbox root).
8. Emacs differential: extend the pinned scenario registry with save semantics (`write-file`-adjacent batch eval producing the file + `POINT/TEXT/MODIFIED` observation), classified parity verdicts in `docs/knowledge/emacs-parity.md`.
9. Docs: README status, `development.md` verified commands, plan status.

## Acceptance

- Full quality gate green on 3.12–3.14 and both CI operating systems; coverage ratchet stays at 100%.
- One save scenario proven end to end by TermVerify on Windows (file content asserted on disk inside the delivered sandbox) and by the in-process harness.
- Filesystem access appears only behind `FilePort`; the command path remains free of ambient I/O (grep-verified in review).
- Replay property: same command sequence → same outcomes, including `SaveFailed` paths (port failures injected deterministically).
- Parity registry updated with explicit verdicts; no baseline changes without human review.

## Risks and decisions

- **Modified-flag parity:** Emacs sets modified on any text change and on some non-changes; Drei's rule is "any `TextInserted` event sets modified; successful save clears it" — recorded as an intentional-deviation verdict if the differential shows drift.
- **Key prefix state** lives in the resolver as a pure value (pending prefix in, resolution out); the session does not know about prefixes. If this grows a third prefix, a keymap record replaces it in a later slice.
- **No newline/line-ending handling:** text is read and written as-is (`utf-8`, surrogateescape off; read errors surface as CLI exit 2 before raw mode). A parity scenario with CRLF files is deferred with the decision recorded.
