"""Second disposable live-model architecture experiment.

Exercises grouped branching history, overlays, deterministic external delivery,
mail-like generated views, replay, and a distinct hybrid ownership model.
This is spike evidence, not production code for Drei.
"""

from __future__ import annotations

import hashlib
import json
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Literal, Protocol, Self

Affinity = Literal["left", "right"]


@dataclass(frozen=True, slots=True)
class OverlayValue:
    overlay_id: int
    start: int
    end: int
    start_affinity: Affinity
    end_affinity: Affinity


@dataclass(frozen=True, slots=True)
class MessageValue:
    message_id: str
    subject: str
    unread: bool = True


@dataclass(frozen=True, slots=True)
class TextValue:
    buffer_id: int
    text: str
    overlays: tuple[OverlayValue, ...]
    process_output: str = ""
    invalidations: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class MailValue:
    buffer_id: int
    messages: tuple[MessageValue, ...] = ()
    selected_id: str | None = None


@dataclass(frozen=True, slots=True)
class StateValue:
    text: TextValue
    mail: MailValue
    last_external_sequence: int = 0


@dataclass(frozen=True, slots=True)
class Observation:
    text: str
    overlay_ranges: tuple[tuple[int, int, int], ...]
    process_output: str
    invalidations: tuple[tuple[int, int], ...]
    mail_rows: tuple[str, ...]
    selected_message_id: str | None
    unread_message_ids: tuple[str, ...]
    last_external_sequence: int


@dataclass(frozen=True, slots=True)
class Insert:
    position: int
    text: str


@dataclass(frozen=True, slots=True)
class ProcessDelivery:
    sequence: int
    text: str


@dataclass(frozen=True, slots=True)
class MailDelivery:
    sequence: int
    message_id: str
    subject: str


@dataclass(frozen=True, slots=True)
class SelectMessage:
    message_id: str


@dataclass(frozen=True, slots=True)
class MarkRead:
    message_id: str


Operation = Insert | ProcessDelivery | MailDelivery | SelectMessage | MarkRead


@dataclass(frozen=True, slots=True)
class Transaction:
    label: str
    operations: tuple[Operation, ...]


@dataclass(frozen=True, slots=True)
class Undo:
    pass


@dataclass(frozen=True, slots=True)
class Redo:
    branch: int


Action = Transaction | Undo | Redo


@dataclass(frozen=True, slots=True)
class EventRecord:
    index: int
    action: str
    label: str
    observation_digest: str


@dataclass(frozen=True, slots=True)
class HistoryNode:
    node_id: int
    parent_id: int | None
    value: StateValue
    label: str


@dataclass(slots=True)
class BranchingHistory:
    nodes: dict[int, HistoryNode]
    children: dict[int, list[int]]
    current_id: int
    next_id: int

    @classmethod
    def create(cls, initial: StateValue) -> Self:
        root = HistoryNode(0, None, initial, "initial")
        return cls(nodes={0: root}, children={0: []}, current_id=0, next_id=1)

    def commit(self, value: StateValue, label: str) -> None:
        node_id = self.next_id
        self.next_id += 1
        node = HistoryNode(node_id, self.current_id, value, label)
        self.nodes[node_id] = node
        self.children.setdefault(self.current_id, []).append(node_id)
        self.children[node_id] = []
        self.current_id = node_id

    def undo(self) -> StateValue:
        parent_id = self.nodes[self.current_id].parent_id
        if parent_id is None:
            raise IndexError("cannot undo the initial state")
        self.current_id = parent_id
        return self.nodes[parent_id].value

    def redo(self, branch: int) -> StateValue:
        child_ids = self.children[self.current_id]
        if not 0 <= branch < len(child_ids):
            raise IndexError(f"unknown redo branch {branch}")
        self.current_id = child_ids[branch]
        return self.nodes[self.current_id].value


class ArchitectureAdapter(Protocol):
    name: str
    counters: dict[str, int]

    def begin_transaction(self) -> None: ...

    def apply(self, operation: Operation) -> None: ...

    def snapshot(self) -> StateValue: ...

    def restore(self, value: StateValue) -> None: ...

    def extension_report(self) -> dict[str, bool]: ...

    def rollback_restored(self, before: StateValue) -> bool: ...


