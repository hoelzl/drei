# First Editor Vertical Slice Implementation Plan

> **For Hermes:** Implement this as one coherent reviewable slice. Use strict TDD for every behavior change and do not open intermediate PRs for incomplete tracer bullets. **Owner approval of this revised plan is required before Task 0.**

**Goal:** Ship the first real Drei behavior: edit and render one in-memory buffer through one production command path shared by an in-process harness and the terminal executable.

**Architecture:** Follow accepted design 0002. A stable `Buffer` identity shell owns a small immutable `BufferValue`; frozen command, event, observation, frame, and outcome records cross the session boundary. The terminal frontend and direct harness only adapt inputs/outputs—they do not reimplement semantics. Start with Python `str` storage; the spike explicitly left production text storage open, and this slice does not justify a rope or piece tree.

**Tech stack:** Python 3.12–3.14, standard library only, pytest, Hypothesis, Ruff, strict mypy, uv, TermVerify for shipped-terminal evidence.

**External evidence tools:**
- **TermVerify:** add as a dev dependency once its publishing workflow is stable. Until then, document the local invocation and defer CI integration. Do not rely on an ambient host installation.
- **GNU Emacs:** pin to a known current version via container or pinned CI runner image. Recursive://Neon pins `ubuntu-24.04` + `emacs-nox` (GNU Emacs 29.3) in CI; Drei should follow the same pattern rather than relying on an arbitrary host Emacs.

---

## Current context and assumptions

- PR #1 merged as remote `main` commit `9166812`; every GitHub CI and security check passed.
- Local `spike/live-model-architecture` and local `main` still point to pre-merge commit `0772ef7`; synchronize before implementation.
- Production code currently exposes only package identity and `drei --version`.
- Design 0002 selects hybrid ownership, immutable evidence, explicit effects, serialized commands, and atomic failure behavior.
- The first-slice contract is `docs/agent/plans/0001-first-editor-slice.md`.
- Deferred: files, undo, kill ring, minibuffer, multiple buffers/windows, major modes, syntax highlighting, extensions, production-scale text storage, and marker/window-point properties.
- **Environment fact:** TermVerify and GNU Emacs are not currently on `PATH` on this Windows host. Their tasks below include explicit discovery/inspection steps before any scenario is written.

## Proposed production modules

- Create `src/drei/model.py` — stable buffer shell and immutable buffer value.
- Create `src/drei/commands.py` — frozen semantic commands and event records.
- Create `src/drei/session.py` — sole command dispatcher, immutable outcomes, replay.
- Create `src/drei/render.py` — immutable fixed-size frame and cursor projection.
- Create `src/drei/keys.py` — symbolic input to semantic-command resolution.
- Create `src/drei/harness.py` — in-process adapter over the production session.
- Create `src/drei/terminal.py` — minimal raw-terminal adapter over the same harness/session.
- Modify `src/drei/cli.py` — retain `--version`; launch the terminal frontend otherwise.
- Add focused tests under `tests/` mirroring those boundaries.

---

### Task 0: Synchronize after PR #1

**Objective:** Start from the exact merged candidate and remove the obsolete local feature branch.

**Steps:**

1. Confirm a clean working tree.
2. Fetch and prune remote branches.
3. Switch to `main`.
4. Fast-forward local `main` to `origin/main`; do not rebase or recreate the merge.
5. Confirm `spike/live-model-architecture` is fully merged into `origin/main`, then delete the local branch.
6. Create `feat/first-editor-slice` from merged `main`.
7. Verify `git rev-parse HEAD` equals `git rev-parse origin/main` and run the bootstrap tests once.

**Commands:**

```bash
git status --short --branch
git fetch --prune
git switch main
git merge --ff-only origin/main
git merge-base --is-ancestor spike/live-model-architecture origin/main && git branch -d spike/live-model-architecture
git switch -c feat/first-editor-slice
uv --no-config run pytest --cov --cov-report=term-missing
```

---

### Task 1: Define the stable buffer shell and immutable value

**Objective:** Establish the smallest production ownership boundary justified by design 0002.

**Files:**

- Create: `src/drei/model.py`
- Create: `tests/test_model.py`

**Contract:**

