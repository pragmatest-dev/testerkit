"""End-to-end workflow tests verifying documented user journeys.

Each test exercises a complete user workflow as described in docs/:
init → pytest → testerkit runs → testerkit show → testerkit yield.

The starter project relies on the global results directory under
``platformdirs.user_data_dir("testerkit")``. To keep tests isolated, each
test sets ``TESTERKIT_HOME=<tmp_path>/home`` so the global root is
redirected into a per-test directory. Test bodies read parquet from
``home / "results" / ...``.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

_TESTERKIT_BIN = shutil.which("testerkit") or "testerkit"
_PYTEST_BIN = shutil.which("pytest") or "pytest"


def _testerkit_env(home: Path) -> dict[str, str]:
    """Return env with TESTERKIT_HOME pointed at a per-test global results dir.

    Starter projects pin ``data_dir: data`` in their ``testerkit.yaml``
    (see ``src/testerkit/init.py:234-248``) so learning runs stay
    project-local instead of polluting the shared global store.
    ``testerkit.yaml`` wins over ``TESTERKIT_HOME`` per the resolution
    chain in ``src/testerkit/data/data_dir.py``, so TESTERKIT_HOME here
    is a belt-and-braces isolation guard for any code path that
    falls through to the global default.
    """
    import os

    env = dict(os.environ)
    env["TESTERKIT_HOME"] = str(home)
    return env


def _testerkit(
    *args: str, cwd: Path | None = None, home: Path | None = None
) -> subprocess.CompletedProcess:
    env = _testerkit_env(home) if home is not None else None
    return subprocess.run(
        [_TESTERKIT_BIN, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _pytest(*args: str, cwd: Path, home: Path | None = None) -> subprocess.CompletedProcess:
    env = _testerkit_env(home) if home is not None else None
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
    """Create a starter project + isolated TESTERKIT_HOME, run its tests once.

    Returns ``(project_dir, home_dir)``. Starter pins ``data_dir:
    data`` in testerkit.yaml so parquet lands in
    ``project_dir / "data" / "runs" / .../*.parquet`` — read via the
    standard CLI / API surface, which auto-resolves the data dir
    from the testerkit.yaml in cwd ancestors.
    """
    home = tmp_path / "home"
    home.mkdir()
    result = _testerkit(
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
    assert result.returncode == 0, f"testerkit init failed:\n{result.stderr}"

    result = _pytest("tests/", "-q", cwd=project, home=home)
    assert result.returncode == 0, f"pytest failed:\n{result.stdout}\n{result.stderr}"

    # Pytest exits as soon as test functions return, but parquet
    # materialization happens in the runs daemon — which is only
    # spawned on first read from a RunsQuery / StepsQuery / etc. Poll
    # `testerkit runs` until it surfaces the row; that both triggers the
    # daemon spawn AND verifies materialization has caught up.
    # Otherwise downstream `testerkit runs` / `testerkit show` calls in the
    # individual tests race the materializer and see "No test runs
    # found."
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        probe = _testerkit("runs", cwd=project, home=home)
        if probe.returncode == 0 and "STARTER001" in probe.stdout:
            break
        time.sleep(0.5)
    else:
        raise AssertionError(
            "timed out waiting for runs daemon to materialize STARTER001 "
            f"in {project / 'data' / 'runs'}"
        )

    return project, home


class TestQuickstart:
    """Verify the documented quickstart workflow."""

    def test_init_creates_expected_structure(self, tmp_path: Path):
        project = tmp_path / "my_project"
        result = _testerkit(
            "init",
            "my_project",
            "--starter",
            "--no-git",
            "--name",
            "my_project",
            cwd=tmp_path,
        )
        assert result.returncode == 0

        assert (project / "testerkit.yaml").exists()
        assert (project / "tests" / "test_example.py").exists()
        assert (project / "stations" / "starter_station.yaml").exists()
        assert (project / "parts" / "example_part.yaml").exists()
        assert (project / "fixtures" / "example_fixture.yaml").exists()
        assert (project / "pyproject.toml").exists()

        cfg = yaml.safe_load((project / "testerkit.yaml").read_text())
        assert cfg["name"] == "my_project"
        # Starter pins data_dir locally so learning runs stay out of
        # the shared global store. See src/testerkit/init.py:234-248.
        assert cfg["data_dir"] == "data"

    def test_starter_tests_pass_with_mocks(self, tmp_path: Path):
        home = tmp_path / "home"
        home.mkdir()
        _testerkit(
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

    def test_results_stored_locally(self, starter_project: tuple[Path, Path]):
        project, _home = starter_project
        # Starter writes to the project-local data dir (testerkit.yaml's
        # data_dir: data setting). The global store is left alone so
        # learning runs don't pollute it.
        runs_dir = project / "data" / "runs"
        assert runs_dir.exists()
        parquet_files = list(runs_dir.rglob("*.parquet"))
        assert len(parquet_files) >= 1


class TestRunsAndShow:
    """Verify testerkit runs and testerkit show produce correct output."""

    def test_runs_lists_results(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        result = _testerkit("runs", cwd=project, home=home)
        assert result.returncode == 0
        assert "STARTER001" in result.stdout
        assert "pass" in result.stdout

    def test_runs_shows_station(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        result = _testerkit("runs", cwd=project, home=home)
        assert "starter_station" in result.stdout

    def test_show_displays_run_details(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        runs = _testerkit("runs", cwd=project, home=home)
        assert runs.returncode == 0
        run_id = runs.stdout.strip().split("\n")[-1].split()[0]

        result = _testerkit("show", run_id, cwd=project, home=home)
        assert result.returncode == 0
        assert "Outcome: pass" in result.stdout
        assert "Measurements:" in result.stdout

    def test_show_no_none_values(self, starter_project: tuple[Path, Path]):
        """Bug regression: testerkit show must not print 'None' for unit/limits."""
        project, home = starter_project
        runs = _testerkit("runs", cwd=project, home=home)
        run_id = runs.stdout.strip().split("\n")[-1].split()[0]

        result = _testerkit("show", run_id, cwd=project, home=home)
        assert result.returncode == 0
        for line in result.stdout.split("\n"):
            if line.strip().startswith(("output_voltage:", "Measurements:")):
                continue
            if ":" in line and "None" in line.split(":", 1)[1]:
                pytest.fail(f"'None' found in show output: {line.strip()}")


class TestReindex:
    """Verify testerkit data reindex works."""

    def test_reindex_and_requery(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        runs_before = _testerkit("runs", cwd=project, home=home)
        assert runs_before.returncode == 0

        result = _testerkit("data", "reindex", cwd=project, home=home)
        assert result.returncode == 0
        assert "rebuild" in result.stdout.lower()

        # Reindex wipes the daemon's materialized table and replays
        # events; the rebuild is async. Poll until the row reappears
        # rather than racing the daemon on the first call.
        deadline = time.monotonic() + 30.0
        runs_after = None
        while time.monotonic() < deadline:
            runs_after = _testerkit("runs", cwd=project, home=home)
            if runs_after.returncode == 0 and "STARTER001" in runs_after.stdout:
                break
            time.sleep(0.5)
        assert runs_after is not None
        assert runs_after.returncode == 0
        assert "STARTER001" in runs_after.stdout, (
            f"reindex never repopulated STARTER001:\n{runs_after.stdout}"
        )


class TestMetricsAnalytics:
    """Verify metrics analytics commands return data."""

    def test_metrics_summary(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        result = _testerkit("metrics", "summary", "--phase", "all", cwd=project, home=home)
        assert result.returncode == 0
        assert "Runs" in result.stdout or "FPY" in result.stdout or "No data" in result.stdout

    def test_metrics_pareto(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        result = _testerkit("metrics", "pareto", "--phase", "all", cwd=project, home=home)
        assert result.returncode == 0

    def test_metrics_ppk(self, starter_project: tuple[Path, Path]):
        project, home = starter_project
        result = _testerkit(
            "metrics",
            "ppk",
            "--phase",
            "all",
            "--min-samples",
            "1",
            cwd=project,
            home=home,
        )
        assert result.returncode == 0


class TestInitVariants:
    """Verify testerkit init works for non-starter projects."""

    def test_bare_init(self, tmp_path: Path):
        result = _testerkit(
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
        assert (project / "testerkit.yaml").exists()

        cfg = yaml.safe_load((project / "testerkit.yaml").read_text())
        assert cfg["name"] == "bare_proj"
        assert cfg.get("data_dir") is None

    def test_init_idempotent(self, tmp_path: Path):
        _testerkit("init", "idem", "--no-git", "--name", "first", cwd=tmp_path)
        project = tmp_path / "idem"
        (project / "testerkit.yaml").write_text("name: custom\n")

        _testerkit("init", cwd=project)
        cfg = yaml.safe_load((project / "testerkit.yaml").read_text())
        assert cfg["name"] == "custom"
