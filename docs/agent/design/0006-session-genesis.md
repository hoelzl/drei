# 0006: Versioned session genesis and startup resolution

**Status:** accepted by the owner on 2026-08-14; effective when this record
merges
**Builds on:** `0001-foundation.md`,
`0002-live-editor-state-architecture-spike.md`
**Refines:** the initial-condition side of the replay rule in
`docs/knowledge/verification-model.md`
**Amends:** `0005-acp-pump.md` D2's claim that the subsequent transcript alone
reproduces state
**Occasioned by:** issue #74's design gate before TD-9, TD-12, and TD-14

## Problem

Drei currently constructs semantic state from facts that never enter immutable
evidence:

- `cli.py` reads an optional startup file before `run_editor`, handles the read
  differently from interactive find-file, and passes raw `file_path` plus
  `initial_text` arguments onward;
- `EditorHarness` derives the initial buffer identity from that path;
- `EditorSession.__init__` converts raw file text into canonical buffer text and
  remembers line-ending and clean-state facts; and
- `EditorSession.__init__` accepts an initial frame size that can change whether
  a later `SplitWindow` or `DisplayBuffer` succeeds, but records no initial-size
  event.

The resulting session can be deterministic when callers repeat all constructor
arguments, but the event record does not identify those arguments. The property
named `test_replay_produces_identical_evidence` therefore proves repeat execution
of the same command list against two independently reconstructed sessions; it
does not prove that a persisted event tuple can reconstruct a session.

The split startup path also violates two already accepted boundaries. A
locale-dependent CLI `OSError` message bypasses the normalized token vocabulary
(TD-9), and a missing path with an empty basename can create a buffer named `""`
that typed `C-x b` input cannot select (TD-12). Fixing either locally would leave
two definitions of visiting a file and would choose replay semantics by accident.

## Decision

**Session evidence begins with one immutable, versioned genesis value, followed
by the ordered commands and external deliveries accepted after the session
exists. Startup resolves its requested path before terminal readiness or raw
mode; rejection creates no partial session, while success is combined with the
adapter's initial semantic geometry to produce genesis before
`EditorSession` construction.**

The contract is:

```text
Session evidence = SessionGenesisV1 + subsequent ordered inputs and outcomes

StartupResult = Started(SessionGenesisV1)
              | Rejected(path, error_token)
```

`SessionGenesisV1` is a semantic Python contract in the first implementation.
This record does not establish a JSON format, canonical bytes, persistence API,
or public replay-file compatibility promise.

## Vocabulary

Several nearby terms have previously been shortened to "initial state." This
record keeps their boundaries explicit:

- **Invocation configuration** is adapter configuration needed to run Drei:
  ports, agent argv/cwd, terminal cooperation, and TTY/raw-mode setup. It is not
  editor semantics and is not genesis.
- A **startup request** is either `scratch` or `visit(path)`. It states what the
  invocation asks Drei to establish, before any filesystem result is known.
- **Startup resolution** is the effect-boundary operation that validates a visit
  target, reads through `FilePort`, and returns canonical success facts or a
  normalized rejection. It is not a session command.
- **Session genesis** is the complete immutable semantic basis from which a new
  `EditorSession` is constructed. It contains resolved facts, never an
  instruction to consult ambient state.
- A **subsequent input** is a user command or explicit external delivery accepted
  after genesis. `ResizeFrame` is subsequent input; initial geometry is genesis.
- An **event record** is the immutable account emitted by a completed subsequent
  input. Genesis is evidence, but it is not an event: no session existed before
  it for an event to transition.

These concepts are ordered rather than interchangeable:

```text
invocation configuration
    -> startup request
    -> startup resolution
    -> resolved initial buffer + initial semantic geometry
    -> Started(genesis) | Rejected
    -> subsequent inputs and event records     # Started only
```

## Decisions

### D1. Genesis is separate from the event stream

Initial buffer identity, canonical text, file-facing clean state, line-ending
policy, initial window state, and initial frame geometry are prerequisites for
interpreting later events. They belong in `SessionGenesisV1` rather than in
synthetic `BufferOpened` or `FrameResized` events.

`BufferOpened` continues to mean that an existing session opened or selected a
file after genesis. `FrameResized` continues to mean that an existing session
accepted changed geometry after genesis. Neither is emitted merely to narrate
construction.

Rejected alternative: create scratch, then dispatch ordinary find-file and
resize commands. That would invent a buffer and transitions the user never
requested, leave a hidden scratch buffer after successful file startup, and
make constructor history look like command history.

