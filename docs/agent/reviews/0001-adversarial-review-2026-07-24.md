# Adversarial review 0001 — full project (2026-07-24)

**Status:** All five clusters and the deferred-item bookkeeping are
**implemented and gated** — C as `5d1b105`, D as `7bf0a68`, A as `2c08f70`,
B as `bd662e0`, E + bookkeeping on 2026-07-25. Remaining work is the
follow-up named at the end of this document: the two design records
(agent-buffer identity; §C pump), then a fresh adversarial pass.
**Baseline:** commit `cabb31a` (A.2 multiple buffers/windows), working tree clean,
full suite green (508 passed, 17 skipped).
**Method:** three independent adversarial reviewers — (1) implementation audit
against the project's own rules, (2) design/documentation audit, (3) executable
bug hunt that proved defects by running code. Every finding marked CONFIRMED
was either reproduced by execution or verified by tracing the exact code path
(two single-source findings, 6 and 4-Windows, were independently re-verified in
source by the coordinating session). Line numbers refer to `cabb31a`.

A later session should resume from **§ Triage** and **§ Remediation plan** below.
The companion tech-debt entries for the deferred findings now live in
`docs/technical-debt.md` (TD-1…TD-9), each with a `TODO: [tech-debt]` marker
at its code location.

---

## Findings

### Critical — user-facing data corruption in the shipped editor

**1. Every save rewrites line endings. CONFIRMED (repro executed).**
`src/drei/files.py:39-45` — `SystemFilePort` opens files in text mode with
default newline handling: reads apply universal-newline translation, writes
translate `\n` to `os.linesep`. `_save` writes unconditionally, so no edit is
needed. Repro (Windows): write `b"line1\nline2\n"`, `port.read` then
`port.write` the identical text → disk bytes become `b"line1\r\nline2\r\n"`.
On POSIX a CRLF file collapses to LF. The class docstring claims "utf-8,
as-is", which is false.

**2. Exhausted undo flips into redo; held C-/ oscillates the buffer. CONFIRMED (repro executed).**
`src/drei/session.py:717-718` — an `Undo` on empty history is a silent no-op
but still executes `undo_descending = bool(events)` → `False`; the next `Undo`
takes the redo branch. Repro: `InsertText("a")`, then Undo ×5 → `''`
(TextUndone), `''` (no-op), `'a'` (TextRedone!), `''`, `''` … period-3
oscillation forever. Violates parity registry row 68 (silent no-ops must not
break the descent). Existing tests stop exactly one keypress short
(`test_undo.py` capacity/truncation tests).