def initial_value() -> StateValue:
    return StateValue(
        text=TextValue(
            buffer_id=1,
            text="abcdef",
            overlays=(OverlayValue(1, 2, 4, "right", "left"),),
        ),
        mail=MailValue(buffer_id=2),
    )


def shifted_boundary(
    boundary: int,
    affinity: Affinity,
    position: int,
    amount: int,
) -> int:
    if boundary > position or (boundary == position and affinity == "right"):
        return boundary + amount
    return boundary


def insert_text(value: TextValue, position: int, text: str) -> TextValue:
    if not 0 <= position <= len(value.text):
        raise IndexError(position)
    amount = len(text)
    overlays = tuple(
        replace(
            overlay,
            start=shifted_boundary(
                overlay.start, overlay.start_affinity, position, amount
            ),
            end=shifted_boundary(overlay.end, overlay.end_affinity, position, amount),
        )
        for overlay in value.overlays
    )
    return replace(
        value,
        text=value.text[:position] + text + value.text[position:],
        overlays=overlays,
        invalidations=(*value.invalidations, (position, position + amount)),
    )


def deliver_process(value: TextValue, text: str) -> TextValue:
    position = len(value.text)
    updated = insert_text(value, position, text)
    return replace(updated, process_output=value.process_output + text)


def deliver_mail(value: MailValue, delivery: MailDelivery) -> MailValue:
    replacement = MessageValue(delivery.message_id, delivery.subject)
    messages = list(value.messages)
    for index, message in enumerate(messages):
        if message.message_id == delivery.message_id:
            messages[index] = replace(replacement, unread=message.unread)
            break
    else:
        messages.append(replacement)
    return replace(value, messages=tuple(messages))


def select_mail(value: MailValue, message_id: str) -> MailValue:
    if message_id not in {message.message_id for message in value.messages}:
        raise KeyError(message_id)
    return replace(value, selected_id=message_id)


def mark_read(value: MailValue, message_id: str) -> MailValue:
    found = False
    messages = []
    for message in value.messages:
        if message.message_id == message_id:
            found = True
            messages.append(replace(message, unread=False))
        else:
            messages.append(message)
    if not found:
        raise KeyError(message_id)
    return replace(value, messages=tuple(messages))


def validate_external(sequence: int, previous: int) -> None:
    if sequence <= previous:
        raise ValueError(
            f"external sequence {sequence} is not after accepted sequence {previous}"
        )


def apply_to_value(value: StateValue, operation: Operation) -> StateValue:
    match operation:
        case Insert(position, text):
            return replace(value, text=insert_text(value.text, position, text))
        case ProcessDelivery(sequence, text):
            validate_external(sequence, value.last_external_sequence)
            return replace(
                value,
                text=deliver_process(value.text, text),
                last_external_sequence=sequence,
            )
        case MailDelivery(sequence, _, _):
            validate_external(sequence, value.last_external_sequence)
            return replace(
                value,
                mail=deliver_mail(value.mail, operation),
                last_external_sequence=sequence,
            )
        case SelectMessage(message_id):
            return replace(value, mail=select_mail(value.mail, message_id))
        case MarkRead(message_id):
            return replace(value, mail=mark_read(value.mail, message_id))


def observe(value: StateValue) -> Observation:
    return Observation(
        text=value.text.text,
        overlay_ranges=tuple(
            (overlay.overlay_id, overlay.start, overlay.end)
            for overlay in value.text.overlays
        ),
        process_output=value.text.process_output,
        invalidations=value.text.invalidations,
        mail_rows=tuple(
            f"{'*' if message.unread else ' '} {message.message_id} {message.subject}"
            for message in value.mail.messages
        ),
        selected_message_id=value.mail.selected_id,
        unread_message_ids=tuple(
            message.message_id for message in value.mail.messages if message.unread
        ),
        last_external_sequence=value.last_external_sequence,
    )


