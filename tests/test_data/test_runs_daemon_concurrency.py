"""Regression tests for the runs daemon's concurrency guarantees.

Three failure modes these tests catch:

1. Foreground-ingest spawn timeout — synchronous ingest before the
   daemon signals ready blows past ``_SPAWN_TIMEOUT`` (30s) on a fresh
   index with many parquets.

2. Background-ingest deadlock — a background ingest thread on a
   separate connection holds DuckDB write transactions while a Flight
   query handler touches the main connection. Two-connection contention
   on DuckDB's global catalog lock under GIL deadlocks the daemon, and
   any Flight query hangs indefinitely.

3. Cascade-delete race — ``_ingest_parquet_files`` prunes ``_ingested``
   rows whose file is "gone from disk", comparing against a disk-list
   snapshot taken at the START of the sweep. A run ingested concurrently
   (a ``notify_new_run``/``do_put`` mid-sweep) is genuinely on disk but
   used to get wrongly deleted, because the OLD prune read ``_ingested``
   AFTER the concurrent ingest yet compared it against that pre-ingest
   snapshot. See the tests under "Cascade-delete race" below.

(1) and (2) are guarded by sharing a single connection + single lock
between the Flight server and the background sweep — see ``daemon_run``
and ``_ingest_parquet_files`` in ``_runs_duckdb_daemon.py``. (3) is
guarded by FREEZING the prune's candidate set to the ``_ingested`` rows
present at sweep START, so a run a concurrent notify adds mid-sweep is
never a deletion candidate.
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
from litmus.data._runs_duckdb_daemon import _ingest_one_file, _ingest_parquet_files, _open_index
from litmus.data.data_dir import resolve_data_dir
from litmus.data.schemas import RUN_ROW_SCHEMA


def _step_row(
    *, run_id: str, session_id: str, started: datetime, uut_serial: str = "SN001"
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
            "step_started_at": started,
            "step_ended_at": ended,
            "step_outcome": "passed",
            "vector_index": 0,
            "measurement_name": None,
            "run_id": run_id,
            "session_id": session_id,
            "run_started_at": started,
            "run_ended_at": ended,
            "run_outcome": "passed",
            "uut_serial_number": uut_serial,
            "station_id": "test-station",
            "test_phase": "production",
            "part_id": "PN-100",
        }
    )
    return populated


def _write_run_parquet(runs_dir: Path, *, run_id: str, started: datetime) -> Path:
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
        _write_run_parquet(
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
        _write_run_parquet(
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


# ── Cascade-delete race (pre-existing bug, made routine by #53 P1) ──────
#
# ``_ingest_parquet_files`` snapshots the on-disk parquet list via
# ``runs_dir.rglob("*.parquet")`` at the START of the sweep, then, after
# ingesting, deletes ``_ingested`` rows whose file is "gone from disk". A
# run that lands concurrently (a ``notify_new_run``/``do_put`` mid-sweep,
# via ``_on_put`` -> ``_ingest_one_file`` under the same ``lock``) is present
# on disk AND in ``_ingested`` but absent from the stale disk snapshot purely
# because it didn't exist yet when the snapshot was taken — and the OLD code
# wrongly cascade-deleted it (index data loss: the run vanishes from queries
# until a later sweep re-ingests it).
#
# The fix FREEZES the prune's candidate set to the ``_ingested`` rows read at
# sweep START (before this sweep's own — and any concurrent — ingest adds to
# the table). A run added mid-sweep is never in that frozen set, so it is
# never a deletion candidate and live/warm serving is never disrupted. It is a
# plain set-difference — no per-candidate work, same cost as the original
# prune. (An earlier version additionally re-checked ``Path.exists()`` per
# candidate at delete time; that only guarded a rare ``rglob``-transient-miss
# the original never guarded either, and the per-candidate stat burst perturbed
# unrelated timing-sensitive tests under full-suite load, so it was dropped —
# the freeze alone closes the race.)
#
# These tests use ``_open_index`` directly (a plain DuckDB file, no Flight
# server, no daemon process) — same pattern as ``test_runs_index_selfheal.py``
# — so ``tmp_path`` is safe here; nothing spawns a daemon.


def _materialized_count(conn) -> int:
    row = conn.execute("SELECT count(*) FROM runs_materialized").fetchone()
    assert row is not None
    return int(row[0])


def _ingested_paths(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT path FROM _ingested").fetchall()}


def test_cascade_delete_still_prunes_genuinely_removed_files(tmp_path: Path) -> None:
    """The fix must NOT weaken the legitimate cascade-delete: a file
    recorded in ``_ingested`` whose parquet was truly deleted from disk
    before the sweep runs must still be pruned."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    base = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)

    _write_run_parquet(runs_dir, run_id=f"gone-a-{uuid4()}", started=base)
    path_b = _write_run_parquet(
        runs_dir, run_id=f"gone-b-{uuid4()}", started=base + timedelta(seconds=1)
    )

    idx = tmp_path / "_index.duckdb"
    conn, _ = _open_index(idx)
    lock = threading.Lock()
    try:
        _ingest_one_file(conn, path_b, path_b.stat())
        assert _materialized_count(conn) == 1

        path_b.unlink()  # genuinely removed from disk before the sweep runs

        _ingest_parquet_files(conn, runs_dir, lock)

        assert _materialized_count(conn) == 1  # only A remains
        assert str(path_b) not in _ingested_paths(conn)
    finally:
        conn.close()