**3. Undo past a save leaves `modified=False` while buffer ≠ disk. CONFIRMED (repro'd independently twice).**
`src/drei/session.py:1127-1157` (`_undo` restores `modified_before` from the
group) + `1183-1194` (`_save` clears the live flag but never invalidates flags
snapshotted in existing undo groups). Repro: buffer `"hello"` unmodified →
`InsertText("!")` (group records `modified_before=False`) → `SaveBuffer` (disk
= `"hello!"`) → `Undo` → buffer `"hello"`, disk `"hello!"`, `modified ==
False`. Modeline lies; any future modified-buffer guard would silently drop
the divergence. Emacs marks modified here (tracks the save boundary in the
undo list). Registry row 71 claims plain "parity" — currently false.
Test-suite mask: `tests/test_undo.py:162-183` undoes exactly *to* the saved
state, never past it; the property test doesn't model the file.

**4. Navigation keys corrupt the buffer on both platforms. CONFIRMED (POSIX repro executed; Windows chain verified in source).**
- POSIX: `assemble_meta` (`src/drei/terminal.py:69-84`) only understands
  ESC+letter. Arrow-up `ESC [ A` → bare-ESC unresolved, then `InsertText("[")`,
  `InsertText("A")`. Every arrow/Home/End/PgUp press types garbage, marks the
  buffer modified, creates undo groups, breaks kill/yank chains.
- Windows: `_read_key_windows` (`terminal.py:216-228`) consumes the extended
  key pair and returns `"\x00"`, with a comment claiming that is "an unresolved
  marker". It is not: `decode_key` maps `"\x00"` → `"C-@"` (`terminal.py:53`)
  and `keys.py:28` binds `C-@` → `SetMark`. Arrow/F-keys silently set the mark;
  a later `C-w` kills point↔stale-mark. Contradicts registry row 64 ("C-@
  undeliverable on Windows"). Test mask: `tests/test_terminal.py:394-434`
  asserts the pair is consumed and equals `"\x00"` but never asserts what
  command that byte executes.

**5. "The agent buffer" does not exist — deliveries land in the focused buffer. CONFIRMED (repro executed). [DEFERRED → design record]**
`src/drei/session.py:557-569` (`InsertAgentText`), `1085-1103`
(`apply_session_effects`) target `self.buffer` — the *currently focused*
buffer. Design 0003 §Vocabulary and registry rows 89-92 speak of "the agent
buffer" as an entity; none exists. Since A.2 shipped `C-x b`/multi-buffer:
- Repro: delivery 1 → `scratch` gets transcript text; `SwitchBuffer("notes")`;
  delivery 2 → `notes` gets the continuation. The documented fold oracle
  ("buffer's agent text = concatenation of every `AgentTranscriptUpdated.rendered`",
  `commands.py:369-379`) then matches **no** buffer.
- A delivery into a file-visiting buffer appends transcript text **without
  setting `modified`** (`session.py:561-566`) — modeline shows clean; a
  subsequent `C-x C-s` writes agent transcript into the user's file.
- Every delivery moves point to end-of-buffer (`session.py:563`, pinned by
  `tests/test_agent_delivery.py:123-126`) — point theft during typing is an
  unregistered hazard.
- `test_fold_cache_reconstructible_from_events` (`test_agent_delivery.py:83-92`)
  never dispatches a buffer switch between deliveries, so the break is
  invisible to the suite. A.2 shipped the feature that breaks the invariant
  without any record deciding what the transcript binds to.

### Important — ACP / permission layer

**6. Auto-approval cache is dead code against a conforming agent. CONFIRMED (verified in source).**
`src/drei/acp/machine.py:285-311` — `_permission_identity` canonicalizes the
**full params** (`json.dumps(params, sort_keys=True, default=str)` at line
308), which includes `toolCall.toolCallId` — the very field the docstring
(lines 288-291) promises to exclude. Conforming agents mint a fresh
`toolCallId` per call, so `allow_session`/`allow_always` never suppress a
re-prompt; the whole auto-answer branch (`machine.py:562-595`) is unreachable
in production. Fail-*closed*, so not a security hole — but the shipped B.8
feature does not function, and registry row 94 ("never the per-call
toolCallId") is false. Test mask: every test in `tests/acp/test_auto_approval.py`
re-sends identical params (same `tool_call_id="tc-1"`);
`test_different_arguments_re_prompts` (lines 99-108) *enshrines* the defect by
varying only the toolCallId and labeling the re-prompt "fail-closed".

**7. Duplicate inbound permission-request id wedges/crashes the resolution path. CONFIRMED (repro executed).**
`src/drei/acp/machine.py:596-605` — `_handle_inbound_request` never checks
`message.id in machine.in_flight_incoming`; a duplicate id overwrites the
entry while emitting a **second** `PermissionRequested`. Two prompts, one
slot: resolving the first clears it; resolving the queued second raises
`AcpStateError("permission request 5 not in flight")` — an unhandled exception
in whatever drives `apply_permission_decision`. One repeated message from a
hostile/buggy agent breaks the "malformed input is a ProtocolError, never a
crash" discipline used everywhere else. The outbound side already has the
guard (`machine.py:416-422`).

**8. Codec discards valid frames parsed before a malformed line. CONFIRMED (repro'd independently twice).**
`src/drei/acp/codec.py:56-68` — `messages()` accumulates parsed frames in a
local `out`, consuming them from the buffer; a later bad line raises and
throws `out` away. Repro: `feed(b'{"a":1}\nnotjson\n{"b":2}\n')` →
`messages()` raises; second `messages()` returns only `[{'b': 2}]` —
`{"a":1}` is gone forever. If the lost frame was the `initialize` response,
`in_flight_outgoing` never clears → machine stuck in `INITIALIZING`; a lost
`session/request_permission` hangs the agent — exactly the failure class B.8
exists to prevent. Test mask: `tests/acp/test_codec.py:133-139` only tests
garbage-**first** ordering.

**9. RET grants the first-listed allow option — ordering the agent controls. CONFIRMED design; PLAUSIBLE exploitation.**
`src/drei/session.py:827-840` (`_choice_accept_decision`), pinned by
`tests/test_permission_prompt.py:217-228` — accept takes the first enum allow
option in the agent-supplied list; an agent listing `allow_always` first turns
a habitual RET into a permanent session-wide grant. Compounded by the
keystroke race: prompts bypass the minibuffer delivery gate, so a prompt can
open between keystrokes and an in-flight `y`/`s`/`a`/`n`/RET typed as text
resolves it instantly.

**10. Turn cancellation leaks pending permission prompts. CONFIRMED (annotated in code, unimplemented).**
`src/drei/acp/machine.py:262-277` — `cancel()` carries a TODO: ACP 0.9.0
requires answering every pending `session/request_permission` with `cancelled`
on turn cancel; it doesn't sweep `in_flight_incoming`. The session's
`_permission_queue` (`session.py:384-389`) likewise has no cancellation path —
a queued prompt survives the cancel and is presented for a dead turn.

**11. ACP subsystem unreachable; §C pump has no design record. CONFIRMED. [DEFERRED → design record]**
Five merged slices (plans 0008-0011, 0013) built port/codec/machine/translation/
approval bridge, but: `EditorHarness.__init__` (`harness.py:27-44`) accepts no
process port; `run_editor` (`terminal.py:94-152`) wires none and its
synchronous `read_key()` loop has no event-injection point; `keys.py` binds no
agent command. `PermissionDecided` is recorded but its `Response` is never
sent (`apply_permission_decision` docstring, `session.py:842-855`: "nothing is
sent here"). The A.1 port (`src/drei/process.py:40-57`) is blocking
run-to-completion only — it cannot speak to a long-lived `hermes acp` child;
design 0003 §A.1 ("launch/monitor/terminate; deliver stdout/stderr lines") was
never amended after plan 0008 deferred the pump. The claimed "atomic delivery"
(design 0003 consequence 2) is actually two dispatches with an observable seam
(`apply_session_effects`, `session.py:1085-1103` — docstring claims atomicity;
fold cache advances in dispatch 1 whether or not dispatch 2 runs). Root cause
shared by 5/9/10: **the §C pump design record does not exist** (streaming
port, injection point, serialization of deliveries vs. keys, cancellation
sweep, agent-buffer binding).

### Medium — documentation / process integrity

**12. Six of thirteen plan Status fields are false. CONFIRMED.**
`docs/agent/plans/0008…0011,0013` all say "Status: ready"; `0012` says
"implemented (PR pending)". All six merged (#18, #20, #22, #27, #32, #33).
Each plan's own closing step ("Docs: … plan status") is systematically
skipped; `scripts/sync-check.sh` never checks statuses. Poisons AGENTS.md's
sources-of-truth table ("Current work → active plan") — an agent trusting
plan status would re-claim five shipped slices.

**13. README omits five merged slices. CONFIRMED.**
`README.md:9` — "Twelfth vertical slice" headline; not one word about the
subprocess port, ACP codec/machine, translation, or approval bridge (~1,900
lines of merged `src/drei/` code). B.8 (thirteenth slice) merged before A.2.

**14. Plan 0013 D2 documents the rejected fail-open identity key. CONFIRMED.**
`docs/agent/plans/0013-approval-bridge.md` D2: "Identity key: … `toolCall.toolCallId`
when present" — the design commit e0174e2 deliberately rejected (fail-open on
id reuse) and registry row 94 supersedes. Never amended; the standard reading
order (AGENTS.md step 3) serves an agent a design the project classified unsafe.

**15. architecture.md never updated for ACP/ports. CONFIRMED.**
Design 0003 (line ~74) promised "`docs/knowledge/architecture.md` — the
dependency arrow gains … `ACP client adapter → ACP port`". The file mentions
neither ACP nor agents nor the process port; its closing line still says
filesystem/process access "**will be** mediated by narrow explicit ports"
although `FilePort`/`ProcessPort` shipped. Agent-integration architecture
lives only in parity-registry deviation rows 89-95, each of which says "n/a —
no Emacs equivalent" (category error). Session-global vs. per-buffer state
split (plan 0012 D2), minibuffer model, window model likewise live only in
plans and docstrings, contradicting AGENTS.md's placement table.

**16. Parity registry drift; no mechanical link to tests. CONFIRMED sub-facts; PLAUSIBLE governance risk.**
`docs/knowledge/emacs-parity.md`: rows 94-95 are malformed table rows (leading
`||` shifts every column); the three A.2 differential scenarios
(`tests/differential/test_emacs_parity_windows.py`) are absent from the
scenario registry (rows 31-37 list only `test_emacs_parity.py`); nothing links
registry rows ↔ tests in either direction, so a deviation can be encoded with
no row and a row can name a vanished test, undetected by CI.

**17. sync-check degrades silently; claims "atomicity" is a convention. CONFIRMED behavior; PLAUSIBLE risk.**
`scripts/sync-check.sh:23-39` — with `gh` missing/unauthenticated the claim
check prints "skipped" and exits 0: the mandatory sync step passes with zero
claim visibility. `docs/agent/claims.md:22-29` — issue creation is not
check-and-set; two agents that both see no claim and open issues seconds apart
both hold "atomic" claims ("first issue wins" is an unenforced clock
tie-break).

### Minor

**18. Trailing-slash find-file creates an unreachable `""` buffer. CONFIRMED (repro executed). [DEFERRED]**
`src/drei/session.py:1061` — `name = path.replace("\\","/").rsplit("/",1)[-1]`
→ `""` for `"notes/"`; `session.py:649` — MRU default `name = text or (…)` then
`if name:` treats `""` as absent. Repro: `C-x C-f notes/ RET` → buffer named
`""`; type text; switch away; `C-x b RET` silently no-ops and no typed name
addresses it — unsaved edits stranded (no kill-buffer, no route back).

**19. `OpenFailed` and all non-save failures are invisible. CONFIRMED. [DEFERRED]**
`src/drei/harness.py:97-106` — `_echo_for` handles only
`KeyboardQuitEvent`/`BufferSaved`/`SaveFailed`. A failed `C-x C-f` (permission
error) closes the minibuffer with a blank echo row — indistinguishable from a
successful no-op. Registry row 72 covers only the missing-file arm.

**20. `C-g` after a `C-x` prefix is swallowed. CONFIRMED (repro executed). [DEFERRED]**
`src/drei/keys.py:74-78` — any non-completing key after a prefix becomes one
`UnresolvedKey("C-x C-g")`: no quit, no echo, mark survives. Emacs cancels the
prefix and quits. Unregistered deviation.

**21. Terminal size read once; resize never observed. CONFIRMED. [DEFERRED]**
`src/drei/terminal.py:106-113` — no SIGWINCH / `WINDOW_BUFFER_SIZE_EVENT`
path; after resize, frames wrap/truncate arbitrarily and the split-window
minimum-height gate (`session.py:911-914`) tests a stale height.

**22. Frozen dataclasses over aliased mutable dicts. CONFIRMED shallow-immutability; exploit theoretical. [DEFERRED]**
`src/drei/acp/machine.py:175-195` — `in_flight_outgoing/incoming`,
`request_params` are plain dicts; event payloads (`PermissionRequested.params`,
`ToolCallStarted.update`, …) are raw dicts aliased across machine, session
`_choice`/`_permission_queue`, and transcript effects. One consumer mutation
retroactively rewrites the transcript oracle and the auto-approval identity
key (`_permission_identity` canonicalizes the same dict at match time).
Discipline holds today by convention; nothing freezes or copies inbound
payloads.

**23. `_undo`/`_kill_line` mutate session state before value validation. CONFIRMED ordering; currently unreachable failure. [DEFERRED]**
`session.py:1134-1135` (history pop / redo append before `replace(...)`
validation), `1209-1213` (kill-ring mutation before construction). If
`BufferValue.__post_init__` ever raises there, stacks/ring are mutated with no
event recorded — transcript and live state desync. The comment at
`session.py:730-732` ("atomic by construction") overstates.

**24. `parse_message` accepts `id: null` on a request. CONFIRMED.**
`src/drei/acp/messages.py:134-143` + `_valid_id` (171-177) — null passes,
producing `Request(id=None)`; `None` flows into `in_flight_incoming` despite
`RequestId = int | str`. JSON-RPC reserves null ids for error responses.

**25. Non-string `optionId` coerced with `str()`. CONFIRMED.**
`session.py:818, 824, 839` — an agent sending `optionId: 1` receives back
`"1"`, which a strict peer won't match. ACP optionIds are strings; a
non-string option should be unselectable (fail-closed), not coerced.

**26. Pathless save emits `SaveFailed("scratch", "not-found")`. CONFIRMED.**
`session.py:1186` — a fake path plus a misleading token for "buffer has no
file"; echoes `scratch: not-found`.

**27. `session/update` accepted outside any turn. CONFIRMED.**
`machine.py:499-515` — once `session_id` is set, updates arriving after
`PromptCompleted` (no turn in flight) still fold into the transcript with no
`ProtocolError`.

**28. Fold-advance comment contradicts code. CONFIRMED.**
`session.py:390-394` claims "the fold only advances after the delivery event
is recorded"; `_render_effects` (`session.py:1071-1083`) advances
`self._agent_fold` *during* dispatch.

**29. CLI `drei FILE` bypasses the command boundary. CONFIRMED. [DEFERRED]**
`src/drei/cli.py:35-47` — raw locale-dependent `strerror` printed, exit 2,
versus `C-x C-f`'s normalized `OpenFailed` tokens — two divergent behaviors
for the same operation; violates the normalized-token rule
(`process.py:60-66`); recorded nowhere.

**30. Stale prose (misc). CONFIRMED.**
- `emacs-parity.md:21-23` describes per-run `emacs-nox` install into
  `ubuntu:24.04`; the actual workflow uses the prebuilt
  `drei-parity-emacs:24.04` image (`development.md:18`,
  `test_emacs_parity.py:7-14`).
- `verification-model.md:20` governs "approved snapshots" — no snapshot
  mechanism exists; "no-regression ratchet" (`development.md:32`,
  `verification-model.md:22`) is vestigial at `fail_under = 100`.
- `tests/differential/test_emacs_parity.py:373` — the first differential
  scenario lacks the per-test "Verdict: …" docstring every later scenario has
  (registry row 31 covers it, so policy is met only registry-side).

### What withstood attack (calibration)

- Allow-kind matching is genuinely fail-closed: enum membership (never
  startswith), duplicate-optionId last-wins shadowing, cancel-on-no-option —
  adversarially pinned (`tests/acp/test_auto_approval.py`,
  `tests/test_permission_prompt.py`).
- `render._sanitize` (`render.py:149-168`) blocked every terminal-escape
  injection attempt through option names, titles, file paths.
- `BufferValue.__post_init__` validation + event-sourced marker adjustment
  could not be driven out of bounds (agent-append/undo/window interleavings
  all failed); property suites are unusually strong pressure.
- Minibuffer gate / delivery-queue orderings correct in all tried orderings,
  including 7-deep FIFO stress.
- Hostile-payload probing of the ACP machine (invented kinds, duplicate
  optionIds, missing sessionId) held — except the duplicate request *id*
  (finding 7).

---

## Triage (user decisions, 2026-07-24)

- **Fix this session:** 1, 2, 3, 4, 6, 7, 8, 9, 10, 24, 25, 26, 27 + all docs
  drift 12-17, 30 (28 folded into docs as comment-only).
- **Defer → design record:** 5 and 11 (agent-buffer identity; §C pump). Tech
  debt entries + TODOs; the design records are the follow-up task.
- **Defer → tech debt:** 18, 19, 20 (minor bugs, scope control), 21, 22, 23,
  29 (hardening).
- **Skip:** none.

## Remediation plan (approved sequencing, not yet executed)

Strict TDD per fix: focused failing test → observed failure → minimal fix →
focused pass; full gates (`AGENTS.md` §Validation) at the end.

1. **Cluster C — ACP wire robustness (8, 7, 24, 27). DONE 2026-07-24.**
   Decoder parks parsed frames on the instance and survives a malformed line
   without losing them; duplicate inbound id → `ProtocolError`, no overwrite
   (mirror of the outbound guard); `parse_message` rejects null id on
   requests and success responses (legal only on error responses —
   `ResponseError.id` widened to `RequestId | None`, and a null-id error
   response is a `ProtocolError` that clears nothing); modelled
   `session/update` kinds outside `PROMPT_IN_FLIGHT` → `ProtocolError`
   (unmodelled kinds stay ignored in any phase). Strict TDD throughout: each
   fix's test observed failing first; the pre-existing pin
   `test_parse_id_null_is_a_request` was superseded by
   `test_parse_rejects_null_id_on_request`. Full gates green (515 passed /
   17 skipped with coverage ratchet, ruff, mypy, pre-commit, pre-push
   hooks, build).
2. **Cluster D — approval semantics (6, 9, 25, 10, 26). DONE 2026-07-24.**
   `_permission_identity` strips top-level `sessionId` + `toolCall.toolCallId`
   before canonicalization; new pin `test_fresh_tool_call_id_same_arguments_auto_approves`
   supersedes `test_different_arguments_re_prompts`'s old toolCallId-only
   variant (rewritten to vary `toolCall.rawInput`). RET → first usable
   `allow_once` only, else `Cancelled` (supersedes the "first valid allow
   option" pin). Non-string optionIds are unusable at all three selection
   sites (session key/accept paths and `_select_auto_option`) — skipped, never
   `str()`-coerced. `cancel()` now returns `(machine, [notification,
   *responses], effects)`: it answers every pending permission request with
   the `cancelled` outcome via `resolve_permission` (TODO removed), and the
   session gained a delivery-class `AbortPendingPermissions` command that
   closes an open *choice* prompt (`MinibufferAborted`, no `PermissionDecided`
   — the machine already answered) and drains the queue, leaving *text*
   prompts untouched. Pathless save emits `SaveFailed(buffer_name,
   "no-file")` (the old event hardcoded the name "scratch" besides the
   misleading token). Strict TDD throughout; full gates green (524 passed /
   17 skipped, ruff, mypy, pre-commit, pre-push, build).
3. **Cluster A — editor data integrity (2, 3, 1). DONE 2026-07-25.**
   An `Undo` sets `undo_descending` only when it emitted events, so an
   exhausted history keeps no-opping instead of flipping into redo (new pin
   `test_exhausted_undo_does_not_flip_into_redo`). `_UndoGroup` lost its
   `modified_before`/`modified_after` fields (`modified_after` was always
   `True`): undo/redo now DERIVE the flag from a per-buffer `saved_text`
   recorded at visit and on `BufferSaved` — clean exactly when the resulting
   text equals the last-saved text, and unknown (`None` → always modified)
   for a buffer handed to the session already modified. The old pin
   `test_undo_restores_modified_from_group` was superseded by four focused
   tests plus the property `test_clean_buffer_always_matches_disk`
   (modified=False ⇒ buffer text == what the port holds; at the file's
   50-example profile the pre-fix code passes it, so the test raises
   `max_examples` to 300 — verified failing against a simulated pre-fix
   `_save`). EOL: `SystemFilePort` uses `newline=""` in both directions and
   the `FilePort` protocol documents "translates nothing"; one `_visit`
   helper serves both entry points (startup buffer and find-file), detecting
   uniform CRLF, storing it per buffer, and holding LF in the buffer, with
   point/mark shifted for the collapsed pairs; `_save` translates back.
   Mixed endings and lone CRs pass through verbatim and render as `^M`
   (`tests/test_line_endings.py`, 13 tests including real-filesystem
   round-trips). `BufferOpened.text_len` now counts buffer characters, not
   file characters. Parity registry updated where this change falsified it:
   the modified-flag paragraph, "Undo restoring mark/modified", "Nothing to
   undo", plus a new "File line endings" row (parity on visited files by
   design, not yet differential-pinned; new files are LF on every platform —
   a deliberate platform-independence deviation). Full gates green (542
   passed / 17 skipped, coverage 100%, ruff, mypy, pre-commit, pre-push,
   build).
4. **Cluster B — terminal input (4). DONE 2026-07-25.**
   `assemble_meta`/`pending_esc` replaced by `KeyAssembler`, a frozen
   two-field state machine (`state`, `params`) whose `feed(char)` returns the
   next state plus the keys that character completed — zero mid-sequence, one
   normally, two when the character proves the escape prefix was not a
   sequence (abandoned prefix, then the character resolved from scratch). The
   `run_editor` loop lost its `pending_byte` reprocessing hatch: it feeds each
   character and dispatches whatever comes back, so quiescence marking stayed
   as it was (no marker mid-sequence, one per dispatched key). ESC+letter →
   `M-<letter>` unchanged; `ESC [` / `ESC O` collect parameter and
   intermediate bytes (0x20-0x3F) through a final byte (0x40-0x7E) into one
   symbolic key: `<up>`/`<down>`/`<right>`/`<left>` for a bare A/B/C/D, else
   `<csi:1;5A>` / `<ss3:P>` (the plan said "generic `<csi:…>`"; the two
   prefixes get distinct tags so an unresolved key names its own origin). A
   byte that cannot appear in a sequence emits `<csi:unterminated>` /
   `<ss3:unterminated>` rather than replaying the buffered bytes as input, and
   the offending byte restarts from the empty state. Windows
   `_read_key_windows` maps the scan code (H/P/M/K → the same arrow names,
   else `<ext:…>`) and never returns `"\x00"`; C-@ stays undeliverable there.
   Every navigation key is unresolved in the keymap by design (registry
   deviation — `next-line`/`previous-line` do not exist yet).
   Tests moved from byte identity to command-level effect: the arrow keys are
   proven inert through the `run_editor` byte loop (buffer text and minibuffer
   input), through the keymap (`resolve("<up>") == UnresolvedKey`), and — new
   TermVerify scenario `test_shipped_editor_navigation_keys_are_inert` — in
   the shipped editor over ConPTY, whose second arm (arrow, `C-b C-b`, `C-w`)
   exists because a stale mark is invisible in a frame; it was observed
   failing against the pre-fix shim (the shipped editor killed "hi"). The old
   pins `test_windows_extended_key_pair_is_consumed` (asserted `== "\x00"`)
   and the four `assemble_meta` tests were superseded. Registry gained
   "Navigation and function keys", "`M-O` (ESC O)" and "Bare `ESC`
   disambiguation" rows (no `escape-time`: the input path stays clock-free)
   and the Windows-console row records the fixed mechanism. Full gates green
   (556 passed / 17 skipped, coverage 100%, ruff, mypy, pre-commit, pre-push,
   build).
5. **Cluster E — docs/process (12-17, 30, 28). DONE 2026-07-25.**
   Plan statuses 0008-0013 carry the merge commit and PR number, and
   `sync-check.sh` now prints each plan's own Status line beside its
   filename so the drift is visible at the moment an agent decides whether a
   slice is free (finding 12's root cause: the closing "update plan status"
   step is systematically skipped and nothing checked it); plan 0013
   gained inline amendments where the shipped design differs from the plan
   (D2's rejected `toolCallId` key, D3's RET semantics, the cancellation
   sweep that is no longer deferred), so the standard reading order can no
   longer serve a design the project rejected. README restructured: thirteen
   merged slices across two arcs, an *Agent integration* section for the ACP
   subsystem, and the cluster A/B behavior (derived modified flag, line
   endings, inert navigation keys). `architecture.md` gained the design-0003
   dependency arrow plus sections on the effect ports (present tense — they
   shipped), the ACP layering and what §C still owes, the session-global vs
   per-buffer state split, and the minibuffer/window models. Parity registry:
   the two malformed `||` rows repaired, the three A.2 differential scenarios
   registered, four new rows for cluster D semantics (RET grants only
   `allow_once`; non-string `optionId`s unselectable; cancellation answers
   pending permissions; `no-file` save token), and the docker prose now
   describes the prebuilt `drei-parity-emacs:24.04` image. Clusters A and B
   had already added their own rows, so no back-fill was needed there.
   Finding 16's third sub-fact — no mechanical registry↔test link — is closed
   in the checkable direction by `tests/test_parity_registry.py`, which fails
   when a row cites a test that does not exist (observed failing against a
   fabricated citation); the converse stays a review responsibility, recorded
   as registry rule 4. `sync-check.sh` now preflights `gh` (present *and*
   authenticated) before any network call and exits 1 otherwise, with
   `DREI_SYNC_CHECK_OFFLINE=1` as the deliberate override; three focused
   tests drive the real script against a throwaway repo with a local `origin`
   and a fake `gh`, two of them observed failing first. `claims.md` records
   the new exit behavior and, for finding 17's second half, states plainly
   that issue creation is **not** check-and-set and what to do about the race.
   First differential test gained its Verdict docstring; snapshot/ratchet
   prose in `verification-model.md` and `development.md` now says what is
   true (no snapshot mechanism exists; 100% is a hard floor, not a ratchet);
   the fold-advance comment (28) describes the real ordering and no longer
   borrows `apply_session_effects`'s atomicity claim; plans 0004/0005 carry
   supersession notes for `assemble_meta`/`pending_esc`.
6. **Deferred bookkeeping. DONE 2026-07-25.** `docs/technical-debt.md`
   created with TD-1…TD-9 (findings 5, 11, 18-23, 29): each entry names the
   location, severity, why it was deferred, and a suggested approach, and the
   file states the rules for adding and removing entries. Thirteen
   `TODO: [tech-debt] TD-n` comments mark the code locations (TD-2 and TD-8
   have several each). TD-1 records the interaction the plan flagged —
   cluster A's derived modified flag makes finding 5's silent agent-append
   *more* visible after an undo, not less. Linked from `AGENTS.md`'s
   placement table and `docs/knowledge/index.md`.

**Interactions.** 6+7 touch `_handle_inbound_request` (7's guard runs before
6's cache check). Fix 3's `saved_text` makes deferred finding 5's
"agent text doesn't set modified" *more* visible after an undo — note in its
tech-debt entry. RET semantics (9) deliberately changes a pinned test; the old
pin was itself the finding.

**Follow-up after remediation:** write the two design records (agent-buffer
identity; §C pump — streaming port, injection point in `run_editor`,
delivery/key serialization, cancellation wiring), then re-run an adversarial
pass over clusters A-D.
