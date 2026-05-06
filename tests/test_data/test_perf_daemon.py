"""Performance benchmarks for daemon spawn time and data-access latency.

Catches regressions in:
1. Daemon spawn time — from process exec to ready signal.
   Baseline (after __init__ cleanup): ~300ms. Hard cap: 1500ms.

2. Warm query latency — Flight round-trip to already-running daemon.
   Hard cap: 100ms. Baseline: ~3ms.
   A regression to 400ms+ flags the parquet-glob regression.

3. Measurement query latency — TABLE scan, no parquet glob.
   Hard cap: 200ms. Baseline after measurements_persisted TABLE: ~5ms.
   Pre-TABLE baseline: 150-479ms.

Run benchmarks with: pytest tests/test_data/test_perf_daemon.py -v --benchmark-only
Regression guards run in the normal suite (no --benchmark-only needed).
Skip benchmarks in CI: marked @pytest.mark.benchmark
"""

from __future__ import annotations

import json
import os
import signal
import time

import pytest

from litmus.analysis.measurements_query import MeasurementsQuery
from litmus.analysis.runs_query import RunsQuery
from litmus.data import runs_duckdb_manager
from litmus.data._flight_query import _drop_pooled_client
from litmus.data.results_dir import resolve_results_dir

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kill_daemon() -> None:
    """Terminate the runs daemon so the next acquire() spawns a fresh one."""
    state = resolve_results_dir() / "runs" / "_runs_duckdb.json"
    if not state.exists():
        return
    try:
        data = json.loads(state.read_text())
        pid = data.get("pid")
        location = data.get("location")
        if pid:
            os.kill(pid, signal.SIGTERM)
            for _ in range(40):  # up to 2s
                time.sleep(0.05)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
        if location:
            _drop_pooled_client(location)
        # Remove stale state so acquire() doesn't reuse dead location
        state.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError):
        pass


def _ensure_daemon_live() -> str:
    """Return a verified-live daemon location, spawning fresh if needed.

    Drops stale pool entries and verifies the daemon responds before
    returning. Used by both benchmarks and regression guards.
    """
    import pyarrow.flight as flight

    runs_dir = resolve_results_dir() / "runs"
    for _attempt in range(2):
        location = runs_duckdb_manager.acquire(runs_dir)
        _drop_pooled_client(location)
        try:
            client = flight.connect(location)
            client.do_get(flight.Ticket(b"runs\x00SELECT 1")).read_all()
            client.close()
            return location
        except Exception:  # noqa: BLE001 — probe failed; kill and respawn
            _kill_daemon()
    raise RuntimeError("Could not establish a live daemon connection after 2 attempts")


# ---------------------------------------------------------------------------
# Spawn-time benchmark
# ---------------------------------------------------------------------------


@pytest.mark.benchmark(group="daemon-spawn")
def test_daemon_spawn_time(benchmark):
    """Daemon spawn: acquire() wall time after a clean kill.

    Hard cap: 1500ms. Baseline: ~300ms.
    Each benchmark round kills the daemon and measures spawn time.
    """

    def _spawn():
        _kill_daemon()
        runs_dir = resolve_results_dir() / "runs"
        t0 = time.perf_counter()
        location = runs_duckdb_manager.acquire(runs_dir)
        ms = (time.perf_counter() - t0) * 1000
        _drop_pooled_client(location)  # clean pool for next round
        return ms

    result = benchmark(_spawn)
    assert result < 1500, f"Daemon spawn took {result:.0f}ms — exceeds 1500ms hard cap"


# ---------------------------------------------------------------------------
# Warm query latency benchmarks
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def warm_daemon():
    """Ensure the daemon is running and the Flight connection is verified live."""
    _ensure_daemon_live()
    with RunsQuery() as q:
        q.count_by_outcome()  # establish pool connection
    yield


@pytest.mark.benchmark(group="daemon-queries")
def test_runs_count_by_outcome(benchmark, warm_daemon):
    """Warm RunsQuery.count_by_outcome — indexed TABLE scan.

    Hard cap: 100ms. Baseline: ~3ms.
    """

    def _query():
        with RunsQuery() as q:
            return q.count_by_outcome()

    result = benchmark(_query)
    assert result is not None