### D2. Startup returns `Started` or `Rejected`; rejection creates no session

A startup path is resolved before `DREI:READY`, before terminal raw mode, and
before `EditorSession` construction.

A successful path resolution is retained while the adapter obtains its initial
semantic geometry, then both become `Started(SessionGenesisV1)` before session
construction. An invalid or unreadable target returns
`Rejected(literal_path, normalized_token)` immediately; the CLI writes one
deterministic diagnostic, `drei: <literal-path>: <normalized-token>`, followed
by a newline to stderr and exits with status 2. It emits no readiness line or
TermVerify marker, enters no raw mode, writes no editor frame, and creates no
scratch fallback. This decision does not otherwise move the successful
invocation's established readiness/raw-mode ordering; an implementation plan
that needs to do so must prove the terminal lifecycle separately.

This is a safety invariant, not merely preservation of current CLI behavior:
if Drei entered a scratch session after rejecting the requested path, a user who
missed the echo-row message could edit a buffer that does not correspond to the
path they intended.

A rejected invocation has no genesis because no semantic session exists. Its
result belongs to the CLI/application startup boundary, not to the session event
stream.

### D3. Startup and interactive find-file share visit resolution, not response policy

One effect-boundary operation owns path-name validation, filesystem reading,
line-ending detection, canonical buffer text, and normalized errors. Its
semantic result is a closed value equivalent to:

```text
VisitResolution = Opened(
    origin,
    literal_path,
    buffer_id,
    buffer_text,
    line_ending,
)
| Rejected(literal_path, error_token)

origin = existing_file | missing_file
```

Startup turns `Opened` into genesis and turns `Rejected` into exit status 2.
Interactive find-file turns `Opened` into an in-session buffer transition and
turns `Rejected` into `OpenFailed`, leaving the current buffer intact. Sharing a
vocabulary and resolution operation does not require identical control flow at
these different product boundaries.

Resolution must occur through the injected `FilePort`. Replay from an already
constructed genesis never rereads the filesystem.

### D4. Startup creates the requested identity directly; it does not visit from scratch

The successful startup cases are:

| Startup request | Resolution | Initial identity |
| --- | --- | --- |
| `scratch` | no filesystem access | buffer id `scratch`, no file path |
| `visit(path)` | existing UTF-8 file | basename-derived id visiting literal `path` |
| `visit(path)` | missing valid path | basename-derived id, empty and clean, visiting literal `path` |

A file startup has exactly one initial ordinary buffer and one initial window.
It does not retain a hidden scratch buffer. No `BufferOpened` event is emitted
for the initial target.

The path is preserved literally. Genesis does not canonicalize it through the
host filesystem, current directory, symlinks, case folding, or `resolve()`. Path
identity remains the existing literal-path contract; changing it requires a
separate parity and compatibility decision.

The buffer id is recorded explicitly in genesis rather than re-derived during
replay. Future changes to basename or collision rules must not reinterpret an
existing genesis.

### D5. Visit classification and normalized tokens are one closed v1 contract

Before filesystem access, replace backslashes with slashes only for basename
classification. An empty basename rejects with `empty-basename`; the literal
path itself is retained in evidence and diagnostics.

After a valid basename:

| File-port outcome | Resolution |
| --- | --- |
| text returned | `Opened(existing_file, ...)` |
| `FileNotFoundError` | `Opened(missing_file, ..., text="")` |
| `PermissionError` | `Rejected(..., "permission-denied")` |
| other `OSError` | `Rejected(..., "io-error")` |
| `UnicodeDecodeError` | `Rejected(..., "io-error")` |

A missing parent directory remains in the `missing_file` arm when the port
reports `FileNotFoundError`, matching existing find-file semantics: Drei creates
an empty visiting buffer and a later save may fail. A directory path whose
basename is non-empty normally produces `io-error` when the port refuses to read
it. Dired remains out of scope.

V1 deliberately does not introduce `not-utf8`. A more specific diagnostic may
be useful, but it changes the shared public token vocabulary and must be decided
as its own behavior change rather than smuggled into genesis work.

### D6. `SessionGenesisV1` is a closed semantic value

The first implementation must expose one immutable value with a literal version
and these semantic members. Names below are normative concepts; the
implementation plan may choose Python spelling consistent with neighboring
records.

```text
SessionGenesisV1
    version = 1
    initial_buffer
        buffer_id
        origin = scratch | existing_file | missing_file | provided
        kind = ordinary
        text
        point
        mark
        file_path
        modified
        saved_text = text | unknown
        line_ending = LF | CRLF
    initial_windows
        ordered windows, each with buffer_id, point, mark
        focused index
    frame = known(width, height) | unknown
```

