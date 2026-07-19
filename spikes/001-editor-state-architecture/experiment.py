"""Disposable comparison of persistent and controlled-mutable editor models.

This is architecture-spike code, not a candidate implementation for Drei.
"""

from __future__ import annotations

import gc
import hashlib
import json
import platform
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

InsertionAffinity = Literal["left", "right"]
LINE_COUNT = 10_000
LINE_WIDTH = 80
EDIT_COUNT = 1_200
MARKER_COUNT = 200


@dataclass(frozen=True, slots=True)
class MarkerValue:
    marker_id: int
    line: int
    column: int
    affinity: InsertionAffinity


@dataclass(frozen=True, slots=True)
class PersistentBuffer:
    buffer_id: int
    lines: tuple[str, ...]
    markers: tuple[MarkerValue, ...]


@dataclass(frozen=True, slots=True)
class WindowValue:
    window_id: int
    buffer_id: int
    point_marker_id: int


@dataclass(frozen=True, slots=True)
class PersistentModel:
    buffer: PersistentBuffer
    windows: tuple[WindowValue, ...]


@dataclass(frozen=True, slots=True)
class BufferHandle:
    buffer_id: int


@dataclass(slots=True)
class PersistentRegistry:
    current_by_id: dict[int, PersistentBuffer]

    def publish(self, buffer: PersistentBuffer) -> None:
        self.current_by_id[buffer.buffer_id] = buffer

    def resolve(self, handle: BufferHandle) -> PersistentBuffer:
        return self.current_by_id[handle.buffer_id]


@dataclass(frozen=True, slots=True)
class Observation:
    text_digest: str
    marker_positions: tuple[tuple[int, int, int], ...]
    window_points: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True, slots=True)
class EditRecord:
    line: int
    column: int
    text: str


@dataclass(slots=True)
class MutableMarker:
    marker_id: int
    line: int
    column: int
    affinity: InsertionAffinity


@dataclass(slots=True)
class MutableBuffer:
    buffer_id: int
    lines: list[str]
    markers: list[MutableMarker]
    undo_records: list[EditRecord]

    def insert(self, line: int, column: int, text: str) -> None:
        original = self.lines[line]
        self.lines[line] = original[:column] + text + original[column:]
        for marker in self.markers:
            if marker.line == line and (
                marker.column > column
                or (marker.column == column and marker.affinity == "right")
            ):
                marker.column += len(text)
        self.undo_records.append(EditRecord(line=line, column=column, text=text))


@dataclass(slots=True)
class MutableWindow:
    window_id: int
    buffer: MutableBuffer
    point: MutableMarker


@dataclass(slots=True)
class MutableModel:
    buffer: MutableBuffer
    windows: list[MutableWindow]


def initial_lines() -> tuple[str, ...]:
    return tuple(f"{line:05d} " + "x" * (LINE_WIDTH - 6) for line in range(LINE_COUNT))


