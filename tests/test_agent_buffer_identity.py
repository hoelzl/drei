"""Agent deliveries target a named buffer, never the focused one (plan 0014).

Design 0004: a transcript binds to an agent buffer derived from the ACP
session. Before this slice ``InsertAgentText`` / ``DeliverSessionEffects``
appended to ``self.buffer`` — whichever buffer happened to be focused when the
delivery landed (review 0001 finding 5). ``dispatch`` had no way to express
"edit that other buffer" at all: it opened with ``self.buffer.current`` and
closed with ``self.buffer.replace(...)``.

V1 of the slice is the general mechanism, not a special case: dispatch
resolves a **target** buffer, focus being the default rather than the rule.
These tests drive it. The per-buffer bookkeeping arm matters as much as the
text arm — the four blocks at the end of dispatch (kill chain, yank, undo
group, undo descent) run for *every* command, deliveries included, so routing
only the text would leave a delivery breaking the focused buffer's chains.
"""

from __future__ import annotations

import pytest
from conftest import FakeFilePort

from drei.acp.machine import AgentTextChunk, PromptCompleted, ThoughtChunk
from drei.commands import (
    AgentTextInserted,
    AgentTranscriptUpdated,
    BufferCreated,
    CreateAgentBuffer,
    DeliverSessionEffects,
    InsertAgentText,
    InsertText,
    KillLine,
    OtherWindow,
    SaveBuffer,
    SaveFailed,
    SplitWindow,
    Yank,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession, WindowValue

FOCUSED = BufferId("alpha")
AGENT = BufferId("*agent*")
ACP_SESSION = "acp-1"


def _session(text: str = "", point: int = 0) -> EditorSession:
    """A session focused on 'alpha', with an agent buffer for ``ACP_SESSION``.

    The second buffer is the *agent* buffer rather than an arbitrary one: since
    V2 only a generated buffer accepts a delivery (design 0004 D3), so the
    targeting tests below have to name a real one.
    """
    session = EditorSession(
        Buffer(FOCUSED, BufferValue(text=text, point=point)),
        file_port=FakeFilePort(),
    )
    session.dispatch(CreateAgentBuffer(ACP_SESSION))
    return session


class TestDeliveryTargetsANamedBuffer:
    def test_insert_agent_text_appends_to_the_target_not_the_focused_buffer(
        self,
    ) -> None:
        session = _session(text="user text", point=4)

        session.dispatch(InsertAgentText("agent says hi", buffer_id=AGENT))

        assert session._buffers[AGENT].current.text == "agent says hi"
        assert session.buffer.buffer_id == FOCUSED  # focus did not move
        assert session.buffer.current.text == "user text"
        assert session.buffer.current.point == 4  # point not stolen

    def test_deliver_session_effects_folds_into_the_target(self) -> None:
        session = _session(text="user text")

        outcome = session.dispatch(
            DeliverSessionEffects((AgentTextChunk(text="chunk"),), buffer_id=AGENT)
        )

        (recorded,) = [
            e for e in outcome.events if isinstance(e, AgentTranscriptUpdated)
        ]
        assert recorded.buffer_id == AGENT.value
        assert session.buffer.current.text == "user text"

    def test_events_name_the_buffer_they_changed(self) -> None:
        session = _session()

        outcome = session.dispatch(InsertAgentText("x", buffer_id=AGENT))

        (inserted,) = [e for e in outcome.events if isinstance(e, AgentTextInserted)]
        assert inserted.buffer_id == AGENT.value

    def test_outcome_observation_stays_the_focused_view(self) -> None:
        """The read model is what the user is looking at, target or not."""
        session = _session(text="user text")

        outcome = session.dispatch(InsertAgentText("agent", buffer_id=AGENT))

        assert outcome.observation.buffer_id == FOCUSED.value
        assert outcome.observation.text == "user text"


class TestDeliveryLeavesTheFocusedBufferBookkeepingAlone:
    """The arm that fails if only the text is routed to the target.

    The kill chain, yank chain, and undo stacks live in a per-buffer record,
    and the blocks that maintain them run at the end of *every* dispatch. If
    they keep resolving to the focused buffer, a delivery into the agent
    buffer silently breaks the human's kill-append chain and their undo
    descent — the same ambient-focus defect as the text arm, one block over.
    """

    def test_delivery_does_not_break_the_focused_kill_chain(self) -> None:
        session = _session(text="aa\nbb\n", point=0)
        session.dispatch(KillLine())

        session.dispatch(
            DeliverSessionEffects((AgentTextChunk(text="x"),), buffer_id=AGENT)
        )
        session.dispatch(KillLine())

        # Consecutive kills append into ONE ring entry: the delivery landed in
        # another buffer and must not have intervened.
        assert session.kill_ring == ("aa\n",)

    def test_delivery_does_not_break_the_focused_yank_chain(self) -> None:
        session = _session(text="aa\nbb\n", point=0)
        session.dispatch(KillLine())
        session.dispatch(Yank())

        session.dispatch(InsertAgentText("x", buffer_id=AGENT))

        assert session._states[FOCUSED].yank_active is True

    def test_delivery_does_not_break_the_focused_undo_descent(self) -> None:
        session = _session()
        session.dispatch(InsertText("abc"))

        session.dispatch(InsertAgentText("x", buffer_id=AGENT))

        # An event-emitting non-undo command breaks the descent; a delivery
        # into another buffer is not one of the focused buffer's commands.
        assert session._states[FOCUSED].undo_descending is False
        assert len(session._states[FOCUSED].undo_history) == 1

    def test_a_delivery_breaks_the_targets_chain_not_the_users(self) -> None:
        """Both halves of plan 0014 pin 1.

        A fold-only delivery is an event-emitting command, so it intervenes in
        the kill-append chain — of the buffer it targeted. Before this slice it
        broke whichever chain the human happened to have open, which is the
        same ambient-focus defect as the text arm, one block over.
        """
        session = _session(text="aa\nbb\n", point=0)
        session.dispatch(KillLine())  # the human's chain is open
        session._states[AGENT].last_was_kill = True  # so is the agent buffer's

        session.dispatch(
            DeliverSessionEffects((AgentTextChunk(text="x"),), buffer_id=AGENT)
        )

        assert session._states[AGENT].last_was_kill is False  # target's: broken
        assert session._states[FOCUSED].last_was_kill is True  # the human's: intact


class TestCreateAgentBuffer:
    """V2: the agent buffer is a thing that exists (design 0004 D1/D4).

    Creation is a command rather than a plain method so ``BufferCreated``
    enters the transcript through the one boundary everything else crosses,
    and it is delivery-class: the buffer springs into existence because the
    *agent's* session was established, so it must disturb the human as little
    as a delivery does.
    """

    def test_creates_a_generated_buffer_named_agent(self) -> None:
        session = EditorSession(Buffer(FOCUSED, BufferValue(text="", point=0)))

        session.dispatch(CreateAgentBuffer(ACP_SESSION))

        assert session.agent_buffer_id(ACP_SESSION) == AGENT
        assert session._states[AGENT].kind == "generated"
        assert "*agent*" in session.buffers

    def test_no_binding_before_creation(self) -> None:
        session = EditorSession(Buffer(FOCUSED, BufferValue(text="", point=0)))

        assert session.agent_buffer_id(ACP_SESSION) is None

    def test_records_buffer_created(self) -> None:
        session = EditorSession(Buffer(FOCUSED, BufferValue(text="", point=0)))

        outcome = session.dispatch(CreateAgentBuffer(ACP_SESSION))

        assert outcome.events == (BufferCreated("*agent*", None),)
        assert session.transcript == (BufferCreated("*agent*", None),)

    def test_idempotent_per_acp_session(self) -> None:
        """A re-fold of ``SessionEstablished`` must not mint a second buffer."""
        session = _session()

        outcome = session.dispatch(CreateAgentBuffer(ACP_SESSION))

        assert outcome.events == ()  # nothing happened: no second BufferCreated
        assert session.agent_buffer_id(ACP_SESSION) == AGENT
        assert session.buffers.count("*agent*") == 1

    def test_a_second_acp_session_gets_its_own_buffer(self) -> None:
        session = _session()

        session.dispatch(CreateAgentBuffer("acp-2"))

        second = session.agent_buffer_id("acp-2")
        assert second == BufferId("*agent*<2>")  # the existing collision rule
        assert second != session.agent_buffer_id(ACP_SESSION)

    def test_does_not_change_focus(self) -> None:
        """The agent buffer appearing must not yank the user out of their
        work (design 0004 D1: it exists and is switchable-to, no more)."""
        session = EditorSession(Buffer(FOCUSED, BufferValue(text="ab", point=1)))

        outcome = session.dispatch(CreateAgentBuffer(ACP_SESSION))

        assert session.buffer.buffer_id == FOCUSED
        assert outcome.observation.buffer_id == FOCUSED.value
        assert session.windows[session.focused].buffer_id == FOCUSED
        assert session.buffer.current.point == 1

    def test_does_not_intervene_in_the_focused_buffer_chains(self) -> None:
        """Delivery-class, so the same rule as a delivery: the creation's
        ``BufferCreated`` is an event, but it is not one of the *user's*
        commands and must not break their kill-append chain."""
        session = EditorSession(Buffer(FOCUSED, BufferValue(text="aa\nbb\n", point=0)))
        session.dispatch(KillLine())

        session.dispatch(CreateAgentBuffer(ACP_SESSION))
        session.dispatch(KillLine())

        assert session.kill_ring == ("aa\n",)  # one entry: the chain survived

    def test_creation_is_not_swallowed_by_an_open_minibuffer(self) -> None:
        """Delivery-class commands bypass the minibuffer gate. A swallowed
        creation would leave every later delivery for this ACP session naming
        a buffer that does not exist — which now raises."""
        from drei.commands import FindFile

        session = EditorSession(Buffer(FOCUSED, BufferValue(text="", point=0)))
        session.dispatch(FindFile())

        session.dispatch(CreateAgentBuffer(ACP_SESSION))

        assert session.agent_buffer_id(ACP_SESSION) == AGENT
        assert session.minibuffer is not None  # prompt undisturbed

    def test_agent_buffer_visits_no_file(self) -> None:
        """Design 0004 D4: there is nothing on disk for ``modified`` to
        describe, and ``C-x C-s`` reports the existing ``no-file`` token."""
        session = _session()
        session._select_buffer(AGENT, [])

        outcome = session.dispatch(SaveBuffer())

        assert session.buffer.current.file_path is None
        assert outcome.events == (SaveFailed("*agent*", "no-file"),)


class TestOnlyGeneratedBuffersAcceptDeliveries:
    """Design 0004 D3: a delivery naming a missing or ordinary buffer is a
    caller bug and raises — not a silent drop (which would desync the fold)
    and not a write into a file buffer (which is the hazard the whole slice
    exists to remove). The pump can only pass ids the session minted, so this
    is a programming error, never peer input."""

    def test_delivery_to_an_unknown_buffer_raises(self) -> None:
        session = _session()

        with pytest.raises(ValueError, match="no such buffer"):
            session.dispatch(
                DeliverSessionEffects(
                    (AgentTextChunk(text="x"),), buffer_id=BufferId("nope")
                )
            )

    def test_delivery_to_an_ordinary_buffer_raises(self) -> None:
        session = _session(text="user text")

        with pytest.raises(ValueError, match="not a generated buffer"):
            session.dispatch(
                DeliverSessionEffects((AgentTextChunk(text="x"),), buffer_id=FOCUSED)
            )

    def test_insert_agent_text_into_an_ordinary_buffer_raises(self) -> None:
        session = _session(text="user text")

        with pytest.raises(ValueError, match="not a generated buffer"):
            session.dispatch(InsertAgentText("agent", buffer_id=FOCUSED))

    def test_a_rejected_delivery_changes_nothing(self) -> None:
        """Atomicity: the raise happens before any mutation, so no event is
        recorded and no fold advances (a half-applied delivery would desync
        the cache from the transcript)."""
        session = _session(text="user text")

        with pytest.raises(ValueError):
            session.dispatch(InsertAgentText("agent", buffer_id=FOCUSED))

        assert session.buffer.current.text == "user text"
        assert [e for e in session.transcript if not isinstance(e, BufferCreated)] == []

    def test_apply_session_effects_requires_a_generated_target(self) -> None:
        session = _session()

        with pytest.raises(ValueError, match="not a generated buffer"):
            session.apply_session_effects((AgentTextChunk(text="x"),), FOCUSED)


class TestEachAgentBufferFoldsIndependently:
    """V3 / design 0004 D5: the fold cache is per target buffer.

    The fold is *interpreter* state — whether a turn header has been emitted,
    whether a thought block is open, how many turns have completed. One shared
    fold would let two ACP sessions rewrite each other's rendering state: the
    second session's ``PromptCompleted`` closes the first session's open turn,
    so the first session's next chunk re-emits the ``── agent ──`` header
    mid-stream, and the per-buffer oracle stops holding for either buffer.
    """

    def _two(self) -> tuple[EditorSession, BufferId, BufferId]:
        session = _session()
        session.dispatch(CreateAgentBuffer("acp-2"))
        first = session.agent_buffer_id(ACP_SESSION)
        second = session.agent_buffer_id("acp-2")
        assert first is not None and second is not None
        return session, first, second

    def _text(self, session: EditorSession, buffer_id: BufferId) -> str:
        return session._buffers[buffer_id].current.text

    def test_a_turn_ending_in_one_buffer_does_not_reopen_anothers_header(
        self,
    ) -> None:
        session, first, second = self._two()

        session.apply_session_effects((AgentTextChunk(text="a"),), first)
        session.apply_session_effects(
            (PromptCompleted(stop_reason="end_turn"),), second
        )
        session.apply_session_effects((AgentTextChunk(text="c"),), first)

        # The first buffer's turn is still open: "c" continues it. Under a
        # shared fold the second buffer's end-turn would have closed it and
        # this would read "...a\n── agent ──\nc".
        assert self._text(session, first) == "\n── agent ──\nac"

    def test_each_buffers_text_is_its_own_fold(self) -> None:
        session, first, second = self._two()

        session.apply_session_effects((ThoughtChunk(text="think"),), first)
        session.apply_session_effects((AgentTextChunk(text="hello"),), second)
        session.apply_session_effects((AgentTextChunk(text="done"),), first)

        for buffer_id in (first, second):
            rendered = "".join(
                e.rendered
                for e in session.transcript
                if isinstance(e, AgentTranscriptUpdated)
                and e.buffer_id == buffer_id.value
            )
            assert self._text(session, buffer_id) == rendered

    def test_turn_counters_are_independent(self) -> None:
        """Turn counting is fold state too: three turns in one transcript must
        not make the other transcript's next turn its fourth."""
        session, first, second = self._two()
        # The second buffer must have a fold of its own before the assertion
        # below means anything: `.get(second, TranscriptFold()).turns == 0`
        # over an absent key asserts a property of the default value, and
        # passes even under one session-wide fold.
        session.apply_session_effects((AgentTextChunk(text="b"),), second)

        for _ in range(3):
            session.apply_session_effects(
                (PromptCompleted(stop_reason="end_turn"),), first
            )

        assert session._agent_folds[first].turns == 3
        assert session._agent_folds[second].turns == 0


class TestDeliveriesFollowTheTail:
    """V4 / design 0004 D6: ``tail -f``, not a cursor grab.

    Point moves to the new end **only** if it was already at the old end.
    Stated over windows as well as the buffer, because A.2 made window point
    distinct from ``BufferValue.point``: a user scrolled back through the
    transcript in one window must not be dragged to the end because output
    arrived in another window showing the same buffer.
    """

    def _split_with_agent_in_the_unfocused_window(
        self, agent_text: str = "", agent_point: int = 0
    ) -> EditorSession:
        """Two windows: the unfocused one shows the agent buffer, the focused
        one the user's. The only arrangement in which the window rule is
        distinguishable from the buffer rule."""
        session = _session()
        session._buffers[AGENT].replace(BufferValue(text=agent_text, point=agent_point))
        session.dispatch(SplitWindow())
        session._select_buffer(AGENT, [])  # focused window shows *agent*
        session.dispatch(OtherWindow())  # …and focus moves off it
        assert session.buffer.buffer_id == FOCUSED
        assert session.windows[0].buffer_id == AGENT
        return session

    def test_point_at_the_tail_follows(self) -> None:
        session = _session()
        session._buffers[AGENT].replace(BufferValue(text="abc", point=3))

        session.dispatch(InsertAgentText("def", buffer_id=AGENT))

        assert session._buffers[AGENT].current.point == 6

    def test_point_before_the_tail_stays_put(self) -> None:
        """The finding: a burst of agent output must not relocate a cursor."""
        session = _session()
        session._buffers[AGENT].replace(BufferValue(text="abc", point=1))

        session.dispatch(InsertAgentText("def", buffer_id=AGENT))

        assert session._buffers[AGENT].current.point == 1
        assert session._buffers[AGENT].current.text == "abcdef"

    def test_a_non_focused_window_at_the_tail_follows(self) -> None:
        session = self._split_with_agent_in_the_unfocused_window("abc", 3)

        session.dispatch(InsertAgentText("def", buffer_id=AGENT))

        assert session.windows[0].point == 6

    def test_a_non_focused_window_scrolled_back_keeps_its_point(self) -> None:
        session = self._split_with_agent_in_the_unfocused_window("abc", 1)

        session.dispatch(InsertAgentText("def", buffer_id=AGENT))

        assert session.windows[0].point == 1

    def test_the_focused_window_over_another_buffer_is_untouched(self) -> None:
        """The end-of-dispatch window refresh is skipped when the target is
        not the focused buffer: the focused window must not be handed a point
        read from a buffer it does not show."""
        session = self._split_with_agent_in_the_unfocused_window("abc", 3)
        focused_before = session.windows[session.focused]

        session.dispatch(InsertAgentText("def", buffer_id=AGENT))

        assert session.windows[session.focused] == focused_before

    def test_a_window_over_another_buffer_does_not_follow_the_targets_tail(
        self,
    ) -> None:
        """The rule reads the **target's** windows. A non-focused window over
        a *different* buffer whose stored point merely coincides with the
        target's old length must not be dragged along — it would land past the
        end of its own buffer, and the staleness clamp would then hide the
        corruption until the next `C-x o`."""
        session = self._split_with_agent_in_the_unfocused_window("abc", 3)
        # Window 1 (focused, showing 'alpha') is excluded by the focus rule,
        # so give window 2 the coinciding point: three windows, the third
        # showing the user's buffer at the target's pre-append length.
        session.dispatch(SplitWindow())
        session._windows = (
            session.windows[0],
            session.windows[1],
            WindowValue(FOCUSED, 3, None),
        )

        session.dispatch(InsertAgentText("def", buffer_id=AGENT))

        assert session.windows[0].point == 6  # the target's window: follows
        assert session.windows[2].point == 3  # the user's window: does not

    def test_mark_adjustment_is_unchanged(self) -> None:
        """Design 0004 D6: the existing insert rule still applies. An append
        happens at end-of-buffer, so a mark is never after it and never
        moves."""
        session = _session()
        session._buffers[AGENT].replace(BufferValue(text="abc", point=3, mark=1))

        session.dispatch(InsertAgentText("def", buffer_id=AGENT))

        assert session._buffers[AGENT].current.mark == 1
