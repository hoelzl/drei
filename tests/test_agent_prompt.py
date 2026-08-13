"""`C-c a`: the one key that starts an agent turn (design 0005 D6).

The session's whole part in this is to open a text prompt and, on accept,
record *that the user asked for something*. It holds no `AcpMachine`, spawns
nothing, and does not know what ACP is — the pump reads
`AgentPromptSubmitted` out of the outcome, exactly as it reads
`PermissionDecided` (0005 D7). Both directions across that seam are events,
which is what keeps the transcript a complete record of what the user did.
"""

from __future__ import annotations

from drei.commands import (
    AgentPromptSubmitted,
    FrameResized,
    MinibufferOpened,
    PromptAgent,
)
from drei.harness import EditorHarness
from drei.keys import PendingKey, resolve


class TestKeymap:
    def test_c_c_opens_a_prefix_of_its_own(self) -> None:
        """Emacs reserves C-c for the user and the major mode, which is where
        an editor-specific command belongs. C-x stays the global prefix."""
        assert resolve(None, "C-c") == PendingKey("C-c")

    def test_c_c_a_resolves_to_the_agent_prompt(self) -> None:
        assert resolve("C-c", "a") == PromptAgent()

    def test_the_two_prefixes_do_not_share_bindings(self) -> None:
        """`C-x a` must stay unbound rather than inherit `C-c a`: a prefix
        table keyed by the pair is the point."""
        assert resolve("C-x", "a") != PromptAgent()
        assert resolve("C-c", "2") != resolve("C-x", "2")


class TestPrompt:
    def test_the_prompt_opens_and_takes_text(self) -> None:
        harness = EditorHarness(width=40, height=6)
        harness.send("C-c")
        outcome = harness.send("a")

        assert outcome is not None
        assert MinibufferOpened("Agent: ") in outcome.events
        assert harness.frame.rows[-1].startswith("Agent: ")

        for char in "hi":
            harness.send(char)
        assert harness.observation.minibuffer == "hi"

    def test_accepting_records_what_the_user_asked_for(self) -> None:
        harness = EditorHarness(width=40, height=6)
        harness.send("C-c")
        harness.send("a")
        for char in "explain this":
            harness.send(char)
        outcome = harness.send("RET")

        assert outcome is not None
        assert AgentPromptSubmitted("explain this") in outcome.events
        assert harness.observation.minibuffer is None

    def test_resize_preserves_agent_prompt_identity(self) -> None:
        harness = EditorHarness(width=40, height=6)
        harness.send("C-c")
        harness.send("a")
        for char in "explain this":
            harness.send(char)

        resized = harness.resize(60, 12)
        outcome = harness.send("RET")

        assert resized.events == (FrameResized(60, 12),)
        assert outcome is not None
        assert outcome.events == (AgentPromptSubmitted("explain this"),)

    def test_the_prompt_does_not_touch_the_buffer(self) -> None:
        """Typed text goes to the prompt, not the file being edited — the
        failure mode worth pinning, since both consume ordinary characters."""
        harness = EditorHarness(width=40, height=6, initial_text="draft")
        harness.send("C-c")
        harness.send("a")
        for char in "hello":
            harness.send(char)
        harness.send("RET")

        assert harness.observation.text == "draft"

    def test_empty_input_closes_the_prompt_and_asks_for_nothing(self) -> None:
        """Consistent with the other text prompts: RET on nothing is a silent
        no-op, not an empty prompt sent to the agent."""
        harness = EditorHarness(width=40, height=6)
        harness.send("C-c")
        harness.send("a")
        outcome = harness.send("RET")

        assert outcome is not None
        assert not any(
            isinstance(event, AgentPromptSubmitted) for event in outcome.events
        )
        assert harness.observation.minibuffer is None

    def test_aborting_asks_for_nothing(self) -> None:
        harness = EditorHarness(width=40, height=6)
        harness.send("C-c")
        harness.send("a")
        for char in "never mind":
            harness.send(char)
        outcome = harness.send("C-g")

        assert outcome is not None
        assert not any(
            isinstance(event, AgentPromptSubmitted) for event in outcome.events
        )
        assert harness.observation.minibuffer is None

    def test_the_session_holds_no_protocol_state(self) -> None:
        """Design 0005 D7 stated as a test: submitting a prompt adds an event
        and nothing else. Protocol phase lives in the pump, so replay never
        has to reproduce it."""
        harness = EditorHarness(width=40, height=6)
        harness.send("C-c")
        harness.send("a")
        harness.send("x")
        harness.send("RET")

        session = harness._session  # noqa: SLF001 - the point of the assertion
        assert not hasattr(session, "_machine")
        assert AgentPromptSubmitted("x") in session.transcript
