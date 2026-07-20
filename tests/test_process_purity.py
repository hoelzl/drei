"""Architectural guard: process/subprocess access stays behind the port.

The deterministic command path must never import ``subprocess`` or ``os`` to
launch a child. Only ``drei.process`` (the effect port module) may import
``subprocess``; no ``drei`` module needs ``os`` for process access. This pins
design 0001's explicit-effect-ports rule for the new port.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "drei"

# Modules whose import means "launch or signal a child process". ``os`` is
# deliberately excluded: the terminal port uses ``os.get_terminal_size`` for
# sizing, which is not process access. The boundary this guard pins is that
# only ``drei.process`` spawns/signals children.
_PROCESS_MODULES = {"subprocess", "pty", "signal"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def test_only_process_module_imports_subprocess() -> None:
    offenders: dict[str, set[str]] = {}
    for path in sorted(_SRC.glob("*.py")):
        if path.name == "process.py":
            continue  # the effect port is the one allowed importer
        bad = _imported_modules(path) & _PROCESS_MODULES
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"core modules importing process APIs: {offenders}"


def test_process_module_is_the_port_boundary() -> None:
    # Sanity: the port module does import subprocess (it's the whole point),
    # so the guard above can't be trivially satisfied by an empty allowlist.
    assert "subprocess" in _imported_modules(_SRC / "process.py")
