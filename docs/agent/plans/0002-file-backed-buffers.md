# Second editor slice: file-backed buffers

**Status:** implemented — file-backed buffers, FilePort effect boundary, C-x C-s save, modeline modified indicator, TermVerify save scenario (Windows), and pinned Emacs save differential complete

**Goal:** Open a file into the scratch path's buffer, edit it through the existing production command path, and save it back — with filesystem effects behind an explicit port, never ambient I/O in the command path.

## Scope

In scope:

- CLI accepts an optional file path: `drei [FILE]` opens the file's content into the buffer (`buffer_id` becomes the file basename; missing file → empty buffer with `file_path` set, Emacs `find-file` semantics; no file → `scratch` as today).
- `C-x C-s` (`SaveBuffer` command) writes the buffer text back to the visited file through an injected `FilePort` effect port. Echo area reports `Wrote <path>` on success and `<path>: <error>` on failure without crashing.
- Buffer tracks a `modified` flag: set by text-changing events, cleared by a successful save. Modeline shows `**` when modified, `--` otherwise (Emacs convention).
- File-backed observation: `BufferObservation` gains `file_path: str | None` and `modified: bool` (additive, frozen; no existing field changes).
- Harness/terminal/TermVerify all drive the same `SaveBuffer` path; the save port is faked in unit/harness tests and real only at the CLI boundary.

Explicitly out of scope (deferred): minibuffer / `C-x C-f` (prompted open), `C-x C-w` write-as, auto-save, backup files, encoding detection, line-ending conversion, multiple buffers, undo, kill ring. Key chord sequences (`C-x` prefix map) are introduced only as far as `C-x C-s` needs: a minimal two-key prefix resolver, not a general keymap framework.

## Sequence

1. Extend `BufferValue`/`BufferObservation` with `file_path` and `modified` as **keyword-only defaulted frozen fields** (`kw_only=True`, so positional construction cannot silently mis-set them); audit **every** construction site (session insert/movement cases, harness observation projection) to thread both fields through — movement/insert rebuild `BufferValue` from `current`, so the flag must be carried explicitly, not dropped. `CommandOutcome.events` union widens to include `BufferSaved | SaveFailed` (non-additive touch, call it out in the diff). Replay property still passes.
2. Define `FilePort` protocol (`read(path) -> str`, `write(path, text) -> None`) and record save outcomes as events (`BufferSaved` / `SaveFailed` with a **normalized Drei-owned error token**, no exception escape).
3. Add the `SaveBuffer` command through `EditorSession.dispatch`; `EditorSession(buffer, file_port)` takes the port at construction; `EditorHarness` gains an optional `FilePort`/initial-file seam defaulting to a fake so harness tests and the terminal drive the identical dispatch path. Session owns the modified-flag transitions and calls the injected port; failure is atomic (buffer state unchanged, `SaveFailed` event recorded).
4. Extend the key resolver: `resolve(pending: str | None, key) -> Resolution` where `Resolution` carries `command | unresolved | new_pending`; the harness owns the pending value. `C-x` enters pending state, `C-s` completes `SaveBuffer`, any other second key records `UnresolvedKey("<pending> <key>")` and clears pending. **Behavior change:** a bare `C-x` no longer records `UnresolvedKey` immediately — update `test_harness_records_unresolved_keys` accordingly.
5. CLI: parse the optional `FILE` argument, load content via the real `FilePort` before entering raw mode; terminal loop unchanged otherwise.
6. Modeline: `Drei: <name> [**]` when modified (name = basename of file or `scratch`); render tests for both states.
7. Harness and TermVerify scenarios: open file → edit → `C-x C-s` → file content on disk matches buffer. The TermVerify scenario passes an **absolute path under the delivered sandbox root** (`tmp_path/"sandbox"` mapped via `CooperationConstraintPorts`) as the CLI `FILE` argument — no `TERMVERIFY_FS_ROOT` resolution in the subject is needed, and the host-side test asserts file content directly. CLI file loading happens before raw mode, outside the deterministic command path, so no design-0002 violation. The scenario proves the save path end to end; OS-level sandbox containment is an explicit TermVerify non-goal and is not claimed.
8. Emacs differential: extend the pinned scenario registry with save semantics. Batch eval: `(progn (find-file "drei-parity-save.txt") (insert "hi") (message "POINT=%d MODIFIED=%s" (point) (buffer-modified-p)) (save-buffer) (message "AFTER MODIFIED=%s" (buffer-modified-p)))` — verified against GNU Emacs 29.3 in the pinned container (`MODIFIED=t` before save, `nil` after). Drei's verdict: parity required on insert-sets-modified and save-clears-modified; file content asserted in Drei's fake port and in the TermVerify sandbox (the Emacs side compares observable `buffer-modified-p` semantics, not disk content).
9. Docs: README status, `development.md` verified commands, plan status.

## Acceptance

- Full quality gate green on 3.12–3.14 and both CI operating systems; coverage ratchet stays at 100%.
- One save scenario proven end to end by TermVerify on Windows (file content asserted on disk inside the delivered sandbox) and by the in-process harness.
- Filesystem access appears only behind `FilePort`; the command path remains free of ambient I/O — verified concretely: `grep -nE "open\(|pathlib|os\.|sys\." src/drei/session.py src/drei/commands.py src/drei/model.py src/drei/keys.py src/drei/render.py` returns nothing.
- Replay property: same command sequence → same outcomes, including `SaveFailed` paths with normalized error tokens (port failures injected deterministically).
- Parity registry updated with explicit verdicts; no baseline changes without human review.

## Risks and decisions

- **Modified-flag parity:** Emacs sets modified on any text change and on some non-changes; Drei's rule is "any `TextInserted` event sets modified; successful save clears it" — recorded as an intentional-deviation verdict if the differential shows drift beyond the save scenario (whose verdict is pre-committed in Sequence 8).
- **`SaveFailed.error` is a normalized, Drei-owned token** (`not-found`, `permission-denied`, `io-error`) mapped from `OSError` subclasses/errno at the port boundary; raw exception text never enters events, observations, or the echo area. The echo format `<path>: <error>` uses the token. This keeps replay outcomes and golden echo text platform-independent.
- **Buffer naming:** no new `name` field. `buffer_id` becomes the file basename when a file is visited (e.g. `drei notes.txt` → buffer_id `notes.txt`), staying `scratch` otherwise; the modeline keeps rendering `buffer_id` unchanged.
- **Missing vs. unreadable:** a nonexistent `FILE` opens an empty buffer with `file_path` set (Emacs `find-file` semantics) — no error. An unreadable existing file (permission, is-a-directory, decode error) exits the CLI with code 2 before raw mode. Echo text uses the path as passed (not absolutized).
- **Key prefix state** lives in the resolver as a pure value (pending prefix in, resolution out); the session does not know about prefixes. If this grows a third prefix, a keymap record replaces it in a later slice.
- **No newline/line-ending handling:** text is read and written as-is (utf-8; read errors other than not-found surface as CLI exit 2 before raw mode). A parity scenario with CRLF files is deferred with the decision recorded.
