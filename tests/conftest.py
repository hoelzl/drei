"""Shared test support for the Drei test suite."""

from __future__ import annotations

from drei.files import FilePort


class FakeFilePort(FilePort):
    """Deterministic in-memory file port for tests.

    ``fail`` selects the raised error type: ``"permission"`` raises
    ``PermissionError``, anything else raises plain ``OSError``.
    """

    def __init__(self, files: dict[str, str] | None = None, fail: str | None = None):
        self.files = dict(files or {})
        self.fail = fail

    def read(self, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write(self, path: str, text: str) -> None:
        if self.fail is not None:
            if self.fail == "permission":
                raise PermissionError(path)
            raise OSError(self.fail)
        self.files[path] = text
