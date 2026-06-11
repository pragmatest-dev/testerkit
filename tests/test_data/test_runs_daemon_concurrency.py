"""Regression tests for the runs daemon's concurrency guarantees.

Two failure modes these tests catch:

1. Foreground-ingest spawn timeout — synchronous ingest before the
   daemon signals ready blows past ``_SPAWN_TIMEOUT`` (30s) on a fresh
   index with many parquets.

2. Background-ingest deadlock — a background ingest thread on a
   separate connection holds DuckDB write transactions while a Flight
   query handler touches the main connection. Two-connection contention
   on DuckDB's global catalog lock under GIL deadlocks the daemon, and
   any Flight query hangs indefinitely.

Both are guarded by sharing a single connection + single lock between
the Flight server and the background sweep — see ``daemon_run`` and
``_ingest_parquet_files`` in ``_runs_duckdb_daemon.py``.
"""

from __future__ import annotations

import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from litmus.analysis.runs_query import RunsQuery
from litmus.data import runs_duckdb_manager
from litmus.data.data_dir import resolve_data_dir
from litmus.data.schemas import RUN_ROW_SCHEMA


def _step_row(
    *, run_id: str, session_id: str, started: datetime, dut_serial: str = "SN001"
) -> dict:
    """Minimal ``record_type='step'`` row in unified RUN_ROW_SCHEMA shape."""
    ended = started + timedelta(seconds=1)
    populated: dict = {f.name: None for f in RUN_ROW_SCHEMA}
    populated.update(
        {
            "record_type": "step",
            "step_index": 0,
            "step_name": "step_a",
            "step_path": "step_a",
            "parent_path": "",
            "step_started_at": started,
            "step_ended_at": ended,
            "step_outcome": "passed",
            "step_vector_count": 1,
            "vector_index": 0,
            "measurement_name": None,
            "run_id": run_id,
            "session_id": session_id,
            "run_started_at": started,
            "run_ended_at": ended,
            "run_outcome": "passed",
            "dut_serial": dut_serial,
            "station_id": "test-station",
            "test_phase": "production",
            "product_id": "PN-100",
        }
    )
    return populated


def _write_steps_parquet(runs_dir: Path, *, run_id: str, started: datetime) -> Path:
    """Write a unified ``{run_id}.parquet`` with one step-summary row.
    Skips ``notify_new_run`` so the daemon picks it up via background sweep."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    row = _step_row(run_id=run_id, session_id=str(uuid4()), started=started)
    cols = {f.name: [row[f.name]] for f in RUN_ROW_SCHEMA}
    path = runs_dir / f"{run_id}.parquet"
    pq.write_table(pa.table(cols, schema=RUN_ROW_SCHEMA), path)
    return path


def test_fresh_daemon_spawns_within_timeout(tmp_path: Path) -> None:
    """A daemon with an empty index and 50 parquets on disk must come up
    within 5 seconds — well under the 30s spawn timeout. Guards against
    re-introducing foreground ingest on the spawn path.

    Uses an isolated, per-test ``runs_dir`` (NOT the canonical) so the
    fresh-spawn doesn't trash the in-process state and background-ingest
    queue of the canonical daemon other tests share. The companion test
    ``test_query_during_ingest_does_not_hang`` documents (line 167-171)
    why wiping the canonical mid-suite is unsafe — this test honored
    that constraint by sandboxing instead.

    The forbidden ``RunStore(_data_dir=tmp_path)`` rule (per-test
    daemons → pids cgroup exhaustion at ~30 such tests) is about
    *accidental* per-test daemons. This test inherently spawns a daemon
    to time the spawn path; it's the one legitimate per-test daemon and
    is bounded by file (single test), not multiplied across the suite.
    """
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    for i in range(50):
        _write_steps_parquet(
            runs_dir,
            run_id=f"fresh-spawn-{i:03d}-{uuid4()}",
            started=base + timedelta(seconds=i),
        )

    t0 = time.perf_counter()
    location = runs_duckdb_manager.acquire(runs_dir)
    elapsed = time.perf_counter() - t0
    try:
        assert location.startswith("grpc://"), location
        assert elapsed < 5.0, f"daemon spawn took {elapsed:.1f}s (expected < 5s)"
    finally:
        runs_duckdb_manager.release(runs_dir)


def test_query_during_ingest_does_not_hang():
    """While the daemon's background ingest is processing files, fire 10
    concurrent ``RunsQuery.list_recent()`` calls and assert each returns
    within 2 seconds. Guards against the catalog deadlock that occurred
    when the background ingest opened its own DuckDB connection.
    """
    runs_dir = resolve_data_dir() / "runs" / "test-concurrent-ingest"
    if runs_dir.exists():
        shutil.rmtree(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)

    base = datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC)
    for i in range(50):
        _write_steps_parquet(
            runs_dir,
            run_id=f"concurrent-{i:03d}-{uuid4()}",
            started=base + timedelta(seconds=i),
        )

    canonical_runs = resolve_data_dir() / "runs"

    # Acquire the daemon without wiping — new parquets will be picked up by
    # the background ingest sweep on the running daemon. Wiping would kill
    # the daemon and force a fresh spawn, which disrupts the LiveRunsSubscriber
    # attachment (5s poll cycle) and breaks subsequent tests that depend on
    # in-flight event delivery.
    runs_duckdb_manager.acquire(canonical_runs)
    try:
        results: list[float] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(10)

        def _query() -> None:
            barrier.wait()  # release all queries simultaneously
            t = time.perf_counter()
            try:
                with RunsQuery() as q:
                    q.list_recent(limit=20, include_incomplete=True)
            except Exception as exc:  # noqa: BLE001 — capture for assertion
                errors.append(exc)
                return
            results.append(time.perf_counter() - t)

        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = [ex.submit(_query) for _ in range(10)]
            for f in futures:
                f.result(timeout=10)

        assert not errors, f"queries raised: {errors}"
        assert len(results) == 10
        slowest = max(results)
        assert slowest < 2.0, (
            f"slowest query took {slowest:.2f}s during ingest (expected < 2s); all={results}"
        )
    finally:
        runs_duckdb_manager.release(canonical_runs)
