"""Raw-terminal adapter over the production editor session.

All platform-specific I/O lives behind :class:`TerminalPort`. The editor loop
itself is platform-independent: it reads symbolic keys, dispatches them
through the production session via the harness, and writes rendered frames.
"""

from __future__ import annotations

import abc
import os
import sys
from dataclasses import dataclass
from typing import Literal

from drei.commands import KeyboardQuitEvent
from drei.files import FilePort
from drei.harness import EditorHarness

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


def run_editor(
    port: TerminalPort,
    *,
    file_port: FilePort | None = None,
    file_path: str | None = None,
    initial_text: str = "",
) -> None:
    """Run the editor loop over an explicit terminal port."""
    port.write("DREI:READY\n")
    port.flush()
    port.enter_raw()
    try:
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
            assembler, keys = assembler.feed(port.read_key())
            # No keys: the character was consumed mid-sequence, so the
            # subject is mid-chord and not quiescent — no marker until the
            # sequence resolves. Two keys: an abandoned escape prefix plus
            # the character that broke it, each marking quiescence of its
            # own (symmetric with the C-x prefix path).
            for key in keys:
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
