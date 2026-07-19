"""Third disposable experiment for Drei's hybrid live-model hypothesis.

Part A compares retained large edit histories using naive whole-table persistence,
controlled mutation with inverse records, and a stable shell over a chunk-shared
immutable line root. Part B exercises a Dired-like generated view, provider
refresh, mode-local values, stable handles, and undo. This is not production code.
"""

from __future__ import annotations

import gc
import hashlib
import json
import platform
import time
import tracemalloc
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Literal

Affinity = Literal["left", "right"]
LINE_COUNT = 10_000
LINE_WIDTH = 80
EDIT_COUNT = 1_200
MARKER_COUNT = 202
CHUNK_SIZE = 128
EXPECTED_FINAL_DIGEST = (
    "9db79e6d6dfaa4e7f1eacb9c7c3123c008f08b69dd9f20b76fef722d85cc1cf9"
)


@dataclass(frozen=True, slots=True)
class MarkerValue:
    marker_id: int
    line: int
    column: int
    affinity: Affinity


@dataclass(frozen=True, slots=True)
class TextObservation:
    digest: str
    marker_positions: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True, slots=True)
class PersistentRoot:
    lines: tuple[str, ...]
    markers: tuple[MarkerValue, ...]


@dataclass(frozen=True, slots=True)
class ChunkedLines:
    chunks: tuple[tuple[str, ...], ...]
    line_count: int

    @classmethod
    def from_lines(cls, lines: tuple[str, ...]) -> ChunkedLines:
        return cls(
            chunks=tuple(
                lines[start : start + CHUNK_SIZE]
                for start in range(0, len(lines), CHUNK_SIZE)
            ),
            line_count=len(lines),
        )

    def line(self, index: int) -> str:
        chunk_index, line_index = divmod(index, CHUNK_SIZE)
        return self.chunks[chunk_index][line_index]

    def replace_line(self, index: int, line: str) -> ChunkedLines:
        chunk_index, line_index = divmod(index, CHUNK_SIZE)
        changed_chunk = list(self.chunks[chunk_index])
        changed_chunk[line_index] = line
        changed_chunks = list(self.chunks)
        changed_chunks[chunk_index] = tuple(changed_chunk)
        return replace(self, chunks=tuple(changed_chunks))

    def iter_lines(self) -> Iterable[str]:
        for chunk in self.chunks:
            yield from chunk


@dataclass(frozen=True, slots=True)
class HybridRoot:
    lines: ChunkedLines
    markers: tuple[MarkerValue, ...]


@dataclass(slots=True)
class HybridShell:
    buffer_id: int
    root: HybridRoot


@dataclass(slots=True)
class MutableMarker:
    marker_id: int
    line: int
    column: int
    affinity: Affinity


@dataclass(frozen=True, slots=True)
class MutableUndoRecord:
    line: int
    previous_line: str
    previous_marker_columns: tuple[int, ...]


@dataclass(slots=True)
class MutableLargeModel:
    lines: list[str]
    markers: list[MutableMarker]
    undo: list[MutableUndoRecord]


@dataclass(frozen=True, slots=True)
class HybridRetention:
    shell: HybridShell
    roots: tuple[HybridRoot, ...]


RetainedHistory = tuple[PersistentRoot, ...] | HybridRetention | MutableLargeModel


@dataclass(frozen=True, slots=True)
class LargeOutcome:
    name: str
    observation: TextObservation
    retained_history: RetainedHistory
    facts: tuple[tuple[str, int | bool], ...]


@dataclass(frozen=True, slots=True)
class MeasuredLargeOutcome:
    outcome: LargeOutcome
    elapsed_seconds: float
    current_traced_bytes: int
    peak_traced_bytes: int


def initial_lines() -> tuple[str, ...]:
    return tuple(f"{line:05d} " + "x" * (LINE_WIDTH - 6) for line in range(LINE_COUNT))


def initial_markers() -> tuple[MarkerValue, ...]:
    return tuple(
        MarkerValue(
            marker_id=marker_id,
            line=(marker_id * 47) % LINE_COUNT,
            column=LINE_WIDTH // 2,
            affinity="right" if marker_id % 2 else "left",
        )
        for marker_id in range(MARKER_COUNT)
    )


