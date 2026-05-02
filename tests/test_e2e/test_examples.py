"""End-to-end tests that exercise the bundled example chapters.

Keeps every example chapter honest: if any of them stop passing under
``--mock-instruments`` (and ``LITMUS_AUTO_CONFIRM=1`` for the
prompts chapters), the suite fails. They run from inside the example
directory so pytest discovers the local
``stations/`` / ``products/`` / ``fixtures/`` folders.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"

CHAPTERS: tuple[str, ...] = (
    "01-vanilla",
    "02-verify",
    "03-inline-limits",
    "04-sidecar-markers",
    "05-product-spec",
    "06-station-catalog",
    "07-profiles",
)


def _run_pytest(cwd: Path) -> subprocess.CompletedProcess:
    """Run the chapter's tests under ``--mock-instruments`` with auto-confirm prompts."""
    env = {**os.environ, "LITMUS_AUTO_CONFIRM": "auto-confirm"}
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--mock-instruments",
        "--dut-serial=test",
        "--no-cov",
        "-q",
        "--tb=short",
    ]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=180, env=env)


class TestExampleChapters:
    """One test per example chapter — all must pass with mock instruments."""

    @pytest.fixture(autouse=True)
    def _check_examples_exist(self) -> None:
        assert EXAMPLES_DIR.exists(), f"examples/ not found: {EXAMPLES_DIR}"

    @pytest.mark.parametrize("chapter", CHAPTERS)
    def test_chapter(self, chapter: str) -> None:
        """Each example chapter passes end-to-end under mocks."""
        chapter_dir = EXAMPLES_DIR / chapter
        assert chapter_dir.exists(), f"chapter not found: {chapter_dir}"
        result = _run_pytest(chapter_dir)
        if result.returncode != 0:
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
        assert result.returncode == 0, f"{chapter} failed:\n{result.stdout}\n{result.stderr}"