def initial_markers() -> tuple[MarkerValue, ...]:
    markers = [
        MarkerValue(
            marker_id=marker_id,
            line=(marker_id * 47) % LINE_COUNT,
            column=LINE_WIDTH // 2,
            affinity="right" if marker_id % 2 else "left",
        )
        for marker_id in range(MARKER_COUNT - 2)
    ]
    markers.extend(
        (
            MarkerValue(MARKER_COUNT - 2, 0, LINE_WIDTH // 2, "left"),
            MarkerValue(MARKER_COUNT - 1, LINE_COUNT // 2, LINE_WIDTH // 2, "left"),
            MarkerValue(MARKER_COUNT, 0, LINE_WIDTH // 2, "right"),
            MarkerValue(MARKER_COUNT + 1, LINE_COUNT // 2, LINE_WIDTH // 2, "right"),
        )
    )
    return tuple(markers)


def make_persistent_model() -> PersistentModel:
    buffer = PersistentBuffer(
        buffer_id=1, lines=initial_lines(), markers=initial_markers()
    )
    return PersistentModel(
        buffer=buffer,
        windows=(
            WindowValue(window_id=1, buffer_id=1, point_marker_id=MARKER_COUNT),
            WindowValue(window_id=2, buffer_id=1, point_marker_id=MARKER_COUNT + 1),
        ),
    )


def make_mutable_model() -> MutableModel:
    markers = [
        MutableMarker(marker.marker_id, marker.line, marker.column, marker.affinity)
        for marker in initial_markers()
    ]
    buffer = MutableBuffer(
        buffer_id=1,
        lines=list(initial_lines()),
        markers=markers,
        undo_records=[],
    )
    marker_by_id = {marker.marker_id: marker for marker in markers}
    return MutableModel(
        buffer=buffer,
        windows=[
            MutableWindow(1, buffer, marker_by_id[MARKER_COUNT]),
            MutableWindow(2, buffer, marker_by_id[MARKER_COUNT + 1]),
        ],
    )


def workload() -> tuple[tuple[int, int, str], ...]:
    generated = tuple(
        (
            ((edit_number + 1) * 7_919) % LINE_COUNT,
            LINE_WIDTH // 2,
            chr(ord("a") + edit_number % 26),
        )
        for edit_number in range(EDIT_COUNT - 2)
    )
    return (
        (0, LINE_WIDTH // 2, "L"),
        (LINE_COUNT // 2, LINE_WIDTH // 2, "M"),
        *generated,
    )


def persistent_insert(
    model: PersistentModel,
    line: int,
    column: int,
    text: str,
) -> PersistentModel:
    lines = list(model.buffer.lines)
    original = lines[line]
    lines[line] = original[:column] + text + original[column:]
    markers = tuple(
        replace(marker, column=marker.column + len(text))
        if marker.line == line
        and (
            marker.column > column
            or (marker.column == column and marker.affinity == "right")
        )
        else marker
        for marker in model.buffer.markers
    )
    return replace(
        model,
        buffer=replace(model.buffer, lines=tuple(lines), markers=markers),
    )


def digest_lines(lines: tuple[str, ...] | list[str]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def observe_persistent(model: PersistentModel) -> Observation:
    marker_by_id = {marker.marker_id: marker for marker in model.buffer.markers}
    return Observation(
        text_digest=digest_lines(model.buffer.lines),
        marker_positions=tuple(
            (marker.marker_id, marker.line, marker.column)
            for marker in model.buffer.markers
        ),
        window_points=tuple(
            (
                window.window_id,
                marker_by_id[window.point_marker_id].line,
                marker_by_id[window.point_marker_id].column,
            )
            for window in model.windows
        ),
    )


def observe_mutable(model: MutableModel) -> Observation:
    return Observation(
        text_digest=digest_lines(model.buffer.lines),
        marker_positions=tuple(
            (marker.marker_id, marker.line, marker.column)
            for marker in model.buffer.markers
        ),
        window_points=tuple(
            (window.window_id, window.point.line, window.point.column)
            for window in model.windows
        ),
    )


def measured[T](run: Callable[[], T]) -> tuple[T, float, int, int]:
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    result = run()
    elapsed = time.perf_counter() - started
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, elapsed, current, peak


def run_persistent() -> tuple[
    PersistentModel,
    list[PersistentModel],
    PersistentRegistry,
    BufferHandle,
    PersistentBuffer,
]:
    model = make_persistent_model()
    registry = PersistentRegistry({model.buffer.buffer_id: model.buffer})
    extension_handle = BufferHandle(model.buffer.buffer_id)
    extension_reference = model.buffer
    roots = [model]
    for line, column, text in workload():
        model = persistent_insert(model, line, column, text)
        registry.publish(model.buffer)
        roots.append(model)
    return model, roots, registry, extension_handle, extension_reference


def run_mutable() -> tuple[MutableModel, MutableBuffer]:
    model = make_mutable_model()
    extension_reference = model.buffer
    for line, column, text in workload():
        model.buffer.insert(line, column, text)
    return model, extension_reference


def main() -> None:
    (
        persistent_result,
        persistent_seconds,
        persistent_current,
        persistent_peak,
    ) = measured(run_persistent)
    mutable_result, mutable_seconds, mutable_current, mutable_peak = measured(
        run_mutable
    )
    (
        persistent_model,
        persistent_roots,
        persistent_registry,
        extension_handle,
        persistent_extension_ref,
    ) = persistent_result
    mutable_model, mutable_extension_ref = mutable_result

    persistent_observation = observe_persistent(persistent_model)
    mutable_observation = observe_mutable(mutable_model)
    assert persistent_observation == mutable_observation
    assert persistent_roots[0].buffer.lines != persistent_model.buffer.lines
    assert mutable_extension_ref is mutable_model.buffer
    assert persistent_observation.window_points == ((1, 0, 41), (2, 5_000, 41))
    marker_positions = {
        marker_id: (line, column)
        for marker_id, line, column in persistent_observation.marker_positions
    }
    assert marker_positions[MARKER_COUNT - 2] == (0, 40)
    assert marker_positions[MARKER_COUNT - 1] == (5_000, 40)
    assert marker_positions[MARKER_COUNT] == (0, 41)
    assert marker_positions[MARKER_COUNT + 1] == (5_000, 41)
    assert persistent_registry.resolve(extension_handle) is persistent_model.buffer
    try:
        persistent_registry.resolve(BufferHandle(buffer_id=999))
    except KeyError:
        unknown_handle_rejected = True
    else:
        unknown_handle_rejected = False
    assert unknown_handle_rejected

    report = {
        "workload": {
            "lines": LINE_COUNT,
            "initial_line_width": LINE_WIDTH,
            "edits": EDIT_COUNT,
            "tracked_markers_including_window_points": MARKER_COUNT + 2,
            "retained_persistent_roots": len(persistent_roots),
            "mutable_undo_records": len(mutable_model.buffer.undo_records),
        },
        "semantic_agreement": persistent_observation == mutable_observation,
        "observation_type": "frozen dataclass",
        "persistent": {
            "elapsed_seconds": round(persistent_seconds, 6),
            "current_traced_bytes_after_run": persistent_current,
            "peak_traced_bytes": persistent_peak,
            "extension_object_is_current": (
                persistent_extension_ref is persistent_model.buffer
            ),
            "extension_handle_resolves_current_version": (
                persistent_registry.resolve(extension_handle) is persistent_model.buffer
            ),
            "unknown_handle_rejected": unknown_handle_rejected,
        },
        "controlled_mutable": {
            "elapsed_seconds": round(mutable_seconds, 6),
            "current_traced_bytes_after_run": mutable_current,
            "peak_traced_bytes": mutable_peak,
            "extension_object_is_current": mutable_extension_ref
            is mutable_model.buffer,
            "both_windows_share_buffer_identity": all(
                window.buffer is mutable_model.buffer
                for window in mutable_model.windows
            ),
        },
        "ratios_persistent_over_mutable": {
            "elapsed": round(persistent_seconds / mutable_seconds, 3),
            "current_traced_allocations_after_run": round(
                persistent_current / mutable_current, 3
            ),
            "peak_traced_allocations": round(persistent_peak / mutable_peak, 3),
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