- `BufferId` is a frozen value with explicit equality.
- `BufferValue` is frozen and contains only `text: str` and `point: int`.
- `Buffer` is a stable runtime-owned shell with `buffer_id` and a private current `BufferValue`.
- Construction rejects points outside `0 <= point <= len(text)`.
- Replacing a value validates the same invariant without replacing the shell.
- Tests retain a `Buffer` reference, replace its value, and prove the shell remains current while the old value remains unchanged.

**Step 1: Write failing tests**

```python
# tests/test_model.py
import pytest

from drei.model import Buffer, BufferId, BufferValue


def test_buffer_value_rejects_negative_point() -> None:
    with pytest.raises(ValueError, match="point"):
        BufferValue(text="", point=-1)


def test_buffer_value_rejects_point_past_end() -> None:
    with pytest.raises(ValueError, match="point"):
        BufferValue(text="abc", point=4)


def test_buffer_shell_identity_is_stable() -> None:
    shell = Buffer(BufferId("scratch"), BufferValue(text="", point=0))
    first_value = shell.current
    shell.replace(BufferValue(text="x", point=1))
    assert shell.current is not first_value
    assert shell.current.text == "x"
    assert first_value.text == ""


def test_buffer_id_structural_equality() -> None:
    assert BufferId("a") == BufferId("a")
    assert BufferId("a") != BufferId("b")
```

**Step 2: Run test to verify failure**

Run: `uv --no-config run pytest tests/test_model.py -q`
Expected: FAIL — `ModuleNotFoundError` / import errors

**Step 3: Write minimal implementation**

```python
# src/drei/model.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BufferId:
    value: str


@dataclass(frozen=True, slots=True)
class BufferValue:
    text: str
    point: int

    def __post_init__(self) -> None:
        if not 0 <= self.point <= len(self.text):
            raise ValueError(
                f"point {self.point} outside 0..{len(self.text)}"
            )


class Buffer:
    def __init__(self, buffer_id: BufferId, initial: BufferValue) -> None:
        self.buffer_id = buffer_id
        self._current = initial

    @property
    def current(self) -> BufferValue:
        return self._current

    def replace(self, value: BufferValue) -> None:
        self._current = value
```

**Step 4: Run test to verify pass**

Run: `uv --no-config run pytest tests/test_model.py -q`
Expected: 4 passed

**Step 5: Run wider checks**

Run: `uv --no-config run ruff check src/drei/model.py tests/test_model.py`
Run: `uv --no-config run mypy src/drei/model.py tests/test_model.py`
Expected: clean

**Step 6: Commit**

```bash
git add src/drei/model.py tests/test_model.py
git commit -m "feat: stable buffer shell and immutable buffer value"
```

---

### Task 2: Add frozen commands, events, observations, and outcomes

**Objective:** Define immutable evidence values before command execution.

**Files:**

- Create: `src/drei/commands.py`
- Create: `tests/test_records.py`

**Contract:**

- Commands: `InsertText(text)`, `ForwardChar()`, `BackwardChar()`, and `KeyboardQuit()`.
- Events: insertion with before/after points and inserted text; point movement with requested/actual displacement; keyboard quit.
- `BufferObservation` contains buffer ID, full text, and point and is never authoritative live state.
- `CommandOutcome` contains an ordered event tuple and the resulting observation.
- All records are frozen, slot-based dataclasses and compare structurally.
- **Pinned decision:** Empty insertion is accepted as a deterministic no-op with no event.

**Step 1: Write failing tests**

```python
# tests/test_records.py
import pytest

from drei.commands import (
    BackwardChar,
    BufferObservation,
    CommandOutcome,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KeyboardQuitEvent,
    PointMoved,
    TextInserted,
)


def test_records_are_frozen() -> None:
    with pytest.raises(AttributeError):
        InsertText("x").text = "y"  # type: ignore[misc]


def test_structural_equality() -> None:
    assert InsertText("x") == InsertText("x")
    assert ForwardChar() == ForwardChar()
    assert BackwardChar() == BackwardChar()
    assert KeyboardQuit() == KeyboardQuit()


def test_event_records_carry_expected_fields() -> None:
    inserted = TextInserted(text="ab", before=0, after=2)
    assert inserted.text == "ab"
    assert inserted.before == 0
    assert inserted.after == 2

    moved = PointMoved(requested=1, actual=1)
    assert moved.requested == 1
    assert moved.actual == 1

    quit_event = KeyboardQuitEvent()
    assert quit_event == KeyboardQuitEvent()


def test_observation_and_outcome_are_values() -> None:
    obs = BufferObservation(buffer_id="scratch", text="x", point=1)
    outcome = CommandOutcome(events=(TextInserted("x", 0, 1),), observation=obs)
    assert outcome.observation is obs
```