def workload() -> tuple[tuple[int, int, str], ...]:
    return tuple(
        (
            (edit_number * 7_919) % LINE_COUNT,
            LINE_WIDTH // 2,
            chr(ord("a") + edit_number % 26),
        )
        for edit_number in range(EDIT_COUNT)
    )


def expected_large_observation() -> TextObservation:
    operations = workload()
    assert len(operations) == EDIT_COUNT
    assert len({line for line, _, _ in operations}) == EDIT_COUNT
    assert all(
        column == LINE_WIDTH // 2 and len(text) == 1 for _, column, text in operations
    )
    inserted_by_line = {line: text for line, _, text in operations}
    expected_lines = []
    for line_number, original in enumerate(initial_lines()):
        inserted = inserted_by_line.get(line_number)
        if inserted is None:
            expected_lines.append(original)
        else:
            column = LINE_WIDTH // 2
            expected_lines.append(original[:column] + inserted + original[column:])
    digest = digest_lines(expected_lines)
    assert digest == EXPECTED_FINAL_DIGEST
    edited_lines = frozenset(inserted_by_line)
    marker_positions = tuple(
        (
            marker.marker_id,
            marker.line,
            marker.column
            + int(marker.affinity == "right" and marker.line in edited_lines),
        )
        for marker in initial_markers()
    )
    assert len(marker_positions) == MARKER_COUNT
    return TextObservation(digest, marker_positions)


def shifted_markers(
    markers: tuple[MarkerValue, ...], line: int, column: int, amount: int
) -> tuple[MarkerValue, ...]:
    return tuple(
        replace(marker, column=marker.column + amount)
        if marker.line == line
        and (
            marker.column > column
            or (marker.column == column and marker.affinity == "right")
        )
        else marker
        for marker in markers
    )


def mutate_markers(
    markers: list[MutableMarker], line: int, column: int, amount: int
) -> None:
    for marker in markers:
        if marker.line == line and (
            marker.column > column
            or (marker.column == column and marker.affinity == "right")
        ):
            marker.column += amount


