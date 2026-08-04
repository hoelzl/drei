# Twenty-second slice: termverify 0.1.1 migration — printable tokenised readiness marker

**Status:** implemented (issue #66; migration brief #58).

**Architecture gate:** none — this is a verification-infrastructure migration,
not an editor-semantics change. The editor's behavior is untouched; what
changes is how the shipped terminal cooperates with TermVerify's epoch
detection, and the evidence the ConPTY tier produces.

**Intent.** TermVerify 0.1.1 (PyPI 2026-07-27, issue #58) replaces the OSC
7791 readiness marker with a printable, tokenised marker — ConPTY relays OSC
on a path *ahead* of rendered text, so the old marker could end an epoch
before its output arrived (termverify #232; the old evidence held by
read-timing luck). Drei must migrate its subject cooperation or the ConPTY
tier dies on abort deadlines.

## 1. Acceptance scenario

`tests/termverify/` runs green against termverify 0.1.1 with the migrated
marker, and the marker is invisible outside verification:

```
TERMVERIFY_SEED set (ConPTY spawn):
  startup        → frame + <<termverify.ready:0>> on the dedicated bottom row
  type "a"       → frame + <<termverify.ready:1>>
  resize         → frame + <<termverify.ready:2>> (a resize IS an input epoch)
  C-x C-c        → final frame, NO marker (quiescence is the process exit)
  every token fresh, decimal, from 0, one per emission; adapter honors each once

TERMVERIFY_SEED absent (production):
  no marker bytes ever; the editor renders the terminal's FULL height
```

Plus: `tests/test_terminal.py`'s fake-port pins assert the token discipline
(fresh, monotonic, addressed to the marker row, cursor restored after), and
the full gate (coverage, ruff, mypy, both pre-commit stages, build) is green.

## 2. What exists today

- `src/drei/terminal.py:162` — `READINESS_MARKER = "\x1b]7791;ready\x1b\\"`,
  emitted **unconditionally** (free while invisible): `_write_frame` after
  the cursor-position escape (mark_ready default True; False on agent-event
  frames and the quit frame), and standalone at the unresolved-key path
  (:375) and the resize/pending-prefix path (:408) with no frame rewrite.
- `tests/test_terminal.py` pins marker *counts* in the written byte stream
  (:127 startup+keys, :1003 resize adds one, :1145 quiet inputs add none).
- `tests/termverify/` scenarios never configure a marker — they ride the
  adapter default (0.1.1: prefix `<<termverify.ready:`, terminator `>>`).
  Frames are asserted via `_frame_lines(observation)` helpers.
- The undo ConPTY scenario already probes C-/ as `TextInput("\x1f")`.
- Drei pins `termverify>=0.1.0` in dev deps; the lockfile holds 0.1.0.

## 3. Decisions

- **D1 — gate on the delivered environment.** Marker emission AND the
  reserved row happen only when `TERMVERIFY_SEED` is present
  (`CooperationConstraintPorts` delivers it on every spawn; honoring a
  delivered constraint variable as the cooperation signal is exactly the
  delivered tier's purpose). Production: zero marker bytes, full-height
  editor. The layout difference under verification (one row) is disclosed
  in `terminal.py` and the scenario helper.
- **D2 — the marker gets the bottom row.** Under the gate the editor
  harness is built with height N-1 of the terminal's N rows; every marker
  emission is cursor-addressed to row N (`\x1b[{N};1H` + marker), then the
  cursor is restored to the editor cursor (addressing rides the renderer
  path, so it is stream-ordered AFTER the marker text — the adapter honors
  the marker and snapshots a screen whose cursor is back at the editor
  position). No trailing newline: on the bottom row it would scroll the
  frame away. The marker still sits on its own line — the screen-layout
  requirement — and a repaint re-emitting it carries an old token, which
  the adapter ignores (the token mechanism exists for exactly this).
- **D3 — tokens from a per-run decimal counter.** `run_editor` owns a
  counter from 0; each emission formats `<<termverify.ready:{n}>>` and
  increments. Fresh per emission (honored once each), run-stable (replay
  comparison needs stable token values), per-`run_editor` (multiple runs
  in one test process each start at 0 — module state would leak).
  The constant becomes `READINESS_MARKER_PREFIX = "<<termverify.ready:"`
  (the adapter default, so scenarios configure nothing).
- **D4 — scenario geometry +1 row, helper owns the marker row.**
  `TerminalConfiguration(rows=R+1)` keeps the editor area at today's
  geometry; `_frame_lines` (or a new `_editor_rows`) drops the bottom row,
  and ONE scenario pins the marker row's shape end-to-end (cooperation
  contract visible in evidence). Every other shifted frame assertion is
  diffed individually — #232's corrected epoch bounding may legitimately
  deliver MORE output per epoch; no blanket snapshot acceptance.
- **D5 — #165 is a no-op for drei (brief correction).** Control/Meta/Shift +
  punctuation chords are expressible in `termverify.key/v1` but
  *unencodable* — ConPTY fails closed with structured
  `unsupported: key-encoding`. The undo scenario keeps its
  `TextInput("\x1f")` C-/ arm. `("Alt","<")` encodes (`ESC <`) but delivery
  still hits the disclosed ESC-swallow (termverify #169, closed as
  disclosed). #58's "what gets easier" section is corrected in the closing
  comment.

## 4. Verification steps (TDD per step)

- **V1 — unit tier.** `uv lock --upgrade-package termverify && uv --no-config
  sync --all-groups --locked`. RED: rewrite the three marker-count pins and
  add pins for D1–D3 (gate off → zero markers + full height; gate on →
  marker row addressing, cursor restore, tokens 0,1,2…, none on the quit
  frame). Implement D1–D3 in `terminal.py`. GREEN; full unit suite.
- **V2 — ConPTY tier.** RED first: run `tests/termverify/` — with drei still
  emitting nothing the adapter honors at the old geometry, epochs should die
  on abort deadlines (observe it; that IS the tier going red for the
  finding's reason). Then D4 (geometry +1, helper, marker-row pin); each
  shifted assertion diffed and its justification in the commit message.
  GREEN.
- **V3 — records.** Close #58 with the #165 correction (D5) and the
  landed-shape summary; grep `docs/knowledge/emacs-parity.md` for
  marker/OSC/epoch references and amend drift; update the
  `termverify-integration` skill's cooperation contract to 0.1.1 (printable
  tokenised marker, screen-cell cost, dedicated-row + cursor-restore
  pattern, TERMVERIFY_SEED gate, #165 encoding limits).
- **V4 — gate, review, PR.** Full gate; fresh-agent adversarial review of
  the range; merge on CLEAN + green checks. PR closes #66.

## 5. Pins that change

- `tests/test_terminal.py:127`, `:1003`, `:1145` — marker counting moves
  from a constant string to prefix-scan + token discipline (V1).
- `tests/termverify/` frame assertions — geometry shift via the helper;
  individually diffed (V2).
- Predicted: no unit pins outside `test_terminal.py` touch the marker (the
  harness never emits it — emission is terminal-loop only).

## 6. Registry and documentation

- `docs/knowledge/emacs-parity.md` rows referencing the readiness marker /
  OSC / epoch mechanics (the resize-observation row names the marker
  contract) — amend to the printable-marker shape.
- `src/drei/terminal.py` docstrings — the cooperation contract comment at
  :158–162 rewritten; the gate (D1) and reserved row (D2) disclosed.
- `termverify-integration` skill — 0.1.1 contract (see V3).

## 7. Risks

- **R1 — evidence faithfulness.** Under the gate the editor renders one row
  fewer than production at the same terminal size. Mitigation: D4 keeps the
  *editor* geometry identical to today's scenarios; the divergence is one
  disclosed cooperation row, same class as the old invisible OSC (which also
  existed only for the verifier). This is the cost termverify's own docs
  name ("markers occupy screen cells and appear in frame evidence").
- **R2 — repaint re-emission.** A ConPTY repaint (resize, scroll, teardown)
  re-sends the marker row with an old token; the adapter honors each token
  once, so this is safe by construction — but the resize scenario must still
  observe exactly one NEW marker per resize epoch (pinned).
- **R3 — DA-stall floor.** 0.1.1 discloses conhost's ~3.1 s device-attributes
  stall; abort deadlines at or below it fail every start by policy. Drei's
  scenarios use 10 s — unaffected, but noted so a future tightening doesn't
  trip it.
- **R4 — frame drift beyond geometry.** Correctly-bounded epochs can deliver
  more output per epoch than the racy reads did; D4's per-assertion diffing
  is the guard. If a shift is NOT explicable by geometry or epoch bounding,
  stop and investigate before accepting.

## 8. Open questions (gate only)

- ~~None pending~~ — D1's gate variable and D2's bottom-row pattern were the
  candidate alternatives (a `DREI_`-owned env var vs the delivered
  `TERMVERIFY_SEED`; echo-row overlay vs dedicated row). Chosen as recorded;
  the reviewer may challenge them at the PR gate.

## 9. Implementation honesty record

1. **The written V1/V2 order was contradictory.** V1 said to implement the
   printable marker before V2 observed the old marker fail. Execution instead
   raised the dependency floor and lock to 0.1.1 first, then observed
   `test_shipped_editor_terminal_scenario` fail at startup with the expected
   10,000 ms readiness deadline before changing `terminal.py`.
2. **A raw marker row is space-padded by the ConPTY screen model.** The one
   physical-row assertion therefore full-matches the marker followed only by
   padding; editor-row helpers still drop exactly one fixed bottom row and do
   no content filtering.
3. **Correct epoch bounding exposed the existing asynchronous-redraw gap.** In
   `test_shipped_editor_cancels_a_held_turn_with_c_g`, an unmarked agent redraw
   can replace the transient `Quit` frame before the completed key epoch is
   returned. The terminal scenario now proves the durable cancelled transcript
   and successful recovery; direct loop tests continue to pin the `Quit` echo.
   No product behavior changed.
4. Apart from that cancellation assertion and the explicit marker-row pin,
   editor-frame assertions remained unchanged: every scenario adds one
   physical row and its helper removes exactly that row. The C-/ scenario kept
   `TextInput("\x1f")` as D5 required.
5. **Fresh review narrowed the one-row assumption at narrow widths.** A
   marker written from the physical bottom row scrolls when it wraps; the
   immediate cursor restoration can then disturb its rendered stream, and a
   width that worked for token 9 can fail when token 10 adds one cell. Marker
   addressing is now bottom-aligned from the first row that lets the complete
   marker end without scrolling, and address + marker + cursor restoration are
   one write. Ordinary scenario widths still use exactly the dedicated final
   row; unusually narrow verification frames may have marker text overlay the
   lowest editor rows. Live ConPTY pins cover width 10 with a nine-row screen
   at startup, token growth through 10, and resize from width 40 to 10.
6. **Fresh review also found a cardinality error.** One physical character can
   resolve both an abandoned escape prefix and the character that broke it.
   The first implementation emitted a marker for each resolved logical key,
   leaving a fresh token buffered to complete the next adapter epoch. The loop
   now emits at most one marker, after the final resolution of each physical
   input. This path remains undeliverable through the current Windows ConPTY
   ESC limitation, but the subject contract is correct for future adapters.
7. **A second fresh review found the hard screen-capacity boundary.** If the
   complete ConPTY screen has fewer cells than the marker, no starting row can
   prevent scrolling. Live probes reproduced startup failure (21×1, 10×1,
   10×2, and 1×9), token-growth failure at 22×1 when token 10 reaches 23
   cells, and wide-to-narrow resize failure. Newline framing, autowrap control,
   and natural wrapping without cursor restoration did not make the complete
   candidate observable. The accepted Drei scenarios all have enough capacity;
   the adapter-level limitation is tracked as TermVerify #287 rather than
   weakening the normal reserved-row/cursor-restoration contract or pretending
   a fake port proves live readiness.