**Step 2: Run test to verify failure**

Run: `uv --no-config run pytest tests/test_records.py -q`
Expected: FAIL — import errors

**Step 3: Write minimal implementation**

```python
# src/drei/commands.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InsertText:
    text: str


@dataclass(frozen=True, slots=True)
class ForwardChar:
    pass


@dataclass(frozen=True, slots=True)
class BackwardChar:
    pass


@dataclass(frozen=True, slots=True)
class KeyboardQuit:
    pass


@dataclass(frozen=True, slots=True)
class TextInserted:
    text: str
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class PointMoved:
    requested: int
    actual: int


@dataclass(frozen=True, slots=True)
class KeyboardQuitEvent:
    pass


@dataclass(frozen=True, slots=True)
class BufferObservation:
    buffer_id: str
    text: str
    point: int


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    events: tuple[TextInserted | PointMoved | KeyboardQuitEvent, ...]
    observation: BufferObservation
```

**Step 4–6:** Run focused tests, Ruff, mypy, then commit `feat: frozen command and evidence records`.

---

### Task 3: Implement the serialized command boundary

**Objective:** Execute insertion and bounded movement through one production dispatcher.

**Files:**

- Create: `src/drei/session.py`
- Create: `tests/test_session.py`

**Contract:**

- `EditorSession` owns one stable `Buffer` shell.
- `dispatch(command)` is the only semantic mutation path.
- Insertion occurs at point and advances point by inserted character count.
- Forward/backward movement clamps at buffer bounds and reports actual displacement.
- `KeyboardQuit` does not alter buffer text or point and emits its event.
- Every accepted command returns one immutable `CommandOutcome` based on the current live value.
- A command failure restores the prior `BufferValue` and emits no event. Exercise failure via a deliberately invalid `BufferValue` constructed inside a test double; do not add a production “fail” command.
- The session stores an ordered immutable event transcript sufficient for replay comparison.

**Step 1: Write failing tests**

```python
# tests/test_session.py
import pytest

from drei.commands import (
    BackwardChar,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KeyboardQuitEvent,
    PointMoved,
    TextInserted,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def make_session() -> EditorSession:
    return EditorSession(Buffer(BufferId("scratch"), BufferValue("", 0)))


def test_insert_into_empty_buffer() -> None:
    session = make_session()
    outcome = session.dispatch(InsertText("hello"))
    assert outcome.observation.text == "hello"
    assert outcome.observation.point == 5
    assert outcome.events == (TextInserted("hello", 0, 5),)


def test_insert_in_middle() -> None:
    session = make_session()
    session.dispatch(InsertText("ac"))
    outcome = session.dispatch(BackwardChar())
    outcome = session.dispatch(InsertText("b"))
    assert outcome.observation.text == "abc"
    assert outcome.observation.point == 2


def test_move_within_bounds() -> None:
    session = make_session()
    session.dispatch(InsertText("ab"))
    outcome = session.dispatch(BackwardChar())
    assert outcome.observation.point == 1
    assert outcome.events == (PointMoved(1, 1),)


def test_clamp_at_beginning_and_end() -> None:
    session = make_session()
    session.dispatch(InsertText("ab"))
    outcome = session.dispatch(ForwardChar())
    assert outcome.observation.point == 2
    assert outcome.events == (PointMoved(1, 0),)

    session.dispatch(BackwardChar())
    session.dispatch(BackwardChar())
    outcome = session.dispatch(BackwardChar())
    assert outcome.observation.point == 0
    assert outcome.events == (PointMoved(-1, 0),)


def test_quit_does_not_mutate() -> None:
    session = make_session()
    session.dispatch(InsertText("x"))
    outcome = session.dispatch(KeyboardQuit())
    assert outcome.observation.text == "x"
    assert outcome.observation.point == 1
    assert outcome.events == (KeyboardQuitEvent(),)


def test_retained_shell_reference_stays_current() -> None:
    shell = Buffer(BufferId("scratch"), BufferValue("", 0))
    session = EditorSession(shell)
    session.dispatch(InsertText("x"))
    assert shell.current.text == "x"


def test_failure_is_atomic() -> None:
    session = make_session()
    session.dispatch(InsertText("ok"))
    before = session.buffer.current
    before_events = len(session.transcript)

    class BadCommand:
        pass

    with pytest.raises(TypeError, match="unsupported"):
        session.dispatch(BadCommand())  # type: ignore[arg-type]

    assert session.buffer.current is before
    assert len(session.transcript) == before_events
```

