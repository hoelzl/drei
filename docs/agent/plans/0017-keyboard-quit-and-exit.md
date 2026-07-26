# Seventeenth slice: `C-g` is keyboard-quit, `C-x C-c` exits

**Status:** ready (issue #45).

**Architecture gate:** none — no design record owns the keymap, and this slice
does not need one. It is a binding decision plus the parity rows that govern
it. It unblocks design `0005-acp-pump.md` D5 (turn cancellation needs `C-g`
free) and is step (1) of `docs/technical-debt.md` TD-11.

**Goal:** stop the editor from throwing away unsaved work on the one keystroke
that, in the reference editor, is guaranteed to destroy nothing. After this
slice `C-g` deactivates the mark and echoes `Quit`, and quitting is a
deliberate `C-x C-c`. The data loss is *not* fixed — `C-x C-c` still discards
modified buffers — it is moved behind a sequence the user has to mean, and
TD-11 stays open until slice 18 adds the prompt.

## 1. The acceptance scenario

```text
type "hello"     → buffer shows "hello", modeline shows "**"
press C-g        → echo row shows "Quit"
                 → buffer still "hello", modeline still "**"
                 → THE EDITOR IS STILL RUNNING          <- the whole slice
press C-x        → prefix pending; frame unchanged, no echo
press C-c        → the process exits, status 0
```

Two more, because they are the cases that make `C-g` worth having:

```text
press C-@ , C-f , C-f   → mark at 0, point at 2 (region active)
press C-g               → echo row shows "Quit", mark is gone, text untouched
press C-x , C-f         → "Find file: " prompt on the echo row
press C-g               → prompt closes, buffer untouched, editor running
press C-x               → prefix pending
press C-g               → echo row shows "Quit", prefix cancelled, mark gone   [TD-5]
```

The last one is today's bug: `C-x` then `C-g` currently produces one silent
`UnresolvedKey("C-x C-g")` — no quit, no echo, and the mark survives.

## 2. What exists today

- `src/drei/keys.py:33` binds `C-g` to `KeyboardQuit()`; `_PREFIXES` holds
  `C-x` and `C-c`, and `C-x C-c` is unbound.
- `src/drei/session.py` — the `KeyboardQuit()` arm already does the right
  thing: `replace(current, mark=None)` plus `KeyboardQuitEvent()`. Nothing
  about the *command* changes in this slice.
- `src/drei/terminal.py:378-389` — the loop treats `KeyboardQuitEvent` as
  "end the run" and returns. **This is the only place the exit actually
  happens**, and it carries TD-11's marker.
- `src/drei/harness.py` — `_echo_for` already maps `KeyboardQuitEvent` to
  `"Quit"`. Today that text is drawn once and the process dies before anyone
  reads it.
- `src/drei/keys.py:85` — `resolve`'s pending-prefix branch turns any
  non-completing key into one `UnresolvedKey`, `C-g` included (TD-5).
- `decode_key` maps `\x03` to `C-c` (slice 16), so `C-x C-c` is deliverable
  on both platforms and through ConPTY — already proven by
  `test_shipped_editor_runs_an_agent_turn`, which sends `C-c` live.

So the semantic work is nearly done. What changes is **which event ends the
run**, plus one new binding and the prefix branch.

## 3. Design decisions

### D1. A new command and event for exiting

`ExitEditor()` → `EditorExited()`. The loop stops keying off
`KeyboardQuitEvent` and keys off `EditorExited` instead.

The alternative — keep `KeyboardQuitEvent` as the exit signal and bind
`C-x C-c` to `KeyboardQuit` — is rejected because it keeps one event meaning
two unrelated things, and the session would have no way to express "the user
aborted something" without also meaning "tear the process down". That
conflation *is* the bug.

`KeyboardQuitEvent` survives unchanged and goes back to meaning exactly what
its name says. All 16 existing assertions on it stay valid.

**On screen:** nothing directly; this is what makes the rest possible.

### D2. `C-g` no longer ends the run

The `KeyboardQuit` command is untouched. Only the loop's reaction changes.

**On screen:** the echo row shows `Quit` and *stays* — the first time that
message has ever been readable.

### D3. `C-x C-c` exits, unconditionally, discarding modified buffers

Emacs's `save-buffers-kill-terminal` offers to save first. Drei will too, in
slice 18. This slice ships the binding without the prompt.

Rejected alternatives:

- **Refuse to exit while a buffer is modified.** Safe until the user actually
  wants to discard, and then there is no escape hatch — which means it needs
  the prompt anyway, so it is slice 18 wearing a disguise.
- **Ship the prompt here.** Two behavior changes plus the test sweep in §5, in
  a slice whose subject is the keymap. Slice 18 follows immediately; the
  window is one slice wide.

This is the decision most worth pushing back on at the gate. The reason it is
defensible: today quitting-and-discarding costs **one** keystroke that Emacs
users press reflexively to mean "never mind". After this slice it costs a
deliberate two-key sequence that means "quit" in every editor the user has
used. That is most of the safety, and TD-11 keeps the rest owed.

**On screen:** `C-x C-c` ends the session; a modified buffer is lost with no
warning, exactly as today.

### D4. A prefix followed by `C-g` cancels the prefix and quits (TD-5)

`resolve`'s pending branch special-cases `C-g` to return `KeyboardQuit()`
rather than an `UnresolvedKey`. Matching Emacs, and it falls out of this slice
rather than deserving its own: it is the same key, the same function, and the
same three lines.

Every *other* non-completing key after a prefix keeps today's behavior — one
`UnresolvedKey` for the whole sequence, silently. Whether that should echo
something is a separate question about prefix semantics, and TD-5's own
"suggested approach" separates them the same way.

**On screen:** `C-x` then `C-g` now echoes `Quit` and deactivates the mark,
where today nothing happens at all.

### D5. What this slice does NOT do

- **The save-buffers prompt.** TD-11 step 2, slice 18, immediately after this
  one. **TD-11 is edited, not removed, by this slice** — its own text says so,
  and treating the safer binding as the fix is the failure that entry exists
  to prevent.
- **Turn cancellation.** Design 0005 D5, slice 19. `C-g` while a turn is in
  flight will cancel the turn; that needs the pump, and it needs `C-g` to have
  stopped meaning "exit" first, which is this slice.
- **An echo mechanism.** TD-4 stands. `Quit` renders through the existing
  three-event `_echo_for`; this slice adds no fourth.

## 4. Pins that change

The sweep, measured rather than estimated (an earlier figure of "~113
references across 15 files" was a count of every `C-g`/`KeyboardQuit` mention
in the repository, not of edit sites; **TD-11's wording is corrected in this
plan's PR**):

| Where | Sites | What changes |
| --- | --- | --- |
| `tests/test_terminal.py` | 31 raw `\x07` + 4 symbolic | scripts that end `"\x07"` become `"\x18", "\x03"` |
| `tests/test_input.py` | 2 | queue-ordering fixtures; `Key("\x07")` is just a key here |
| `tests/termverify/*.py` | 14 chord sites | `("Control","g")` → `("Control","x")`, `("Control","c")` |
| `KeyboardQuitEvent` assertions | 16 | unchanged — the event still means "aborted" |

Roughly 50 edit sites in four files, not 113 in fifteen.

**The sweep is self-checking, which is the mitigation.** `scripted()` closes
the queue behind its events, so a test whose script no longer quits raises
`EndOfInput` instead of hanging. Anything missed fails loudly and immediately;
there is no silent "test still passes but no longer asserts the ending" mode,
which is the usual hazard of a rename this wide.

Two specific pins:

1. `test_editor_writes_readiness_and_exits_on_quit` (`test_terminal.py`) — the
   name becomes a lie. It should split: one test that `C-g` does **not** exit,
   one that `C-x C-c` does.
2. The parity row added in `3367b1d` (`C-g` exits, discarding unsaved work) is
   falsified by this slice and must be rewritten, not deleted — the deviation
   does not go away, it narrows to "exits without offering to save".

## 5. Owned deviations (parity-registry rows)

1. **`C-g` at top level** becomes parity and the existing deviation row is
   rewritten to cover what remains.
2. **`C-x C-c` exits without offering to save.** Emacs prompts per modified
   buffer. Intentional for one slice, tracked as TD-11, removed by slice 18.
3. **`C-g` after a prefix** becomes parity (TD-5 paid); if the registry has no
   row for the current swallowing, none is needed once it matches.
4. **Every other non-completing key after a prefix is still silent.** Emacs
   echoes `C-x <key> is undefined`. Unchanged by this slice, and it needs a
   row if it does not have one — the echo mechanism is TD-4.

## 6. Implementation order (vertical slices, strict TDD)

1. **V1 — the user-visible change, end to end.** `ExitEditor`/`EditorExited`,
   the `C-x C-c` binding, and the loop keying off the new event; `C-g` stops
   exiting. Rewrite the two pins in §4 first, watch them fail, then make them
   pass. This is the acceptance scenario, and it is V1 rather than V3 because
   building the command and the event before anything could exercise them is
   the framework-first ordering that hid slice 16's gap.
2. **V2 — the test sweep.** `test_terminal.py`, `test_input.py`, and the
   TermVerify scenarios. Mechanical; `EndOfInput` is the safety net.
3. **V3 — TD-5.** The pending-prefix `C-g` branch.
4. **V4 — records.** Parity rows rewritten, TD-11 edited (not removed) and its
   sweep figure corrected, TD-5 removed, README and `architecture.md` updated.
5. **V5 — adversarial review → fix → code PR (`Closes #45`) → merge.**

## 7. Risks / open questions

- **The gate question is D3**, and it is the one I would most like an explicit
  answer on: `C-x C-c` discards modified buffers for exactly one slice. The
  alternative is folding slice 18 in here and accepting two behavior changes
  plus a 50-site sweep in one slice. My recommendation is to keep them
  separate; the risk of a wide sweep silently weakening assertions is real,
  and `EndOfInput` protects the *loop* tests but not the parity ones.
- **A user who quits by habit during the window loses work exactly as they do
  today.** The window is one slice, and the failure mode is not new — but it
  is worth saying plainly rather than letting "safer" imply "safe".
- **`C-c` is now both a prefix and the second key of `C-x C-c`.** `resolve`
  checks `_PREFIX_COMMANDS` before `_PREFIXES`, so the pair wins and there is
  no ambiguity. Worth a test that pins the precedence, since a future reorder
  of those two branches would break exiting and nothing else would notice.
- **Does anything else observe `KeyboardQuitEvent` as "the run ends"?** The
  loop is the only site I found. V1 should confirm by mutation: make the loop
  ignore the event entirely and check that no test outside the two pins fails
  for the wrong reason.

## 8. Acceptance criteria

- `C-g` at top level leaves the editor running, with `Quit` on the echo row
  and the mark deactivated.
- `C-x C-c` exits with status 0, proven through the shipped executable on a
  real ConPTY, not only in-process.
- `C-x` followed by `C-g` cancels the prefix, echoes `Quit`, and deactivates
  the mark (TD-5 removed).
- No test asserts that `C-g` ends a run.
- The parity registry describes the new behavior, and the deviation that
  remains is "`C-x C-c` does not offer to save".
- TD-11 is **edited and still open**, with its sweep figure corrected; TD-5 is
  removed.
- Full gate green on 3.12–3.14 and both CI OSes; coverage floor held at 100%.