@pytest.mark.benchmark(group="daemon-queries")
def test_runs_filter_options(benchmark, warm_daemon):
    """Warm get_runs_filter_options — distinct values from indexed TABLE.

    Hard cap: 100ms. Baseline: ~13ms.
    """
    from litmus.ui.shared.services import get_runs_filter_options

    result = benchmark(get_runs_filter_options)
    assert isinstance(result, dict)


@pytest.mark.benchmark(group="daemon-queries")
def test_measurements_summary_counts(benchmark, warm_daemon):
    """Warm MeasurementsQuery.summary_counts — TABLE scan, no parquet glob.

    Hard cap: 200ms. Baseline after measurements_persisted: ~5ms.
    Pre-fix baseline: 150-479ms per query.
    """

    def _query():
        with MeasurementsQuery() as q:
            return q.summary_counts()

    result = benchmark(_query)
    assert result is not None


@pytest.mark.benchmark(group="daemon-queries")
def test_measurements_describe_columns(benchmark, warm_daemon):
    """Warm MeasurementsQuery.describe_columns — schema + dynamic catalog.

    Hard cap: 200ms. Baseline: ~10ms.
    """

    def _query():
        with MeasurementsQuery() as q:
            return q.describe_columns()

    result = benchmark(_query)
    assert isinstance(result, list)


@pytest.mark.benchmark(group="daemon-queries")
def test_measurements_yield_summary(benchmark, warm_daemon):
    """Warm MeasurementsQuery.yield_summary — aggregation over TABLE rows.

    Hard cap: 200ms. Baseline after measurements_persisted: ~5ms.
    Pre-fix baseline: 150-400ms.
    """

    def _query():
        with MeasurementsQuery() as q:
            return q.yield_summary(period="day")

    result = benchmark(_query)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Hard-limit regression guards — always run in the normal test suite
# ---------------------------------------------------------------------------


def test_warm_runs_query_under_100ms():
    """Warm RunsQuery must respond in < 100ms.

    Catches a regression where runs_persisted is reverted to a
    parquet-glob view. Pre-fix: 400ms+. Post-fix: ~3ms.
    """
    _ensure_daemon_live()
    with RunsQuery() as q:
        q.count_by_outcome()  # warm

    t0 = time.perf_counter()
    with RunsQuery() as q:
        q.count_by_outcome()
    ms = (time.perf_counter() - t0) * 1000

    assert ms < 100, (
        f"Warm RunsQuery took {ms:.1f}ms — exceeds 100ms hard cap. "
        "Likely a parquet-glob regression on runs/steps view."
    )


def test_warm_measurements_query_under_200ms():
    """Warm MeasurementsQuery must respond in < 200ms.

    Catches a regression where measurements_persisted is reverted to
    read_parquet(glob, union_by_name=true). Pre-fix: 150-479ms. Post-fix: ~5ms.

    Skipped while the daemon's background backfill is active — during
    backfill, the write_lock is held per file (~50-100ms each), which
    adds lock-wait latency unrelated to query performance. The backfill
    completes within ~30s of a fresh daemon start; steady-state queries
    are ~5ms.
    """
    _ensure_daemon_live()

    # Skip if measurements backfill is still running
    with MeasurementsQuery() as q:
        status = q.backfill_status()
    total = status.get("total", 0)
    completed = status.get("completed", 0)
    if total > 0 and completed < total:
        pytest.skip(
            f"Skipping: daemon backfill in progress ({completed}/{total} files). "
            "Re-run once backfill completes."
        )

    with MeasurementsQuery() as q:
        q.summary_counts()  # warm

    t0 = time.perf_counter()
    with MeasurementsQuery() as q:
        q.summary_counts()
    ms = (time.perf_counter() - t0) * 1000

    assert ms < 200, (
        f"Warm MeasurementsQuery took {ms:.1f}ms — exceeds 200ms hard cap. "
        "Likely regression: measurements view querying parquet glob instead of TABLE. "
        "Pre-fix baseline: 150-479ms; post-fix baseline: ~5ms."
    )
