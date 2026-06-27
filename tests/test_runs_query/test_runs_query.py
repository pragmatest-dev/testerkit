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
from litmus.data.data_dir import resolve_data_dir
from litmus.data.run_store import RunStore
from litmus.data.schemas import RUN_ROW_SCHEMA

_INGEST_TIMEOUT_S = 10.0


def _step_row(
    *,
    run_id: str,
    session_id: str,
    run_started_at: datetime,
    run_ended_at: datetime | None,
    run_outcome: str | None,
    step_started_at: datetime,
    step_ended_at: datetime | None,
    step_index: int = 0,
    step_name: str = "test_step",
    uut_serial: str = "SN001",
    station_id: str = "STA-01",
    test_phase: str = "production",
    part_id: str = "PN-100",
) -> dict:
    """Build one ``record_type='step'`` row in unified RUN_ROW_SCHEMA shape.

    ``run_started_at`` / ``run_ended_at`` / ``run_outcome`` are run-level
    (same for every row of a given run); ``step_started_at`` /
    ``step_ended_at`` are per-step.
    """
    populated: dict = {f.name: None for f in RUN_ROW_SCHEMA}
    populated.update(
        {
            "record_type": "step",
            "run_id": run_id,
            "session_id": session_id,
            "run_started_at": run_started_at,
            "run_ended_at": run_ended_at,
            "run_outcome": run_outcome,
            "step_index": step_index,
            "step_name": step_name,
            "step_path": step_name,
            "parent_path": "",
            "step_started_at": step_started_at,
            "step_ended_at": step_ended_at,
            "step_outcome": run_outcome,
            "step_vector_count": 1,
            "vector_index": 0,
            "measurement_name": None,
            "uut_serial_number": uut_serial,
            "station_id": station_id,
            "test_phase": test_phase,
            "part_id": part_id,
        }
    )
    return populated


