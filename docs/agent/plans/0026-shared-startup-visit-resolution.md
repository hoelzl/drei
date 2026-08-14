# Twenty-sixth slice: shared startup visit resolution (TD-9 + TD-12)

**Status:** ready (issue #86).

**Architecture gate:** design 0006 D2–D6 and implementation boundary 1. Startup
must resolve a requested path before readiness/raw mode, successful resolution
must produce immutable versioned genesis evidence, and startup plus interactive
find-file must share one visit-resolution operation and normalized vocabulary.
Design 0006 assigns constructor-wide initial-geometry/replay evidence to the
separate TD-14 boundary; this slice must not claim that debt paid.

**Goal:** make `drei FILE` and `C-x C-f FILE` classify the same path and file-port
outcome through one resolver. A valid existing or missing startup target opens
under its requested identity directly; an empty-basename or unreadable startup
target exits 2 with a deterministic token before readiness, raw mode, frame
output, or session construction. The same rejection remains nonfatal and visible
as `OpenFailed` inside an existing editor.

## 1. The acceptance scenario

```text
run `drei notes/` in a TTY
                     → exit status is 2
                     → stderr is exactly `drei: notes/: empty-basename\n`
                     → the file port is not called
                     → stdout has no `DREI:READY`, TermVerify marker, or frame
                     → terminal raw mode is never entered

run `drei unreadable.txt` where the port raises PermissionError
                     → exit status is 2
                     → stderr is exactly
                       `drei: unreadable.txt: permission-denied\n`
                     → no readiness, raw mode, frame, session, or scratch fallback

inside an existing scratch editor, press C-x C-f
                     → echo row shows `Find file: `
type the same unreadable path and press RET
                     → scratch remains current and unchanged
                     → echo row shows `unreadable.txt: permission-denied`
                     → editor continues
```

Successful startup is pinned alongside the rejection:

```text
run `drei notes.txt`, port returns `a\r\nb\r\n`
                     → first frame is one clean `notes.txt` buffer visiting the
                       literal path with canonical text `a\nb\n`
                     → no hidden scratch buffer and no BufferOpened event

run `drei new/notes.txt`, port raises FileNotFoundError
                     → first frame is one empty clean `notes.txt` buffer visiting
                       the literal path
```

## 2. What exists today

- `src/drei/cli.py:43-70` constructs `SystemFilePort`, reads a startup path
  directly, treats `FileNotFoundError` as an empty new file, gives
  `UnicodeDecodeError` its own prose, and prints locale-dependent `OSError`
  text. Its TD-9 and TD-12 TODOs mark the two divergent arms.
- `src/drei/cli.py:82-87` passes raw `file_path` and `initial_text` to
  `run_editor`; successful startup therefore has no immutable resolution value.
- `src/drei/terminal.py:295-326` writes `DREI:READY`, enters raw mode, reads the
  initial size, and only then creates `EditorHarness`. Any resolution moved into
  the harness would therefore occur too late for design 0006 D2.
- `src/drei/harness.py:56-73` derives a buffer id from the literal path and
  constructs the initial `BufferValue`; a trailing slash derives `BufferId("")`.
- `src/drei/session.py:1588-1634` implements interactive find-file independently:
  existing literal paths select without rereading; otherwise it validates the
  basename, reads through `FilePort`, maps missing to an empty visiting buffer,
  maps OS/decode failures to `OpenFailed`, canonicalizes through `_create_buffer`
  and `_visit`, then selects the new buffer.
- `src/drei/files.py:32-72` already owns line-ending conversion and normalized OS
  tokens. `normalize_os_error(FileNotFoundError)` returns `not-found`, but
  find-file deliberately treats missing as a successful new-file origin.
- Design 0006 D3–D6 specifies a closed visit result and `SessionGenesisV1`.
  Implementation boundary 1 requires this slice to introduce immutable
  resolution/genesis values; boundary 2 keeps TD-14 open until every production
  and direct constructor consumes explicit known/unknown genesis geometry and
  replay evidence proves the distinction.
- There is no `tests/test_cli.py`; CLI behavior is currently covered indirectly.
  `tests/test_terminal.py` has a journaled fake terminal that can prove ordering,
  readiness/raw calls, frame writes, and restoration. `FakeFilePort` in
  `tests/conftest.py` supplies existing, missing, permission, decode, and generic
  I/O outcomes.

## 3. Design decisions

### D1. Put visit resolution at the file-effect boundary, not in CLI or session

Add one pure-or-effect-boundary function beside `FilePort` that accepts a port
and literal path and returns one immutable closed value:

- opened: origin `existing_file | missing_file`, literal path, non-empty basename
  id, canonical buffer text, saved text, and LF/CRLF policy;
- rejected: literal path and `empty-basename | permission-denied | io-error`.

It validates basename before calling the port, treats `FileNotFoundError` as
missing-file success, and catches `UnicodeDecodeError` before/general alongside
OS errors as `io-error`. Both startup and `_open_file` consume this function;
neither duplicates classification.

Alternative rejected: move the resolver into `EditorSession`. Startup occurs
before any session exists, and an effectful constructor command would manufacture
scratch/history that design 0006 rejects. Alternative rejected: keep two wrappers
around a shared token mapper; that still leaves basename, missing-file, CRLF, and
origin classification duplicated.

**On screen:** startup and interactive diagnostics use the same stable token; a
missing valid target still opens as a new empty visiting buffer.

### D2. Preserve interactive select-before-resolve semantics

`_open_file` first checks whether the literal path is already open. It selects
that buffer without calling the resolver or rereading the filesystem. Only a new
literal path is resolved, then adapted to the existing create/select event path.
A rejection emits exactly one `OpenFailed` and leaves all current buffer/window,
history, ring, prompt, and transcript semantics otherwise unchanged.

Alternative rejected: resolve first and then check open buffers. That can fail an
operation which currently succeeds, or overwrite/reinterpret unsaved edits based
on ambient filesystem changes.

**On screen:** reopening an already visited path keeps unsaved edits; a rejected
new path closes the prompt, shows the normalized diagnostic, and preserves the
current buffer.

### D3. Build startup resolution and genesis before readiness/raw mode

`run_editor` receives a startup request rather than pre-read `initial_text`. It
resolves the request before writing readiness or entering raw mode. On rejection
it returns the immutable rejection to `cli.main`; the CLI alone formats
`drei: <literal-path>: <token>\n` on stderr and exits 2. No pump child, readers,
frame, harness, or session is created for a rejected request.

On success, the adapter obtains the initial editor geometry and creates a
validated `SessionGenesisV1` before constructing the harness. Scratch startup
requires no file read. File startup carries the resolver's explicit origin,
literal path, basename id, canonical text, clean basis, and line ending into
that genesis. The harness compatibility seam consumes those initial-buffer facts
directly; it must not rederive basename or recanonicalize text.

Alternative rejected: resolve in `cli.main` and pass raw success fields. That
would leave the successful initial condition unversioned and make the CLI own
semantic assembly. Alternative rejected: raise `SystemExit` from the terminal
adapter; process policy and stderr formatting belong to the CLI.

**On screen:** successful startup retains the existing initial frame and
readiness behavior. Rejected startup produces only the deterministic stderr line.

### D4. Introduce the complete immutable V1 shape, but keep TD-14's consumption proof open

Define the closed, frozen genesis values from design 0006 D6: initial ordinary
buffer, one matching focused window, and `known(width, height) | unknown` frame,
with literal `version=1` and full invariant validation. Production startup creates
known geometry. The compatibility constructor path may adapt a validated genesis
to the session's existing constructor during this slice, but no semantic member
may be derived again after genesis creation.

TD-14 remains open because this slice does not yet migrate every direct
`EditorSession`/`EditorHarness` construction to genesis, remove constructor-only
`frame_size`, or add genesis-plus-input replay properties across known/unknown
geometry. Slice 27 must make genesis the only construction evidence rather than
adding a second genesis type or changing V1.

Alternative rejected: introduce a partial `StartupGenesis` now and replace it
with `SessionGenesisV1` in TD-14. That creates a throwaway public concept and risks
two incompatible definitions of V1. Alternative rejected: mark TD-14 paid merely
because production startup happens to create a known frame value; the recorded
debt covers constructor-wide replay evidence, not one caller.

**On screen:** nothing beyond D3; this is immutable evidence for the first frame,
not a new event or UI.

### D5. Startup direct identity emits no construction event and retains no scratch

The started genesis contains exactly one ordinary initial buffer and one window.
Existing and missing file origins both visit the literal path under the explicit
basename-derived id. Construction emits no `BufferCreated`, `BufferOpened`, or
`BufferSelected`: those events continue to describe transitions in an already
existing session.

Alternative rejected: start scratch and dispatch find-file. That creates an
unrequested hidden buffer and transition history, and a startup rejection would
occur after a session exists.

**On screen:** the first modeline names the requested file buffer; `C-x b` has no
hidden scratch destination.

### D6. Keep startup failure lifecycle observable at two tiers

Use focused resolver tests for the complete outcome matrix and port-call order;
terminal-adapter tests for no readiness/raw/frame/session side effects; and a
shipped subprocess test for exact exit status/stdout/stderr. The subprocess case
uses a deterministic empty-basename path so it needs no platform-specific
permission fixture and proves rejection before filesystem access.

Alternative rejected: only test `cli.main` with monkeypatched internals. That
would not prove the shipped entry point's byte streams or process status.
Alternative rejected: TermVerify readiness for rejection. Design 0006 explicitly
says no interactive session exists; process exit is the oracle.

**On screen:** no frame appears on rejection; the shell sees one stable stderr
line and status 2.

## 4. What this slice does NOT do

- TD-14 remains open: direct/session constructors are not yet all genesis-only,
  constructor-only geometry is not yet eliminated, and known/unknown geometry
  replay properties are not yet delivered.
- No persisted JSON/JSONL genesis or replay format, schema, canonical bytes,
  migration, restore, checkpoint, session resume, or event-only replayer.
- No path canonicalization, `resolve()`, symlink/case folding, current-directory
  capture, directory mode, Dired, alternate encoding, or `not-utf8` token.
- No change to missing-parent behavior: `FileNotFoundError` remains a successful
  empty visiting buffer and a later save may fail.
- No change to interactive same-path reuse, collision suffixes for later buffers,
  `OpenFailed` rendering, or save semantics.
- No startup `BufferOpened`/`FrameResized` synthetic events.
- No GNU Emacs parity claim for CLI diagnostics. The deterministic pre-session
  rejection is Drei-specified by design 0006.

## 5. Pins that change

Existing assertions that pass `file_path` plus `initial_text` into `run_editor`
or rely on harness-side basename/CRLF derivation will migrate to explicit startup
request/genesis helpers without changing their visible frame assertions.

New discriminating pins:

1. empty basename rejects before `FilePort.read` for both `/` and `\\` separators;
2. existing, missing, permission, Unicode decode, and generic I/O outcomes map to
   the exact closed resolver values and normalized tokens;
3. interactive find-file consumes the resolver result, preserves same-path
   no-reread behavior, and emits one `OpenFailed` on rejection;
4. startup rejection occurs before readiness write, flush, raw mode, size-dependent
   frame construction, readers, and session/harness creation;
5. shipped `drei notes/` exits 2 with exact stderr and empty stdout;
6. existing CRLF and missing startup targets create one direct clean identity,
   no scratch, no construction event, and no second canonicalization;
7. table-driven invalid genesis values reject unsupported version, empty id,
   mismatched window coordinates/reference, invalid origin/path/clean-basis
   combinations, out-of-bounds point/mark, and invalid geometry.

Sabotage evidence must independently restore the old duplicated paths: bypass the
basename precheck, map a generic/permission failure from raw exception text, and
make interactive find-file classify without the resolver. The new focused pins
must fail for the named disagreement, then pass restored code.

## 6. Owned deviations (parity-registry rows)

Update the existing directory/trailing-slash find-file deviation to own startup
as the same Drei-specified class: an empty basename is refused before filesystem
access. Add the token-based startup failure behavior only if the registry's
scope includes invocation diagnostics; otherwise record it in architecture/README
without manufacturing a GNU Emacs comparison that was not probed.

No existing interactive parity verdict changes: missing valid paths open empty,
unreadable paths preserve the current buffer, and same literal paths select
without rereading.

## 7. Implementation order (vertical slices, strict TDD)

1. **V1 — shipped rejection RED → shared resolver GREEN.** Add the exact-process
   empty-basename scenario first and observe current Drei emit readiness/open an
   unreachable identity or fail the expected stderr/status assertion. Add the
   focused resolver empty-basename and error-token matrix; implement the closed
   visit values and resolver; keep production startup wired only far enough to
   make rejection occur before readiness/raw mode.
2. **V2 — successful startup identity RED → genesis GREEN.** Pin existing CRLF
   and missing-file startup as one direct initial identity with no scratch or
   construction event. Add complete frozen V1 values/invariant table, construct
   production genesis from resolution plus initial geometry, and adapt the
   validated facts once into the current session boundary. Focused GREEN.
3. **V3 — interactive shared-mechanics RED → GREEN.** Add a spy/discriminating
   test proving new-path find-file calls the shared resolver while same-path
   selection does not reread. Replace `_open_file`'s duplicated basename/read/
   normalization branches with resolver consumption; preserve events, collision
   naming, current-buffer atomicity, and echo rendering.
4. **V4 — lifecycle and sabotage evidence.** Pin the rejection ordering journal,
   exact CLI stderr/status, no readiness marker under `TERMVERIFY_SEED`, no raw
   mode/frame/session fallback, no double canonicalization, and each old-path
   sabotage. Run focused file/session/terminal/CLI and shipped-terminal suites.
5. **V5 — records and debt removal.** Remove TD-9, TD-12, and both CLI TODOs only
   after all focused and shipped evidence passes. Update issue #74 only for the
   combined checkbox. Reconcile README, architecture, verification model,
   parity registry, CLI/help/docstrings, and this plan's honesty block; leave
   TD-14 explicitly open.
6. **V6 — full gates → draft code PR (`Closes #86`) → fresh exact-SHA adversarial
   review → fixes and re-gate → ready/merge.**

## 8. Risks / open questions

- **Resolved by design 0006: rejection before readiness/raw mode.** Do not enter
  scratch and report `OpenFailed`; there is no session to preserve.
- **Resolved by design 0006: one resolver, different response policy.** Startup
  exits 2; interactive find-file emits `OpenFailed` and continues.
- **Resolved by design 0006: complete V1 value now, constructor migration later.**
  This slice may use one explicit compatibility adaptation, but TD-14 must remove
  raw constructor geometry rather than revise V1 or create another genesis type.
- **Pump allocation ordering:** `run_editor` currently creates `AgentPump` before
  readiness. A rejected startup must not allocate a child-facing runtime object
  at all, even though the pump is lazy, because design 0006 says no partial
  session. Resolve startup before pump construction; successful lazy launch and
  teardown ordering must remain unchanged.
- **TTY validation ordering:** CLI's existing `stdin/stdout must be TTYs` guard
  remains before editor startup. This slice does not read an arbitrary file for
  an invocation that cannot run interactively; the design's pre-readiness/raw
  guarantee is still met.
- **Terminal size failure:** geometry acquisition after successful visit but
  before readiness can itself raise. Existing terminal error policy does not
  normalize that failure. Do not broaden this slice into terminal-startup error
  UX; ensure no raw mode was entered and let the existing exception boundary
  apply.
- **No open owner question.** The design record settles all behavior choices
  needed by this slice; any need to reread the filesystem during genesis replay
  or to omit a D6 invariant is a re-evaluation trigger, not an implementation
  convenience.

## 9. Acceptance criteria

- One shared visit resolver owns basename validation, file reads, existing/missing
  origin, CRLF canonicalization, and normalized rejection tokens for startup and
  new-path interactive find-file.
- Empty basename rejects with `empty-basename` before any filesystem call on both
  slash conventions; no empty `BufferId` can be minted by startup.
- `PermissionError` maps to `permission-denied`; `UnicodeDecodeError` and other
  `OSError` map to `io-error`; raw exception text never enters startup stderr,
  events, observations, or frames.
- Missing valid paths remain successful empty clean visiting buffers; existing
  files preserve literal path, canonical text, saved text, and line ending.
- Same literal interactive path selects the existing buffer without rereading or
  losing edits; resolver rejection records one `OpenFailed` and preserves the
  current editor state.
- Production successful startup creates one validated version-1 genesis with one
  ordinary buffer/window and known editor geometry before session construction;
  initial identity is direct and emits no construction event.
- Rejected startup exits 2 with exactly
  `drei: <literal-path>: <normalized-token>\n` on stderr and no stdout,
  readiness line/marker, raw mode, frame, session, scratch fallback, readers,
  or pump.
- Focused regressions demonstrably fail under the named old-path sabotages and
  pass restored code with source binding to the candidate worktree.
- TD-9, TD-12, and their CLI TODOs are removed only after evidence passes; issue
  #74 marks only their combined checkbox paid. TD-14 remains recorded and open.
- Current-facing architecture, verification, README, parity, CLI/help, and
  docstrings agree on shared resolution, direct startup identity, deterministic
  rejection, and the still-open constructor/replay geometry boundary.
- No dependency, persistence format, path-identity rule, interactive success UX,
  or unrelated parity behavior changes.
- Full local gates from `AGENTS.md` are green, coverage remains 100%, and GitHub
  CI passes on Python 3.12–3.14 across Windows and Linux.
