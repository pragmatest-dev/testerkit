"""End-to-end tests for multi-DUT parallel execution.

Tests the full orchestrator → workers → results path using
subprocess-based slot execution with fixture YAML configs.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _write_fixture_yaml(path, slots: dict[str, dict]) -> None:
    """Write a minimal fixture YAML."""
    import yaml

    fixture = {
        "id": path.stem,
        "slots": slots,
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


class TestMultiDutE2E:
    """Full orchestrator → workers → results tests."""

    def test_two_slots_both_pass(self, tmp_path):
        """Full run: 2-slot fixture, both slots pass."""
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_simple.py"

        _write_fixture_yaml(
            fixture_path,
            {
                "slot_1": {"connections": {}},
                "slot_2": {"connections": {}},
            },
        )
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
                f"--results-dir={tmp_path / 'results'}",
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
        assert "Multi-DUT Results" in result.stdout
        assert "slot_1: PASS" in result.stdout
        assert "slot_2: PASS" in result.stdout

    def test_one_slot_fails(self, tmp_path):
        """One slot conditionally fails, verify per-slot reporting."""
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_conditional.py"

        _write_fixture_yaml(
            fixture_path,
            {
                "slot_1": {"connections": {}},
                "slot_2": {"connections": {}},
            },
        )
        _write_station_yaml(station_path)
        _write_test_file(
            test_file,
            """\
            import os

            def test_conditional():
                slot_id = os.environ.get("_LITMUS_SLOT_ID", "")
                if slot_id == "slot_2":
                    assert False, "Intentional failure for slot_2"
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
                f"--results-dir={tmp_path / 'results'}",
                "--mock-instruments",
                "-v",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode != 0
        assert "Multi-DUT Results" in result.stdout
        assert "slot_1: PASS" in result.stdout
        assert "slot_2: FAIL" in result.stdout

    def test_single_serial_warning(self, tmp_path):
        """Single --dut-serial with 2 slots emits warning."""
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_pass.py"

        _write_fixture_yaml(
            fixture_path,
            {
                "slot_1": {"connections": {}},
                "slot_2": {"connections": {}},
            },
        )
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
                f"--results-dir={tmp_path / 'results'}",
                "--mock-instruments",
                "--dut-serial=SINGLE_SN",
                "-v",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined = result.stdout + result.stderr
        assert "Single --dut-serial" in combined, f"Expected serial warning in output:\n{combined}"