def observation_digest(observation: Observation) -> str:
    payload = json.dumps(asdict(observation), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class TextHandle:
    buffer_id: int


@dataclass(frozen=True, slots=True)
class OverlayHandle:
    overlay_id: int


@dataclass(slots=True)
class PersistentAdapter:
    name: str
    root: StateValue
    registry: dict[int, TextValue]
    extension_handle: TextHandle
    extension_overlay_handle: OverlayHandle
    original_text_value: TextValue
    counters: dict[str, int]

    @classmethod
    def create(cls) -> Self:
        root = initial_value()
        return cls(
            name="fully_persistent",
            root=root,
            registry={root.text.buffer_id: root.text},
            extension_handle=TextHandle(root.text.buffer_id),
            extension_overlay_handle=OverlayHandle(root.text.overlays[0].overlay_id),
            original_text_value=root.text,
            counters={"whole_root_replacements": 0, "handle_resolutions": 0},
        )

    def publish(self) -> None:
        self.registry[self.root.text.buffer_id] = self.root.text

    def begin_transaction(self) -> None:
        if not self.root.text.invalidations:
            return
        self.root = replace(self.root, text=replace(self.root.text, invalidations=()))
        self.counters["whole_root_replacements"] += 1
        self.publish()

    def apply(self, operation: Operation) -> None:
        self.root = apply_to_value(self.root, operation)
        self.counters["whole_root_replacements"] += 1
        self.publish()

    def snapshot(self) -> StateValue:
        return self.root

    def restore(self, value: StateValue) -> None:
        self.root = value
        self.counters["whole_root_replacements"] += 1
        self.publish()

    def extension_report(self) -> dict[str, bool]:
        self.counters["handle_resolutions"] += 1
        resolved = self.registry[self.extension_handle.buffer_id]
        overlay_resolved = any(
            overlay.overlay_id == self.extension_overlay_handle.overlay_id
            for overlay in resolved.overlays
        )
        return {
            "direct_value_remains_current": self.original_text_value is self.root.text,
            "overlay_reference_or_handle_remains_current": overlay_resolved,
            "stable_reference_remains_current": resolved is self.root.text,
        }

    def rollback_restored(self, before: StateValue) -> bool:
        return self.root is before


@dataclass(slots=True)
class MutableOverlay:
    overlay_id: int
    start: int
    end: int
    start_affinity: Affinity
    end_affinity: Affinity


@dataclass(slots=True)
class MutableMessage:
    message_id: str
    subject: str
    unread: bool


@dataclass(slots=True)
class MutableTextBuffer:
    buffer_id: int
    text: str
    overlays: list[MutableOverlay]
    process_output: str
    invalidations: list[tuple[int, int]]


@dataclass(slots=True)
class MutableMailBuffer:
    buffer_id: int
    messages: dict[str, MutableMessage]
    selected_id: str | None


@dataclass(slots=True)
class ControlledMutableAdapter:
    name: str
    text_buffer: MutableTextBuffer
    mail_buffer: MutableMailBuffer
    last_external_sequence: int
    extension_reference: MutableTextBuffer
    extension_overlay_reference: MutableOverlay
    counters: dict[str, int]

    @classmethod
    def create(cls) -> Self:
        value = initial_value()
        text_buffer = MutableTextBuffer(
            buffer_id=value.text.buffer_id,
            text=value.text.text,
            overlays=[
                MutableOverlay(
                    overlay.overlay_id,
                    overlay.start,
                    overlay.end,
                    overlay.start_affinity,
                    overlay.end_affinity,
                )
                for overlay in value.text.overlays
            ],
            process_output="",
            invalidations=[],
        )
        return cls(
            name="controlled_mutable",
            text_buffer=text_buffer,
            mail_buffer=MutableMailBuffer(value.mail.buffer_id, {}, None),
            last_external_sequence=0,
            extension_reference=text_buffer,
            extension_overlay_reference=text_buffer.overlays[0],
            counters={"in_place_operations": 0, "snapshot_materializations": 0},
        )

    def begin_transaction(self) -> None:
        self.text_buffer.invalidations.clear()

    def apply(self, operation: Operation) -> None:
        match operation:
            case Insert(position, text):
                self._insert(position, text)
            case ProcessDelivery(sequence, text):
                validate_external(sequence, self.last_external_sequence)
                self._insert(len(self.text_buffer.text), text)
                self.text_buffer.process_output += text
                self.last_external_sequence = sequence
            case MailDelivery(sequence, message_id, subject):
                validate_external(sequence, self.last_external_sequence)
                if message_id in self.mail_buffer.messages:
                    self.mail_buffer.messages[message_id].subject = subject
                else:
                    self.mail_buffer.messages[message_id] = MutableMessage(
                        message_id, subject, True
                    )
                self.last_external_sequence = sequence
            case SelectMessage(message_id):
                if message_id not in self.mail_buffer.messages:
                    raise KeyError(message_id)
                self.mail_buffer.selected_id = message_id
            case MarkRead(message_id):
                try:
                    self.mail_buffer.messages[message_id].unread = False
                except KeyError:
                    raise KeyError(message_id) from None
        self.counters["in_place_operations"] += 1

    def _insert(self, position: int, text: str) -> None:
        if not 0 <= position <= len(self.text_buffer.text):
            raise IndexError(position)
        amount = len(text)
        self.text_buffer.text = (
            self.text_buffer.text[:position] + text + self.text_buffer.text[position:]
        )
        for overlay in self.text_buffer.overlays:
            overlay.start = shifted_boundary(
                overlay.start, overlay.start_affinity, position, amount
            )
            overlay.end = shifted_boundary(
                overlay.end, overlay.end_affinity, position, amount
            )
        self.text_buffer.invalidations.append((position, position + amount))

    def snapshot(self) -> StateValue:
        self.counters["snapshot_materializations"] += 1
        return StateValue(
            text=TextValue(
                buffer_id=self.text_buffer.buffer_id,
                text=self.text_buffer.text,
                overlays=tuple(
                    OverlayValue(
                        overlay.overlay_id,
                        overlay.start,
                        overlay.end,
                        overlay.start_affinity,
                        overlay.end_affinity,
                    )
                    for overlay in self.text_buffer.overlays
                ),
                process_output=self.text_buffer.process_output,
                invalidations=tuple(self.text_buffer.invalidations),
            ),
            mail=MailValue(
                buffer_id=self.mail_buffer.buffer_id,
                messages=tuple(
                    MessageValue(message.message_id, message.subject, message.unread)
                    for message in self.mail_buffer.messages.values()
                ),
                selected_id=self.mail_buffer.selected_id,
            ),
            last_external_sequence=self.last_external_sequence,
        )

    def _restore_fields(self, value: StateValue) -> None:
        self.text_buffer.text = value.text.text
        overlays_by_id = {
            overlay.overlay_id: overlay for overlay in self.text_buffer.overlays
        }
        restored_overlays = []
        for value_overlay in value.text.overlays:
            overlay = overlays_by_id.get(value_overlay.overlay_id)
            if overlay is None:
                overlay = MutableOverlay(
                    value_overlay.overlay_id,
                    value_overlay.start,
                    value_overlay.end,
                    value_overlay.start_affinity,
                    value_overlay.end_affinity,
                )
            else:
                overlay.start = value_overlay.start
                overlay.end = value_overlay.end
                overlay.start_affinity = value_overlay.start_affinity
                overlay.end_affinity = value_overlay.end_affinity
            restored_overlays.append(overlay)
        self.text_buffer.overlays[:] = restored_overlays
        self.text_buffer.process_output = value.text.process_output
        self.text_buffer.invalidations[:] = value.text.invalidations
        restored_messages: dict[str, MutableMessage] = {}
        for value_message in value.mail.messages:
            message = self.mail_buffer.messages.get(value_message.message_id)
            if message is None:
                message = MutableMessage(
                    value_message.message_id,
                    value_message.subject,
                    value_message.unread,
                )
            else:
                message.subject = value_message.subject
                message.unread = value_message.unread
            restored_messages[value_message.message_id] = message
        self.mail_buffer.messages.clear()
        self.mail_buffer.messages.update(restored_messages)
        self.mail_buffer.selected_id = value.mail.selected_id
        self.last_external_sequence = value.last_external_sequence

    def restore(self, value: StateValue) -> None:
        self._restore_fields(value)
        self.counters["in_place_operations"] += 1

    def extension_report(self) -> dict[str, bool]:
        current = self.extension_reference is self.text_buffer
        return {
            "direct_value_remains_current": current,
            "overlay_reference_or_handle_remains_current": (
                self.extension_overlay_reference is self.text_buffer.overlays[0]
            ),
            "stable_reference_remains_current": current,
        }

    def rollback_restored(self, before: StateValue) -> bool:
        return (
            self.snapshot() == before
            and self.extension_reference is self.text_buffer
            and self.extension_overlay_reference is self.text_buffer.overlays[0]
        )


@dataclass(slots=True)
class TextShell:
    buffer_id: int
    value: TextValue


@dataclass(slots=True)
class MailShell:
    buffer_id: int
    value: MailValue


@dataclass(slots=True)
class HybridAdapter:
    name: str
    text_shell: TextShell
    mail_shell: MailShell
    last_external_sequence: int
    extension_reference: TextShell
    extension_overlay_handle: OverlayHandle
    original_text_value: TextValue
    counters: dict[str, int]

    @classmethod
    def create(cls) -> Self:
        value = initial_value()
        text_shell = TextShell(value.text.buffer_id, value.text)
        return cls(
            name="hybrid",
            text_shell=text_shell,
            mail_shell=MailShell(value.mail.buffer_id, value.mail),
            last_external_sequence=0,
            extension_reference=text_shell,
            extension_overlay_handle=OverlayHandle(value.text.overlays[0].overlay_id),
            original_text_value=value.text,
            counters={"text_root_replacements": 0, "mail_root_replacements": 0},
        )

    def begin_transaction(self) -> None:
        if not self.text_shell.value.invalidations:
            return
        self.text_shell.value = replace(self.text_shell.value, invalidations=())
        self.counters["text_root_replacements"] += 1

    def apply(self, operation: Operation) -> None:
        before = self.snapshot()
        after = apply_to_value(before, operation)
        if after.text is not before.text:
            self.text_shell.value = after.text
            self.counters["text_root_replacements"] += 1
        if after.mail is not before.mail:
            self.mail_shell.value = after.mail
            self.counters["mail_root_replacements"] += 1
        self.last_external_sequence = after.last_external_sequence

    def snapshot(self) -> StateValue:
        return StateValue(
            text=self.text_shell.value,
            mail=self.mail_shell.value,
            last_external_sequence=self.last_external_sequence,
        )

    def restore(self, value: StateValue) -> None:
        self.text_shell.value = value.text
        self.mail_shell.value = value.mail
        self.last_external_sequence = value.last_external_sequence
        self.counters["text_root_replacements"] += 1
        self.counters["mail_root_replacements"] += 1

    def extension_report(self) -> dict[str, bool]:
        overlay_resolved = any(
            overlay.overlay_id == self.extension_overlay_handle.overlay_id
            for overlay in self.text_shell.value.overlays
        )
        return {
            "direct_value_remains_current": (
                self.original_text_value is self.text_shell.value
            ),
            "overlay_reference_or_handle_remains_current": overlay_resolved,
            "stable_reference_remains_current": (
                self.extension_reference is self.text_shell
            ),
        }

    def rollback_restored(self, before: StateValue) -> bool:
        return (
            self.text_shell.value is before.text
            and self.mail_shell.value is before.mail
            and self.last_external_sequence == before.last_external_sequence
        )


@dataclass(slots=True)
class Session:
    adapter: ArchitectureAdapter
    history: BranchingHistory
    events: list[EventRecord]

    @classmethod
    def create(cls, adapter: ArchitectureAdapter) -> Self:
        return cls(adapter, BranchingHistory.create(adapter.snapshot()), [])

    def dispatch(self, action: Action) -> Observation:
        if isinstance(action, Transaction):
            before = self.adapter.snapshot()
            self.adapter.begin_transaction()
            try:
                for operation in action.operations:
                    self.adapter.apply(operation)
            except (IndexError, KeyError, ValueError):
                self.adapter.restore(before)
                raise
            self.history.commit(self.adapter.snapshot(), action.label)
            label = action.label
            action_name = "transaction"
        elif isinstance(action, Undo):
            self.adapter.restore(self.history.undo())
            label = self.history.nodes[self.history.current_id].label
            action_name = "undo"
        else:
            self.adapter.restore(self.history.redo(action.branch))
            label = self.history.nodes[self.history.current_id].label
            action_name = "redo"

        observation = observe(self.adapter.snapshot())
        self.events.append(
            EventRecord(
                index=len(self.events),
                action=action_name,
                label=label,
                observation_digest=observation_digest(observation),
            )
        )
        return observation


ACTIONS: tuple[Action, ...] = (
    Transaction("grouped-edit", (Insert(2, "XX"), Insert(6, "Y"))),
    Transaction("process", (ProcessDelivery(1, "\nproc-a"),)),
    Transaction(
        "original-mail",
        (
            MailDelivery(2, "m1", "First"),
            MailDelivery(3, "m2", "Second"),
            SelectMessage("m2"),
            MarkRead("m2"),
            MailDelivery(4, "m2", "Second revised"),
        ),
    ),
    Undo(),
    Transaction(
        "alternative-mail",
        (
            MailDelivery(2, "m3", "Third"),
            MailDelivery(3, "m4", "Fourth"),
            MailDelivery(4, "m3", "Third revised"),
            SelectMessage("m4"),
        ),
    ),
    Undo(),
    Redo(0),
    Undo(),
    Redo(1),
)


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    name: str
    final_observation: Observation
    accepted_observations: tuple[Observation, ...]
    events: tuple[EventRecord, ...]
    counters: tuple[tuple[str, int], ...]
    extension_report: tuple[tuple[str, bool], ...]
    history_nodes: int
    process_branch_count: int
    rejected_delivery_is_atomic: bool
    rollback_restored_architecture_identity: bool
    invalid_redo_branches_rejected: bool
    replay_matches: bool
    elapsed_seconds: float
    current_traced_bytes: int
    peak_traced_bytes: int


def execute_actions(adapter: ArchitectureAdapter) -> tuple[Session, list[Observation]]:
    session = Session.create(adapter)
    observations = [session.dispatch(action) for action in ACTIONS]
    return session, observations


def run_scenario(
    factory: Callable[[], ArchitectureAdapter],
) -> ScenarioResult:
    tracemalloc.start()
    started = time.perf_counter()
    adapter = factory()
    session, observations = execute_actions(adapter)

    grouped = observations[0]
    assert grouped.text == "abXXcdYef"
    assert grouped.overlay_ranges == ((1, 4, 6),)
    assert grouped.invalidations == ((2, 4), (6, 7))

    process = observations[1]
    assert process.text == "abXXcdYef\nproc-a"
    assert process.invalidations == ((9, 16),)

    original_mail = observations[2]
    assert original_mail.mail_rows == (
        "* m1 First",
        "  m2 Second revised",
    )
    assert original_mail.selected_message_id == "m2"
    assert original_mail.unread_message_ids == ("m1",)

    alternative_mail = observations[4]
    assert alternative_mail.mail_rows == (
        "* m3 Third revised",
        "* m4 Fourth",
    )
    assert observations[6] == original_mail
    assert observations[8] == alternative_mail

    process_node_id = session.history.nodes[session.history.current_id].parent_id
    assert process_node_id is not None
    branch_count = len(session.history.children[process_node_id])
    assert branch_count == 2

    final_history_id = session.history.current_id
    session.history.current_id = process_node_id
    invalid_redo_branches_rejected = True
    for invalid_branch in (-1, branch_count):
        try:
            session.history.redo(invalid_branch)
        except IndexError:
            if session.history.current_id != process_node_id:
                invalid_redo_branches_rejected = False
        else:
            invalid_redo_branches_rejected = False
    session.history.current_id = final_history_id
    assert invalid_redo_branches_rejected

    before_rejection = observe(adapter.snapshot())
    state_before_rejection = adapter.snapshot()
    history_before_rejection = session.history.current_id
    events_before_rejection = tuple(session.events)
    try:
        session.dispatch(
            Transaction(
                "out-of-order",
                (Insert(0, "TEMP"), ProcessDelivery(4, "rejected")),
            )
        )
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-order external delivery was accepted")
    rejected_delivery_is_atomic = (
        observe(adapter.snapshot()) == before_rejection
        and session.history.current_id == history_before_rejection
        and tuple(session.events) == events_before_rejection
    )
    assert rejected_delivery_is_atomic
    rollback_restored_architecture_identity = adapter.rollback_restored(
        state_before_rejection
    )
    assert rollback_restored_architecture_identity

    replay_adapter = factory()
    replay_session, replay_observations = execute_actions(replay_adapter)
    replay_matches = (
        replay_observations == observations
        and replay_session.events == session.events
        and observe(replay_adapter.snapshot()) == before_rejection
    )
    assert replay_matches

    elapsed = time.perf_counter() - started
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    extension_report = adapter.extension_report()
    return ScenarioResult(
        name=adapter.name,
        final_observation=before_rejection,
        accepted_observations=tuple(observations),
        events=tuple(session.events),
        counters=tuple(sorted(adapter.counters.items())),
        extension_report=tuple(sorted(extension_report.items())),
        history_nodes=len(session.history.nodes),
        process_branch_count=branch_count,
        rejected_delivery_is_atomic=rejected_delivery_is_atomic,
        rollback_restored_architecture_identity=(
            rollback_restored_architecture_identity
        ),
        invalid_redo_branches_rejected=invalid_redo_branches_rejected,
        replay_matches=replay_matches,
        elapsed_seconds=elapsed,
        current_traced_bytes=current,
        peak_traced_bytes=peak,
    )


def main() -> None:
    results = (
        run_scenario(PersistentAdapter.create),
        run_scenario(ControlledMutableAdapter.create),
        run_scenario(HybridAdapter.create),
    )
    observations = {result.final_observation for result in results}
    accepted_observation_streams = {result.accepted_observations for result in results}
    event_streams = {result.events for result in results}
    assert len(observations) == 1
    assert len(accepted_observation_streams) == 1
    assert len(event_streams) == 1
    extension_reports = {
        result.name: dict(result.extension_report) for result in results
    }
    assert all(
        report["stable_reference_remains_current"]
        and report["overlay_reference_or_handle_remains_current"]
        for report in extension_reports.values()
    )
    assert extension_reports["controlled_mutable"]["direct_value_remains_current"]
    assert not extension_reports["fully_persistent"]["direct_value_remains_current"]
    assert not extension_reports["hybrid"]["direct_value_remains_current"]

    report = {
        "semantic_agreement_across_models": True,
        "event_agreement_across_models": True,
        "scenario": {
            "accepted_actions": len(ACTIONS),
            "history_nodes": results[0].history_nodes,
            "branches_after_process_node": results[0].process_branch_count,
            "overlay_after_grouped_edit": [4, 6],
            "out_of_order_external_delivery_rejected_atomically": True,
            "rollback_restored_architecture_identity": True,
            "negative_and_out_of_range_redo_rejected": True,
            "mail_refresh_preserved_stable_message_id": True,
            "cross_model_and_replay_match_every_observation_and_event": True,
        },
        "models": {
            result.name: {
                "counters": dict(result.counters),
                "extension_reference": dict(result.extension_report),
                "elapsed_seconds": round(result.elapsed_seconds, 6),
                "current_traced_bytes": result.current_traced_bytes,
                "peak_traced_bytes": result.peak_traced_bytes,
            }
            for result in results
        },
        "final_observation": asdict(results[0].final_observation),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
