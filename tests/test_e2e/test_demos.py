"""End-to-end tests that exercise the demo test suites.

These tests ensure the demo examples continue to work correctly.
They run the actual demo tests with mock instruments to verify
the framework integrates properly.
"""

import subprocess
import sys
from pathlib import Path

import pytest

DEMO_DIR = Path(__file__).parent.parent.parent / "demo"


class TestDemoTestSuites:
    """Run demo test suites to ensure they don't break."""

    @pytest.fixture(autouse=True)
    def check_demo_exists(self):
        assert DEMO_DIR.exists(), f"Demo directory not found: {DEMO_DIR}"

    def _run_demo_tests(
        self,
        test_file: str,
        extra_args: list[str] | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a demo test file with mock instruments.

        Args:
            test_file: Name of the test file (relative to demo/tests/)
            extra_args: Additional pytest arguments

        Returns:
            CompletedProcess result
        """
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            f"tests/{test_file}",
            "--station=demo_station_001",
            "--mock-instruments",
            "-v",
            "--tb=short",
        ]
        if extra_args:
            cmd.extend(extra_args)

        return subprocess.run(
            cmd,
            cwd=DEMO_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_power_board_demo(self):
        """Run the power board demo tests."""
        result = self._run_demo_tests("test_power_board.py")

        # Print output for debugging if failed
        if result.returncode != 0:
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)

        assert result.returncode == 0, (
            f"Power board demo tests failed:\n{result.stdout}\n{result.stderr}"
        )

    def test_pure_pytest_demo(self):
        """Run the pure pytest demo tests."""
        result = self._run_demo_tests("test_pure_pytest.py")

        if result.returncode != 0:
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)

        assert result.returncode == 0, (
            f"Pure pytest demo tests failed:\n{result.stdout}\n{result.stderr}"
        )

    def test_architect_demo(self):
        """Run the architect demo tests."""
        result = self._run_demo_tests("test_architect.py")

        if result.returncode != 0:
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)

        assert result.returncode == 0, (
            f"Architect demo tests failed:\n{result.stdout}\n{result.stderr}"
        )
