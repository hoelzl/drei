"""Raw-terminal adapter over the production editor session.

All platform-specific I/O lives behind :class:`TerminalPort`. The editor loop
itself is platform-independent: it consumes one ordered stream of
:class:`~drei.input.InputEvent` from an injected
:class:`~drei.input.InputSource`, dispatches the resulting commands through
the production session via the harness, and writes rendered frames.

The sources defined here are the terminal-backed ones. They are adapters:
they may block, own threads, and consult a clock, none of which the loop or
the session may do.
"""

from __future__ import annotations

import abc
import os
import queue
import sys
import threading
from dataclasses import dataclass
from typing import Literal

from drei.commands import KeyboardQuitEvent
from drei.files import FilePort
from drei.harness import EditorHarness
from drei.input import InputEvent, InputSource, Key, Resize

_CLEAR_SCREEN = "\x1b[2J\x1b[H"
_CURSOR_HOME = "\x1b[H"


class TerminalPort(abc.ABC):
    """Narrow effect port for native terminal I/O."""

    @abc.abstractmethod
    def enter_raw(self) -> None:
        """Put the terminal into raw mode and save prior settings."""

    @abc.abstractmethod
    def read_key(self) -> str:
        """Read one input unit for :class:`KeyAssembler`.

        Either a raw character (control bytes and escape sequences are
        decoded by the assembler) or, for a platform key event with no byte
        form, its symbolic name (Windows extended keys → ``<up>``, …).
        """

    @abc.abstractmethod
    def write(self, text: str) -> None:
        """Write text to the terminal."""

    @abc.abstractmethod
    def flush(self) -> None:
        """Flush pending output."""

    @abc.abstractmethod
    def get_size(self) -> tuple[int, int]:
        """Return (width, height) in character cells."""

    @abc.abstractmethod
    def restore(self) -> None:
        """Restore terminal settings saved by :meth:`enter_raw`."""


def decode_key(char: str) -> str:
    """Convert a raw input character to a symbolic key name."""
    control = {
        "\x00": "C-@",
        "\x06": "C-f",
        "\x02": "C-b",
        "\x07": "C-g",
        "\x18": "C-x",
        "\x13": "C-s",
        "\x0b": "C-k",
        "\x19": "C-y",
        "\x17": "C-w",
        "\x1f": "C-/",  # C-_ produces the same byte
        "\x0d": "RET",  # Enter; unresolved in the main map (minibuffer-only)
        "\x7f": "DEL",  # backspace; unresolved in the main map
    }
    return control.get(char, char)


_ARROW_FINALS = {"A": "<up>", "B": "<down>", "C": "<right>", "D": "<left>"}

# msvcrt scan codes for the navigation keys, mapped to the same symbolic
# names the POSIX escape-sequence path produces.
_WINDOWS_EXTENDED = {"H": "<up>", "P": "<down>", "M": "<right>", "K": "<left>"}


@dataclass(frozen=True, slots=True)
class KeyAssembler:
    """Pure incremental assembler from input characters to symbolic keys.

    Recognizes three escape shapes: ESC + letter → ``M-<letter>``; a CSI
    sequence (``ESC [``) and an SS3 sequence (``ESC O``) collected through
    their final byte (0x40-0x7E) → one symbolic key. Navigation keys
    therefore arrive as a single unresolved key instead of their raw bytes
    (``ESC [ A`` used to insert "[" and "A" into the buffer).

    ``feed`` returns the next state plus the keys the character completed:
    zero while a sequence is still being assembled, one for the common
    case, two when the character proves the sequence was not one after all
    (the abandoned prefix, then the character resolved from scratch).
    """

    state: Literal["", "esc", "csi", "ss3"] = ""
    params: str = ""

    def feed(self, char: str) -> tuple[KeyAssembler, tuple[str, ...]]:
        if self.state == "":
            if char == "\x1b":
                return KeyAssembler("esc"), ()
            return self, (decode_key(char),)
        if self.state == "esc":
            if char == "[":
                return KeyAssembler("csi"), ()
            if char == "O":
                # Application-cursor mode prefix; costs the M-O chord, which
                # a terminal cannot distinguish from it either (see the
                # parity registry).
                return KeyAssembler("ss3"), ()
            if len(char) == 1 and char.isalpha():
                return KeyAssembler(), (f"M-{char}",)
            # ESC + anything else: the bare ESC is its own (unresolved) key
            # and the character starts over from the empty state.
            return _restart(char, "\x1b")
        if len(char) == 1 and "\x20" <= char <= "\x3f":
            # Parameter and intermediate bytes: keep collecting.
            return KeyAssembler(self.state, self.params + char), ()
        if len(char) == 1 and "\x40" <= char <= "\x7e":
            if not self.params and char in _ARROW_FINALS:
                return KeyAssembler(), (_ARROW_FINALS[char],)
            return KeyAssembler(), (f"<{self.state}:{self.params}{char}>",)
        # Not a legal sequence byte: abandon the partial sequence as one
        # unresolved key rather than replaying its bytes into the buffer.
        return _restart(char, f"<{self.state}:unterminated>")


