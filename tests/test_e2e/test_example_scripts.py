"""End-to-end tests for the script/demo example chapters.

A few example chapters ship runnable scripts instead of a pytest suite
(``09-instrument-streaming``, ``11-querying-data``). The companion
``test_examples.py`` runs the pytest-suite chapters; this harness runs each
script chapter's entrypoint(s) in order and asserts a clean exit, so a
refactor that breaks an example's imports or runtime fails the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"

# chapter -> ordered ``((script_relpath, env_overrides), ...)``. Order matters:
# ``11`` seeds the data its second script then queries; ``09`` streams a 1 s
# slice via ``LITMUS_STREAM_SECONDS`` instead of the demo's 60 s default, which
# still exercises the full connect -> instrument -> stream -> write path.
SCRIPT_CHAPTERS: dict[str, tuple[tuple[str, dict[str, str]], ...]] = {
    "11-querying-data": (
        ("scripts/seed_runs.py", {}),
        ("scripts/analyze.py", {}),
    ),
    "09-instrument-streaming": (("scripts/live_dmm_monitor.py", {"LITMUS_STREAM_SECONDS": "1"}),),
}


def _run_script(cwd: Path, rel: str, env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    """Run one example script in its chapter dir and capture the result."""
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [sys.executable, rel],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


class TestExampleScripts:
    """One test per script-based example chapter — every entrypoint exits 0."""

    @pytest.fixture(autouse=True)
    def _check_examples_exist(self) -> None:
        assert EXAMPLES_DIR.exists(), f"examples/ not found: {EXAMPLES_DIR}"

    @pytest.mark.parametrize("chapter", list(SCRIPT_CHAPTERS))
    def test_script_chapter(self, chapter: str) -> None:
        """Each script chapter's entrypoint(s) run end-to-end and exit 0."""
        chapter_dir = EXAMPLES_DIR / chapter
        assert chapter_dir.exists(), f"chapter not found: {chapter_dir}"
        for rel, env_overrides in SCRIPT_CHAPTERS[chapter]:
            result = _run_script(chapter_dir, rel, env_overrides)
            if result.returncode != 0:
                print("STDOUT:", result.stdout)
                print("STDERR:", result.stderr)
            assert result.returncode == 0, (
                f"{chapter}/{rel} failed:\n{result.stdout}\n{result.stderr}"
            )
