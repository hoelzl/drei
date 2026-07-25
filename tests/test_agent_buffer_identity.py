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

from conftest import FakeFilePort

from drei.acp.machine import AgentTextChunk
from drei.commands import (
    AgentTextInserted,
    AgentTranscriptUpdated,
    DeliverSessionEffects,
    InsertAgentText,
    InsertText,
    KillLine,
    Yank,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.session import EditorSession

FOCUSED = BufferId("alpha")
OTHER = BufferId("beta")


def _session(text: str = "", point: int = 0) -> EditorSession:
    """A session focused on 'alpha', with a second buffer 'beta' available."""
    session = EditorSession(
        Buffer(FOCUSED, BufferValue(text=text, point=point)),
        file_port=FakeFilePort(),
    )
    session._create_buffer("beta", BufferValue(text="", point=0), [])
    return session


class TestDeliveryTargetsANamedBuffer:
    def test_insert_agent_text_appends_to_the_target_not_the_focused_buffer(
        self,
    ) -> None:
        session = _session(text="user text", point=4)

        session.dispatch(InsertAgentText("agent says hi", buffer_id=OTHER))

        assert session._buffers[OTHER].current.text == "agent says hi"
        assert session.buffer.buffer_id == FOCUSED  # focus did not move
        assert session.buffer.current.text == "user text"
        assert session.buffer.current.point == 4  # point not stolen

    def test_deliver_session_effects_folds_into_the_target(self) -> None:
        session = _session(text="user text")

        outcome = session.dispatch(
            DeliverSessionEffects((AgentTextChunk(text="chunk"),), buffer_id=OTHER)
        )

        (recorded,) = [
            e for e in outcome.events if isinstance(e, AgentTranscriptUpdated)
        ]
        assert recorded.buffer_id == OTHER.value
        assert session.buffer.current.text == "user text"

    def test_events_name_the_buffer_they_changed(self) -> None:
        session = _session()

        outcome = session.dispatch(InsertAgentText("x", buffer_id=OTHER))

        (inserted,) = [e for e in outcome.events if isinstance(e, AgentTextInserted)]
        assert inserted.buffer_id == OTHER.value

    def test_outcome_observation_stays_the_focused_view(self) -> None:
        """The read model is what the user is looking at, target or not."""
        session = _session(text="user text")

        outcome = session.dispatch(InsertAgentText("agent", buffer_id=OTHER))

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
            DeliverSessionEffects((AgentTextChunk(text="x"),), buffer_id=OTHER)
        )
        session.dispatch(KillLine())

        # Consecutive kills append into ONE ring entry: the delivery landed in
        # another buffer and must not have intervened.
        assert session.kill_ring == ("aa\n",)

    def test_delivery_does_not_break_the_focused_yank_chain(self) -> None:
        session = _session(text="aa\nbb\n", point=0)
        session.dispatch(KillLine())
        session.dispatch(Yank())

        session.dispatch(InsertAgentText("x", buffer_id=OTHER))

        assert session._states[FOCUSED].yank_active is True

    def test_delivery_does_not_break_the_focused_undo_descent(self) -> None:
        session = _session()
        session.dispatch(InsertText("abc"))

        session.dispatch(InsertAgentText("x", buffer_id=OTHER))

        # An event-emitting non-undo command breaks the descent; a delivery
        # into another buffer is not one of the focused buffer's commands.
        assert session._states[FOCUSED].undo_descending is False
        assert len(session._states[FOCUSED].undo_history) == 1

    def test_delivery_bookkeeping_lands_on_the_target(self) -> None:
        """The flip side: the target's own chains DO see the delivery."""
        session = _session()
        target_state = session._states[OTHER]
        target_state.last_was_kill = True

        session.dispatch(
            DeliverSessionEffects((AgentTextChunk(text="x"),), buffer_id=OTHER)
        )

        assert target_state.last_was_kill is False