def _vector_row(
    *,
    run_id: str,
    session_id: str,
    run_started_at: datetime,
    run_ended_at: datetime,
    run_outcome: str,
    step_started_at: datetime,
    step_ended_at: datetime,
    step_index: int,
    step_name: str,
    measurement_names: list[str],
    uut_serial: str,
) -> dict:
    """Build one ``record_type='vector'`` row carrying nested measurements."""
    populated: dict = {f.name: None for f in RUN_ROW_SCHEMA}
    populated.update(
        {
            "record_type": "vector",
            "run_id": run_id,
            "session_id": session_id,
            "run_started_at": run_started_at,
            "run_ended_at": run_ended_at,
            "run_outcome": run_outcome,
            "step_index": step_index,
            "step_name": step_name,
            "step_path": step_name,
            "parent_path": "",
            "step_started_at": step_started_at,
            "step_ended_at": step_ended_at,
            "vector_index": 0,
            "vector_retry": 0,
            "vector_outcome": run_outcome,
            "uut_serial_number": uut_serial,
            "station_id": "STA-01",
            "test_phase": "production",
            "part_id": "PN-100",
            "measurements": [
                {
                    "name": name,
                    "value": 1.0,
                    "unit": None,
                    "outcome": run_outcome,
                    "timestamp": None,
                    "limit_low": None,
                    "limit_high": None,
                    "limit_nominal": None,
                    "limit_comparator": None,
                    "characteristic_id": None,
                    "spec_ref": None,
                    "uut_pin": None,
                    "fixture_connection": None,
                    "instrument_name": None,
                    "instrument_resource": None,
                    "instrument_channel": None,
                    "ref": None,
                }
                for name in measurement_names
            ],
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
    measurements_per_step: int = 10,
    uut_serial: str = "SN001",
) -> None:
    """Write a unified per-run parquet (n_steps × measurements_per_step rows)
    and notify the daemon to ingest it."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_ended = started + timedelta(seconds=n_steps) + timedelta(minutes=2)
    rows = []
    for step_i in range(n_steps):
        step_start = started + timedelta(seconds=step_i)
        step_end = step_start + timedelta(minutes=2)
        # One step row per (step, vector) — kind='step' carries step
        # context; measurement rows then accumulate under it.
        rows.append(
            _step_row(
                run_id=run_id,
                session_id=session_id,
                run_started_at=started,
                run_ended_at=run_ended,
                run_outcome=outcome,
                step_started_at=step_start,
                step_ended_at=step_end,
                step_index=step_i,
                step_name=f"step_{step_i}",
                uut_serial=uut_serial,
            )
        )
        rows.append(
            _vector_row(
                run_id=run_id,
                session_id=session_id,
                run_started_at=started,
                run_ended_at=run_ended,
                run_outcome=outcome,
                step_started_at=step_start,
                step_ended_at=step_end,
                step_index=step_i,
                step_name=f"step_{step_i}",
                measurement_names=[f"meas_{step_i}_{m}" for m in range(measurements_per_step)],
                uut_serial=uut_serial,
            )
        )
    cols = {f.name: [r[f.name] for r in rows] for f in RUN_ROW_SCHEMA}
    parquet_path = runs_dir / f"{run_id}.parquet"
    pq.write_table(pa.table(cols, schema=RUN_ROW_SCHEMA), parquet_path)
    notifier.notify_new_run(parquet_path)


def _write_in_flight_run(
    runs_dir: Path,
    *,
    run_id: str,
    session_id: str,
    started: datetime,
) -> None:
    """Write a unified parquet for an in-flight run (no ended_at, no outcome).

    Mirrors what the daemon's ``runs_materialized`` table looks like after
    a ``RunStarted`` event lands but before ``RunEnded`` — ``ended_at``
    and ``outcome`` are NULL.
    """
    runs_dir.mkdir(parents=True, exist_ok=True)
    populated = _step_row(
        run_id=run_id,
        session_id=session_id,
        run_started_at=started,
        run_ended_at=None,
        run_outcome=None,
        step_started_at=started,
        step_ended_at=None,
        step_index=0,
        step_name="in_flight_step",
        uut_serial="SN-LIVE",
    )
    cols = {f.name: [populated[f.name]] for f in RUN_ROW_SCHEMA}
    pq.write_table(
        pa.table(cols, schema=RUN_ROW_SCHEMA),
        runs_dir / f"{run_id}.parquet",
    )


def _wait_for_ingest(session_id: str, expected: int) -> list[RunRow]:
    """Poll the canonical daemon until the synthetic runs land.

    The daemon ingests parquet files asynchronously on its own
    cadence; tests poll ``list_for_session`` until the expected
    row count appears. Same eventual-consistency model the UI sees.
    """
    deadline = time.monotonic() + _INGEST_TIMEOUT_S
    while time.monotonic() < deadline:
        with RunsQuery() as q:
            rows = q.list_for_session(session_id, include_incomplete=True)
        if len(rows) >= expected:
            return rows
        time.sleep(0.2)
    with RunsQuery() as q:
        return q.list_for_session(session_id, include_incomplete=True)


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

    runs_dir = resolve_data_dir() / "runs" / "test-runs-query"
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
            uut_serial="SN002",
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
            session_a_rows = q.list_for_session(fixture_data["session_a"])
            session_b_rows = q.list_for_session(fixture_data["session_b"])
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
        """num_steps and num_measurements are aggregated from the run's step/measurement rows."""
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

    runs_dir = resolve_data_dir() / "runs" / "test-runs-query-inflight"
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
            uut_serial="SN002",
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
            rows = q.list_for_session(fixture_data_with_in_flight["session_live"])
        assert rows == []

    def test_include_incomplete_true_surfaces_in_flight(self, fixture_data_with_in_flight):
        """``include_incomplete=True`` returns rows with ended_at=NULL."""
        with RunsQuery() as q:
            rows = q.list_for_session(
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


class TestListForSession:
    def test_returns_session_siblings(self, fixture_data):
        """All runs sharing a session_id come back."""
        with RunsQuery() as q:
            rows = q.list_for_session(fixture_data["session_a"])
        assert {r.run_id for r in rows} == {fixture_data["run_a"], fixture_data["run_b"]}

    def test_unknown_session_returns_empty(self, fixture_data):
        """A session id that no run carries returns an empty list."""
        unknown = str(uuid4())
        with RunsQuery() as q:
            assert q.list_for_session(unknown) == []


class TestDescribeColumns:
    def test_returns_table_columns(self, fixture_data):
        """``DESCRIBE runs`` exposes the expected schema columns."""
        from litmus.analysis.measurement_facets import ColumnSchema

        with RunsQuery() as q:
            schema = q.describe_columns()
        assert isinstance(schema, ColumnSchema)
        names = {c.name for c in schema.fixed}
        assert {"run_id", "session_id", "outcome", "started_at", "num_steps"} <= names
