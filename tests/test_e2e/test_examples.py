"""End-to-end tests that exercise the bundled example projects.

Keeps the three example tiers honest: if any of them stop passing
under ``--mock-instruments``, the suite fails. They run from inside
the example directory so pytest discovers the local
``stations/``/``products/``/``fixtures/`` folders.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


def _run_pytest(
    cwd: Path,
    target: str,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Run pytest against ``target`` (relative to ``cwd``) with mocks."""
    cmd = [sys.executable, "-m", "pytest", target, "--mock-instruments", "-v", "--tb=short"]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=180)


class TestExampleTiers:
    """One test per example tier — all three must pass with mock instruments."""

    @pytest.fixture(autouse=True)
    def _check_examples_exist(self) -> None:
        assert EXAMPLES_DIR.exists(), f"examples/ not found: {EXAMPLES_DIR}"

    def test_tier1_bringup(self) -> None:
        """Tier 0/1 — no station, no product."""
        result = _run_pytest(EXAMPLES_DIR / "01-bringup", "tests/test_smoke.py")
        if result.returncode != 0:
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
        assert result.returncode == 0, f"01-bringup failed:\n{result.stdout}\n{result.stderr}"

    def test_tier2_station(self) -> None:
        """Tier 2 — station + product + fixture."""
        result = _run_pytest(
            EXAMPLES_DIR / "02-station",
            "tests/test_power_board_smoke.py",
            extra_args=["--station=demo_station_001"],
        )
        if result.returncode != 0:
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
        assert result.returncode == 0, f"02-station failed:\n{result.stdout}\n{result.stderr}"

    def test_tier3_profiles(self) -> None:
        """Tier 3/4 — profiles + multi-pin."""
        result = _run_pytest(EXAMPLES_DIR / "03-profiles", "tests/")
        if result.returncode != 0:
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
        assert result.returncode == 0, f"03-profiles failed:\n{result.stdout}\n{result.stderr}"
