# Twenty-sixth slice: session genesis and shared startup resolution (TD-9 + TD-12 + TD-14)

**Status:** ready (issue #86).

**Architecture gate:** design 0006 D1–D10 and both implementation boundaries.
The owner decided after exact-candidate review to combine TD-14 with TD-9 and
TD-12: splitting them required either an incomplete genesis or a throwaway
constructor adapter that could not carry CRLF policy, origin, windows, and
geometry without rederivation. Startup
must resolve a requested path before readiness/raw mode, successful resolution
must produce immutable versioned genesis evidence, and startup plus interactive
find-file must share one visit-resolution operation and normalized vocabulary.
Every production and direct construction path must consume explicit known or
unknown genesis geometry; later resizes remain subsequent command evidence.

**Goal:** make `drei FILE` and `C-x C-f FILE` classify the same path and file-port
outcome through one resolver. A valid existing or missing startup target opens
under its requested identity directly; an empty-basename or unreadable startup
target exits 2 with a deterministic token before readiness, raw mode, frame
output, or session construction. The same rejection remains nonfatal and visible
as `OpenFailed` inside an existing editor. Every session then begins from one
validated `SessionGenesisV1`, so initial buffer facts and known/unknown geometry
are immutable evidence governing later save, split, display, and replay behavior.

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

Genesis is also discriminating evidence rather than a constructor wrapper:

```text
construct equivalent scratch, existing-file, missing-file, and provided genesis
values, then replay the same subsequent inputs with equivalent fake effects
                     → outcomes, events, observations, and frames are equivalent

construct known-small, known-large, and unknown frame genesis, press C-x 2
                     → small refuses, large splits, unknown preserves today's
                       unconstrained split behavior
resize any of them before C-x 2
                     → FrameResized is subsequent evidence and the new size wins

start from the CRLF-file genesis, edit and save
                     → the port receives CRLF bytes, proving canonical buffer
                       text did not discard the genesis line-ending policy
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
- Design 0006 D1–D10 specifies a closed visit result and `SessionGenesisV1`.
  Boundary 1 introduces resolution/genesis and startup behavior; boundary 2
  requires every production and direct constructor to consume explicit
  known/unknown genesis geometry and requires replay evidence. Exact-candidate
  review proved a split could not preserve the full value through the existing
  constructor, so the owner combined both boundaries in this slice.
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

### D3. Reject before readiness/raw mode; preserve successful lifecycle order

`run_editor` receives a startup request rather than pre-read `initial_text`. It
resolves the request before allocating the pump, writing readiness, or entering
raw mode. On rejection
it returns the immutable rejection to `cli.main`; the CLI alone formats
`drei: <literal-path>: <token>\n` on stderr and exits 2. No pump child, readers,
frame, harness, or session is created for a rejected request.

On success, retain the resolved initial-buffer facts, then preserve the current
observable lifecycle: allocate the lazy pump, write and flush `DREI:READY`, enter
raw mode, and read terminal size. Combine that size with the retained facts into
a validated `SessionGenesisV1` immediately before constructing the harness and
session. Scratch startup requires no file read. No successful-path readiness,
flush, raw-mode, size-failure/restoration, or first-frame ordering changes.

Alternative rejected: resolve in `cli.main` and pass raw success fields. That
would leave the successful initial condition unversioned and make the CLI own
semantic assembly. Alternative rejected: raise `SystemExit` from the terminal
adapter; process policy and stderr formatting belong to the CLI.

**On screen:** successful startup retains the existing readiness and initial frame
behavior. Rejected startup produces only the deterministic stderr line.

### D4. Make complete immutable V1 the only semantic construction seam

Define the closed, frozen, slotted genesis values from design 0006 D6: initial
ordinary buffer, one matching focused window, and
`known(width, height) | unknown` frame, with literal `version=1` and full
invariant validation. Production startup creates known geometry. One internal
genesis-aware `EditorSession` seam installs canonical buffer text, saved basis,
line ending, origin, initial window, and frame directly after validating the
whole value; it never calls `_visit`, rereads a file, redetects line endings, or
rederives id/path/origin.

The existing direct/in-process constructor remains only as design 0006 D6's
legacy `provided` profile adapter: it performs today's pure `_visit`
canonicalization first, converts `frame_size` immediately to known/unknown frame
genesis, creates and validates a complete `SessionGenesisV1`, then delegates to
the same internal seam. It stores no parallel raw geometry or initial-condition
facts. `EditorHarness` likewise prepares or receives one complete genesis before
session construction, and the session exposes its immutable genesis as evidence.

Alternative rejected: adapt an already-canonical genesis back through the current
`Buffer` + `frame_size` constructor. `_visit` would redetect canonical LF text as
LF and lose CRLF save policy; origin and initial-window evidence have no
parameter; raw frame size would remain outside evidence. The legacy profile must
build genesis before the installing seam rather than consume genesis after it.
Alternative rejected: introduce a partial
`StartupGenesis` and replace it later. That creates a throwaway public concept
and two incompatible definitions of V1.

**On screen:** later split/display decisions use the recorded initial geometry;
save restores the recorded line ending. Genesis itself is not an event or UI.

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
terminal-adapter tests for no readiness/raw/frame/session side effects; a direct
`cli.main` test with TTY predicates controlled for exact separate stdout/stderr
and `SystemExit(2)`; and a Windows ConPTY test of the shipped process. Because a
rejected process deliberately emits no readiness marker, `ConptyAdapter.start`
must return `StartTerminated`, containing `RunFinished(ExitStatus("code", 2))`,
rather than `Started`. Its terminal observation must contain exactly the
empty-basename diagnostic and no readiness token or editor frame. Together the
direct CLI oracle proves the stderr channel and exact bytes, while ConPTY proves
the real TTY executable exits before an interactive epoch. The path needs no
platform-specific permission fixture and rejects before filesystem access.

Alternative rejected: only test `cli.main` with controlled TTY predicates. That
would not prove the shipped TTY entry point's process status. Alternative
rejected: claim ConPTY separates stderr from stdout; a pseudoconsole presents one
terminal stream, so the channel assertion belongs to the direct CLI test.
Alternative rejected: TermVerify readiness for rejection. Design 0006 explicitly
says no interactive session exists; process exit is the oracle.

**On screen:** no frame appears on rejection; the shell sees one stable stderr
line and status 2.

## 4. What this slice does NOT do

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
5. controlled-TTY `cli.main` exits 2 with exact separate stderr and empty stdout;
   shipped ConPTY startup returns `StartTerminated` with code 2, the diagnostic
   in its terminal observation, and no readiness token/editor frame;
6. existing CRLF and missing startup targets create one direct clean identity,
   no scratch, no construction event, and no second canonicalization; editing and
   saving the CRLF genesis writes CRLF through `FakeFilePort`;
7. table-driven invalid genesis values reject unsupported version, empty id,
   mismatched window coordinates/reference, invalid origin/path/clean-basis
   combinations, out-of-bounds point/mark, invalid geometry, zero or multiple
   windows, invalid focused index, and a non-ordinary initial buffer kind;
8. scratch genesis is exact, clean, LF, one-window, and causes no filesystem read;
9. known-small, known-large, and unknown genesis geometry discriminates split and
   display behavior; a later resize supersedes each initial value;
10. equivalent scratch/file/provided genesis plus equivalent subsequent inputs
    and fake effect outcomes produce equivalent outcomes, events, observations,
    and frames; varying line ending or frame height changes the relevant save or
    split result;
11. every new resolution/genesis record is frozen and slotted in the repository's
    structural record matrix;
12. the legacy `provided` profile maps raw `a\r\nb\r\n`, point 6, mark 3, and
    `modified=true` to canonical `a\nb\n`, point 4, mark 2, CRLF policy, and
    unknown saved text before the installer runs; the installer performs no
    second shift or normalization.

Sabotage evidence must independently restore the old duplicated paths: bypass the
basename precheck, map a generic/permission failure from raw exception text, and
make interactive find-file classify without the resolver. It must also bypass
genesis during construction, redetect canonical CRLF text, ignore genesis frame
geometry, move legacy `_visit` preparation after genesis creation, omit the point
shift, omit the mark shift, and remove `frozen=True` from each new record; the
relevant focused pin must fail for the named disagreement, then pass restored
code.

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

1. **V1 — rejection RED → shared resolver GREEN.** Add controlled-TTY CLI and
   terminal-journal empty-basename tests first; observe current Drei bypass the
   expected token/lifecycle contract. Add the focused resolver basename/error
   matrix; implement closed frozen/slotted visit values and the resolver; wire
   rejection before pump/readiness/raw mode while preserving successful order.
2. **V2 — direct identity RED → complete genesis-aware construction GREEN.** Pin
   scratch, existing CRLF, and missing-file startup as one exact initial identity
   with no construction event. Add complete frozen/slotted V1 records and invalid
   matrix, including the closed one-ordinary-buffer/one-window/focused-index
   invariants. Add one internal genesis installer plus the direct `provided`
   profile adapter; pin design 0006 A8's exact CRLF point/mark shift and unknown
   saved basis before installation, no reread/rederivation, and edit/save CRLF
   fidelity.
3. **V3 — geometry/replay RED → GREEN.** Pin known-small, known-large, and unknown
   split/display behavior, resize supersession, and equivalent genesis + inputs +
   fake effects. Route production geometry and the direct/harness profiles through
   genesis, expose the immutable genesis evidence, and remove any parallel
   constructor-only geometry state. Vary line ending and frame height to prove
   the property consumes load-bearing genesis members.
4. **V4 — interactive shared-mechanics RED → GREEN.** Add a spy/discriminating
   test proving new-path find-file calls the shared resolver while same-path
   selection does not reread. Replace `_open_file`'s duplicated basename/read/
   normalization branches with resolver consumption; preserve events, collision
   naming, current-buffer atomicity, and echo rendering.
5. **V5 — lifecycle, shipped process, records, and sabotage evidence.** Pin
   successful lifecycle order unchanged; rejection ordering; exact CLI channel
   bytes/status; ConPTY `StartTerminated`; no marker/raw/frame/session fallback;
   CRLF save fidelity; record immutability; and each old-path/genesis mutation.
6. **V6 — records and debt removal.** Remove TD-9, TD-12, TD-14, and their code
   TODOs only after all focused, property, mutation, and shipped evidence passes.
   Mark both issue #74 checkboxes paid. Reconcile README, architecture,
   verification model, parity registry, CLI/help/docstrings, and this plan's
   honesty block.
7. **V7 — full gates → draft code PR (`Closes #86`) → fresh exact-SHA adversarial
   review → fixes and re-gate → ready/merge.**

## 8. Risks / open questions

- **Resolved by design 0006: rejection before readiness/raw mode.** Do not enter
  scratch and report `OpenFailed`; there is no session to preserve.
- **Resolved by design 0006: one resolver, different response policy.** Startup
  exits 2; interactive find-file emits `OpenFailed` and continues.
- **Owner decision after first exact-candidate review: combine both boundaries.**
  TD-14 lands here because a separate compatibility adapter could not carry CRLF
  policy, origin, windows, and geometry honestly. One complete V1 and one
  genesis-aware installer replace that split.
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
  after the established readiness/raw steps can itself raise. Preserve today's
  restoration and exception behavior exactly; genesis does not justify moving
  `get_size()` ahead of readiness/raw mode.
- **Legacy direct profile:** keeping the public constructor shape is permitted
  only as design 0006 D6's pre-genesis canonicalization adapter. Tests must prove
  it builds the same explicit `provided` genesis as a direct factory and that the
  internal installer never invokes `_visit` again.
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
- Successful startup preserves the existing ordering of lazy pump allocation,
  readiness write/flush, raw entry, size acquisition, session construction, first
  frame, and restoration on size/construction failure.
- Every direct/in-process profile creates a complete scratch/file/provided genesis
  with known or explicit unknown geometry before the internal session installer;
  no constructor-only initial condition remains outside immutable evidence.
- The raw provided-profile A8 value canonicalizes before genesis creation from
  `a\r\nb\r\n`, point 6, mark 3, modified true to `a\nb\n`, point 4, mark 2,
  CRLF, unknown saved text; installation does not normalize or shift again.
- V1 rejects zero/multiple initial windows, invalid focused index, non-ordinary
  initial kind, mismatched window reference/coordinates, and every other D6
  invariant violation rather than accepting workspace-like state.
- Known-small, known-large, and unknown genesis reproduce their respective
  split/display decisions; later `ResizeFrame` evidence supersedes each.
- Equivalent genesis, fake effects, and subsequent inputs reproduce equivalent
  outcomes, events, semantic observations, and frames. Varying line ending and
  frame height changes the relevant save/split result.
- Existing CRLF startup edited then saved writes CRLF, proving the genesis-aware
  installer preserves canonical text and saved line-ending policy without a
  second `_visit`.
- Rejected startup exits 2 with exactly
  `drei: <literal-path>: <normalized-token>\n` on stderr and no stdout,
  readiness line/marker, raw mode, frame, session, scratch fallback, readers,
  or pump.
- Focused regressions demonstrably fail under the named old-path sabotages and
  pass restored code with source binding to the candidate worktree.
- TD-9, TD-12, TD-14, and their code TODOs are removed only after evidence
  passes; issue #74 marks both the combined startup and geometry checkboxes paid.
- Current-facing architecture, verification, README, parity, CLI/help, and
  docstrings agree on shared resolution, direct startup identity, deterministic
  rejection, complete genesis construction, and known/unknown replay geometry.
- No dependency, persistence format, path-identity rule, interactive success UX,
  or unrelated parity behavior changes.
- Full local gates from `AGENTS.md` are green, coverage remains 100%, and GitHub
  CI passes on Python 3.12–3.14 across Windows and Linux.
