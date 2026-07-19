import sys

import pytest

from drei import identity
from drei.cli import main


def test_identity_expands_the_name() -> None:
    assert identity() == "Drei Resembles Emacs Intentionally"


def test_version_command_reports_package_identity(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["drei", "--version"])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == "drei 0.1.0 — Drei Resembles Emacs Intentionally\n"
    assert captured.err == ""
