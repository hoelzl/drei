"""Shared test support for the Drei test suite."""

from __future__ import annotations

from drei.files import FilePort
from drei.process import ProcessResult, ProcessTimedOut


class FakeFilePort(FilePort):
    """Deterministic in-memory file port for tests.

    ``fail`` selects the raised error type on write: ``"permission"`` raises
    ``PermissionError``, anything else raises plain ``OSError``. ``fail_read``
    does the same for read (missing keys still raise ``FileNotFoundError``).
    """

    def __init__(
        self,
        files: dict[str, str] | None = None,
        fail: str | None = None,
        fail_read: str | None = None,
    ):
        self.files = dict(files or {})
        self.fail = fail
        self.fail_read = fail_read

    def read(self, path: str) -> str:
        if self.fail_read is not None:
            if self.fail_read == "permission":
                raise PermissionError(path)
            if self.fail_read == "binary":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            raise OSError(self.fail_read)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write(self, path: str, text: str) -> None:
        if self.fail is not None:
            if self.fail == "permission":
                raise PermissionError(path)
            raise OSError(self.fail)
        self.files[path] = text


class FakeProcessPort:
    """Deterministic in-memory process port for tests.

    ``default`` is the result returned for every call unless ``fail`` is set:
    ``"not-found"`` raises ``FileNotFoundError``, ``"permission"`` raises
    ``PermissionError``, anything else raises plain ``OSError``. ``calls``
    records ``(argv, input_text, timeout)`` for each invocation.
    """

    def __init__(
        self,
        default: ProcessResult | None = None,
        fail: str | None = None,
    ) -> None:
        self.default = default or ProcessResult(
            argv=(), exit_code=0, stdout="", stderr=""
        )
        self.fail = fail
        self.calls: list[tuple[tuple[str, ...], str | None, float | None]] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        self.calls.append((argv, input_text, timeout))
        if self.fail is not None:
            if self.fail == "not-found":
                raise FileNotFoundError(argv[0])
            if self.fail == "permission":
                raise PermissionError(argv[0])
            if self.fail == "timeout":
                raise ProcessTimedOut(argv, timeout if timeout is not None else 0.0)
            raise OSError(self.fail)
        return ProcessResult(
            argv=argv,
            exit_code=self.default.exit_code,
            stdout=self.default.stdout,
            stderr=self.default.stderr,
        )
