# Seventh editor slice: minibuffer + C-x C-f (find-file)

**Status:** ready — architecture gate: the minibuffer is a **second `Buffer` on the session** (design 0002's buffer-list spike direction) with a session flag routing keys to it while active. No new ports (reuses `FilePort.read`); `BufferValue` unchanged.

**Goal:** `C-x C-f` prompts for a path in the minibuffer; RET opens the file (contents loaded, `file_path` set, modified cleared) or creates a new empty buffer for a missing path; `C-g` aborts the prompt. This is the first interactive prompt — the pattern for all later minibuffer commands (`C-x C-s`-as, `M-x`, search).

## Behavior contract (Drei-specified, Emacs-informed)

- `C-x C-f` opens the minibuffer: a session flag `minibuffer_active`, a dedicated `Buffer` whose text is the input-so-far, and a prompt label (`Find file: `). **Point and editing keys route to the minibuffer** while active: printable chars append, `DEL` (backspace, `\x7f`) removes the last char (no-op at empty — deviation: Emacs signals "Text is read-only" at the prompt boundary... actually backspace at empty minibuffer is a no-op in Emacs; the prompt itself is protected text Drei doesn't model). `C-g` aborts (minibuffer closes, no event effect on the main buffer; Emacs: `abort-recursive-edit`). RET (`\r`/`\x0d` → decode "RET") accepts.
- On accept with input path P: **read via the injected `FilePort`**; success → main buffer's value replaced: `text=contents, point=0, file_path=P, modified=False, mark=None` + undo history cleared (a new file is a new editing session — Emacs keeps per-buffer undo, but Drei has ONE buffer so far; replacing its contents wholesale with a cleared history is the honest single-buffer semantics; deviation noted vs Emacs keeping the old buffer). Failure (read error token via `normalize_os_error`) → `OpenFailed(path, token)` event, minibuffer closes, main buffer untouched (deviation: Emacs creates the empty buffer anyway — probed: `find-file` in a missing directory yields an empty `f.txt` buffer, no error; but a permission-denied read differs. Drei's honest model: only ENOENT-class "missing" creates empty; other read errors leave the buffer untouched and report).
- **Missing file (not-found token) → new empty buffer** at that path (probed: `NEW NAME="drei-new.txt" TEXT="" MOD=nil`), modified=False.
- RET on empty input: silent no-op-close? Emacs re-prompts the default (the current buffer's file). Drei: closing with empty input is a no-op (deviation: no default-path modeling yet).
- Events: `MinibufferOpened(prompt)`, `MinibufferAborted()`, `BufferOpened(path, text_len)`, `OpenFailed(path, token)`. The minibuffer's per-keystroke edits reuse `TextInserted`? **Decision: NO** — minibuffer input is not transcript-buffer state; it gets its own lightweight session state (`_minibuffer_input: str`), events only at open/abort/accept boundaries (the transcript oracle covers the OBSERVABLE editor state; the prompt content is rendered state, proven via TermVerify frames). Simpler and keeps the fold untouched.
- Buffer switching is NOT in scope: one main buffer; `C-x b` deferred with the multi-buffer slice.
- `C-x C-f` while the minibuffer is already open: ignored (no recursive minibuffer — Emacs allows recursion with a flag; Drei deviation).
- Keys while minibuffer active: only printable, `DEL`, `RET`, `C-g` resolve; everything else (control/meta) is unresolved → ignored (deviation: Emacs runs minibuffer local map commands; completion/deferred).
- Modeline/render: when active, the minibuffer row shows `Find file: <input>`; cursor at input end (Emacs: prompt + overlay). Proven via TermVerify frames.

## Emacs evidence (probed vs pinned 29.3, batch)

- `find-file` existing: `NAME="drei-ff.txt" TEXT=<contents> POINT=1 MOD=nil` — contents loaded, point at start, modified cleared. Buffer NAME = basename (Drei has no buffer-name concept; single buffer keeps its id — deviation noted).
- `find-file` missing file: empty buffer at that path, `MOD=nil` — **not** an error.
- `find-file` missing DIRECTORY: ALSO creates the empty buffer (no error in 29.3 — the `condition-case` never fired).
- Batch minibuffer reads stdin and dies on EOF (`read-from-minibuffer` → "Error reading from stdin") — interactive-only; batch parity is on `find-file` semantics only, the prompt interaction is TermVerify's job.

## Implementation order

1. Session: `_minibuffer: str | None` (None = inactive), `OpenFile` command (accept-with-path), `MinibufferInput(char)`, `MinibufferBackspace`, `MinibufferAccept`, `MinibufferAbort` commands + the four boundary events; routing in `dispatch` — when `_minibuffer is not None`, only the minibuffer commands act (others no-op).
2. `FindFile` command → `MinibufferOpened`; key routing in keys.py: `C-x C-f` prefix entry; while active, decode printable→`MinibufferInput`, `\x7f`→`MinibufferBackspace`, `\x0d`→`MinibufferAccept`, `C-g`→`MinibufferAbort` (the session ALSO exposes this via observation so the terminal layer can route — actually routing lives in keys.resolve? **Decision:** routing in the session is wrong for keys; the terminal's key loop needs the state. The `BufferObservation` gains `minibuffer: str | None` + `prompt: str | None`; `run_editor` routes bytes to minibuffer commands when observation says active).
3. `OpenFile` semantics via `FilePort.read` + `normalize_os_error`: success/not-found/other-error as contracted; undo history cleared on successful open; kill ring PRESERVED (Emacs: ring is global — kill in one buffer, yank in another works).
4. Render: minibuffer row when active (modeline unchanged).
5. Property tests: strategy stays (minibuffer commands excluded from the main strategy — they need the active flag; a dedicated minibuffer property: random printable/backspace sequences keep input derivable from open/input/backspace events... **simpler:** no property extension this slice; the fold is untouched (no minibuffer edit events). The replay property already covers the rest. A focused property: open→N inputs→accept always yields a BufferOpened/OpenFailed event and closes the prompt.)
6. TermVerify scenario: `C-x C-f` → frame shows `Find file: `; type a path → frame shows it; RET → file contents in frame (host-created fixture under the sandbox, like the save scenario); abort arm: `C-x C-f C-g` → prompt gone, buffer unchanged.
7. Emacs differential: batch eval — create fixture, find-file → NAME/TEXT/POINT/MOD pinned; find-file missing → empty+MOD nil; Drei drives `FindFile`+input+accept through the session with the FakeFilePort... **the differential needs a REAL file port for Drei** — drive Drei's `SystemFilePort` (or a real-file-backed port) against the same fixture inside the docker mount? No — Drei runs on the host; the fixture is created host-side, the Emacs side creates its own identical fixture in the container. Parity on resulting text/point/modified.
8. Docs: README, registry rows (no buffer-name/switching, undo-cleared-on-open, empty-input no-op, no recursion, minibuffer local-map absence, backspace-at-empty no-op), plan status.

## Acceptance criteria

- Full quality gate green; coverage ratchet 100%.
- find-file semantics pinned by unit + differential (existing + missing + error arms vs 29.3).
- TermVerify: prompt visible, input echoed, RET opens contents, C-g aborts — through ConPTY (`\x0d` and `\x7f` are ordinary bytes — no delivery risk expected; probe the RET byte live in the scenario).
- Registry rows for the six deviations; no silent drift.

## Risks and decisions

- Single-buffer wholesale replacement is the largest deviation (Emacs: new buffer, old one kept with its undo) — owned, resolves naturally when the multi-buffer slice lands.
- Minibuffer input not being transcript events is a deliberate oracle-boundary choice: the prompt is presentation; its CONTENT is never observable state that affects the main buffer except through the four boundary events.
- RET byte (`\x0d` vs `\x0a`): Windows console sends `\r` for Enter; decode maps `\x0d`→"RET". Probe live in the scenario (the slice-5/6 lesson).
