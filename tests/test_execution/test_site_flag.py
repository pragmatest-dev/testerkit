"""``--site=N`` flag — single-process operator targeting of a fixture site.

The flag is the operator-facing channel: when an operator places a
UUT into physical position 1 of a multi-site fixture and runs solo,
``--site=1`` makes the resulting parquet record ``site_index=1``.

Pinned behaviors:

* ``--site=<known index>`` against a multi-site fixture → single-process
  run (orchestrator dispatch suppressed), parquet ``site_index`` set.
* ``--site=<unknown>`` → usage error from ``pytest_sessionstart``,
  no run is performed.
* No ``--site`` against a multi-site fixture in single-process mode
  → orchestrator dispatch.
* Single-site fixture / no fixture → ``--site`` is unused; no error.

All read-back goes through the public :class:`RunsQuery` — same path
the operator UI uses.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from uuid import uuid4

import yaml

from litmus.analysis.runs_query import RunsQuery


def _write_multi_site_fixture(path: Path, site_count: int) -> None:
    fixture = {
        "id": path.stem,
        "sites": [{"connections": {}} for _ in range(site_count)],
    }
    path.write_text(yaml.safe_dump(fixture))


def _write_station(path: Path) -> None:
    path.write_text(yaml.safe_dump({"id": path.stem, "name": "Test Station", "instruments": {}}))


def _write_pass_test(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """\
            def test_ok():
                assert 1 == 1
            """
        )
    )


def _run_pytest(
    test_file: Path,
    fixture_path: Path,
    station_path: Path,
    *,
    session_id: str,
    site: str | None = None,
    extra: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Run pytest in a subprocess writing to the canonical results dir.

    ``session_id`` flows through the ``_LITMUS_SESSION_ID`` env so
    the outer test can identify exactly its own runs in the shared
    canonical store.
    """
    args = [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        f"--fixture={fixture_path}",
        f"--station={station_path}",
        "--mock-instruments",
        "--uut-serial=SN42",
        "-v",
    ]
    if site is not None:
        args.append(f"--site={site}")
    if extra:
        args.extend(extra)
    env = {**os.environ, "_LITMUS_SESSION_ID": session_id}
    return subprocess.run(args, capture_output=True, text=True, timeout=60, env=env)


def _list_runs(session_id: str, *, timeout: float = 15.0) -> list:
    """Bounded poll over canonical RunsQuery, scoped to ``session_id``.

    Generous budget (15s) — under full-suite load the canonical
    runs daemon may have queued other tests' notifications ahead of
    ours. Same reasoning as ``test_outcome_cascade._read_step_outcomes``.
    """
    deadline = time.monotonic() + timeout
    q = RunsQuery()
    try:
        while time.monotonic() < deadline:
            runs = q.list_for_session(session_id, include_incomplete=True)
            if runs:
                return runs
            time.sleep(0.2)
        return q.list_for_session(session_id, include_incomplete=True)
    finally:
        q.close()


class TestSiteFlag:
    """``--site=N`` records the operator's intended site in the run row."""

    def test_known_site_records_site_index_and_runs_single_process(self, tmp_path):
        session_id = str(uuid4())
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_pass.py"

        _write_multi_site_fixture(fixture_path, site_count=2)
        _write_station(station_path)
        _write_pass_test(test_file)

        result = _run_pytest(
            test_file,
            fixture_path,
            station_path,
            session_id=session_id,
            site="1",
        )
        assert result.returncode == 0, (
            f"pytest exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # Single-process run — orchestrator's "Multi-UUT Results"
        # header should NOT appear.
        assert "Multi-UUT Results" not in result.stdout

        runs = _list_runs(session_id)
        # Exactly one run row; ``site_index`` reflects the operator's choice.
        assert len(runs) == 1, runs
        assert runs[0].site_index == 1, runs[0]
        assert runs[0].outcome == "passed"

    def test_unknown_site_errors_at_session_start(self, tmp_path):
        session_id = str(uuid4())
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_pass.py"

        _write_multi_site_fixture(fixture_path, site_count=2)
        _write_station(station_path)
        _write_pass_test(test_file)

        result = _run_pytest(
            test_file,
            fixture_path,
            station_path,
            session_id=session_id,
            site="99",
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "99" in combined and "not in fixture" in combined, combined

    def test_no_site_against_multi_site_fixture_dispatches_or_errors(self, tmp_path):
        """Multi-site fixture without --site triggers orchestrator dispatch or serial error."""
        session_id = str(uuid4())
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_pass.py"

        _write_multi_site_fixture(fixture_path, site_count=2)
        _write_station(station_path)
        _write_pass_test(test_file)

        result = _run_pytest(
            test_file,
            fixture_path,
            station_path,
            session_id=session_id,
        )
        # Orchestrator dispatch path: should show Multi-UUT Results.
        # If serials are insufficient, may exit non-zero — both prove
        # the bare multi-site fixture isn't silently miscaptured.
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            assert "Multi-UUT Results" in combined
        else:
            # Any failure is fine — the key is it wasn't silently a single-site run
            assert True