V1 invariants:

- `version` is exactly `1`; other values are rejected rather than
  guessed.
- There is exactly one initial ordinary buffer and exactly one initial window.
- The buffer id is non-empty.
- The initial window references that buffer and has the same point and mark.
- Point and mark are within the canonical text bounds. Production startup sets
  point to `0`, mark to `None`, and modified to false.
- Every genesis carries canonical buffer text and buffer-coordinate point/mark;
  construction never normalizes or shifts an already-created genesis.
- The accepted origin/clean-basis combinations are closed:

  | Origin | `modified` | `saved_text` |
  | --- | --- | --- |
  | `scratch` | false | exactly empty canonical text |
  | `existing_file` | false | exactly canonical `text` |
  | `missing_file` | false | exactly empty canonical text |
  | `provided` | false | exactly canonical `text` |
  | `provided` | true | `unknown` |

  Every other combination is invalid, including a modified startup origin,
  unknown saved text on a clean value, known saved text on a modified provided
  value, or `scratch`/file origins with the wrong clean basis. No initial
  undo/redo history exists.
- Scratch has id `scratch`, origin `scratch`, no file path, empty text, and LF
  line endings.
- File origins have a non-empty buffer id and a literal file path.
- `existing_file` text is canonical buffer text: uniformly CRLF file text is
  represented with LF separators and `line_ending=CRLF`; all other text is
  preserved with `line_ending=LF` under the existing policy.
- `missing_file` has empty text, empty saved text, and LF line endings.
- `provided` is reserved for the direct/in-process profile that currently
  constructs an `EditorSession` from an explicit `BufferValue`. Every semantic
  member is supplied and validated; no filesystem read or ambient derivation is
  permitted. Production CLI startup never emits `provided`. It is not a
  workspace-restoration promise.
- A `provided` genesis is already canonical. During migration, the legacy
  direct constructor's raw `BufferValue` adapter must perform today's pure
  `_visit` preparation *before* creating genesis: detect the raw text's line
  ending, convert uniform CRLF to LF, and shift point/mark across collapsed CRLF
  pairs. It then emits explicit canonical text/coordinates/line ending and the
  clean basis from the table above. Genesis construction itself must not repeat
  that conversion. This preserves current direct-profile behavior while making
  replay independent of an implicit constructor transform.
- Known frame width and height are non-negative integers; zero remains valid for
  the deliberately tiny-frame paths already supported by the renderer.
- Initial transcript, process log, kill ring, prompts, permission queue, agent
  bindings, undo/redo stacks, yank state, and command-chain flags are empty or
  inactive by definition. They are fixed v1 invariants, not optional fields.

Construction validates the whole value before exposing an `EditorSession`.
Failure to construct a valid genesis is a programming/invariant failure, not a
normalized hostile-input result.

### D7. Initial geometry is genesis; `unknown` is explicit

`frame=known(width, height)` records the editor geometry used to construct the
first semantic frame. Under TermVerify cooperation this is the editor height
after the adapter reserves its marker row, not the physical terminal height;
marker geometry remains adapter configuration.

`frame=unknown` means the caller has deliberately supplied no geometry. V1
preserves today's direct-harness behavior: size-dependent gates cannot refuse a
split solely from unknown geometry. It does not mean 80x24, zero size, or "read
the terminal later."

Production `run_editor` resolves a known initial editor width/height before
session construction. Later observed changes remain explicit `ResizeFrame`
inputs emitting `FrameResized`. Replaying the same subsequent split against
known-small, known-large, and unknown genesis values must reproduce the three
corresponding gate decisions.

### D8. Replay equivalence requires genesis plus ordered inputs

The supported v1 replay statement is:

> Given equivalent `SessionGenesisV1`, equivalent injected effect outcomes, and
> the same ordered subsequent commands and external deliveries, Drei produces
> equivalent command outcomes, event records, semantic observations, and
> rendered frames at the same declared geometry.

This statement does not claim that today's event tuple is a complete command
log or that events alone reconstruct private live state. It also does not claim
that replay may consult the current filesystem, terminal, environment, clock,
ACP peer, or process table.

Tests should rename or clarify repeat-execution properties that currently call
themselves replay without carrying genesis. A future event-only replayer requires
an audit of whether every state-changing input is reconstructible from current
events; that is outside TD-9, TD-12, and TD-14.

