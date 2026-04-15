"""Git introspection helpers for code traceability.

All git traceability logic is consolidated here. Callers pass a ``cwd``
to control which repository is inspected (defaults to process cwd).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


def _run_git(
    *args: str,
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a git command, optionally in a specific directory."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=5,
        cwd=cwd,
    )


@dataclass(frozen=True, slots=True)
class GitInfo:
    """Resolved git traceability fields."""

    commit: str | None = None
    branch: str | None = None
    remote: str | None = None


def get_git_info(cwd: Path | str | None = None) -> GitInfo:
    """Resolve all git traceability fields for the given directory.

    Single entry point that runs all three git queries. Returns a
    ``GitInfo`` with ``None`` for any field that can't be determined.

    Args:
        cwd: Directory inside the git repo to inspect.  Defaults to
            the process working directory.
    """
    return GitInfo(
        commit=get_git_commit(cwd),
        branch=get_git_branch(cwd),
        remote=get_git_remote(cwd),
    )


def get_git_commit(cwd: Path | str | None = None) -> str | None:
    """Get current git commit hash with dirty flag.

    Returns a 12-char short hash, suffixed with ``-dirty`` if the working
    tree has uncommitted changes.  Returns ``None`` if not in a git repo.

    Args:
        cwd: Directory inside the git repo to inspect.  Defaults to the
            process working directory.
    """
    try:
        result = _run_git("rev-parse", "HEAD", cwd=cwd)
        if result.returncode != 0:
            return None
        sha = result.stdout.strip()[:12]

        dirty = _run_git("status", "--porcelain", cwd=cwd)
        if dirty.returncode == 0 and dirty.stdout.strip():
            return f"{sha}-dirty"
        return sha
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def get_git_branch(cwd: Path | str | None = None) -> str | None:
    """Get current git branch name, or None if detached/not in a repo.

    Args:
        cwd: Directory inside the git repo to inspect.  Defaults to the
            process working directory.
    """
    try:
        result = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
        if result.returncode == 0:
            branch = result.stdout.strip()
            # "HEAD" means detached HEAD — not useful as a branch name
            return branch if branch != "HEAD" else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def get_git_remote(cwd: Path | str | None = None) -> str | None:
    """Get the URL of the ``origin`` remote, or None if not configured.

    Args:
        cwd: Directory inside the git repo to inspect.  Defaults to the
            process working directory.
    """
    try:
        result = _run_git("remote", "get-url", "origin", cwd=cwd)
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def is_git_clean(cwd: Path | str | None = None) -> bool:
    """Check if the working tree is clean (no uncommitted changes).

    Returns ``True`` only if git is available, ``cwd`` is inside a repo,
    and there are no staged or unstaged changes.

    Args:
        cwd: Directory inside the git repo to inspect.  Defaults to the
            process working directory.
    """
    try:
        if _run_git("rev-parse", "--git-dir", cwd=cwd).returncode != 0:
            return False
        result = _run_git("status", "--porcelain", cwd=cwd)
        return result.returncode == 0 and not result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