def _restart(char: str, *emitted: str) -> tuple[KeyAssembler, tuple[str, ...]]:
    """Emit ``emitted``, then reprocess ``char`` from the empty state."""
    assembler, keys = KeyAssembler().feed(char)
    return assembler, (*emitted, *keys)


# TermVerify subject-cooperation readiness marker (OSC 7791;ready ST). The
# subject emits it after startup and after processing each input so the
# verifier can detect quiescence without sleeps. A compliant screen model
# does not render the unknown OSC sequence, so it is invisible in frames.
READINESS_MARKER = "\x1b]7791;ready\x1b\\"


class SynchronousTerminalSource(InputSource):
    """The events of one terminal, produced on demand by blocking in the port.

    **Test-only.** Production uses :class:`ThreadedTerminalSource`; this has
    no caller in `src/`. It exists because `next_event` is exactly the
    `read_key()` the loop used to call inline before the seam, which makes it
    the regression gate the terminal suite injects: those tests pin the loop,
    not the threads, and stay as deterministic as they were.

    It produces `Key` and nothing else — a terminal size is not observable
    without either a signal handler or a watcher, and this source has neither,
    so a loop driven by it never sees a resize.
    """

    def __init__(self, port: TerminalPort) -> None:
        self._port = port

    def next_event(self) -> InputEvent:
        return Key(self._port.read_key())

    def close(self) -> None:
        """Nothing to release: the source owns no thread and no queue."""


@dataclass(frozen=True, slots=True)
class _ReaderFailed:
    """Queued by a reader thread that is about to die, carrying why.

    A thread cannot raise into the loop, and a dead reader must not look like
    a quiet one: without this the loop would block in `next_event` forever
    holding the terminal in raw mode, and `C-g` could not reach it because
    the thread that would have delivered it is the one that died.
    """

    error: BaseException


