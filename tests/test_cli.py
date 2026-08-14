from __future__ import annotations

import sys

import pytest

import drei.cli as cli_module
import drei.terminal as terminal_module
from drei.files import VisitRejected


class MissingFilePort:
    def read(self, path: str) -> str:
        raise FileNotFoundError(path)

    def write(self, path: str, text: str) -> None:
        raise AssertionError("not used")


def test_startup_rejection_exits_two_with_exact_stderr(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli_module, "SystemFilePort", MissingFilePort)
    monkeypatch.setattr(
        terminal_module,
        "run_editor",
        lambda *args, **kwargs: VisitRejected("notes/", "empty-basename"),
    )

    with pytest.raises(SystemExit) as exit_info:
        cli_module.main(["notes/"])

    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "drei: notes/: empty-basename\n"
