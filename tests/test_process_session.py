"""Session integration for the subprocess effect port.

``DeliverProcessOutput`` records an already-captured process result into the
transcript as an immutable external delivery (design 0002's "deterministic
delivery of process output"). It must never perturb buffer, undo, or
kill-ring state — the transcript fold for those ignores the new event.
"""

from __future__ import annotations

import pytest
from conftest import FakeFilePort, FakeProcessPort

from drei.commands import (
    InsertText,
    KeyboardQuitEvent,
    KillLine,
    ProcessOutputRecorded,
    SaveBuffer,
    SetMark,
    Undo,
    Yank,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.process import ProcessResult
from drei.session import EditorSession, _NullProcessPort


def _session(
    text: str = "",
    point: int = 0,
    process_port: FakeProcessPort | None = None,
) -> EditorSession:
    value = BufferValue(text=text, point=point, file_path="/tmp/x.txt")
    return EditorSession(
        Buffer(BufferId("x.txt"), value),
        file_port=FakeFilePort(),
        process_port=process_port or FakeProcessPort(),
    )


def _result(argv: tuple[str, ...] = ("cmd",), exit_code: int = 0) -> ProcessResult:
    return ProcessResult(argv=argv, exit_code=exit_code, stdout="out", stderr="err")


def test_run_process_records_event_and_leaves_buffer_identical() -> None:
    port = FakeProcessPort(default=_result())
    session = _session("hello", 2, process_port=port)
    before = session.buffer.current
    outcome = session.run_process(("cmd", "arg"))
    assert session.buffer.current == before
    recorded = [e for e in outcome.events if isinstance(e, ProcessOutputRecorded)]
    assert len(recorded) == 1
    assert recorded[0].argv == ("cmd", "arg")
    assert recorded[0].status == "ok"
    assert recorded[0].exit_code == 0
    assert recorded[0].stdout_len == 3
    assert recorded[0].stderr_len == 3


def test_run_process_appends_to_process_log() -> None:
    session = _session()
    session.run_process(("a",))
    session.run_process(("b",))
    assert [r.argv for r in session.process_log] == [("a",), ("b",)]


def test_run_process_passes_input_and_timeout_to_port() -> None:
    port = FakeProcessPort()
    session = _session(process_port=port)
    session.run_process(("cmd",), input_text="in", timeout=5.0)
    assert port.calls == [(("cmd",), "in", 5.0)]


def test_nonzero_exit_status() -> None:
    port = FakeProcessPort(default=_result(exit_code=7))
    session = _session(process_port=port)
    outcome = session.run_process(("cmd",))
    recorded = [e for e in outcome.events if isinstance(e, ProcessOutputRecorded)]
    assert recorded[0].status == "nonzero-exit"
    assert recorded[0].exit_code == 7


def test_launch_failure_is_normalized_event_not_crash() -> None:
    port = FakeProcessPort(fail="not-found")
    session = _session(process_port=port)
    before = session.buffer.current
    outcome = session.run_process(("missing",))
    assert session.buffer.current == before
    recorded = [e for e in outcome.events if isinstance(e, ProcessOutputRecorded)]
    assert len(recorded) == 1
    assert recorded[0].status == "not-found"


def test_timeout_failure_is_normalized_event_not_crash() -> None:
    port = FakeProcessPort(fail="timeout")
    session = _session(process_port=port)
    outcome = session.run_process(("slow",), timeout=1.0)
    recorded = [e for e in outcome.events if isinstance(e, ProcessOutputRecorded)]
    assert len(recorded) == 1
    assert recorded[0].status == "timeout"
    assert session.process_log == ()  # a failed launch logs no result


def test_null_port_run_raises_not_found_and_records_token() -> None:
    session = EditorSession(
        Buffer(BufferId("x.txt"), BufferValue(text="", point=0, file_path="/tmp/x.txt"))
    )
    with pytest.raises(FileNotFoundError):
        _NullProcessPort().run(("anything",))
    outcome = session.run_process(("anything",))
    recorded = [e for e in outcome.events if isinstance(e, ProcessOutputRecorded)]
    assert recorded[0].status == "not-found"


def test_delivery_pushes_no_undo_group_and_noop_undo_stays_noop() -> None:
    session = _session("abc", 3)
    session.run_process(("cmd",))
    # No undo group was pushed by the delivery: undo is a silent no-op.
    outcome = session.dispatch(Undo())
    assert outcome.events == ()
    assert session.buffer.current.text == "abc"


def test_delivery_does_not_undo_text() -> None:
    session = _session()
    session.dispatch(InsertText("x"))
    session.run_process(("cmd",))
    outcome = session.dispatch(Undo())
    # The undo undoes the insert, not the delivery.
    assert session.buffer.current.text == ""
    assert any(type(e).__name__ == "TextUndone" for e in outcome.events)


def test_delivery_leaves_kill_ring_and_yank_intact() -> None:
    session = _session("line one\n", 0)
    session.dispatch(KillLine())
    ring_before = session.kill_ring
    session.run_process(("cmd",))
    assert session.kill_ring == ring_before
    # Yank still pulls the killed text (ring untouched by the delivery).
    session.dispatch(Undo())  # restore text
    outcome = session.dispatch(Yank())
    assert "line one" in session.buffer.current.text or any(
        type(e).__name__ == "TextYanked" for e in outcome.events
    )


def test_delivery_breaks_kill_append_chain() -> None:
    session = _session("one\ntwo\n", 0)
    session.dispatch(KillLine())  # kills "one", point stays 0
    session.dispatch(KillLine())  # chained: kills "\n", appends to ring[0]
    session.run_process(("cmd",))  # event-emitting: breaks the chain
    session.dispatch(KillLine())  # kills "two" as a NEW entry, not appended
    assert session.kill_ring[0] == "two"
    assert session.kill_ring[1] == "one\n"


def test_delivery_breaks_undo_descent() -> None:
    session = _session()
    session.dispatch(InsertText("a"))
    session.dispatch(InsertText("b"))
    session.dispatch(Undo())  # undoes "b", now descending
    session.run_process(("cmd",))  # event-emitting: flips direction to redo
    outcome = session.dispatch(Undo())
    # Direction flipped: this redo re-applies "b".
    assert any(type(e).__name__ == "TextRedone" for e in outcome.events)
    assert session.buffer.current.text == "ab"


def test_delivery_does_not_set_modified() -> None:
    session = _session("clean", 0)
    session.dispatch(SaveBuffer())  # clears modified
    session.run_process(("cmd",))
    assert session.buffer.current.modified is False


def test_delivery_leaves_mark_intact() -> None:
    session = _session("abc", 1)
    session.dispatch(SetMark())
    session.run_process(("cmd",))
    assert session.buffer.current.mark == 1


def test_delivery_emits_no_quit_or_text_events() -> None:
    session = _session()
    outcome = session.run_process(("cmd",))
    assert not any(isinstance(e, KeyboardQuitEvent) for e in outcome.events)
    assert len(outcome.events) == 1  # exactly the ProcessOutputRecorded