class ThreadedTerminalSource(InputSource):
    """The production source: two reader threads feeding one queue.

    Design 0005 D2 — there is no portable `select` over a Windows console
    handle, so each input is read by a thread that is allowed to block, and
    the threads meet on a `queue.Queue`. **The threads live entirely here.**
    The loop pops one event at a time and the session sees only commands, so
    the nondeterministic interleaving is serialized before it can reach
    anything that has to be deterministic — and it is *recorded*, because
    every event that changes state becomes a command in the transcript.

    The size watcher polls instead of using SIGWINCH / the Windows console's
    `WINDOW_BUFFER_SIZE_EVENT`: those share no shape across the two
    platforms, and this is not the place to grow two platform paths. Polling
    is a clock dependency in an *adapter*, which is where the rules allow one
    — no editor semantics depend on the interval, only how soon a resize is
    noticed (parity registry: presentation-only lag).

    Startup race, owned and small: the watcher seeds its baseline with its own
    `get_size()` call, while `run_editor` reads the size separately to build
    the harness. A resize landing between the two is not reported, and the
    frame keeps the size the loop read until the *next* resize. Closing it
    would mean threading the initial size in and adding a construction-order
    branch for a window of microseconds at startup.
    """

    def __init__(self, port: TerminalPort, *, poll_interval: float = 0.2) -> None:
        self._port = port
        self._events: queue.Queue[InputEvent | _ReaderFailed] = queue.Queue()
        self._stopped = threading.Event()
        self._poll_interval = poll_interval
        self._keys = threading.Thread(
            target=self._read_keys, name="drei-input-keys", daemon=True
        )
        self._sizes = threading.Thread(
            target=self._watch_size, name="drei-input-size", daemon=True
        )
        self._keys.start()
        self._sizes.start()

    def next_event(self) -> InputEvent:
        event = self._events.get()
        if isinstance(event, _ReaderFailed):
            # Re-raised on the loop's thread, which restores the terminal on
            # the way out — the same end state a synchronous `read_key()`
            # failure produced before the threads existed.
            raise event.error
        return event

    def close(self) -> None:
        """Stop the watcher and let the key reader go.

        Only the watcher can be joined. The key reader is parked inside a
        blocking `read_key()` that nothing portable can interrupt, so it is a
        daemon: it notices the stop flag only if one more key ever arrives,
        and otherwise dies with the process. Joining it would hang until the
        user pressed a key.
        """
        self._stopped.set()
        self._sizes.join(timeout=self._poll_interval * 10)

    def _read_keys(self) -> None:
        try:
            while not self._stopped.is_set():
                char = self._port.read_key()
                if not char:
                    # POSIX `read(1)` returns "" at end of stream and keeps
                    # returning it. Left alone this is not a quiet idle but an
                    # unbounded hot loop filling the queue — measured at ~10^6
                    # events per second. End of input is the end of the run.
                    raise EOFError("terminal input stream reached end of file")
                self._events.put(Key(char))
        except Exception as error:
            self._events.put(_ReaderFailed(error))

    def _watch_size(self) -> None:
        try:
            last = self._port.get_size()
            # `wait` returns True only when the flag is set, so this both
            # paces the poll and exits promptly on close — no sleep-then-check
            # race.
            while not self._stopped.wait(self._poll_interval):
                size = self._port.get_size()
                if size != last:
                    last = size
                    self._events.put(Resize(*size))
        except Exception as error:
            # Surfaced rather than swallowed: a dead watcher silently loses
            # every resize for the rest of the run, which looks exactly like
            # a terminal nobody resized.
            self._events.put(_ReaderFailed(error))


def run_editor(
    port: TerminalPort,
    *,
    source: InputSource | None = None,
    file_port: FilePort | None = None,
    file_path: str | None = None,
    initial_text: str = "",
) -> None:
    """Run the editor loop over an explicit terminal port.

    Input arrives as one totally ordered stream of :class:`InputEvent` from
    ``source`` (design 0005 D2). ``port`` remains the output side and the
    source of the initial frame size; when ``source`` is omitted the loop
    builds the production :class:`ThreadedTerminalSource` over the same port,
    so the shipped editor notices a resize within one poll interval rather
    than not at all. Tests inject
    either a scripted source or :class:`SynchronousTerminalSource`.
    """
    port.write("DREI:READY\n")
    port.flush()
    port.enter_raw()
    # Built inside the try: constructing the default source starts threads,
    # and a failure there must still restore the terminal rather than leave
    # it raw.
    events: InputSource | None = None
    try:
        events = ThreadedTerminalSource(port) if source is None else source
        # The size is read once here to build the first frame; from then on
        # the source reports every change as a Resize event (plan 0015 V4).
        width, height = port.get_size()
        harness = EditorHarness(
            width=width,
            height=height,
            file_port=file_port,
            file_path=file_path,
            initial_text=initial_text,
        )
        _write_frame(port, harness)
        assembler = KeyAssembler()
        while True:
            event = events.next_event()
            if isinstance(event, Resize):
                harness.resize(event.width, event.height)
                # Marked, like any other consumed input. A resize is an input
                # epoch under the cooperation protocol itself: TermVerify
                # dispatches it on the same ordered input stream as a key and
                # reads until exactly one marker. Leaving it unmarked would
                # not make the epoch quiet — it would make this epoch consume
                # the *next* input's marker and shift every epoch after it.
                #
                # The marker follows an *observed size change*, not a
                # dispatched resize: the watcher emits nothing when the polled
                # size is unchanged, so resizing a terminal to the dimensions
                # it already has produces no event and no marker here. A
                # TermVerify scenario must therefore change the geometry, or
                # its epoch waits for a marker that is never coming. See the
                # parity registry row and design 0005's evidence note.
                _write_frame(port, harness)
                continue
            assembler, resolved = assembler.feed(event.char)
            # No keys: the character was consumed mid-sequence, so the
            # subject is mid-chord and not quiescent — no marker until the
            # sequence resolves. Two keys: an abandoned escape prefix plus
            # the character that broke it, each marking quiescence of its
            # own (symmetric with the C-x prefix path).
            for key in resolved:
                outcome = harness.send(key)
                quit_requested = outcome is not None and any(
                    isinstance(e, KeyboardQuitEvent) for e in outcome.events
                )
                if outcome is None:
                    # Unresolved key: state did not change, so skip the frame
                    # rewrite but still mark quiescence for this input.
                    port.write(READINESS_MARKER)
                    port.flush()
                    continue
                # On quit the run ends: quiescence is the process exit itself,
                # so the final frame carries no readiness marker.
                _write_frame(port, harness, mark_ready=not quit_requested)
                if quit_requested:
                    return
    finally:
        if events is not None:
            events.close()
        port.restore()


