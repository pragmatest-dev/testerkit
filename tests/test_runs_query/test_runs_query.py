"""Unit tests for RunsQuery — typed run-level queries over the daemon's ``runs`` table."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.analysis.runs_query import RunRow, RunsQuery
from litmus.data.schemas import STEP_SCHEMA


def _step_row(
    *,
    run_id: str,
    session_id: str,
    started: datetime,
    outcome: str,
    step_index: int = 0,
    step_name: str = "test_step",
    measurement_count: int = 1,
    dut_serial: str = "SN001",
    station_id: str = "STA-01",
    test_phase: str = "production",
    product_id: str = "PN-100",
) -> dict:
    """Build one step row matching STEP_SCHEMA — runs table aggregates these."""
    ended = started + timedelta(minutes=2)
    populated: dict = {f.name: None for f in STEP_SCHEMA}
    populated.update(
        {
            "index": step_index,
            "name": step_name,
            "step_path": step_name,
            "outcome": outcome,
            "started_at": started,
            "ended_at": ended,
            "duration_s": (ended - started).total_seconds(),
            "has_measurements": measurement_count > 0,
            "measurement_count": measurement_count,
            "vector_count": 1,
            "run_id": run_id,
            "session_id": session_id,
            "run_started_at": started,
            "run_ended_at": ended,
            "run_outcome": outcome,
            "dut_serial": dut_serial,
            "station_id": station_id,
            "test_phase": test_phase,
            "product_id": product_id,
        }
    )
    return populated


def _write_run(
    runs_dir: Path,
    *,
    run_id: str,
    session_id: str,
    started: datetime,
    outcome: str,
    n_steps: int = 2,
    dut_serial: str = "SN001",
) -> None:
    """Write a ``_steps.parquet`` for one run (multiple step rows)."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        _step_row(
            run_id=run_id,
            session_id=session_id,
            started=started + timedelta(seconds=i),
            outcome=outcome,
            step_index=i,
            step_name=f"step_{i}",
            measurement_count=10,
            dut_serial=dut_serial,
        )
        for i in range(n_steps)
    ]
    cols = {f.name: [r[f.name] for r in rows] for f in STEP_SCHEMA}
    pq.write_table(
        pa.table(cols, schema=STEP_SCHEMA),
        runs_dir / f"{run_id}_steps.parquet",
    )


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    """3 runs across 2 sessions, mixed outcomes."""
    runs_dir = tmp_path / "runs"
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    _write_run(
        runs_dir,
        run_id="run-aaaaaaaa",
        session_id="sess-A",
        started=base,
        outcome="passed",
        n_steps=2,
    )
    _write_run(
        runs_dir,
        run_id="run-bbbbbbbb",
        session_id="sess-A",
        started=base + timedelta(minutes=10),
        outcome="failed",
        n_steps=3,
        dut_serial="SN002",
    )
    _write_run(
        runs_dir,
        run_id="run-cccccccc",
        session_id="sess-B",
        started=base + timedelta(minutes=20),
        outcome="passed",
        n_steps=1,
    )
    return tmp_path


class TestListRecent:
    def test_returns_typed_rows_newest_first(self, results_dir: Path):
        q = RunsQuery(_results_dir=results_dir)
        try:
            rows = q.list_recent(limit=10)
        finally:
            q.close()
        assert len(rows) == 3
        assert all(isinstance(r, RunRow) for r in rows)
        ids = [r.run_id for r in rows]
        assert ids == ["run-cccccccc", "run-bbbbbbbb", "run-aaaaaaaa"]

    def test_respects_limit(self, results_dir: Path):
        q = RunsQuery(_results_dir=results_dir)
        try:
            rows = q.list_recent(limit=2)
        finally:
            q.close()
        assert len(rows) == 2

    def test_aggregates_steps(self, results_dir: Path):
        """num_steps and num_measurements come from aggregating _steps.parquet."""
        q = RunsQuery(_results_dir=results_dir)
        try:
            by_id = {r.run_id: r for r in q.list_recent(limit=10)}
        finally:
            q.close()
        assert by_id["run-aaaaaaaa"].num_steps == 2
        assert by_id["run-aaaaaaaa"].num_measurements == 20  # 2 steps × 10 each
        assert by_id["run-bbbbbbbb"].num_steps == 3
        assert by_id["run-cccccccc"].num_steps == 1


@pytest.fixture()
def results_dir_with_in_flight(tmp_path: Path) -> Path:
    """Same fixture as ``results_dir`` plus one in-flight run.

    The in-flight run is written as a parquet with ``run_ended_at``
    and ``run_outcome`` set to NULL — parquet ingest then produces a
    runs row with ``ended_at IS NULL``, exactly the shape the
    streaming UPSERT path would produce after a ``RunStarted`` event.
    """
    runs_dir = tmp_path / "runs"
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    _write_run(
        runs_dir,
        run_id="run-aaaaaaaa",
        session_id="sess-A",
        started=base,
        outcome="passed",
        n_steps=2,
    )
    _write_run(
        runs_dir,
        run_id="run-bbbbbbbb",
        session_id="sess-A",
        started=base + timedelta(minutes=10),
        outcome="failed",
        n_steps=3,
        dut_serial="SN002",
    )
    _write_run(
        runs_dir,
        run_id="run-cccccccc",
        session_id="sess-B",
        started=base + timedelta(minutes=20),
        outcome="passed",
        n_steps=1,
    )
    _write_in_flight_run(
        runs_dir,
        run_id="run-in-flight",
        session_id="sess-LIVE",
        started=base + timedelta(minutes=30),
    )
    return tmp_path