def test_concurrent_notify_during_sweep_is_queryable_after_sweep(tmp_path: Path) -> None:
    """Exercises the actual concurrent path (not a monkeypatched snapshot):
    a ``notify_new_run``-equivalent ingest for a brand-new run lands via the
    ``on_ingested`` callback, which fires in the real gap between the
    sweep's ingest loop and its cascade-delete pass — the same window a
    genuine concurrent ``_on_put`` contends ``lock`` for.

    Asserts the STRONGER, positive property the fix must preserve: the
    notified run is directly queryable (by ``run_id``, in
    ``runs_materialized``) once the sweep completes — not merely "was not
    deleted". Immediate/warm serving of a concurrently-notified run must
    survive this sweep unimpeded (rule 1 — the candidate set is frozen at
    sweep start, so this run is never even considered for deletion — and
    rule 2 — a fresh existence check at delete time — together guarantee
    this without delaying or serializing the notify itself).
    """
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    base = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)

    _write_run_parquet(runs_dir, run_id=f"concur-a-{uuid4()}", started=base)

    idx = tmp_path / "_index.duckdb"
    conn, _ = _open_index(idx)
    lock = threading.Lock()
    run_id_b = f"concur-b-{uuid4()}"
    landed: list[Path] = []
    try:

        def on_ingested(_run_ids: list[str]) -> None:
            # Stands in for a concurrent notify_new_run/do_put: writes and
            # ingests a brand-new run AFTER this sweep's disk snapshot (and
            # its frozen _ingested candidate set) were taken, but BEFORE its
            # cascade-delete pass runs.
            path_b = _write_run_parquet(
                runs_dir, run_id=run_id_b, started=base + timedelta(seconds=1)
            )
            with lock:
                _ingest_one_file(conn, path_b, path_b.stat())
            landed.append(path_b)

        _ingest_parquet_files(conn, runs_dir, lock, on_ingested=on_ingested)

        assert landed, "on_ingested callback never fired — test setup invalid"
        assert _materialized_count(conn) == 2
        assert str(landed[0]) in _ingested_paths(conn)
        # The positive, "queryable" assertion: the notified run resolves by
        # run_id in the materialized table the query layer reads.
        row = conn.execute(
            "SELECT run_id FROM runs_materialized WHERE run_id = ?", [run_id_b]
        ).fetchone()
        assert row is not None, f"run {run_id_b} not queryable after sweep completed"
    finally:
        conn.close()