**Step 2: Run test to verify failure**

Run: `uv --no-config run pytest tests/test_session.py -q`
Expected: FAIL — import errors

**Step 3: Write minimal implementation**

```python
# src/drei/session.py
from __future__ import annotations

from typing import assert_never

from drei.commands import (
    BackwardChar,
    BufferObservation,
    CommandOutcome,
    ForwardChar,
    InsertText,
    KeyboardQuit,
    KeyboardQuitEvent,
    PointMoved,
    TextInserted,
)
from drei.model import Buffer, BufferValue

Command = InsertText | ForwardChar | BackwardChar | KeyboardQuit
Event = TextInserted | PointMoved | KeyboardQuitEvent


class EditorSession:
    def __init__(self, buffer: Buffer) -> None:
        self.buffer = buffer
        self._transcript: list[Event] = []

    @property
    def transcript(self) -> tuple[Event, ...]:
        return tuple(self._transcript)

    def dispatch(self, command: Command) -> CommandOutcome:
        current = self.buffer.current
        events: list[Event] = []
        new_value: BufferValue

        match command:
            case InsertText(text=text):
                if text:
                    before = current.point
                    after = before + len(text)
                    new_text = (
                        current.text[:before] + text + current.text[before:]
                    )
                    new_value = BufferValue(new_text, after)
                    events.append(TextInserted(text, before, after))
                else:
                    new_value = current
            case ForwardChar():
                new_point = min(current.point + 1, len(current.text))
                actual = new_point - current.point
                new_value = BufferValue(current.text, new_point)
                events.append(PointMoved(1, actual))
            case BackwardChar():
                new_point = max(current.point - 1, 0)
                actual = new_point - current.point
                new_value = BufferValue(current.text, new_point)
                events.append(PointMoved(-1, actual))
            case KeyboardQuit():
                new_value = current
                events.append(KeyboardQuitEvent())
            case _:
                raise TypeError(f"unsupported command: {type(command)}")

        try:
            self.buffer.replace(new_value)
        except ValueError:
            self.buffer.replace(current)
            raise

        self._transcript.extend(events)
        observation = BufferObservation(
            buffer_id=self.buffer.buffer_id.value,
            text=new_value.text,
            point=new_value.point,
        )
        return CommandOutcome(tuple(events), observation)
```

**Step 4–6:** Run focused tests, Ruff, mypy, then commit `feat: serialized editor session with atomic dispatch`.

---

### Task 4: Add deterministic replay and property tests

**Objective:** Prove command histories preserve invariants and reproduce evidence.

**Files:**

- Modify: `src/drei/session.py`
- Create: `tests/test_session_properties.py`

**Properties:**

- Point always satisfies `0 <= point <= len(text)`.
- Insertion preserves all preexisting text in order.
- Replaying a frozen command tuple from the same initial value produces exactly the same outcome tuple, transcript, and final observation.
- Forward then backward restores point when neither operation clamps.
- Shell identity remains stable across every generated history.
- **Marker/window-point properties are explicitly deferred** to a later slice that introduces windows; design 0002 stress cases do not require them for a single-buffer slice.

**Hypothesis settings (pin determinism):**

```python
from hypothesis import settings

settings.register_profile("ci", max_examples=50, derandomize=True, deadline=None)
settings.load_profile("ci")
```

**Step 1: Write failing property tests**