def _write_in_flight_run(
    runs_dir: Path,
    *,
    run_id: str,
    session_id: str,
    started: datetime,
) -> None:
    """Write a steps parquet for an in-flight run (no ended_at, no outcome).

    Mirrors what the daemon's ``runs`` table looks like after a
    ``RunStarted`` event: ``ended_at IS NULL``, ``outcome IS NULL``.
    """
    runs_dir.mkdir(parents=True, exist_ok=True)
    populated: dict = {f.name: None for f in STEP_SCHEMA}
    populated.update(
        {
            "index": 0,
            "name": "in_flight_step",
            "step_path": "in_flight_step",
            "outcome": None,
            "started_at": started,
            "ended_at": None,
            "duration_s": None,
            "has_measurements": False,
            "measurement_count": 0,
            "vector_count": 1,
            "run_id": run_id,
            "session_id": session_id,
            "run_started_at": started,
            "run_ended_at": None,
            "run_outcome": None,
            "dut_serial": "SN-LIVE",
            "station_id": "STA-01",
            "test_phase": "production",
            "product_id": "PN-100",
        }
    )
    cols = {f.name: [populated[f.name]] for f in STEP_SCHEMA}
    pq.write_table(
        pa.table(cols, schema=STEP_SCHEMA),
        runs_dir / f"{run_id}_steps.parquet",
    )


class TestIncludeIncomplete:
    """``include_incomplete`` switch — surfaces or hides in-flight rows.

    A partial run row has ``ended_at IS NULL`` / ``outcome IS NULL``.
    ``list_recent`` defaults to excluding them; UI list pages opt in
    to see live runs.
    """

    def test_default_excludes_in_flight(self, results_dir_with_in_flight: Path):
        """Default ``include_incomplete=False`` skips rows with ended_at=NULL."""
        q = RunsQuery(_results_dir=results_dir_with_in_flight)
        try:
            ids = [r.run_id for r in q.list_recent(limit=10)]
        finally:
            q.close()
        assert "run-in-flight" not in ids
        assert set(ids) == {"run-aaaaaaaa", "run-bbbbbbbb", "run-cccccccc"}

    def test_include_incomplete_true_surfaces_in_flight(self, results_dir_with_in_flight: Path):
        """``include_incomplete=True`` returns rows with ended_at=NULL."""
        q = RunsQuery(_results_dir=results_dir_with_in_flight)
        try:
            rows = q.list_recent(limit=10, include_incomplete=True)
        finally:
            q.close()
        ids = [r.run_id for r in rows]
        assert "run-in-flight" in ids
        live = next(r for r in rows if r.run_id == "run-in-flight")
        assert live.ended_at is None
        assert live.outcome is None


class TestGet:
    def test_id_prefix_match(self, results_dir: Path):
        q = RunsQuery(_results_dir=results_dir)
        try:
            run = q.get("run-aaaa")
        finally:
            q.close()
        assert run is not None
        assert run.run_id == "run-aaaaaaaa"
        assert run.outcome == "passed"

    def test_unknown_returns_none(self, results_dir: Path):
        q = RunsQuery(_results_dir=results_dir)
        try:
            assert q.get("nope-xxxx") is None
        finally:
            q.close()


class TestFindForSession:
    def test_returns_session_siblings(self, results_dir: Path):
        q = RunsQuery(_results_dir=results_dir)
        try:
            rows = q.find_for_session("sess-A")
        finally:
            q.close()
        assert {r.run_id for r in rows} == {"run-aaaaaaaa", "run-bbbbbbbb"}

    def test_unknown_session_returns_empty(self, results_dir: Path):
        q = RunsQuery(_results_dir=results_dir)
        try:
            assert q.find_for_session("nope") == []
        finally:
            q.close()


class TestCountByOutcome:
    def test_rollup(self, results_dir: Path):
        q = RunsQuery(_results_dir=results_dir)
        try:
            counts = q.count_by_outcome()
        finally:
            q.close()
        assert counts == {"passed": 2, "failed": 1}


class TestDescribeColumns:
    def test_returns_table_columns(self, results_dir: Path):
        q = RunsQuery(_results_dir=results_dir)
        try:
            cols = q.describe_columns()
        finally:
            q.close()
        names = {c["column_name"] for c in cols}
        assert {"run_id", "session_id", "outcome", "started_at", "num_steps"} <= names
