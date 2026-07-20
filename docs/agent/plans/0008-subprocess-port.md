# Eighth editor slice: subprocess effect port

**Status:** ready — architecture gate: a new **effect port** (`ProcessPort`) behind the same boundary discipline as `FilePort`/`TerminalPort` (design 0001's explicit-effect-ports rule; design 0003 §A.1). No change to `BufferValue`, the `Command`/`Event` surface, the transcript fold, or any existing property. Process output enters only as explicit **deliveries** recorded as immutable events — design 0002's "deterministic delivery of process output" stress case made concrete.

**Goal:** Drei can launch a child process, capture its stdout/stderr and exit status, and record that run as immutable events — **without** the deterministic core importing `subprocess`, `os`, or touching a pipe. This is the first prerequisite for the Hermes–Drei integration (design 0003 §A.1): every later ACP slice launches `hermes acp` as a subprocess and speaks stdio to it. This slice lands the port and its delivery boundary only; the ACP client, the always-on I/O pump, and any editor command that *uses* a subprocess are deferred (see Non-goals).

## Why this slice, and why scoped this way

Design 0003 orders feature A.1 (subprocess effect port) first because everything in the ACP client (B) and the `hermes acp` launcher (C) depends on it. The honest scoping question is the async boundary: a live process produces output *over time*, but every current Drei command is synchronous request→event. Two resolutions were possible:

- **(a) Synchronous run-to-completion port.** `run(argv) -> ProcessResult` blocks until exit and returns the whole capture. Deterministic, trivially testable, no pump.
- **(b) Async spawn + streaming deliveries now.** `spawn` returns a handle; output arrives as `ProcessOutput` events interleaved with edits.

**Decision: (a), with a delivery-command seam that (b) will reuse.** The ACP transport genuinely needs streaming (an agent streams chunks for minutes), but streaming requires a reader pump and an event-injection point in `run_editor` that do not exist yet and are not needed to *prove* the port boundary. Building the pump now would be a speculative framework layer (violating 0001's "no speculative framework layers"). So this slice ships the port with a blocking `run`, **plus** the explicit `DeliverProcessOutput` command/event that records an already-captured result into the transcript. That command is the exact injection point the later pump will call: when the streaming slice lands, the pump captures a chunk and dispatches `DeliverProcessOutput(chunk)`; nothing about the core changes. This keeps the async boundary explicit and owned, and makes the seam testable today with a fake port.

This is deliberately **agent-independent** — it also cleanly subsumes what the parity workflow already does by hand (`subprocess.run("docker run … ubuntu:24.04 …")` in `tests/differential/test_emacs_parity.py` and slice probes). The port generalizes that pattern behind an injectable boundary.

## Behavior contract (Drei-specified; no Emacs parity scenario — see parity note)

The port is a **new module `src/drei/process.py`** mirroring `files.py`'s shape (Protocol + `System*` real implementation + error normalization), keeping the core free of `subprocess`:

- **`ProcessPort` (Protocol)** — one method:
  ```python
  class ProcessPort(Protocol):
      def run(self, argv: tuple[str, ...], *, input_text: str | None = None,
              timeout: float | None = None) -> ProcessResult: ...
  ```
  `argv` is a tuple (never a shell string — no shell interpolation, matching the security rule). `input_text` is fed to the child's stdin. `timeout` (seconds) bounds the wait; expiry raises `ProcessTimedOut`.
- **`ProcessResult` (frozen dataclass)** — `argv: tuple[str, ...]`, `exit_code: int`, `stdout: str`, `stderr: str`. An immutable value; the port returns it, the core never sees a `Popen` handle. Non-zero exit is **data, not an exception** (parity probes rely on inspecting non-zero exits).
- **`normalize_process_error(error)`** — map launch-time `OSError` to a Drei-owned token, exactly like `normalize_os_error`: executable-not-found → `"not-found"`, permission → `"permission-denied"`, else `"io-error"`. Timeout is its own token `"timeout"` (from `ProcessTimedOut`, not an `OSError`). Tokens keep replay/golden assertions portable; raw exception text never enters an event.
- **`SystemProcessPort`** — production implementation over `subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=timeout)`. Marked `# pragma: no cover` (exercised via CLI/integration, like `SystemFilePort`/`SystemTerminalPort`).
- **`_NullProcessPort`** (in `session.py`, mirroring `_NullFilePort`) — default when no port is injected: `run` raises `FileNotFoundError(argv[0])`, so a port-less session records a normalized failure rather than crashing.

### Delivery into the transcript

- **New command `DeliverProcessOutput`** (in `commands.py`, frozen) carrying a `ProcessResult` (or a launch-failure token). This is an **external delivery**, not a user edit — the same category 0002 reserves for "delivered external input."
- **New event `ProcessOutputRecorded`** carrying `argv`, `exit_code`, `stdout_len`, `stderr_len`, and `status: str` (`"ok"` | `"nonzero-exit"` | a `normalize_process_error` token). The event records **lengths and status, not full text** — the transcript is the evidence oracle, and embedding unbounded process output in every event would bloat the fold and make goldens brittle. The full `ProcessResult` is available on the `CommandOutcome`/session for the (deferred) consumer; the *event* proves the delivery happened, its argv, and its shape. (This mirrors how `RegionCopied` carries text the fold needs but `BufferSaved` carries only a path.)
- `DeliverProcessOutput` dispatch: does **not** touch `BufferValue` (text/point/mark/modified unchanged), does **not** push an undo group, does **not** touch the kill ring or yank state, **does** break the kill-append chain and the undo descent **only if** it emits an event — and it always emits exactly one `ProcessOutputRecorded`. It appends to a session-level `_process_log: list[ProcessResult]` (derived cache, like `_kill_ring` — the transcript event is the oracle). Session `run_process(argv, ...)` helper: calls the injected port, wraps the result (or normalized failure) in `DeliverProcessOutput`, dispatches it, returns the outcome.
- The `Command` and `Event` unions and `CommandOutcome.events` tuple gain the new types. `BufferObservation` is **unchanged** (process state is not buffer state).

### What this slice does NOT change

No key binding launches a process; no editor command consumes one. `run_process` is reachable only programmatically (and from tests). The minibuffer (slice 7) is unaffected and uninvolved. This keeps the slice a pure boundary with zero user-visible behavior change — correct for an infrastructure prerequisite.

## Emacs / parity note

There is **no GNU Emacs differential scenario** for this slice: it adds a Drei-internal effect port with no editing behavior to compare. This is consistent with the parity policy (parity is selected scenario by scenario; a port boundary has no Emacs counterpart). The port is, however, the mechanism future parity/infrastructure code will use to launch the pinned Emacs container — the existing `subprocess.run("docker run …")` call sites in `tests/differential/test_emacs_parity.py` can later route through `SystemProcessPort` (a follow-up refactor, not this slice). No registry rows are added (no behavioral deviation from Emacs because there is no Emacs-facing behavior).

## Implementation order

1. **`src/drei/process.py`**: `ProcessResult`, `ProcessTimedOut`, `ProcessPort` Protocol, `normalize_process_error`, `SystemProcessPort`. Mirror `files.py` structure and docstring style (module docstring stating the boundary). **Tests `tests/test_process.py`:** token mapping (`not-found`/`permission-denied`/`io-error`/`timeout`), `ProcessResult` immutability, `SystemProcessPort` real round-trips behind a fake-free unit (`run(("python","-c","print('hi')"))` — but see cross-platform note: use `sys.executable`), non-zero exit captured as data, stdin `input_text` delivered, timeout raises `ProcessTimedOut`.
2. **`commands.py`**: `DeliverProcessOutput` (carrying `result: ProcessResult | None`, `error: str | None`) and `ProcessOutputRecorded` event; extend the `Command`/`Event` unions in `session.py` and the `CommandOutcome.events` tuple type.
3. **`session.py`**: `_NullProcessPort`, `__init__` accepts `process_port: ProcessPort | None = None` (default `_NullProcessPort()`), `_process_log` cache, `run_process(...)` helper, and the `DeliverProcessOutput` dispatch arm (emits one `ProcessOutputRecorded`; buffer untouched; appends to log; chain/descend intervention per the always-emits rule). **Tests:** dispatch leaves `BufferValue` identical, pushes no undo group, leaves kill ring/yank untouched, emits exactly one event, breaks kill-append chain and undo descent, records into `_process_log`, null-port failure → `ProcessOutputRecorded(status="not-found")` with no exception.
4. **`conftest.py`**: `FakeProcessPort` mirroring `FakeFilePort` — constructor takes a mapping or scripted results (`{(argv): ProcessResult}` or a queue), records calls, can be told to raise. Used by session tests and later by ACP fakes.
5. **Property test** (`tests/test_session_properties.py`): extend the strategy with an occasional `DeliverProcessOutput` (via `run_process` against a `FakeProcessPort`); invariant — injecting any number of process deliveries never changes the buffer-fold (text/point/mark/modified), the undo history, or the kill-ring derivation; the replay property still holds with deliveries interleaved (the transcript fold ignores `ProcessOutputRecorded` for buffer state but asserts `_process_log` length equals the count of `ProcessOutputRecorded` events).
6. **Integration smoke** (gated, `tests/` — a real `SystemProcessPort` end-to-end through `run_process` with `sys.executable -c`, asserting the transcript event and `_process_log`). No TermVerify scenario (no user-visible frame change) and no CLI change.

## Acceptance criteria

- Full quality gate green (`pytest --cov`, `ruff check`, `ruff format --check`, `mypy src tests`, `pre-commit run --all-files`); coverage ratchet held at 100%.
- The deterministic core (`session.py`, `model.py`, `commands.py`, `process.py`'s value/Protocol parts) imports no `subprocess`/`os`; only `SystemProcessPort` does (guard: an import-lint-style test asserting `subprocess` appears only in the `System*` implementation, mirroring how `# pragma: no cover` walls off platform shims).
- Delivery boundary pinned by unit + property: process output never alters buffer/undo/kill state; exactly one event per delivery; `_process_log` derivable from the transcript.
- Cross-platform: the real-port test uses `sys.executable` (not a hardcoded `python`) so it passes on Windows and Linux CI.
- No parity registry rows (no Emacs-facing behavior); README/docs note the new port under the effect-port list.

## Risks and decisions

- **Scope risk: shipping an unused port looks like the "speculative framework layer" 0001 forbids.** Mitigated by the explicit `DeliverProcessOutput` seam and the property test that exercises it — the port is *proven* against fakes and a real child this slice, and its consumer (ACP transport) is a named, scheduled follow-up (0003 §B), not a vague someday. If a reviewer wants a consumer in the same slice, the honest one is routing `test_emacs_parity.py`'s `subprocess.run` through `SystemProcessPort` — call that out as an optional stretch, not core.
- **Event payload size.** Decision: events carry lengths/status, not full output (keeps the transcript fold cheap and goldens stable). If a future slice needs full output in the transcript (e.g. an ACP transcript buffer), that slice amends the event explicitly — recording the decision now avoids silent drift.
- **Async deferred.** The blocking `run` cannot serve a streaming agent; the streaming pump is 0003 §B/C work that will *reuse* `DeliverProcessOutput` as its injection point. The port's `run` shape does not preclude adding `spawn`/`read`/`write` later — they are additive Protocol methods, and choosing `run` first keeps this slice deterministic and pump-free.
- **`DeliverProcessOutput` breaking the kill chain / undo descent.** Chosen to match the existing "only event-emitting commands intervene" rule (it always emits, so it always intervenes). This is the *consistent* reading of the current invariant; the alternative (process deliveries never intervene) would carve a special case into a rule the codebase currently keeps uniform. Flagged for review because it is a judgment call about an invariant, not a mechanical consequence.