def digest_lines(lines: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def observe_persistent(root: PersistentRoot) -> TextObservation:
    return TextObservation(
        digest=digest_lines(root.lines),
        marker_positions=tuple(
            (marker.marker_id, marker.line, marker.column) for marker in root.markers
        ),
    )


def observe_hybrid(root: HybridRoot) -> TextObservation:
    return TextObservation(
        digest=digest_lines(root.lines.iter_lines()),
        marker_positions=tuple(
            (marker.marker_id, marker.line, marker.column) for marker in root.markers
        ),
    )


def observe_mutable(model: MutableLargeModel) -> TextObservation:
    return TextObservation(
        digest=digest_lines(model.lines),
        marker_positions=tuple(
            (marker.marker_id, marker.line, marker.column) for marker in model.markers
        ),
    )


def run_persistent_large() -> LargeOutcome:
    root = PersistentRoot(initial_lines(), initial_markers())
    history = [root]
    for line, column, text in workload():
        lines = list(root.lines)
        previous = lines[line]
        lines[line] = previous[:column] + text + previous[column:]
        root = PersistentRoot(
            tuple(lines), shifted_markers(root.markers, line, column, len(text))
        )
        history.append(root)
    return LargeOutcome(
        name="naive_fully_persistent",
        observation=observe_persistent(root),
        retained_history=tuple(history),
        facts=(("retained_roots", len(history)),),
    )


def run_controlled_mutable_large() -> LargeOutcome:
    markers = [
        MutableMarker(marker.marker_id, marker.line, marker.column, marker.affinity)
        for marker in initial_markers()
    ]
    model = MutableLargeModel(list(initial_lines()), markers, [])
    extension_reference = model.lines
    for line, column, text in workload():
        previous = model.lines[line]
        model.undo.append(
            MutableUndoRecord(
                line=line,
                previous_line=previous,
                previous_marker_columns=tuple(
                    marker.column for marker in model.markers
                ),
            )
        )
        model.lines[line] = previous[:column] + text + previous[column:]
        mutate_markers(model.markers, line, column, len(text))
    observation = observe_mutable(model)
    extension_reference_is_current = extension_reference is model.lines
    return LargeOutcome(
        name="controlled_mutable",
        observation=observation,
        retained_history=model,
        facts=(
            ("undo_records", len(model.undo)),
            ("stable_live_container", extension_reference_is_current),
        ),
    )


def run_hybrid_large() -> LargeOutcome:
    initial_root = HybridRoot(
        ChunkedLines.from_lines(initial_lines()), initial_markers()
    )
    shell = HybridShell(buffer_id=1, root=initial_root)
    extension_reference = shell
    history = [initial_root]
    shared_unchanged_chunks = 0
    expected_shared_per_edit = len(initial_root.lines.chunks) - 1
    for line, column, text in workload():
        previous_root = shell.root
        previous_line = previous_root.lines.line(line)
        lines = previous_root.lines.replace_line(
            line, previous_line[:column] + text + previous_line[column:]
        )
        shared = sum(
            before is after
            for before, after in zip(
                previous_root.lines.chunks, lines.chunks, strict=True
            )
        )
        assert shared == expected_shared_per_edit
        shared_unchanged_chunks += shared
        shell.root = HybridRoot(
            lines,
            shifted_markers(previous_root.markers, line, column, len(text)),
        )
        history.append(shell.root)
    return LargeOutcome(
        name="hybrid_chunked",
        observation=observe_hybrid(shell.root),
        retained_history=HybridRetention(shell, tuple(history)),
        facts=(
            ("retained_roots", len(history)),
            ("chunks_per_root", len(shell.root.lines.chunks)),
            ("unchanged_chunks_shared_per_edit", expected_shared_per_edit),
            ("total_unchanged_chunk_links", shared_unchanged_chunks),
            ("stable_shell_reference", extension_reference is shell),
        ),
    )


def measured_large(run: Callable[[], LargeOutcome]) -> MeasuredLargeOutcome:
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    outcome = run()
    elapsed = time.perf_counter() - started
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return MeasuredLargeOutcome(outcome, elapsed, current, peak)


def verify_controlled_mutable_undo() -> None:
    markers = [
        MutableMarker(marker.marker_id, marker.line, marker.column, marker.affinity)
        for marker in initial_markers()
    ]
    model = MutableLargeModel(list(initial_lines()), markers, [])
    for line, column, text in workload():
        previous = model.lines[line]
        model.undo.append(
            MutableUndoRecord(
                line=line,
                previous_line=previous,
                previous_marker_columns=tuple(
                    marker.column for marker in model.markers
                ),
            )
        )
        model.lines[line] = previous[:column] + text + previous[column:]
        mutate_markers(model.markers, line, column, len(text))
    for record in reversed(model.undo):
        model.lines[record.line] = record.previous_line
        for marker, column in zip(
            model.markers, record.previous_marker_columns, strict=True
        ):
            marker.column = column
    assert tuple(model.lines) == initial_lines()
    assert (
        tuple(
            MarkerValue(marker.marker_id, marker.line, marker.column, marker.affinity)
            for marker in model.markers
        )
        == initial_markers()
    )


@dataclass(frozen=True, slots=True)
class DiredEntry:
    entry_id: str
    name: str
    is_directory: bool = False


@dataclass(frozen=True, slots=True)
class DiredValue:
    provider_sequence: int
    entries: tuple[DiredEntry, ...]
    selected_id: str | None
    marked_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class ModeLocalValue:
    show_hidden: bool = False
    refresh_count: int = 0


@dataclass(frozen=True, slots=True)
class DiredSnapshot:
    dired: DiredValue
    mode: ModeLocalValue


@dataclass(slots=True)
class DiredShell:
    buffer_id: int
    value: DiredValue


@dataclass(slots=True)
class ModeLocalShell:
    value: ModeLocalValue


@dataclass(frozen=True, slots=True)
class EntryHandle:
    buffer_id: int
    entry_id: str


@dataclass(frozen=True, slots=True)
class DiredObservation:
    rows: tuple[str, ...]
    selected_id: str | None
    selected_row: int | None
    marked_ids: tuple[str, ...]
    provider_sequence: int
    show_hidden: bool
    refresh_count: int


@dataclass(slots=True)
class DiredHistory:
    snapshots: list[DiredSnapshot]
    cursor: int

    @classmethod
    def create(cls, snapshot: DiredSnapshot) -> DiredHistory:
        return cls([snapshot], 0)

    def commit(self, snapshot: DiredSnapshot) -> None:
        del self.snapshots[self.cursor + 1 :]
        self.snapshots.append(snapshot)
        self.cursor += 1

    def undo(self) -> DiredSnapshot:
        if self.cursor == 0:
            raise IndexError("cannot undo initial Dired state")
        self.cursor -= 1
        return self.snapshots[self.cursor]

    def redo(self) -> DiredSnapshot:
        if self.cursor + 1 >= len(self.snapshots):
            raise IndexError("cannot redo latest Dired state")
        self.cursor += 1
        return self.snapshots[self.cursor]


@dataclass(slots=True)
class HybridDiredSession:
    dired_shell: DiredShell
    mode_shell: ModeLocalShell
    history: DiredHistory

    @classmethod
    def create(cls) -> HybridDiredSession:
        dired = DiredValue(
            provider_sequence=0,
            entries=(
                DiredEntry("a", "alpha.txt"),
                DiredEntry("b", "beta.py"),
                DiredEntry("c", "src", is_directory=True),
                DiredEntry("h", ".hidden"),
            ),
            selected_id="b",
            marked_ids=frozenset({"c"}),
        )
        mode = ModeLocalValue()
        return cls(
            dired_shell=DiredShell(2, dired),
            mode_shell=ModeLocalShell(mode),
            history=DiredHistory.create(DiredSnapshot(dired, mode)),
        )

    def snapshot(self) -> DiredSnapshot:
        return DiredSnapshot(self.dired_shell.value, self.mode_shell.value)

    def restore(self, snapshot: DiredSnapshot) -> None:
        self.dired_shell.value = snapshot.dired
        self.mode_shell.value = snapshot.mode

    def resolve_entry(self, handle: EntryHandle) -> DiredEntry:
        if handle.buffer_id != self.dired_shell.buffer_id:
            raise KeyError((handle.buffer_id, handle.entry_id))
        for entry in self.dired_shell.value.entries:
            if entry.entry_id == handle.entry_id:
                return entry
        raise KeyError(handle.entry_id)

    def refresh(self, sequence: int, entries: tuple[DiredEntry, ...]) -> None:
        previous = self.dired_shell.value
        if sequence <= previous.provider_sequence:
            raise ValueError(sequence)
        entry_ids = {entry.entry_id for entry in entries}
        if len(entry_ids) != len(entries):
            raise ValueError("provider entry IDs must be unique")
        selected = previous.selected_id
        if selected not in entry_ids:
            selected = entries[0].entry_id if entries else None
        self.dired_shell.value = DiredValue(
            provider_sequence=sequence,
            entries=entries,
            selected_id=selected,
            marked_ids=previous.marked_ids & entry_ids,
        )
        self.mode_shell.value = replace(
            self.mode_shell.value,
            refresh_count=self.mode_shell.value.refresh_count + 1,
        )
        self.history.commit(self.snapshot())

    def toggle_hidden(self) -> None:
        self.mode_shell.value = replace(
            self.mode_shell.value,
            show_hidden=not self.mode_shell.value.show_hidden,
        )
        self.history.commit(self.snapshot())

    def undo(self) -> None:
        self.restore(self.history.undo())

    def redo(self) -> None:
        self.restore(self.history.redo())

    def observe(self) -> DiredObservation:
        visible = tuple(
            entry
            for entry in self.dired_shell.value.entries
            if self.mode_shell.value.show_hidden or not entry.name.startswith(".")
        )
        rows = tuple(
            f"{'*' if entry.entry_id in self.dired_shell.value.marked_ids else ' '} "
            f"{'d' if entry.is_directory else '-'} {entry.name} [{entry.entry_id}]"
            for entry in visible
        )
        selected_row = next(
            (
                index
                for index, entry in enumerate(visible)
                if entry.entry_id == self.dired_shell.value.selected_id
            ),
            None,
        )
        return DiredObservation(
            rows=rows,
            selected_id=self.dired_shell.value.selected_id,
            selected_row=selected_row,
            marked_ids=tuple(sorted(self.dired_shell.value.marked_ids)),
            provider_sequence=self.dired_shell.value.provider_sequence,
            show_hidden=self.mode_shell.value.show_hidden,
            refresh_count=self.mode_shell.value.refresh_count,
        )


def expected_dired_observations() -> tuple[DiredObservation, ...]:
    initial = DiredObservation(
        rows=(
            "  - alpha.txt [a]",
            "  - beta.py [b]",
            "* d src [c]",
        ),
        selected_id="b",
        selected_row=1,
        marked_ids=("c",),
        provider_sequence=0,
        show_hidden=False,
        refresh_count=0,
    )
    refreshed = DiredObservation(
        rows=(
            "* d src [c]",
            "  - alpha.txt [a]",
            "  - beta-renamed.py [b]",
            "  d docs [d]",
        ),
        selected_id="b",
        selected_row=2,
        marked_ids=("c",),
        provider_sequence=1,
        show_hidden=False,
        refresh_count=1,
    )
    visible_hidden = replace(
        refreshed,
        rows=(
            "* d src [c]",
            "  - alpha.txt [a]",
            "  - beta-renamed.py [b]",
            "  - .hidden [h]",
            "  d docs [d]",
        ),
        show_hidden=True,
    )
    removed_selection = DiredObservation(
        rows=(
            "* d src [c]",
            "  - alpha.txt [a]",
            "  - .hidden [h]",
            "  d docs [d]",
        ),
        selected_id="c",
        selected_row=0,
        marked_ids=("c",),
        provider_sequence=2,
        show_hidden=True,
        refresh_count=2,
    )
    return initial, refreshed, visible_hidden, removed_selection


def run_dired_scenario() -> dict[str, bool | int]:
    session = HybridDiredSession.create()
    dired_extension_reference = session.dired_shell
    mode_extension_reference = session.mode_shell
    beta_handle = EntryHandle(2, "b")
    expected_initial, expected_refreshed, expected_hidden, expected_removed = (
        expected_dired_observations()
    )

    initial = session.observe()
    observation_stream = [initial]
    assert initial == expected_initial
    try:
        session.resolve_entry(EntryHandle(999, "b"))
    except KeyError:
        wrong_owner_handle_rejected = True
    else:
        wrong_owner_handle_rejected = False
    assert wrong_owner_handle_rejected

    session.refresh(
        1,
        (
            DiredEntry("c", "src", is_directory=True),
            DiredEntry("a", "alpha.txt"),
            DiredEntry("b", "beta-renamed.py"),
            DiredEntry("h", ".hidden"),
            DiredEntry("d", "docs", is_directory=True),
        ),
    )
    refreshed = session.observe()
    observation_stream.append(refreshed)
    assert refreshed == expected_refreshed
    assert session.resolve_entry(beta_handle).name == "beta-renamed.py"
    history_before_duplicate = session.history.cursor
    try:
        session.refresh(
            2,
            (DiredEntry("duplicate", "one"), DiredEntry("duplicate", "two")),
        )
    except ValueError:
        duplicate_entry_ids_rejected = True
    else:
        duplicate_entry_ids_rejected = False
    assert duplicate_entry_ids_rejected
    assert session.observe() == expected_refreshed
    assert session.history.cursor == history_before_duplicate

    session.toggle_hidden()
    visible_hidden = session.observe()
    observation_stream.append(visible_hidden)
    assert visible_hidden == expected_hidden

    session.refresh(
        2,
        (
            DiredEntry("c", "src", is_directory=True),
            DiredEntry("a", "alpha.txt"),
            DiredEntry("h", ".hidden"),
            DiredEntry("d", "docs", is_directory=True),
        ),
    )
    removed_selection = session.observe()
    observation_stream.append(removed_selection)
    assert removed_selection == expected_removed
    try:
        session.resolve_entry(beta_handle)
    except KeyError:
        removed_handle_rejected = True
    else:
        removed_handle_rejected = False
    assert removed_handle_rejected

    session.undo()
    restored_refresh = session.observe()
    observation_stream.append(restored_refresh)
    assert restored_refresh == expected_hidden
    assert session.resolve_entry(beta_handle).name == "beta-renamed.py"
    session.undo()
    restored_before_toggle = session.observe()
    observation_stream.append(restored_before_toggle)
    assert restored_before_toggle == expected_refreshed
    session.redo()
    redone_toggle = session.observe()
    observation_stream.append(redone_toggle)
    assert redone_toggle == expected_hidden
    session.redo()
    redone_deletion = session.observe()
    observation_stream.append(redone_deletion)
    assert redone_deletion == expected_removed
    session.undo()
    restored_after_redo = session.observe()
    observation_stream.append(restored_after_redo)
    assert restored_after_redo == expected_hidden

    replay = HybridDiredSession.create()
    replay_observations = [replay.observe()]
    replay.refresh(
        1,
        (
            DiredEntry("c", "src", is_directory=True),
            DiredEntry("a", "alpha.txt"),
            DiredEntry("b", "beta-renamed.py"),
            DiredEntry("h", ".hidden"),
            DiredEntry("d", "docs", is_directory=True),
        ),
    )
    replay_observations.append(replay.observe())
    replay.toggle_hidden()
    replay_observations.append(replay.observe())
    replay.refresh(
        2,
        (
            DiredEntry("c", "src", is_directory=True),
            DiredEntry("a", "alpha.txt"),
            DiredEntry("h", ".hidden"),
            DiredEntry("d", "docs", is_directory=True),
        ),
    )
    replay_observations.append(replay.observe())
    replay.undo()
    replay_observations.append(replay.observe())
    replay.undo()
    replay_observations.append(replay.observe())
    replay.redo()
    replay_observations.append(replay.observe())
    replay.redo()
    replay_observations.append(replay.observe())
    replay.undo()
    replay_observations.append(replay.observe())
    provider_replay_matched = tuple(replay_observations) == tuple(observation_stream)
    assert provider_replay_matched

    selection_preserved = refreshed.selected_id == initial.selected_id
    marks_preserved = refreshed.marked_ids == initial.marked_ids
    deterministic_fallback = removed_selection.selected_id == "c"
    undo_redo_restored = (
        restored_refresh == visible_hidden
        and restored_before_toggle == refreshed
        and redone_toggle == visible_hidden
        and redone_deletion == removed_selection
        and restored_after_redo == visible_hidden
    )
    dired_reference_stable = dired_extension_reference is session.dired_shell
    mode_reference_stable = mode_extension_reference is session.mode_shell
    history_snapshot_count = len(session.history.snapshots)
    assert selection_preserved
    assert marks_preserved
    assert deterministic_fallback
    assert undo_redo_restored
    assert dired_reference_stable
    assert mode_reference_stable
    assert history_snapshot_count == 4
    return {
        "provider_refresh_preserved_selection_by_id": selection_preserved,
        "provider_refresh_preserved_marks_by_id": marks_preserved,
        "removed_selection_used_deterministic_fallback": deterministic_fallback,
        "removed_entry_handle_rejected": removed_handle_rejected,
        "wrong_owner_handle_rejected": wrong_owner_handle_rejected,
        "duplicate_entry_ids_rejected": duplicate_entry_ids_rejected,
        "undo_redo_restored_generated_view": undo_redo_restored,
        "explicit_provider_replay_matched_every_observation": provider_replay_matched,
        "dired_shell_reference_stable": dired_reference_stable,
        "mode_local_shell_reference_stable": mode_reference_stable,
        "history_snapshots": history_snapshot_count,
    }


def main() -> None:
    large_results = (
        measured_large(run_persistent_large),
        measured_large(run_controlled_mutable_large),
        measured_large(run_hybrid_large),
    )
    observations = {result.outcome.observation for result in large_results}
    expected_observation = expected_large_observation()
    semantic_agreement = observations == {expected_observation}
    assert semantic_agreement
    verify_controlled_mutable_undo()

    dired_result = run_dired_scenario()
    report = {
        "large_workload": {
            "lines": LINE_COUNT,
            "edits": EDIT_COUNT,
            "markers": MARKER_COUNT,
            "chunk_size": CHUNK_SIZE,
            "semantic_agreement_with_independent_oracle": semantic_agreement,
            "models": {
                result.outcome.name: {
                    "elapsed_seconds": round(result.elapsed_seconds, 6),
                    "current_traced_bytes": result.current_traced_bytes,
                    "peak_traced_bytes": result.peak_traced_bytes,
                    "facts": dict(result.outcome.facts),
                }
                for result in large_results
            },
        },
        "dired_like_hybrid": dired_result,
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