```python
# tests/test_session_properties.py
from hypothesis import given, settings, strategies as st

from drei.commands import BackwardChar, ForwardChar, InsertText
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


@st.composite
def command_history(draw: st.DrawFn) -> list[object]:
    size = draw(st.integers(min_value=0, max_value=20))
    return [
        draw(
            st.one_of(
                st.builds(InsertText, st.text(min_size=0, max_size=5)),
                st.just(ForwardChar()),
                st.just(BackwardChar()),
            )
        )
        for _ in range(size)
    ]


@given(command_history())
def test_point_always_in_bounds(history: list[object]) -> None:
    session = EditorSession(Buffer(BufferId("scratch"), BufferValue("", 0)))
    for command in history:
        session.dispatch(command)  # type: ignore[arg-type]
        current = session.buffer.current
        assert 0 <= current.point <= len(current.text)


@given(command_history())
def test_replay_produces_identical_evidence(history: list[object]) -> None:
    def run() -> tuple[tuple[object, ...], str, int]:
        session = EditorSession(Buffer(BufferId("scratch"), BufferValue("", 0)))
        outcomes = tuple(session.dispatch(c) for c in history)  # type: ignore[arg-type]
        current = session.buffer.current
        return outcomes, current.text, current.point

    first, text1, point1 = run()
    second, text2, point2 = run()
    assert first == second
    assert text1 == text2
    assert point1 == point2


@given(command_history())
def test_insertion_preserves_existing_text(history: list[object]) -> None:
    session = EditorSession(Buffer(BufferId("scratch"), BufferValue("", 0)))
    for command in history:
        if isinstance(command, InsertText) and command.text:
            before = session.buffer.current
            session.dispatch(command)
            after = session.buffer.current
            assert after.text[: before.point] == before.text[: before.point]
            assert after.text[after.point :] == before.text[before.point :]
```

**Step 2–5:** Run focused properties, add explicit regression examples, run Ruff/mypy, then commit `test: hypothesis properties for session invariants and replay`.

---

### Task 5: Render an immutable fixed-size frame

**Objective:** Project semantic state into deterministic terminal-shaped data without terminal I/O.

**Files:**

- Create: `src/drei/render.py`
- Create: `tests/test_render.py`

**Contract:**

- `Frame` is frozen and contains exactly `height` rows, cursor row/column, and dimensions.
- Layout reserves the final two rows for modeline and echo area when `height >= 2`.
- When `height == 1`, the single row is the modeline (body is omitted).
- When `height == 0`, the frame has zero rows and cursor is clamped to `(0, 0)`.
- Body renders buffer text with deterministic clipping and padding.
- Modeline identifies Drei and the buffer; echo area reflects keyboard quit.
- Cursor is always within the returned frame.
- Widths of 0, 1, and 2 are explicitly defined:
  - `width == 0`: every row is empty string; cursor column is 0.
  - `width == 1`: modeline shows `D`; body shows at most one character per line.
  - `width == 2`: modeline shows `D:`; body shows at most two characters per line.
- Rendering consumes `BufferObservation` plus presentation state; it never reads or mutates the live buffer shell.

**Step 1: Write failing tests**

```python
# tests/test_render.py
from drei.commands import BufferObservation
from drei.render import Frame, render


def obs(text: str, point: int, buffer_id: str = "scratch") -> BufferObservation:
    return BufferObservation(buffer_id, text, point)


def test_empty_buffer_frame() -> None:
    frame = render(obs("", 0), width=10, height=4)
    assert frame.rows == (
        "          ",
        "          ",
        "Drei: scratch",
        "",
    )
    assert frame.cursor == (0, 0)


def test_inserted_text_frame() -> None:
    frame = render(obs("hello", 5), width=10, height=4)
    assert frame.rows == (
        "hello     ",
        "          ",
        "Drei: scratch",
        "",
    )
    assert frame.cursor == (0, 5)


def test_multiline_frame() -> None:
    frame = render(obs("ab\ncd", 4), width=10, height=4)
    assert frame.rows == (
        "ab        ",
        "cd        ",
        "Drei: scratch",
        "",
    )
    assert frame.cursor == (1, 2)


def test_horizontal_clipping() -> None:
    frame = render(obs("abcdef", 3), width=4, height=3)
    assert frame.rows == (
        "abcd",
        "Drei",
        "",
    )
    assert frame.cursor == (0, 3)


def test_height_one_is_modeline_only() -> None:
    frame = render(obs("x", 1), width=10, height=1)
    assert frame.rows == ("Drei: scr ",)
    assert frame.cursor == (0, 0)


def test_height_zero() -> None:
    frame = render(obs("x", 1), width=10, height=0)
    assert frame.rows == ()
    assert frame.cursor == (0, 0)


def test_width_zero() -> None:
    frame = render(obs("x", 1), width=0, height=3)
    assert frame.rows == ("", "", "")
    assert frame.cursor == (0, 0)


def test_echo_area_reflects_quit() -> None:
    frame = render(obs("", 0), width=10, height=4, echo="Quit")
    assert frame.rows == (
        "          ",
        "          ",
        "Drei: scratch",
        "Quit      ",
    )
```

