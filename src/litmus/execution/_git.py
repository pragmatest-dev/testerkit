"""Git introspection helpers for code traceability.

All git traceability logic is consolidated here. Callers pass a ``cwd``
to control which repository is inspected (defaults to process cwd).
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from litmus.store import load_project_config

logger = logging.getLogger(__name__)


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


def _git_repo_root(cwd: Path | str | None = None) -> Path | None:
    """Return the git repo root directory, or None if not in a repo."""
    try:
        result = _run_git("rev-parse", "--show-toplevel", cwd=cwd)
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _remote_leaf_name(remote_url: str) -> str | None:
    """Extract the repository name from a git remote URL.

    Handles HTTPS (``https://github.com/org/repo.git``) and SSH
    (``git@github.com:org/repo.git``) URLs.
    """
    from posixpath import basename

    name = basename(remote_url.rstrip("/"))
    if name.endswith(".git"):
        name = name[:-4]
    return name or None


def get_project_name(cwd: Path | str | None = None) -> str:
    """Resolve a human-readable project name.

    Resolution chain (first non-None wins):
    1. ``litmus.yaml`` → ``name`` field
    2. Git remote leaf name (e.g. ``github.com/org/board_a.git`` → ``board_a``)
    3. Git repo root folder name
    4. CWD folder name
    """
    resolved_cwd = Path(cwd) if cwd is not None else Path.cwd()

    try:
        config = load_project_config(resolved_cwd)
        if config.name != "litmus":
            return config.name
    except Exception:
        logger.debug("Could not read litmus.yaml for project name", exc_info=True)

    remote = get_git_remote(cwd)
    if remote:
        leaf = _remote_leaf_name(remote)
        if leaf:
            return leaf

    root = _git_repo_root(cwd)
    if root:
        return root.name

    return resolved_cwd.name


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
