"""Session-side wiring for agent deliveries (design 0003 §B.7, plan 0011).

``apply_session_effects`` is the delivery entry point: one ``handle()`` call's
``SessionEffect`` list lands as one ``DeliverSessionEffects`` event plus at
most one ``InsertAgentText`` append — the same command boundary every user
edit crosses (design 0003 §consequence-2). Agent text is not a user edit:
the buffer stays unmodified, and deliveries are not undoable.
"""

from __future__ import annotations

import pytest

from drei.acp.machine import (
    AgentTextChunk,
    PermissionRequested,
    PromptCompleted,
    ProtocolError,
    SessionEstablished,
    ThoughtChunk,
)
from drei.commands import (
    AgentTextInserted,
    AgentTranscriptUpdated,
    DeliverProcessOutput,
    DeliverSessionEffects,
    FindFile,
    InsertAgentText,
    InsertText,
    KillLine,
    MinibufferAbort,
    Undo,
)
from drei.model import Buffer, BufferId, BufferValue
from drei.process import ProcessResult
from drei.session import EditorSession

AGENT = BufferId("*agent*")


def make_session(text: str = "", point: int = 0) -> EditorSession:
    """A session whose focused buffer IS the agent buffer.

    Since plan 0014 V2 a delivery must name a generated buffer (design 0004
    D3), so these tests create one. They focus it as well, because what they
    pin is delivery *semantics* — the fold, the append, not-undoable,
    not-modified — which is the same whether or not the human is looking at
    the transcript, and focusing it keeps the outcome observation a view of
    the buffer that changed. That deliveries do not follow focus is pinned
    separately, in ``test_agent_buffer_identity.py``.

    Seed text is written into the buffer rather than typed: a typed seed
    would leave an undo group for the not-undoable pins to trip over.
    """
    session = EditorSession(Buffer(BufferId("scratch"), BufferValue(text="", point=0)))
    agent = session._create_buffer(
        "*agent*", BufferValue(text=text, point=point), [], kind="generated"
    )
    session._select_buffer(agent, [])
    return session


class TestDeliverSessionEffectsValidation:
    def test_empty_effects_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            DeliverSessionEffects((), AGENT)

    def test_non_effect_members_rejected(self) -> None:
        with pytest.raises(ValueError, match="SessionEffect"):
            DeliverSessionEffects((AgentTextChunk(text="a"), "junk"), AGENT)  # type: ignore[arg-type]


class TestApplySessionEffects:
    def test_one_delivery_one_append(self) -> None:
        session = make_session()
        outcome = session.apply_session_effects(
            (AgentTextChunk(text="hi"), PromptCompleted(stop_reason="end_turn")), AGENT
        )
        assert (
            outcome.observation.text == "\n── agent ──\nhi\n── end turn (end_turn) ──\n"
        )
        kinds = [type(e) for e in session.transcript]
        assert kinds == [AgentTranscriptUpdated, AgentTextInserted]

    def test_delivery_event_carries_exactly_the_new_suffix(self) -> None:
        session = make_session()
        session.apply_session_effects((AgentTextChunk(text="a"),), AGENT)
        outcome = session.apply_session_effects((AgentTextChunk(text="b"),), AGENT)
        delivery = outcome.events[0]
        assert isinstance(delivery, AgentTranscriptUpdated)
        assert delivery.rendered == "b"  # the increment, not the whole transcript
        assert delivery.effects == (AgentTextChunk(text="b"),)
        assert outcome.observation.text == "\n── agent ──\nab"

    def test_silent_effects_deliver_without_append(self) -> None:
        session = make_session()
        outcome = session.apply_session_effects(
            (SessionEstablished(session_id="s"),), AGENT
        )
        assert [type(e) for e in outcome.events] == [AgentTranscriptUpdated]
        assert outcome.observation.text == ""

    def test_fold_cache_reconstructible_from_events(self) -> None:
        session = make_session()
        session.apply_session_effects((AgentTextChunk(text="x"),), AGENT)
        session.apply_session_effects((ThoughtChunk(text="t"),), AGENT)
        rendered = "".join(
            e.rendered
            for e in session.transcript
            if isinstance(e, AgentTranscriptUpdated)
        )
        assert session.buffer.current.text == rendered

    def test_audit_lines_land_in_buffer(self) -> None:
        session = make_session()
        session.apply_session_effects(
            (
                PermissionRequested(request_id=3, params={}),
                ProtocolError(detail="weird"),
            ),
            AGENT,
        )
        assert session.buffer.current.text == (
            "\n── permission requested (id 3) ──\n\n── protocol error: weird ──\n"
        )


class TestInsertAgentText:
    def test_appends_at_end_regardless_of_point(self) -> None:
        session = make_session(text="user text", point=0)
        outcome = session.dispatch(InsertAgentText(" AGENT", AGENT))
        assert outcome.observation.text == "user text AGENT"
        assert outcome.events == (AgentTextInserted(" AGENT", 9, 15, "*agent*"),)

    def test_buffer_stays_unmodified(self) -> None:
        session = make_session(text="x", point=1)
        session.dispatch(InsertText("y"))  # user edit: modified=True
        outcome = session.dispatch(InsertAgentText("z", AGENT))
        assert outcome.observation.modified is True  # inherited, not set by delivery
        session2 = make_session(text="x", point=1)
        outcome2 = session2.dispatch(InsertAgentText("z", AGENT))
        assert outcome2.observation.modified is False

    def test_point_follows_the_tail_only_from_the_tail(self) -> None:
        """Plan 0014 pin 3 / design 0004 D6. This used to assert that point
        tracked the new end unconditionally, which is how a burst of agent
        output relocated the cursor of a human mid-edit (review 0001 finding
        5). The negative arm below *is* the finding."""
        at_tail = make_session(text="abc", point=3)
        outcome = at_tail.dispatch(InsertAgentText("def", AGENT))
        assert outcome.observation.point == 6

        mid_buffer = make_session(text="abc", point=1)
        outcome = mid_buffer.dispatch(InsertAgentText("def", AGENT))
        assert outcome.observation.point == 1

    def test_mark_adjusted_marker_style(self) -> None:
        session = make_session(text="abc", point=3)
        session.dispatch(__import__("drei.commands", fromlist=["SetMark"]).SetMark())
        outcome = session.dispatch(InsertAgentText("zz", AGENT))
        # Insertion at the mark keeps the mark before the inserted text.
        assert outcome.observation.mark == 3

    def test_empty_insert_is_silent_noop(self) -> None:
        session = make_session()
        outcome = session.dispatch(InsertAgentText("", AGENT))
        assert outcome.events == ()
        assert session.transcript == ()


