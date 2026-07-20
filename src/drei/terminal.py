"""Raw-terminal adapter over the production editor session.

All platform-specific I/O lives behind :class:`TerminalPort`. The editor loop
itself is platform-independent: it reads symbolic keys, dispatches them
through the production session via the harness, and writes rendered frames.
"""

from __future__ import annotations

import abc
import os
import sys

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
        """Read one symbolic key. Control bytes map to ``C-x`` names."""

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
        "\x06": "C-f",
        "\x02": "C-b",
        "\x07": "C-g",
        "\x18": "C-x",
        "\x13": "C-s",
        "\x0b": "C-k",
        "\x19": "C-y",
    }
    return control.get(char, char)


def assemble_meta(pending_esc: bool, char: str) -> tuple[bool, str | None]:
    """Byte-level ESC lookbehind for meta chords (ESC + letter → "M-<letter>").

    Returns ``(new_pending_esc, key_or_none)``: ``None`` means the byte was
    consumed as the start of a potential chord (bare ESC so far); a key
    string means dispatch it. ESC + non-letter yields the unresolved ESC
    marker and reprocesses the byte on the next call with no pending state.
    """
    if pending_esc:
        if char.isalpha():
            return False, f"M-{char}"
        # ESC + non-letter: report the bare ESC; the caller reprocesses char.
        return False, "\x1b"
    if char == "\x1b":
        return True, None
    return False, decode_key(char)


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
        pending_esc = False
        pending_byte: str | None = None
        while True:
            char = pending_byte if pending_byte is not None else port.read_key()
            pending_byte = None
            pending_esc, key = assemble_meta(pending_esc, char)
            if key == "\x1b":
                # ESC + non-letter: the bare ESC is unresolved (no state
                # change) — mark quiescence for it, then reprocess the byte
                # that followed with no pending state (its own iteration
                # marks quiescence again).
                harness.send(key)
                port.write(READINESS_MARKER)
                port.flush()
                pending_byte = char
                continue
            if key is None:
                # Bare ESC consumed as a potential chord start: the subject
                # is mid-chord, not quiescent — no marker until the next
                # byte resolves the chord.
                continue
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
            # On quit the run ends: quiescence is the process exit itself, so
            # the final frame carries no readiness marker.
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

        def _read_key_windows(self) -> str:  # pragma: no cover
            import msvcrt

            return msvcrt.getwch()

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