**Step 2–6:** Run RED, implement `render.py`, run GREEN + Ruff + mypy, then commit `feat: deterministic fixed-size frame renderer`.

---

### Task 6: Resolve symbolic keys to commands

**Objective:** Separate key encoding from semantic commands.

**Files:**

- Create: `src/drei/keys.py`
- Create: `tests/test_keys.py`

**Contract:**

- Printable Unicode text resolves to `InsertText`.
- `C-f`, `C-b`, and `C-g` resolve to forward, backward, and quit.
- Unsupported symbolic keys return an explicit unresolved result; they do not mutate state.
- Resolution is pure and deterministic.

**Step 1: Write failing tests**

```python
# tests/test_keys.py
from drei.commands import BackwardChar, ForwardChar, InsertText, KeyboardQuit
from drei.keys import UnresolvedKey, resolve


def test_printable_text_resolves_to_insert() -> None:
    assert resolve("a") == InsertText("a")
    assert resolve("λ") == InsertText("λ")
    assert resolve(" ") == InsertText(" ")


def test_control_keys_resolve_to_commands() -> None:
    assert resolve("C-f") == ForwardChar()
    assert resolve("C-b") == BackwardChar()
    assert resolve("C-g") == KeyboardQuit()


def test_unsupported_key_is_explicitly_unresolved() -> None:
    result = resolve("C-x")
    assert isinstance(result, UnresolvedKey)
    assert result.key == "C-x"
```

**Step 2–6:** Run RED, implement `keys.py`, run GREEN + Ruff + mypy, then commit `feat: symbolic key resolution`.

---

### Task 7: Add the in-process production harness

**Objective:** Give tests and agents a semantic adapter over the real session path.

**Files:**

- Create: `src/drei/harness.py`
- Create: `tests/test_harness.py`

**Contract:**

- The harness accepts symbolic keys, calls the production resolver and session dispatcher, then calls the production renderer.
- It exposes immutable latest observation, frame, accepted outcomes, and unresolved-input records.
- Direct harness evidence for `text -> C-b -> text -> C-f -> C-g` has exact expected observations, events, and frame.
- The harness contains no duplicate edit, movement, or render logic.

**Step 1: Write failing tests**

```python
# tests/test_harness.py
from drei.harness import EditorHarness


def test_harness_produces_exact_evidence() -> None:
    harness = EditorHarness(width=20, height=5)
    harness.send("hello")
    harness.send("C-b")
    harness.send("!")
    harness.send("C-f")
    harness.send("C-g")

    assert harness.observation.text == "hell!o"
    assert harness.observation.point == 6
    assert harness.frame.rows[0] == "hell!o              "
    assert harness.frame.cursor == (0, 6)
    assert harness.outcomes[-1].events == (KeyboardQuitEvent(),)
```

**Step 2–6:** Run RED, implement `harness.py`, run GREEN + Ruff + mypy, then commit `feat: in-process editor harness`.

---

### Task 8: Build the minimal shipped terminal frontend

**Objective:** Run the same production path through a real raw terminal on Windows and Linux.

**Files:**

- Create: `src/drei/terminal.py`
- Modify: `src/drei/cli.py`
- Create: `tests/test_terminal.py`
- Create: `tests/test_cli.py`

**Constraints:**

