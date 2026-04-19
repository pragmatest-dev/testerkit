"""End-to-end workflow tests verifying documented user journeys.

Each test exercises a complete user workflow as described in docs/:
init → pytest → litmus runs → litmus show → litmus yield.

These tests run against a temp directory with local results
(``results_dir: results`` in litmus.yaml) so they are fully
isolated from the global results directory.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_LITMUS_BIN = shutil.which("litmus") or "litmus"
_PYTEST_BIN = shutil.which("pytest") or "pytest"


def _litmus(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_LITMUS_BIN, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _pytest(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_PYTEST_BIN, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture()
def starter_project(tmp_path: Path) -> Path:
    """Create a starter project and run its tests once."""
    result = _litmus(
        "init",
        "proj",
        "--starter",
        "--no-git",
        "--name",
        "wf_test",
        cwd=tmp_path,
    )
    project = tmp_path / "proj"
    assert result.returncode == 0, f"litmus init failed:\n{result.stderr}"

    result = _pytest("tests/", "-q", cwd=project)
    assert result.returncode == 0, f"pytest failed:\n{result.stdout}\n{result.stderr}"
    return project


class TestQuickstart:
    """Verify the documented quickstart workflow."""

    def test_init_creates_expected_structure(self, tmp_path: Path):
        project = tmp_path / "my_project"
        result = _litmus(
            "init",
            "my_project",
            "--starter",
            "--no-git",
            "--name",
            "my_project",
            cwd=tmp_path,
        )
        assert result.returncode == 0

        assert (project / "litmus.yaml").exists()
        assert (project / "tests" / "test_example.py").exists()
        assert (project / "stations" / "starter_station.yaml").exists()
        assert (project / "sequences" / "example_sequence.yaml").exists()
        assert (project / "pyproject.toml").exists()

        cfg = yaml.safe_load((project / "litmus.yaml").read_text())
        assert cfg["name"] == "my_project"
        assert cfg["results_dir"] == "results"

    def test_starter_tests_pass_with_mocks(self, tmp_path: Path):
        _litmus("init", "proj", "--starter", "--no-git", "--name", "proj", cwd=tmp_path)
        project = tmp_path / "proj"
        result = _pytest("tests/", "-q", cwd=project)
        assert result.returncode == 0
        assert "passed" in result.stdout

    def test_results_stored_locally(self, starter_project: Path):
        results = starter_project / "results"
        assert results.exists()
        parquet_files = list(results.rglob("*.parquet"))
        assert len(parquet_files) >= 1


class TestRunsAndShow:
    """Verify litmus runs and litmus show produce correct output."""

    def test_runs_lists_results(self, starter_project: Path):
        result = _litmus("runs", cwd=starter_project)
        assert result.returncode == 0
        assert "STARTER001" in result.stdout
        assert "pass" in result.stdout

    def test_runs_shows_station(self, starter_project: Path):
        result = _litmus("runs", cwd=starter_project)
        assert "starter_station" in result.stdout

    def test_show_displays_run_details(self, starter_project: Path):
        runs = _litmus("runs", cwd=starter_project)
        assert runs.returncode == 0
        run_id = runs.stdout.strip().split("\n")[-1].split()[0]

        result = _litmus("show", run_id, cwd=starter_project)
        assert result.returncode == 0
        assert "Outcome: pass" in result.stdout
        assert "Measurements:" in result.stdout

    def test_show_no_none_values(self, starter_project: Path):
        """Bug regression: litmus show must not print 'None' for units/limits."""
        runs = _litmus("runs", cwd=starter_project)
        run_id = runs.stdout.strip().split("\n")[-1].split()[0]

        result = _litmus("show", run_id, cwd=starter_project)
        assert result.returncode == 0
        for line in result.stdout.split("\n"):
            if line.strip().startswith(("output_voltage:", "Measurements:")):
                continue
            if ":" in line and "None" in line.split(":", 1)[1]:
                pytest.fail(f"'None' found in show output: {line.strip()}")


class TestReindex:
    """Verify litmus data reindex works."""

    def test_reindex_and_requery(self, starter_project: Path):
        runs_before = _litmus("runs", cwd=starter_project)
        assert runs_before.returncode == 0

        result = _litmus("data", "reindex", cwd=starter_project)
        assert result.returncode == 0
        assert "rebuild" in result.stdout.lower()

        runs_after = _litmus("runs", cwd=starter_project)
        assert runs_after.returncode == 0
        assert "STARTER001" in runs_after.stdout


class TestYieldAnalytics:
    """Verify yield analytics commands return data."""

    def test_yield_summary(self, starter_project: Path):
        result = _litmus("yield", "summary", "--phase", "all", cwd=starter_project)
        assert result.returncode == 0
        assert "Runs:" in result.stdout or "First Pass Yield" in result.stdout

    def test_yield_pareto(self, starter_project: Path):
        result = _litmus("yield", "pareto", "--phase", "all", cwd=starter_project)
        assert result.returncode == 0

    def test_yield_cpk(self, starter_project: Path):
        result = _litmus(
            "yield",
            "cpk",
            "--phase",
            "all",
            "--min-samples",
            "1",
            cwd=starter_project,
        )
        assert result.returncode == 0


class TestInitVariants:
    """Verify litmus init works for non-starter projects."""

    def test_bare_init(self, tmp_path: Path):
        result = _litmus(
            "init",
            "bare",
            "--no-git",
            "--no-starter",
            "--name",
            "bare_proj",
            cwd=tmp_path,
        )
        project = tmp_path / "bare"
        assert result.returncode == 0
        assert (project / "litmus.yaml").exists()

        cfg = yaml.safe_load((project / "litmus.yaml").read_text())
        assert cfg["name"] == "bare_proj"
        assert cfg.get("results_dir") is None

    def test_init_idempotent(self, tmp_path: Path):
        _litmus("init", "idem", "--no-git", "--name", "first", cwd=tmp_path)
        project = tmp_path / "idem"
        (project / "litmus.yaml").write_text("name: custom\n")

        _litmus("init", cwd=project)
        cfg = yaml.safe_load((project / "litmus.yaml").read_text())
        assert cfg["name"] == "custom"
