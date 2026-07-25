"""Architectural guard: process/subprocess access stays behind the port.

The deterministic command path must never launch or signal a child. Only the
two process *ports* — ``drei.process`` (run-to-completion) and
``drei.streaming`` (the long-lived ACP child, design 0005 D1) — may import
``subprocess``; and no core module may launch via ``os`` either — ``import
os`` is allowed only for non-process uses (``os.get_terminal_size``), so a
separate attribute check flags ``os.<launch>`` calls. This pins design
0001/0003's effect-port rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "drei"

# Modules whose import means "launch or signal a child process". ``os`` is
# excluded from the *import* check because the terminal port legitimately
# uses ``os.get_terminal_size`` — but ``os`` can also launch children, so a
# separate attribute check (below) flags os.<launch> usage. The boundary
# this guard pins is that only ``drei.process`` spawns/signals children.
_PROCESS_MODULES = {"subprocess", "pty", "signal"}

# ``os`` attributes that launch or signal a child process. Any ``os.<attr>``
# access matching one of these (exactly, or an exec*/spawn*/popen* prefix)
# is a boundary violation, even though ``import os`` alone is allowed.
_OS_LAUNCH_EXACT = {"system", "fork", "kill", "killpg", "waitpid", "popen"}
_OS_LAUNCH_PREFIXES = ("exec", "spawn", "popen")


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def _os_launch_calls(source: str) -> set[str]:
    """``os.<attr>`` accesses whose attribute launches/signals a child."""
    tree = ast.parse(source)
    hits: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            attr = node.attr
            if attr in _OS_LAUNCH_EXACT or attr.startswith(_OS_LAUNCH_PREFIXES):
                hits.add(f"os.{attr}")
    return hits


# The port modules, which exist precisely to hold this import. Two, not one,
# because design 0005 D1 keeps the blocking run-to-completion port exactly as
# it is and adds the streaming one beside it rather than widening it: the
# boundary this guard pins is "only a port spawns a child", not "only one
# file does".
_PORT_MODULES = {"process.py", "streaming.py"}


def _core_sources() -> list[tuple[str, str]]:
    """(filename, source) for every core module except the process ports.

    Recursive (``**/*.py``) so subpackages like ``drei.acp`` are covered.
    """
    return [
        (str(path.relative_to(_SRC)), path.read_text(encoding="utf-8"))
        for path in sorted(_SRC.glob("**/*.py"))
        if path.name not in _PORT_MODULES
    ]


def test_only_process_module_imports_subprocess() -> None:
    offenders = {
        name: _imported_modules(src) & _PROCESS_MODULES for name, src in _core_sources()
    }
    offenders = {name: bad for name, bad in offenders.items() if bad}
    assert not offenders, f"core modules importing process APIs: {offenders}"


def test_no_core_module_launches_via_os() -> None:
    offenders = {name: _os_launch_calls(src) for name, src in _core_sources()}
    offenders = {name: hits for name, hits in offenders.items() if hits}
    assert not offenders, f"core modules launching via os: {offenders}"


def test_os_launch_detector_catches_each_launch_family() -> None:
    # The attribute check is only meaningful if it actually fires on every
    # launch family, and stays silent for a legitimate non-process os call.
    hits = _os_launch_calls(
        "import os\n"
        'os.system("x")\n'
        "os.execv('x', [])\n"
        "os.spawnlp('x')\n"
        "os.popen('x')\n"
        "os.fork()\n"
        "os.kill(1, 2)\n"
        "os.get_terminal_size()\n"
    )
    assert {
        "os.system",
        "os.execv",
        "os.spawnlp",
        "os.popen",
        "os.fork",
        "os.kill",
    } <= hits
    assert "os.get_terminal_size" not in hits


def test_the_port_modules_are_the_boundary() -> None:
    # Sanity: the port modules do import subprocess (it is the whole point),
    # so the import guard cannot be trivially satisfied by an allowlist that
    # names files which never needed the exemption.
    for name in sorted(_PORT_MODULES):
        assert "subprocess" in _imported_modules(
            (_SRC / name).read_text(encoding="utf-8")
        ), name
