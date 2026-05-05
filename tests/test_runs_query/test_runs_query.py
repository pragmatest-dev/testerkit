"""Unit tests for RunsQuery — typed run-level queries over the daemon's ``runs`` table.

Tests use the canonical singleton runs daemon (every Litmus
process shares it). Each fixture writes synthetic step parquets
into the canonical runs dir under unique uuid run_ids /
session_ids, then asserts via session-scoped queries so the
shared store's other rows don't interfere.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.analysis.runs_query import RunRow, RunsQuery
from litmus.data.results_dir import resolve_results_dir
from litmus.data.run_store import RunStore
from litmus.data.schemas import STEP_SCHEMA

_INGEST_TIMEOUT_S = 10.0


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
    notifier: RunStore,
    *,
    run_id: str,
    session_id: str,
    started: datetime,
    outcome: str,
    n_steps: int = 2,
    dut_serial: str = "SN001",
) -> None:
    """Write a ``_steps.parquet`` and notify the canonical daemon to ingest it."""
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
    parquet_path = runs_dir / f"{run_id}_steps.parquet"
    pq.write_table(pa.table(cols, schema=STEP_SCHEMA), parquet_path)
    # ``notify_new_run`` keys on the regular parquet path; it
    # auto-includes the sibling ``_steps.parquet``. Passing the
    # steps path directly tells the daemon to do_put-ingest it.
    notifier.notify_new_run(parquet_path.with_name(f"{run_id}.parquet"))


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


def _wait_for_ingest(session_id: str, expected: int) -> list[RunRow]:
    """Poll the canonical daemon until the synthetic runs land.

    The daemon ingests parquet files asynchronously on its own
    cadence; tests poll ``find_for_session`` until the expected
    row count appears. Same eventual-consistency model the UI sees.
    """
    deadline = time.monotonic() + _INGEST_TIMEOUT_S
    while time.monotonic() < deadline:
        with RunsQuery() as q:
            rows = q.find_for_session(session_id, include_incomplete=True)
        if len(rows) >= expected:
            return rows
        time.sleep(0.2)
    with RunsQuery() as q:
        return q.find_for_session(session_id, include_incomplete=True)


@pytest.fixture(scope="module")
def fixture_data():
    """3 runs across 2 sessions with unique uuids; written to canonical.

    Module-scoped so the daemon ingests once for the whole file.
    Uses uuid4 IDs so the synthetic data is identifiable in the
    shared canonical store and doesn't collide with real runs.
    """
    session_a = str(uuid4())
    session_b = str(uuid4())
    run_a = str(uuid4())
    run_b = str(uuid4())
    run_c = str(uuid4())

    runs_dir = resolve_results_dir() / "runs" / "test-runs-query"
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    notifier = RunStore()
    try:
        _write_run(
            runs_dir,
            notifier,
            run_id=run_a,
            session_id=session_a,
            started=base,
            outcome="passed",
            n_steps=2,
        )
        _write_run(
            runs_dir,
            notifier,
            run_id=run_b,
            session_id=session_a,
            started=base + timedelta(minutes=10),
            outcome="failed",
            n_steps=3,
            dut_serial="SN002",
        )
        _write_run(
            runs_dir,
            notifier,
            run_id=run_c,
            session_id=session_b,
            started=base + timedelta(minutes=20),
            outcome="passed",
            n_steps=1,
        )
    finally:
        notifier.close()

    # Wait for both sessions' rows to land before any test runs.
    _wait_for_ingest(session_a, expected=2)
    _wait_for_ingest(session_b, expected=1)

    return {
        "session_a": session_a,
        "session_b": session_b,
        "run_a": run_a,
        "run_b": run_b,
        "run_c": run_c,
    }


class TestListRecent:
    def test_returns_typed_rows_newest_first(self, fixture_data):
        """Within this fixture's session, runs come back newest-first."""
        with RunsQuery() as q:
            session_a_rows = q.find_for_session(fixture_data["session_a"])
            session_b_rows = q.find_for_session(fixture_data["session_b"])
        all_rows = sorted(
            session_a_rows + session_b_rows,
            key=lambda r: r.started_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        assert len(all_rows) == 3
        assert all(isinstance(r, RunRow) for r in all_rows)
        ids = [r.run_id for r in all_rows]
        assert ids == [fixture_data["run_c"], fixture_data["run_b"], fixture_data["run_a"]]

    def test_respects_limit(self, fixture_data):
        """``list_recent(limit=N)`` caps the result at N rows."""
        with RunsQuery() as q:
            rows = q.list_recent(limit=2)
        assert len(rows) <= 2

    def test_aggregates_steps(self, fixture_data):
        """num_steps and num_measurements come from aggregating _steps.parquet."""
        with RunsQuery() as q:
            run_a = q.get(fixture_data["run_a"])
            run_b = q.get(fixture_data["run_b"])
            run_c = q.get(fixture_data["run_c"])
        assert run_a is not None
        assert run_a.num_steps == 2
        assert run_a.num_measurements == 20  # 2 steps × 10 each
        assert run_b is not None
        assert run_b.num_steps == 3
        assert run_c is not None
        assert run_c.num_steps == 1


@pytest.fixture(scope="module")
def fixture_data_with_in_flight():
    """Same as ``fixture_data`` plus one in-flight run.

    The in-flight run's parquet has ``run_ended_at`` and
    ``run_outcome`` NULL — same shape the streaming UPSERT path
    produces after a ``RunStarted`` event.
    """
    session_a = str(uuid4())
    session_b = str(uuid4())
    session_live = str(uuid4())
    run_a = str(uuid4())
    run_b = str(uuid4())
    run_c = str(uuid4())
    run_live = str(uuid4())

    runs_dir = resolve_results_dir() / "runs" / "test-runs-query-inflight"
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    notifier = RunStore()
    try:
        _write_run(
            runs_dir,
            notifier,
            run_id=run_a,
            session_id=session_a,
            started=base,
            outcome="passed",
            n_steps=2,
        )
        _write_run(
            runs_dir,
            notifier,
            run_id=run_b,
            session_id=session_a,
            started=base + timedelta(minutes=10),
            outcome="failed",
            n_steps=3,
            dut_serial="SN002",
        )
        _write_run(
            runs_dir,
            notifier,
            run_id=run_c,
            session_id=session_b,
            started=base + timedelta(minutes=20),
            outcome="passed",
            n_steps=1,
        )
        _write_in_flight_run(
            runs_dir,
            run_id=run_live,
            session_id=session_live,
            started=base + timedelta(minutes=30),
        )
        notifier.notify_new_run(runs_dir / f"{run_live}.parquet")
    finally:
        notifier.close()
    _wait_for_ingest(session_a, expected=2)
    _wait_for_ingest(session_b, expected=1)
    _wait_for_ingest(session_live, expected=1)

    return {
        "session_a": session_a,
        "session_b": session_b,
        "session_live": session_live,
        "run_a": run_a,
        "run_b": run_b,
        "run_c": run_c,
        "run_live": run_live,
    }


class TestIncludeIncomplete:
    """``include_incomplete`` switch — surfaces or hides in-flight rows."""

    def test_default_excludes_in_flight(self, fixture_data_with_in_flight):
        """``include_incomplete=False`` (default) skips ended_at=NULL rows."""
        with RunsQuery() as q:
            rows = q.find_for_session(fixture_data_with_in_flight["session_live"])
        assert rows == []

    def test_include_incomplete_true_surfaces_in_flight(self, fixture_data_with_in_flight):
        """``include_incomplete=True`` returns rows with ended_at=NULL."""
        with RunsQuery() as q:
            rows = q.find_for_session(
                fixture_data_with_in_flight["session_live"],
                include_incomplete=True,
            )
        assert len(rows) == 1
        assert rows[0].run_id == fixture_data_with_in_flight["run_live"]
        assert rows[0].ended_at is None
        assert rows[0].outcome is None


class TestGet:
    def test_id_prefix_match(self, fixture_data):
        """``get(prefix)`` matches by leading characters."""
        prefix = fixture_data["run_a"][:8]
        with RunsQuery() as q:
            run = q.get(prefix)
        assert run is not None
        assert run.run_id == fixture_data["run_a"]
        assert run.outcome == "passed"

    def test_unknown_returns_none(self, fixture_data):
        """An id that matches nothing returns None."""
        with RunsQuery() as q:
            assert q.get("00000000-no-such-run") is None


class TestFindForSession:
    def test_returns_session_siblings(self, fixture_data):
        """All runs sharing a session_id come back."""
        with RunsQuery() as q:
            rows = q.find_for_session(fixture_data["session_a"])
        assert {r.run_id for r in rows} == {fixture_data["run_a"], fixture_data["run_b"]}

    def test_unknown_session_returns_empty(self, fixture_data):
        """A session id that no run carries returns an empty list."""
        unknown = str(uuid4())
        with RunsQuery() as q:
            assert q.find_for_session(unknown) == []


class TestDescribeColumns:
    def test_returns_table_columns(self, fixture_data):
        """``DESCRIBE runs`` exposes the expected schema columns."""
        with RunsQuery() as q:
            cols = q.describe_columns()
        names = {c["column_name"] for c in cols}
        assert {"run_id", "session_id", "outcome", "started_at", "num_steps"} <= names
