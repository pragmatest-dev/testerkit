"""End-to-end tests for multi-UUT parallel execution.

Tests the full orchestrator → workers → results path using
subprocess-based site execution with fixture YAML configs.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _write_fixture_yaml(path, site_count: int = 2) -> None:
    """Write a minimal multi-site fixture YAML."""
    import yaml

    fixture = {
        "id": path.stem,
        "sites": [{"connections": {}} for _ in range(site_count)],
    }
    path.write_text(yaml.safe_dump(fixture))


def _write_station_yaml(path) -> None:
    """Write a minimal station YAML."""
    import yaml

    station = {
        "id": path.stem,
        "name": "Test Station",
        "instruments": {},
    }
    path.write_text(yaml.safe_dump(station))


def _write_test_file(path, content: str) -> None:
    """Write a test file."""
    path.write_text(textwrap.dedent(content))


class TestMultiUutE2E:
    """Full orchestrator → workers → results tests."""

    def test_two_sites_both_pass(self, tmp_path):
        """Full run: 2-site fixture, both sites pass."""
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_simple.py"

        _write_fixture_yaml(fixture_path, site_count=2)
        _write_station_yaml(station_path)
        _write_test_file(
            test_file,
            """\
            def test_always_passes():
                assert True
        """,
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(test_file),
                f"--fixture={fixture_path}",
                f"--station={station_path}",
                "--mock-instruments",
                "-v",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, (
            f"Expected pass but got rc={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "Multi-UUT Results" in result.stdout
        assert "site[0]: PASS" in result.stdout
        assert "site[1]: PASS" in result.stdout

    def test_one_site_fails(self, tmp_path):
        """One site conditionally fails, verify per-site reporting."""
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_conditional.py"

        _write_fixture_yaml(fixture_path, site_count=2)
        _write_station_yaml(station_path)
        _write_test_file(
            test_file,
            """\
            import os

            def test_conditional():
                site_index = os.environ.get("_TESTERKIT_SITE_INDEX", "")
                if site_index == "1":
                    assert False, "Intentional failure for site 1"
                assert True
        """,
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(test_file),
                f"--fixture={fixture_path}",
                f"--station={station_path}",
                "--mock-instruments",
                "-v",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode != 0
        assert "Multi-UUT Results" in result.stdout
        assert "site[0]: PASS" in result.stdout
        assert "site[1]: FAIL" in result.stdout

    def test_single_serial_warning(self, tmp_path):
        """Single --uut-serial with 2 sites emits warning."""
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_pass.py"

        _write_fixture_yaml(fixture_path, site_count=2)
        _write_station_yaml(station_path)
        _write_test_file(
            test_file,
            """\
            def test_ok():
                pass
        """,
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(test_file),
                f"--fixture={fixture_path}",
                f"--station={station_path}",
                "--mock-instruments",
                "--uut-serial=SINGLE_SN",
                "-v",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined = result.stdout + result.stderr
        assert "Single --uut-serial" in combined, f"Expected serial warning in output:\n{combined}"