def _write_frame(
    port: TerminalPort, harness: EditorHarness, *, mark_ready: bool = True
) -> None:
    frame = harness.frame
    port.write(_CLEAR_SCREEN)
    port.write("\r\n".join(frame.rows))
    row, col = frame.cursor
    port.write(f"\x1b[{row + 1};{col + 1}H")
    if mark_ready:
        port.write(READINESS_MARKER)
    port.flush()


class SystemTerminalPort(TerminalPort):
    """Production terminal port using stdin/stdout."""

    def __init__(self) -> None:
        self._saved: object = None

    def enter_raw(self) -> None:  # pragma: no cover - platform raw-mode shim
        if sys.platform == "win32":
            self._enter_raw_windows()
        else:
            self._enter_raw_posix()

    if sys.platform != "win32":

        def _enter_raw_posix(self) -> None:  # pragma: no cover
            import termios
            import tty

            fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(fd)
            tty.setraw(fd)

    if sys.platform == "win32":

        def _enter_raw_windows(self) -> None:  # pragma: no cover
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
            mode = ctypes.c_uint32()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            self._saved = mode.value
            # Clear ENABLE_ECHO_INPUT | ENABLE_LINE_INPUT | ENABLE_PROCESSED_INPUT
            kernel32.SetConsoleMode(handle, mode.value & ~0x0007)

    def read_key(self) -> str:  # pragma: no cover - platform input shim
        if sys.platform == "win32":
            return self._read_key_windows()
        else:
            return self._read_key_posix()

    if sys.platform != "win32":

        def _read_key_posix(self) -> str:  # pragma: no cover
            return sys.stdin.read(1)

    if sys.platform == "win32":

        def _read_key_windows(self) -> str:  # pragma: no cover - platform shim
            import msvcrt

            char = msvcrt.getwch()
            if char in ("\x00", "\xe0"):
                # Extended key prefix (arrows, function keys, ...): the scan
                # code follows in the next read. The pair becomes ONE
                # symbolic key — the same names the POSIX escape-sequence
                # path produces — never the raw NUL, which decode_key would
                # turn into C-@ → SetMark. No extended key is bound yet, so
                # the key is unresolved in the keymap by design.
                # C-@ (NUL) stays undeliverable through msvcrt on the
                # Windows console (recorded in the parity registry).
                scan = msvcrt.getwch()
                return _WINDOWS_EXTENDED.get(scan, f"<ext:{scan}>")
            return char

    def write(self, text: str) -> None:
        sys.stdout.write(text)

    def flush(self) -> None:
        sys.stdout.flush()

    def get_size(self) -> tuple[int, int]:
        size = os.get_terminal_size()
        return (size.columns, size.lines)

    def restore(self) -> None:
        if self._saved is None:
            return
        if sys.platform == "win32":
            self._restore_windows()  # pragma: no cover - platform shim
        else:
            self._restore_posix()  # pragma: no cover - platform shim
        self._saved = None

    if sys.platform != "win32":

        def _restore_posix(self) -> None:  # pragma: no cover
            import termios

            saved = self._saved
            assert isinstance(saved, list)
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)

    if sys.platform == "win32":

        def _restore_windows(self) -> None:  # pragma: no cover
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-10)
            kernel32.SetConsoleMode(handle, self._saved)
