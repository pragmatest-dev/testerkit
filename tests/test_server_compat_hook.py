"""Tests for scripts/check-server-compat.sh's module-selection logic
(docs/44 §1, testerkit-server compatibility gate).

The hook's real job — actually running testerkit-server's suite — needs a
sibling checkout, `uv sync`, and network access, so it isn't exercised
here. What IS exercised, hermetically and fast, is the part most likely to
silently rot: which staged files count as "touches a module
testerkit-server imports". ``SERVER_COMPAT_DRY_RUN=1`` makes the script
stop right after printing its decision (``decision=touched`` /
``decision=not-touched``), before it ever looks for the sibling checkout
or shells out to uv/pytest — see the script's own docstring comment.

Each test builds a disposable scratch git repo under ``tmp_path`` (a
throwaway fixture the hook itself never touches — not the two-repo
compatibility mechanism this test verifies) so `git diff --cached` has
something real to read.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOOK_SCRIPT = _REPO_ROOT / "scripts" / "check-server-compat.sh"

_MODULE_LIST = """\
# comment lines and blanks are ignored

testerkit.data.read_models
testerkit.data.gated_pkg
"""


def _clean_git_env() -> dict[str, str]:
    """The environment minus every GIT_* variable. Under pre-commit, GIT_DIR /
    GIT_INDEX_FILE point at the repo being committed; a `git init` or
    `git config` in a scratch dir that inherits them rewrites THAT repo (it
    once flipped testerkit's core.bare to true and wrote a test identity)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=_clean_git_env(),
    )


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    """A minimal git repo shaped like testerkit's own layout, with a
    committed baseline (an empty tree with the module list + a couple of
    source files) so tests can stage changes on top of it."""
    repo = tmp_path / "scratch"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")

    (repo / "scripts").mkdir()
    (repo / "scripts" / "server_compat_modules.txt").write_text(_MODULE_LIST)

    src = repo / "src" / "testerkit" / "data"
    src.mkdir(parents=True)
    (src / "read_models.py").write_text("VALUE = 1\n")
    (src / "unrelated.py").write_text("VALUE = 2\n")
    (src / "gated_pkg").mkdir()
    (src / "gated_pkg" / "__init__.py").write_text("")

    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "baseline")
    return repo


def _decision(repo: Path, *, server_dir: str = "/does/not/exist") -> str:
    """Stage whatever's dirty in `repo` and run the hook in dry-run mode,
    returning its printed decision line."""
    _run_git(repo, "add", "-A")
    result = subprocess.run(
        [str(_HOOK_SCRIPT)],
        cwd=repo,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "SERVER_COMPAT_DRY_RUN": "1",
            "TESTERKIT_SERVER_DIR": server_dir,
        },
    )
    assert result.returncode == 0, result.stderr
    lines = [
        line for line in result.stdout.splitlines() if line.startswith("server-compat: decision=")
    ]
    assert len(lines) == 1, result.stdout
    return lines[0].removeprefix("server-compat: decision=")


def test_unrelated_file_is_not_touched(scratch_repo: Path) -> None:
    (scratch_repo / "src" / "testerkit" / "data" / "unrelated.py").write_text("VALUE = 3\n")
    assert _decision(scratch_repo) == "not-touched"


def test_gated_module_file_is_touched(scratch_repo: Path) -> None:
    (scratch_repo / "src" / "testerkit" / "data" / "read_models.py").write_text("VALUE = 3\n")
    assert _decision(scratch_repo) == "touched"


def test_gated_package_directory_member_is_touched(scratch_repo: Path) -> None:
    """`testerkit.data.gated_pkg` is a package in the module list; a new
    file anywhere under its directory counts as touching it, not just an
    edit to a same-named `.py` file."""
    (scratch_repo / "src" / "testerkit" / "data" / "gated_pkg" / "new_submodule.py").write_text(
        "X = 1\n"
    )
    assert _decision(scratch_repo) == "touched"


def test_unrelated_module_named_as_a_prefix_is_not_touched(scratch_repo: Path) -> None:
    """`read_models_extra.py` must not match the `read_models` entry just
    because the filename starts with it (a naive substring/prefix check
    would false-positive here)."""
    (scratch_repo / "src" / "testerkit" / "data" / "read_models_extra.py").write_text("X = 1\n")
    assert _decision(scratch_repo) == "not-touched"


def test_file_outside_src_is_not_touched(scratch_repo: Path) -> None:
    (scratch_repo / "README.md").write_text("docs change\n")
    assert _decision(scratch_repo) == "not-touched"


def test_missing_sibling_fails_hard_when_a_gated_module_is_touched(scratch_repo: Path) -> None:
    """Outside dry-run, a touched gated module with no sibling checkout
    must FAIL the commit, never silently pass."""
    (scratch_repo / "src" / "testerkit" / "data" / "read_models.py").write_text("VALUE = 3\n")
    _run_git(scratch_repo, "add", "-A")
    result = subprocess.run(
        [str(_HOOK_SCRIPT)],
        cwd=scratch_repo,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "TESTERKIT_SERVER_DIR": "/does/not/exist",
        },
    )
    assert result.returncode == 1
    assert "CANNOT RUN" in result.stderr


def test_missing_sibling_does_not_fail_when_nothing_gated_is_touched(scratch_repo: Path) -> None:
    """The gate must never punish an unrelated commit just because no
    sibling checkout happens to be configured."""
    (scratch_repo / "src" / "testerkit" / "data" / "unrelated.py").write_text("VALUE = 3\n")
    _run_git(scratch_repo, "add", "-A")
    result = subprocess.run(
        [str(_HOOK_SCRIPT)],
        cwd=scratch_repo,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "TESTERKIT_SERVER_DIR": "/does/not/exist",
        },
    )
    assert result.returncode == 0, result.stderr


def test_missing_module_list_fails_with_regeneration_hint(scratch_repo: Path) -> None:
    (scratch_repo / "scripts" / "server_compat_modules.txt").unlink()
    _run_git(scratch_repo, "add", "-A")
    result = subprocess.run(
        [str(_HOOK_SCRIPT)],
        cwd=scratch_repo,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 1
    assert "generate_server_compat_list.py" in result.stderr