- Preserve `drei --version` exactly.
- With no bootstrap option, enter the editor only when stdin/stdout are TTYs; write `drei: stdin and stdout must be TTYs` to stderr and exit with code `2` otherwise.
- **Readiness marker:** before entering raw mode, write `DREI:READY\n` to stdout and flush. TermVerify can wait for this literal line without sleeps.
- **Key decoding:** read one Unicode character at a time. Map byte `0x06` → `C-f`, `0x02` → `C-b`, `0x07` → `C-g`. Every other non-printable byte is `UnresolvedKey`. On Windows, `msvcrt.getwch()` returns `\x06` etc. directly; multi-byte arrow sequences are explicitly out of scope for this slice.
- **Windows strategy:** use `msvcrt.getwch()` for input. For raw mode, use `ctypes` to call `GetConsoleMode`/`SetConsoleMode` on the stdin handle, clearing `ENABLE_ECHO_INPUT | ENABLE_LINE_INPUT | ENABLE_PROCESSED_INPUT`. No third-party dependency.
- **Restoration contract:** save terminal modes on entry; restore in a `try/finally` block that covers normal exit, `C-g` exit, and exceptions. Register `atexit` as a backstop. Test restoration with a fake port.
- Keep native I/O behind a narrow adapter; session and renderer remain platform-independent.
- **Coverage:** platform-specific raw-mode shims (`termios` and `msvcrt` branches) are allowed a targeted `# pragma: no cover` with a comment explaining why; the deterministic adapter logic must remain at 100%.

**Step 1: Write failing tests**

```python
# tests/test_terminal.py
import io

from drei.terminal import TerminalPort, run_editor


class FakePort(TerminalPort):
    def __init__(self, inputs: list[str]) -> None:
        self.inputs = inputs
        self.outputs: list[str] = []
        self.restored = False

    def read_key(self) -> str:
        return self.inputs.pop(0)

    def write(self, text: str) -> None:
        self.outputs.append(text)

    def flush(self) -> None:
        pass

    def restore(self) -> None:
        self.restored = True


def test_editor_writes_readiness_and_exits_on_quit() -> None:
    port = FakePort(["a", "\x07"])
    run_editor(port, width=10, height=3)
    assert port.outputs[0] == "DREI:READY\n"
    assert port.restored


def test_editor_rejects_non_tty(capsys) -> None:
    # cli-level test in tests/test_cli.py
    pass
```

**Step 2–6:** Run RED with fake port, implement `terminal.py` and `cli.py` adapter, run GREEN + Ruff + mypy, then commit `feat: raw terminal frontend over production session`.

---

### Task 9: Prove the shipped frontend with TermVerify

**Objective:** Capture terminal integration evidence on the real executable.

**Files:**

- Create: `tests/termverify/` scenario files in the format supported by the installed TermVerify version.
- Modify: `docs/developer-guide/development.md` with the exact verified invocation.
- Modify: CI/pre-push configuration only if the TermVerify boundary can run reliably on both supported OS families.

**Steps:**

1. Add TermVerify as a dev dependency in `pyproject.toml` once its publishing workflow is stable. Until then, document the exact local invocation and keep TermVerify scenarios out of CI.
2. Inspect the installed TermVerify CLI help and current project conventions; do not invent flags. Run:
   ```bash
   uv --no-config run termverify --help
   ```
   If TermVerify is not yet available as a dependency, record the blocker and defer scenario authoring until it is.
3. Build the smallest scenario: wait for `DREI:READY`, insert text, move backward/forward, send `C-g`, assert clean exit and terminal frame/cursor evidence.
4. Record the exact scenario file format in this plan after inspection (YAML/JSON/TermVerify DSL) and update `development.md` with the verified invocation.
5. Run on local Windows and the available Linux boundary. If TermVerify cannot capture required evidence, reduce the gap to a concrete failing test and address/file it under TermVerify's conventions before weakening Drei's acceptance.
6. **No snapshot or divergence baseline may be auto-approved.**

**Open environment issue:** TermVerify is not currently on `PATH` on this Windows host, and its publishing workflow is not yet stable. Resolve installation before starting this task.

---

### Task 10: Add one pinned GNU Emacs differential scenario

**Objective:** Classify the slice's intentional parity boundary.

**Files:**

- Create: `tests/differential/` harness/scenario files.
- Create: `docs/knowledge/emacs-parity.md` or the narrower established parity document after checking repository conventions.

**Scenario:** startup in an empty scratch-like buffer, insert text, move one character backward and forward.

**Specification:**

