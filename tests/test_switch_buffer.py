"""A.2 step 3: C-x b switch-to-buffer (plan 0012 D7).

Minibuffer prompt accepting a buffer NAME; unknown name creates a new empty
buffer (Emacs behavior); empty input selects the most-recently-used other
buffer; abort leaves everything untouched. Also: buffer switches break
kill/yank chains (plan 0012 evidence 3) and restore per-buffer point/mark
(plan 0012 D8).
"""

from __future__ import annotations

from conftest import FakeFilePort

from drei.commands import (
    BufferCreated,
    BufferSelected,
    ForwardChar,
    InsertText,
    KillLine,
    MinibufferAbort,
    MinibufferAccept,
    MinibufferInput,
    MinibufferOpened,
    SetMark,
    SwitchBuffer,
    Undo,
    Yank,
    YankPop,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession


def _session() -> EditorSession:
    return EditorSession(
        Buffer(BufferId("alpha"), BufferValue(text="aaa", point=0)),
        file_port=FakeFilePort(),
    )


def _switch(session: EditorSession, name: str) -> tuple:
    session.dispatch(SwitchBuffer())
    for char in name:
        session.dispatch(MinibufferInput(char))
    return session.dispatch(MinibufferAccept()).events


def _two_buffers(session: EditorSession) -> None:
    """Give the session a second buffer named 'beta' with text 'bbb'."""
    session._create_buffer("beta", BufferValue(text="bbb", point=0), [])
    session._select_buffer(BufferId("beta"), [])


def test_switch_to_existing_buffer_by_name() -> None:
    session = _session()
    _two_buffers(session)  # current: beta
    events = _switch(session, "alpha")
    assert BufferSelected("alpha") in events
    assert session.buffer.buffer_id == BufferId("alpha")
    assert session.buffer.current.text == "aaa"


def test_switch_to_unknown_name_creates_empty_buffer() -> None:
    session = _session()
    events = _switch(session, "gamma")
    assert BufferCreated("gamma", None) in events
    assert BufferSelected("gamma") in events
    current = session.buffer.current
    assert current.text == ""
    assert current.file_path is None
    assert not current.modified


def test_empty_input_selects_the_mru_other_buffer() -> None:
    session = _session()
    _two_buffers(session)  # MRU: beta (current), alpha (most recent other)
    events = _switch(session, "")
    assert BufferSelected("alpha") in events
    assert session.buffer.buffer_id == BufferId("alpha")
    # And back: alpha current, beta the MRU other.
    events = _switch(session, "")
    assert BufferSelected("beta") in events


def test_empty_input_with_one_buffer_is_a_quiet_noop() -> None:
    session = _session()
    events = _switch(session, "")
    assert events == ()
    assert session.buffer.buffer_id == BufferId("alpha")


def test_switch_prompt_carries_the_mru_default() -> None:
    session = _session()
    _two_buffers(session)
    outcome = session.dispatch(SwitchBuffer())
    assert MinibufferOpened("Switch to buffer: ") in outcome.events
    assert session.minibuffer_prompt == "Switch to buffer: "


def test_abort_leaves_buffer_untouched() -> None:
    session = _session()
    _two_buffers(session)
    session.dispatch(SwitchBuffer())
    session.dispatch(MinibufferInput("a"))
    session.dispatch(MinibufferAbort())
    assert session.buffer.buffer_id == BufferId("beta")
    assert session.minibuffer is None


def test_switch_breaks_the_kill_chain() -> None:
    """Plan 0012 evidence 3: kill in A, switch, kill in B → no append."""
    session = _session()
    session.dispatch(KillLine())  # ring: "aaa"
    _two_buffers(session)
    session.dispatch(KillLine())  # ring: "bbb" fresh entry
    assert session.kill_ring == ("bbb", "aaa")


def test_switch_breaks_yank_pop_chaining() -> None:
    session = _session()
    session.dispatch(KillLine())  # ring: "aaa"
    _two_buffers(session)
    session.dispatch(KillLine())  # ring: ("bbb", "aaa")
    session.dispatch(Yank())  # yanks "bbb"
    session._select_buffer(BufferId("alpha"), [])
    assert session.dispatch(YankPop()).events == ()  # no pop after switch


def test_switch_restores_per_buffer_point_and_mark() -> None:
    """Plan 0012 D8: each buffer resumes its own point/mark on return."""
    session = _session()
    session.dispatch(ForwardChar())
    session.dispatch(ForwardChar())
    session.dispatch(SetMark())  # alpha: point 2, mark 2
    _two_buffers(session)
    assert session.buffer.current.point == 0  # beta fresh
    session._select_buffer(BufferId("alpha"), [])
    current = session.buffer.current
    assert current.point == 2
    assert current.mark == 2


def test_undo_isolated_per_buffer() -> None:
    """Plan 0012 evidence 2: undo in B never touches A's history."""
    session = _session()
    session.dispatch(InsertText("!"))  # alpha edit (point 0 → prepends)
    _two_buffers(session)
    session.dispatch(InsertText("?"))  # beta edit
    session.dispatch(Undo())  # reverts beta only
    assert session.buffer.current.text == "bbb"
    session._select_buffer(BufferId("alpha"), [])
    assert session.buffer.current.text == "!aaa"
    session.dispatch(Undo())  # alpha's history intact
    assert session.buffer.current.text == "aaa"
