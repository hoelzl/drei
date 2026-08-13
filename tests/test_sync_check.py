"""Coordination-gate contract for ``scripts/sync-check.sh``.

Review 0001 finding 17: with ``gh`` missing or unauthenticated the script
printed "(gh not available — skipped)" and exited 0, so the mandatory
pre-claim sync step passed with **zero** claim visibility — an agent could
read a clean run as "no one has claimed this slice". The script must instead
fail loudly, and going ahead without claim visibility must be a deliberate,
explicit act (the offline override).

The scenarios run the real script against a throwaway git repository whose
``origin`` is a local bare repo, with a fake ``gh`` first on ``PATH``: no
network, no GitHub account, no dependence on the developer's own auth state.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_CHECK = REPO_ROOT / "scripts" / "sync-check.sh"

pytestmark = pytest.mark.integration

bash = shutil.which("bash")
requires_bash = pytest.mark.skipif(bash is None, reason="bash is not available")


def _foreign_repo_env() -> dict[str, str]:
    """Environment for Git commands that must ignore the caller repository.

    Git exports repository-local variables to hooks. Those variables override
    ``cwd`` for nested Git commands, so a test creating a foreign repository
    must clear the complete list Git declares rather than guessing a subset.
    """
    local_names = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    env = dict(os.environ)
    for name in local_names:
        env.pop(name, None)
    return env


def _write_gh_stub(directory: Path, *, authenticated: bool) -> None:
    """A ``gh`` that answers ``auth status`` as asked and lists nothing else.

    Extensionless and first on ``PATH``, so the script's ``command -v gh``
    resolves this one rather than a real ``gh`` the developer may have.
    """
    auth_exit = 0 if authenticated else 1
    stub = directory / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "$1" = "auth" ]; then exit {auth_exit}; fi\n'
        "exit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    stub.chmod(0o755)


def _make_repo(tmp_path: Path) -> Path:
    """A git repo with a local ``origin`` — ``git ls-remote`` stays offline."""
    env = _foreign_repo_env()
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--quiet", str(origin)],
        check=True,
        cwd=tmp_path,
        env=env,
    )
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "--quiet"], check=True, cwd=work, env=env)
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin)],
        check=True,
        cwd=work,
        env=env,
    )
    (work / "docs" / "agent" / "plans").mkdir(parents=True)
    return work


def test_make_repo_ignores_the_callers_git_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-push hook exports repository-local Git variables.

    ``cwd`` does not override ``GIT_DIR``: without explicit isolation, the
    fixture's ``git init`` and ``git remote add`` mutate the repository whose
    push invoked pytest instead of the throwaway repository.
    """
    caller = tmp_path / "caller"
    caller.mkdir()
    clean_env = _foreign_repo_env()
    subprocess.run(["git", "init", "--quiet"], check=True, cwd=caller, env=clean_env)
    subprocess.run(
        ["git", "remote", "add", "origin", "caller.invalid:repo.git"],
        check=True,
        cwd=caller,
        env=clean_env,
    )

    monkeypatch.setenv("GIT_DIR", str(caller / ".git"))
    subject = tmp_path / "subject"
    subject.mkdir()
    work = _make_repo(subject)

    subject_origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        check=True,
        cwd=work,
        env=clean_env,
        capture_output=True,
        text=True,
    ).stdout.strip()
    caller_origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        check=True,
        cwd=caller,
        env=clean_env,
        capture_output=True,
        text=True,
    ).stdout.strip()
    caller_bare = subprocess.run(
        ["git", "config", "--bool", "core.bare"],
        check=True,
        cwd=caller,
        env=clean_env,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert Path(subject_origin) == subject / "origin.git"
    assert caller_origin == "caller.invalid:repo.git"
    assert caller_bare == "false"


def _write_plan(work: Path, name: str, status_line: str) -> None:
    (work / "docs" / "agent" / "plans" / name).write_text(
        f"# {name}\n\n{status_line}\n\nBody.\n", encoding="utf-8", newline="\n"
    )


def _run_sync_check(
    work: Path, stub_dir: Path, **env_overrides: str
) -> subprocess.CompletedProcess[str]:
    assert bash is not None
    env = _foreign_repo_env()
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
    env.pop("DREI_SYNC_CHECK_OFFLINE", None)
    env.update(env_overrides)
    return subprocess.run(
        [bash, str(SYNC_CHECK)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )


@requires_bash
def test_unusable_gh_fails_the_sync_check(tmp_path: Path) -> None:
    """Unauthenticated ``gh`` → exit 1, not a silently degraded pass."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    _write_gh_stub(stub_dir, authenticated=False)

    result = _run_sync_check(_make_repo(tmp_path), stub_dir)

    assert result.returncode == 1
    assert "gh" in result.stderr
    assert "DREI_SYNC_CHECK_OFFLINE" in result.stderr


@requires_bash
def test_offline_override_completes_with_a_visible_warning(tmp_path: Path) -> None:
    """The override is the deliberate act: it passes, and it says so loudly."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    _write_gh_stub(stub_dir, authenticated=False)

    result = _run_sync_check(
        _make_repo(tmp_path), stub_dir, DREI_SYNC_CHECK_OFFLINE="1"
    )

    assert result.returncode == 0
    assert "DREI_SYNC_CHECK_OFFLINE" in result.stdout
    assert "no claim visibility" in result.stdout


@requires_bash
def test_authenticated_gh_runs_every_section(tmp_path: Path) -> None:
    """The normal path is unchanged: all sections run, exit 0."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    _write_gh_stub(stub_dir, authenticated=True)

    result = _run_sync_check(_make_repo(tmp_path), stub_dir)

    assert result.returncode == 0, result.stderr
    assert "Claimed slices" in result.stdout
    assert "skipped" not in result.stdout


@requires_bash
def test_plan_listing_shows_each_plan_status(tmp_path: Path) -> None:
    """Review 0001 finding 12: six plan statuses stayed "ready" after merge.

    The scan is where an agent decides whether a slice is free, so it must
    show the status it is about to be trusted on — listing bare filenames
    hid the drift for five slices.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    _write_gh_stub(stub_dir, authenticated=True)
    work = _make_repo(tmp_path)
    _write_plan(
        work,
        "0008-example.md",
        "**Status:** merged (PR #18, commit `abc1234`) — architecture gate: "
        "a long tail of rationale that must not reach the summary line.",
    )
    _write_plan(work, "0009-example.md", "**Status:** ready")

    result = _run_sync_check(work, stub_dir)

    assert result.returncode == 0, result.stderr
    listing = [line for line in result.stdout.splitlines() if "-example.md" in line]
    assert len(listing) == 2, result.stdout
    merged, ready = listing
    assert "0008-example.md" in merged
    assert "merged (PR #18, commit `abc1234`)" in merged
    assert "architecture gate" not in merged
    assert "0009-example.md" in ready
    assert ready.rstrip().endswith("ready")


@requires_bash
def test_plan_without_a_status_line_is_flagged(tmp_path: Path) -> None:
    """A plan with no Status at all is a louder failure than a stale one."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    _write_gh_stub(stub_dir, authenticated=True)
    work = _make_repo(tmp_path)
    _write_plan(work, "0010-example.md", "Status: merged (not in bold)")

    result = _run_sync_check(work, stub_dir)

    assert result.returncode == 0, result.stderr
    line = next(ln for ln in result.stdout.splitlines() if "0010-example.md" in ln)
    assert "NO STATUS LINE" in line