class TestAgentDeliveriesNotUndoable:
    """Parity registry row: agent deliveries are not undoable."""

    def test_insert_agent_text_pushes_no_undo_group(self) -> None:
        session = make_session()
        session.apply_session_effects((AgentTextChunk(text="stream"),), AGENT)
        outcome = session.dispatch(Undo())
        assert outcome.events == ()  # nothing to undo: silent no-op
        assert outcome.observation.text == "\n── agent ──\nstream"

    def test_undo_skips_agent_delivery_reaches_user_edit(self) -> None:
        session = make_session()
        session.dispatch(InsertText("mine"))
        session.apply_session_effects((AgentTextChunk(text="a"),), AGENT)
        session.dispatch(Undo())
        # The user insert is undone; the agent text is untouched.
        assert session.buffer.current.text == "\n── agent ──\na"


class TestUserEditsToAgentBufferNotRejected:
    """Parity registry row: user edits to the agent buffer are not rejected
    (hazard owned; §A.3 owns the enforcement mechanism)."""

    def test_user_edit_preserved_next_delivery_appends_after_it(self) -> None:
        session = make_session()
        session.apply_session_effects((AgentTextChunk(text="a"),), AGENT)
        session.dispatch(InsertText(" USER"))
        outcome = session.apply_session_effects((AgentTextChunk(text="b"),), AGENT)
        assert outcome.observation.text == "\n── agent ──\na USERb"
        # The fold cache and the live text now diverge — the owned hazard:
        rendered = "".join(
            e.rendered
            for e in session.transcript
            if isinstance(e, AgentTranscriptUpdated)
        )
        assert rendered == "\n── agent ──\nab" != outcome.observation.text


class TestMinibufferDoesNotSwallowDeliveries:
    """Parity registry row: external deliveries bypass the minibuffer gate."""

    def test_agent_delivery_lands_while_minibuffer_open(self) -> None:
        session = make_session()
        session.dispatch(FindFile())  # opens the minibuffer
        assert session.minibuffer is not None
        outcome = session.apply_session_effects((AgentTextChunk(text="live"),), AGENT)
        assert outcome.observation.text == "\n── agent ──\nlive"
        assert session.minibuffer is not None  # prompt undisturbed
        session.dispatch(MinibufferAbort())

    def test_process_delivery_lands_while_minibuffer_open(self) -> None:
        session = make_session()
        session.dispatch(FindFile())
        outcome = session.dispatch(
            DeliverProcessOutput(("cmd",), ProcessResult(("cmd",), 0, "out", ""), None)
        )
        assert any(type(e).__name__ == "ProcessOutputRecorded" for e in outcome.events)


class TestDeliveriesAndTheKillChain:
    """An event-emitting delivery breaks the kill-append chain of the buffer
    it targeted (the chain rule is 'event-emitting commands intervene'), and
    a command that emits nothing intervenes in nothing. Pinned as deliberate.

    That the broken chain is the *target's* and not the focused buffer's is
    plan 0014 pin 1, pinned in ``test_agent_buffer_identity.py`` where the two
    buffers are distinct; here the agent buffer is the focused one.
    """

    def test_fold_only_delivery_breaks_its_targets_chain(self) -> None:
        session = make_session(text="aa\nbb\n", point=0)
        session.dispatch(KillLine())
        session.dispatch(DeliverSessionEffects((AgentTextChunk(text="x"),), AGENT))
        session.dispatch(KillLine())
        assert session.kill_ring == ("\n", "aa")  # two entries: chain broken

    def test_a_silent_noop_does_not_break_the_chain(self) -> None:
        """Plan 0014 pin 2. This rule used to be pinned *through* a delivery:
        the delivery dragged point to end-of-buffer, which turned the next
        KillLine into the silent no-op under test. Design 0004 D6 removed that
        mechanism (point no longer jumps), so the rule is pinned directly —
        an empty insert emits nothing and therefore intervenes in nothing."""
        session = make_session(text="aa\nbb\n", point=0)
        session.dispatch(KillLine())
        outcome = session.dispatch(InsertText(""))  # silent no-op
        assert outcome.events == ()
        session.dispatch(KillLine())
        assert session.kill_ring == ("aa\n",)  # one entry: chain intact


class TestDispatchRejectsCorruptDelivery:
    def test_deliver_sessioneffects_through_dispatch(self) -> None:
        session = make_session()
        outcome = session.dispatch(
            DeliverSessionEffects((AgentTextChunk(text="q"),), AGENT)
        )
        assert [type(e) for e in outcome.events] == [AgentTranscriptUpdated]
        # Raw dispatch does NOT append text — the fold→append step belongs to
        # apply_session_effects (the atomic delivery seam).
        assert outcome.observation.text == ""
