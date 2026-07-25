"""The parity registry's test citations must name tests that exist.

Review 0001 finding 16: nothing linked registry rows to tests in either
direction, so a row could keep naming a test that had been renamed or
deleted and CI would never notice — the registry is the governance record
for every intentional deviation, and a citation nobody checks is not
evidence. This closes the mechanically checkable direction: every
``test_*``/``Test*`` identifier the registry cites is defined under
``tests/``.

The other direction — a deviation shipped with no row at all — is not
mechanically checkable and stays a review responsibility (see
`docs/knowledge/verification-model.md`).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "docs" / "knowledge" / "emacs-parity.md"
TESTS = REPO_ROOT / "tests"

# Backticked identifiers only: prose mentions of a name outside code spans
# are not citations. A trailing "*" cites a family (`test_invented_allow_kind_*`).
_CITATION_RE = re.compile(r"`((?:test_|Test)[A-Za-z0-9_]*\*?)`")


def _cited_names() -> set[str]:
    return set(_CITATION_RE.findall(REGISTRY.read_text(encoding="utf-8")))


def _defined_names() -> set[str]:
    defined: set[str] = set()
    pattern = re.compile(r"^\s*(?:async def|def|class)\s+((?:test_|Test)[A-Za-z0-9_]*)")
    for path in TESTS.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = pattern.match(line)
            if match:
                defined.add(match.group(1))
    return defined


def _is_defined(citation: str, defined: set[str]) -> bool:
    if citation.endswith("*"):
        prefix = citation[:-1]
        return any(name.startswith(prefix) for name in defined)
    return citation in defined


def test_every_registry_test_citation_exists() -> None:
    defined = _defined_names()
    citations = _cited_names()
    assert citations, "the registry cites no tests — the parser is broken"

    missing = sorted(c for c in citations if not _is_defined(c, defined))
    assert not missing, (
        "docs/knowledge/emacs-parity.md cites tests that do not exist: "
        f"{missing}. Update the row (or restore the test) — a deviation "
        "pinned by a vanished test is unpinned."
    )