### D9. Version 1 begins in inception and freezes on the first supported artifact

No external consumer or supported persisted replay artifact currently uses a
session-genesis contract. V1 is therefore in inception while its defining
design and implementation slices are in flight.

Before the freeze trigger, an incompatible correction to V1 may revise V1 in
place only with explicit owner approval and coherent updates to all repository
fixtures, tests, and documentation. This avoids manufacturing a false V1-to-V2
compatibility history before V1 exists operationally.

The freeze trigger is the first of:

1. Drei accepts a persisted genesis/replay artifact as supported input;
2. Drei emits such an artifact with a compatibility promise; or
3. an external consumer implements against this contract with project support.

After the trigger:

- incompatible changes require a new version;
- additive members may remain backward-compatible only when absence has one
  explicit deterministic meaning and old artifacts remain accepted; and
- readers reject unsupported versions rather than guessing or filling semantic
  identity from ambient state.

The Python value's `version` records semantic evolution; it does not by
itself establish serialization. A future persistence design must separately
define encoding, framing, duplicate-member handling, canonical bytes,
validation, migration, and evidence governance.

### D10. Invocation configuration stays outside genesis

Genesis excludes:

- `FilePort`, `ProcessPort`, `StreamingProcessPort`, and `TerminalPort` objects;
- agent argv, agent working directory, ACP machine phase, and child identity;
- TTY handles, physical terminal geometry, raw-mode state, and readiness tokens;
- `TERMVERIFY_SEED`, environment variables, locale, hostname, account, and
  absolute current working directory; and
- live filesystem metadata beyond the literal requested path and resolved
  content/origin represented by genesis.

Ports are injected capabilities used by later commands. Agent protocol phase is
transport state per designs 0003/0005. TermVerify cooperation changes adapter
presentation geometry but not editor semantics. Recording any of these in
session genesis would conflate invocation provenance with the semantic state the
session owns.

## Acceptance scenarios

### A1. Scratch startup

```text
start without a path at known editor geometry 80x24
    -> Started(SessionGenesisV1)
    -> one ordinary `scratch` buffer, empty and clean
    -> one focused window over scratch
    -> frame known(80, 24)
    -> no filesystem read and no construction event
```

### A2. Existing CRLF file

```text
port returns "a\r\nb\r\n" for literal path "notes.txt"
    -> Started genesis with origin existing_file
    -> id "notes.txt", literal file path retained
    -> text "a\nb\n", saved_text "a\nb\n", line ending CRLF
    -> point 0, mark absent, clean
    -> replay construction performs no filesystem read
```

### A3. Missing valid file

```text
port raises FileNotFoundError for "new/notes.txt"
    -> Started genesis with origin missing_file
    -> one directly-created `notes.txt` buffer visiting the literal path
    -> empty, clean, LF
    -> no hidden scratch and no BufferOpened event
```

### A4. Invalid or unreadable startup target

```text
port would be available, request path has an empty basename
    -> Rejected(path, "empty-basename") before the port is called

port raises PermissionError / UnicodeDecodeError / other OSError
    -> Rejected(path, "permission-denied" | "io-error")
    -> CLI writes a deterministic token-based stderr diagnostic and exits 2
    -> no DREI:READY, raw mode, editor frame, session, or scratch fallback
```

The shipped subprocess case must prove exit status and stderr. Port-order tests
must prove terminal methods capable of readiness/raw mode were not called.

### A5. Interactive find-file shares resolution but remains nonfatal

```text
inside an existing scratch session, request the same unreadable path
    -> the same normalized VisitResolution rejection
    -> OpenFailed recorded and rendered on the echo row
    -> current buffer and genesis remain unchanged
    -> editor continues
```

### A6. Geometry discriminates later behavior

```text
construct from known-small genesis, dispatch SplitWindow
    -> existing too-small refusal

construct from known-large genesis, dispatch SplitWindow
    -> split succeeds

construct from unknown genesis, dispatch SplitWindow
    -> v1 unknown-geometry behavior is preserved

construct from any genesis, then dispatch ResizeFrame before SplitWindow
    -> FrameResized is subsequent evidence and the new size governs the split
```

### A7. Equivalent genesis and inputs produce equivalent evidence

Run generated subsequent command/delivery histories twice from the same validated
genesis, including scratch, existing-file, missing-file, known-size, and
unknown-size variants, plus caller-provided initial values needed by the direct
profile. Outcomes, transcript events, session observations, and rendered frames
must agree. Mutating only a load-bearing genesis member such as line ending or
known frame height must be shown to change the relevant later save or split
result, proving the test does not ignore genesis.