- **Emacs source:** pin via container or pinned CI runner image. Follow the Recursive://Neon pattern: `ubuntu-24.04` + `apt-get install -y emacs-nox` yields GNU Emacs 29.3. Record the exact `emacs --version` output in the test file and in `docs/knowledge/emacs-parity.md`.
- **Local development:** use a container image (e.g., `docker run --rm -v "${PWD}:/src" ubuntu:24.04 bash -c "apt-get update && apt-get install -y emacs-nox && emacs --version"`) or document the exact local Emacs version if a container is unavailable. Do not rely on an arbitrary host installation.
- **Emacs invocation:** `emacs -Q --batch --eval "(progn (insert \"hello\") (backward-char) (forward-char) (message \"point=%s text=%s\" (point) (buffer-string)))"` (exact form to be validated against the pinned Emacs version).
- **Normalized observation:** parse the `message` output into `{point: int, text: str}` and compare with Drei's semantic observation for the same command sequence.
- **Availability:** if no pinned Emacs is available, the test is skipped with `pytest.mark.skipif(shutil.which("emacs") is None, reason="pinned GNU Emacs not available")`. Do not fabricate a baseline.
- **CI:** add a dedicated parity job pinned to `ubuntu-24.04` + `emacs-nox`, following the Recursive://Neon pattern. Do not add Emacs to the main test matrix.

**Open environment issue:** GNU Emacs is not currently on `PATH` on this Windows host. The scenario must be validated against a pinned container or CI runner before the baseline is recorded.

---

### Task 11: Close documentation and quality gates

**Objective:** Make maintained prose reflect shipped behavior and verify the exact candidate.

**Files:**

- Modify: `README.md` — replace “Bootstrap only” with the exact first-slice capability, no broader editor claim.
- Modify: `docs/agent/plans/0001-first-editor-slice.md` — mark complete only after direct and terminal acceptance passes.
- Modify: `docs/knowledge/architecture.md` only if production implementation clarifies the accepted ownership pattern.
- Modify: `docs/developer-guide/development.md` with verified direct, terminal, and differential commands.

**Full validation:**

```bash
uv --no-config sync --all-groups --locked
uv --no-config run pytest --cov --cov-report=term-missing
uv --no-config run ruff check .
uv --no-config run ruff format --check .
uv --no-config run mypy src tests spikes/001-editor-state-architecture
uv --no-config run pre-commit run --all-files
uv --no-config run pre-commit run --hook-stage pre-push --all-files
uv --no-config build
uv --no-config --preview-features audit-command audit --locked
git diff --check
```

Also run the exact verified TermVerify and GNU Emacs differential commands established in Tasks 9–10.

Freeze the staged tree and binary-diff hash, obtain an independent fail-closed review of security, ownership, semantics, evidence, and claim accuracy, then commit/push/open one PR only after all blocking findings are remediated. Do not merge automatically if a parity, terminal-evidence, or architecture invariant needs owner judgment.

## Key risks and controls

- **Premature framework:** Keep one buffer and four commands; no generalized mode/keymap/extension framework.
- **Observation becoming authority:** Tests should retain the shell and inspect canonical observations, but production writes only through the session owner.
- **Terminal semantics drift:** Terminal and harness must call the same resolver/session/renderer modules.
- **Text-store overcommitment:** Use `str` until measured production behavior disproves it.
- **Hidden platform behavior:** Isolate terminal effects and prove restoration on Windows and Linux.
- **Self-fulfilling replay tests:** Pin independent expected values for representative histories before asserting replay equality.
- **False completion claims:** Do not change “Bootstrap only” or plan status until shipped TUI and TermVerify evidence pass.
- **Missing tool baselines:** Do not pin TermVerify or Emacs versions until they are discovered on a host where they are installed. TermVerify is added as a dev dependency only after its publishing workflow is stable; Emacs is pinned via container or pinned CI runner image, not via host installation.

## Open questions settled during RED tests

1. **Empty insertion:** accepted as a deterministic no-op with no event.
2. **`C-g` behavior:** for the first terminal slice, `C-g` exits the editor cleanly while preserving a semantic `KeyboardQuit` event.
3. **Small frames:** `height == 0` → zero rows; `height == 1` → modeline only; `width == 0` → empty rows; `width == 1`/`2` → clipped single-character modeline/body.
4. **GNU Emacs version:** pinned to GNU Emacs 29.3 via `ubuntu-24.04` + `emacs-nox` in CI (Recursive://Neon pattern), or via `ubuntu:24.04` container locally. Validate against the pinned source before recording the baseline.
