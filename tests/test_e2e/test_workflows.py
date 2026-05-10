"""End-to-end workflow tests verifying documented user journeys.

Each test exercises a complete user workflow as described in docs/:
init → pytest → litmus runs → litmus show → litmus yield.

The starter project relies on the global results directory under
``platformdirs.user_data_dir("litmus")``. To keep tests isolated, each
test sets ``LITMUS_HOME=<tmp_path>/home`` so the global root is
redirected into a per-test directory. Test bodies read parquet from
``home / "results" / ...``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_LITMUS_BIN = shutil.which("litmus") or "litmus"
_PYTEST_BIN = shutil.which("pytest") or "pytest"


def _litmus_env(home: Path) -> dict[str, str]:
    """Return env with LITMUS_HOME pointed at a per-test global results dir.

    The starter no longer sets ``data_dir:`` (it relies on the
    global default under ``platformdirs.user_data_dir("litmus")``).
    Tests redirect that root via ``LITMUS_HOME`` so each test gets
    its own isolated ``<home>/results/`` tree.
    """
    import os

    env = dict(os.environ)
    env["LITMUS_HOME"] = str(home)
    return env


def _litmus(
    *args: str, cwd: Path | None = None, home: Path | None = None
) -> subprocess.CompletedProcess:
    env = _litmus_env(home) if home is not None else None
    return subprocess.run(
        [_LITMUS_BIN, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _pytest(*args: str, cwd: Path, home: Path | None = None) -> subprocess.CompletedProcess:
    env = _litmus_env(home) if home is not None else None
    return subprocess.run(
        [_PYTEST_BIN, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


@pytest.fixture()
def starter_project(tmp_path: Path) -> tuple[Path, Path]:
    """Create a starter project + isolated LITMUS_HOME, run its tests once.

    Returns ``(project_dir, home_dir)`` so tests can read the
    parquet results from ``home_dir / "results" / ...``.
    """
    home = tmp_path / "home"
    home.mkdir()
    result = _litmus(
        "init",
        "proj",
        "--starter",
        "--no-git",
        "--name",
        "wf_test",
        cwd=tmp_path,
        home=home,
    )
    project = tmp_path / "proj"
    assert result.returncode == 0, f"litmus init failed:\n{result.stderr}"

    result = _pytest("tests/", "-q", cwd=project, home=home)
    assert result.returncode == 0, f"pytest failed:\n{result.stdout}\n{result.stderr}"
    return project, home


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
        assert (project / "products" / "example_product.yaml").exists()
        assert (project / "fixtures" / "example_fixture.yaml").exists()
        assert (project / "pyproject.toml").exists()

        cfg = yaml.safe_load((project / "litmus.yaml").read_text())
        assert cfg["name"] == "my_project"
        # Starter relies on the global default — no ``data_dir`` key.
        assert cfg.get("data_dir") is None

    def test_starter_tests_pass_with_mocks(self, tmp_path: Path):
        home = tmp_path / "home"
        home.mkdir()
        _litmus(
            "init",
            "proj",
            "--starter",
            "--no-git",
            "--name",
            "proj",
            cwd=tmp_path,
            home=home,
        )
        project = tmp_path / "proj"
        result = _pytest("tests/", "-q", cwd=project, home=home)
        assert result.returncode == 0
        assert "passed" in result.stdout

    def test_results_stored_globally(self, starter_project: tuple[Path, Path]):
        _project, home = starter_project
        # Starter writes to the global LITMUS_HOME/results, not the
        # project directory.
        results = home / "results"
        assert results.exists()
        parquet_files = list(results.rglob("*.parquet"))
        assert len(parquet_files) >= 1


class TestRunsAndShow:
    """Verify litmus runs and litmus show produce correct output."""

    def test_runs_lists_results(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        result = _litmus("runs", cwd=project, home=home)
        assert result.returncode == 0
        assert "STARTER001" in result.stdout
        assert "pass" in result.stdout

    def test_runs_shows_station(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        result = _litmus("runs", cwd=project, home=home)
        assert "starter_station" in result.stdout

    def test_show_displays_run_details(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        runs = _litmus("runs", cwd=project, home=home)
        assert runs.returncode == 0
        run_id = runs.stdout.strip().split("\n")[-1].split()[0]

        result = _litmus("show", run_id, cwd=project, home=home)
        assert result.returncode == 0
        assert "Outcome: pass" in result.stdout
        assert "Measurements:" in result.stdout

    def test_show_no_none_values(self, starter_project: tuple[Path, Path]):
        """Bug regression: litmus show must not print 'None' for units/limits."""
        project, home = starter_project
        runs = _litmus("runs", cwd=project, home=home)
        run_id = runs.stdout.strip().split("\n")[-1].split()[0]

        result = _litmus("show", run_id, cwd=project, home=home)
        assert result.returncode == 0
        for line in result.stdout.split("\n"):
            if line.strip().startswith(("output_voltage:", "Measurements:")):
                continue
            if ":" in line and "None" in line.split(":", 1)[1]:
                pytest.fail(f"'None' found in show output: {line.strip()}")


class TestReindex:
    """Verify litmus data reindex works."""

    def test_reindex_and_requery(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        runs_before = _litmus("runs", cwd=project, home=home)
        assert runs_before.returncode == 0

        result = _litmus("data", "reindex", cwd=project, home=home)
        assert result.returncode == 0
        assert "rebuild" in result.stdout.lower()

        runs_after = _litmus("runs", cwd=project, home=home)
        assert runs_after.returncode == 0
        assert "STARTER001" in runs_after.stdout


class TestMetricsAnalytics:
    """Verify metrics analytics commands return data."""

    def test_metrics_summary(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        result = _litmus("metrics", "summary", "--phase", "all", cwd=project, home=home)
        assert result.returncode == 0
        assert "Runs" in result.stdout or "FPY" in result.stdout or "No data" in result.stdout

    def test_metrics_pareto(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        result = _litmus("metrics", "pareto", "--phase", "all", cwd=project, home=home)
        assert result.returncode == 0

    def test_metrics_cpk(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        result = _litmus(
            "metrics",
            "cpk",
            "--phase",
            "all",
            "--min-samples",
            "1",
            cwd=project,
            home=home,
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
        assert cfg.get("data_dir") is None

    def test_init_idempotent(self, tmp_path: Path):
        _litmus("init", "idem", "--no-git", "--name", "first", cwd=tmp_path)
        project = tmp_path / "idem"
        (project / "litmus.yaml").write_text("name: custom\n")

        _litmus("init", cwd=project)
        cfg = yaml.safe_load((project / "litmus.yaml").read_text())
        assert cfg["name"] == "custom"