### A8. Provided values cross one canonicalization boundary

```text
legacy direct input:
    text "a\r\nb\r\n", point 6, mark 3, modified true
adapter prepares provided genesis:
    canonical text "a\nb\n", shifted point 4, shifted mark 2
    line ending CRLF, modified true, saved_text unknown
construct from that genesis:
    -> no second normalization or coordinate shift
```

Table-driven invalid cases must reject every origin/modified/saved-text
combination not admitted by D6, including modified startup origins and a
provided modified value with known saved text.

## Consequences accepted deliberately

1. **Startup and interactive find-file share mechanics but not failure UX.** A
   failed startup exits while failed interactive find-file leaves the editor
   running. Accepted: one occurs before a session exists; the other must preserve
   an existing session.
2. **Missing and unreadable are intentionally different.** A missing valid path
   creates a new visiting buffer; an unreadable path creates no session.
   Accepted: this preserves established find-file/new-file behavior while
   preventing edits under a misleading scratch identity.
3. **Origin becomes durable semantic evidence.** Existing-empty and missing files
   have equal initial text but distinct origins. Accepted: future deterministic
   `New file` presentation must not need to reread the filesystem.
4. **The first implementation carries some apparently derivable fields.** Buffer
   id, modified/clean basis, saved text, line ending, and initial window state
   could be recomputed from today's rules. Accepted: they are load-bearing
   replay facts whose derivation may evolve; explicit validated evidence
   prevents reinterpretation.
5. **No event-only replay format ships with these debts.** Accepted: closing the
   missing initial-condition boundary is necessary but not sufficient for a
   persistence protocol, and inventing that protocol would turn a bounded debt
   sequence into speculative framework work.
6. **Startup rejection has no TermVerify readiness epoch.** Accepted: no editor
   starts, so process exit and stderr are the correct shipped evidence. Emitting
   readiness would falsely claim an interactive session exists.

## Implementation boundaries and ordering

This record authorizes focused implementation plans after it merges; it does not
itself claim a slice number.

1. **Contract and startup resolution (TD-9 + TD-12).** Introduce immutable
   startup-resolution/genesis values, one shared visit resolver, pre-readiness
   startup rejection, direct requested-buffer construction, and focused
   scratch/existing/missing/rejected acceptance evidence. Remove TD-9 and TD-12
   only when their old CLI branches and TODOs are gone and startup/find-file
   classification agrees.
2. **Initial geometry and replay evidence (TD-14).** Route production and direct
   construction through explicit known/unknown genesis geometry; prove later
   split/display decisions and genesis-plus-input repeatability; remove TD-14
   only when no constructor-only geometry remains outside genesis evidence.
3. **Wider reconciliation.** Update stable architecture/verification prose,
   CLI help or parity rows only where shipped behavior changes, and issue #74's
   ledger. Do not claim event-only persistence, JSON artifacts, restoration,
   or session resume.

The two implementation concerns may be separate slices. A plan may combine them
only if its acceptance-first argument shows that splitting would create a
throwaway constructor API or a genesis that cannot yet carry all load-bearing
initial conditions. Normal slice-claim, strict-TDD, exact-candidate review, and
full-gate rules apply either way.

## What this record does not decide

- A JSON/JSONL replay format, schema, canonical serialization, storage location,
  artifact retention, redaction, migration CLI, or public compatibility API.
- Event-only reconstruction, checkpointing, crash recovery, session resume, or
  reopening ACP sessions after restart.
- Path canonicalization, symlink/case identity, filesystem sandboxing, encodings
  other than UTF-8, directory modes, or `write-file`.
- A `not-utf8` token, informational `New file` echo, or changes to interactive
  find-file's established success/failure behavior.
- Initial multiple buffers/windows, restored undo history, generated buffers, or
  persisted kill rings. V1 genesis is intentionally the new-session contract,
  not a workspace restoration contract.
- Physical terminal and TermVerify marker geometry. Genesis records only the
  editor geometry consumed by semantic layout decisions.

## Re-evaluation triggers

Write a new design record or explicit amendment before proceeding if:

- implementing the shared resolver requires filesystem access inside the pure
  session transition rather than through an explicit startup/application
  boundary;
- a real consumer needs workspace restoration rather than new-session genesis;
- a supported persisted artifact or external consumer appears before V1's
  implementation candidate merges, crossing the freeze trigger;
- explicit derived fields cannot be validated without ambient state; or
- unknown geometry cannot retain its current deterministic gate behavior.
