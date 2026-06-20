"""Performance benchmarks for daemon spawn time and data-access latency.

Catches regressions in:
1. Daemon spawn time — from process exec to ready signal.
   Baseline (after __init__ cleanup): ~300ms. Hard cap: 1500ms.

2. Warm query latency — Flight round-trip to already-running daemon.
   Hard cap: 100ms. Baseline: ~3ms.
   A regression to 400ms+ flags the parquet-glob regression.

3. Measurement query latency — TABLE scan, no parquet glob.
   Hard cap: 200ms. Baseline after measurements_materialized TABLE: ~5ms.
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
from collections.abc import Callable
from typing import Any

import pytest

from litmus.analysis.measurement_facets import FilterSet
from litmus.analysis.measurements_query import MeasurementsQuery
from litmus.analysis.runs_query import RunsQuery
from litmus.data import runs_duckdb_manager
from litmus.data._flight_query import _drop_pooled_client
from litmus.data.data_dir import resolve_data_dir

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kill_daemon() -> None:
    """Terminate the runs daemon so the next acquire() spawns a fresh one."""
    state = resolve_data_dir() / "runs" / "_runs_duckdb.json"
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

    Uses the production probe function to avoid duplicating logic.
    """
    from litmus.data._flight_query import probe_sql

    runs_dir = resolve_data_dir() / "runs"
    for _attempt in range(2):
        location = runs_duckdb_manager.acquire(runs_dir)
        _drop_pooled_client(location)
        if probe_sql(location, "runs"):
            return location
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
        runs_dir = resolve_data_dir() / "runs"
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


@pytest.mark.benchmark(group="daemon-queries", warmup=True, min_rounds=30, disable_gc=True)
def test_runs_count_by_outcome(benchmark, warm_daemon):
    """Warm RunsQuery.count_by_outcome — indexed TABLE scan.

    Hard cap: 100ms. Baseline: ~3ms.
    """

    def _query():
        with RunsQuery() as q:
            return q.count_by_outcome()

    result = benchmark(_query)
    assert result is not None


@pytest.mark.benchmark(group="daemon-queries", warmup=True, min_rounds=30, disable_gc=True)
def test_runs_filter_options(benchmark, warm_daemon):
    """Warm get_runs_filter_options — distinct values from indexed TABLE.

    Hard cap: 100ms. Baseline: ~13ms.
    """
    from litmus.ui.shared.services import get_runs_filter_options

    result = benchmark(get_runs_filter_options)
    assert isinstance(result, dict)


@pytest.mark.benchmark(group="daemon-queries", warmup=True, min_rounds=30, disable_gc=True)
def test_measurements_summary_counts(benchmark, warm_daemon):
    """Warm MeasurementsQuery.summary_counts — TABLE scan, no parquet glob.

    Hard cap: 200ms. Baseline after measurements_materialized: ~5ms.
    Pre-fix baseline: 150-479ms per query.
    """

    def _query():
        with MeasurementsQuery() as q:
            return q.summary_counts()

    result = benchmark(_query)
    assert result is not None


@pytest.mark.benchmark(group="daemon-queries", warmup=True, min_rounds=30, disable_gc=True)
def test_measurements_describe_columns(benchmark, warm_daemon):
    """Warm MeasurementsQuery.describe_columns — schema + dynamic catalog.

    Hard cap: 200ms. Baseline: ~10ms.
    """

    def _query():
        with MeasurementsQuery() as q:
            return q.describe_columns()

    result = benchmark(_query)
    assert isinstance(result, list)


@pytest.mark.benchmark(group="daemon-queries", warmup=True, min_rounds=30, disable_gc=True)
def test_measurements_yield_summary(benchmark, warm_daemon):
    """Warm MeasurementsQuery.yield_summary — aggregation over TABLE rows.

    Hard cap: 200ms. Baseline after measurements_materialized: ~5ms.
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


def _sample_min_ms(query_fn: Callable[[], Any], rounds: int = 11) -> float:
    """Take ``rounds`` warm samples; return the MIN in ms.

    Single-shot timers (one warmup + one measure) flake under suite
    load — GC, scheduler, IO contention from concurrent daemons all
    spike a single call past the hard cap even when steady-state
    performance is fine. The minimum across N samples filters those
    transient spikes out: the floor reflects the actual work, the
    spikes don't pull it up. Same sampling discipline the
    ``benchmark`` fixture uses below, hand-rolled here because we
    want a hard-cap assertion rather than a benchmark report.
    """
    samples: list[float] = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        query_fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return min(samples)


def test_warm_runs_query_under_100ms():
    """Warm RunsQuery must respond in < 100ms.

    Catches a regression where runs_materialized is reverted to a
    parquet-glob view. Pre-fix: 400ms+. Post-fix: ~3ms.
    """
    _ensure_daemon_live()
    with RunsQuery() as q:
        q.count_by_outcome()  # warm

    def _query() -> None:
        with RunsQuery() as q:
            q.count_by_outcome()

    ms = _sample_min_ms(_query)

    assert ms < 100, (
        f"Warm RunsQuery min over 11 samples was {ms:.1f}ms — exceeds 100ms hard cap. "
        "Likely a parquet-glob regression on runs/steps view."
    )


def test_warm_measurements_query_under_200ms():
    """Warm MeasurementsQuery must respond in < 200ms.

    Catches a regression where measurements_materialized is reverted to
    read_parquet(glob, union_by_name=true). Pre-fix: 150-479ms. Post-fix: ~5ms.
    """
    _ensure_daemon_live()

    with MeasurementsQuery() as q:
        q.summary_counts()  # warm

    def _query() -> None:
        with MeasurementsQuery() as q:
            q.summary_counts()

    ms = _sample_min_ms(_query)

    assert ms < 200, (
        f"Warm MeasurementsQuery min over 11 samples was {ms:.1f}ms — exceeds 200ms hard cap. "
        "Likely regression: measurements view querying parquet glob instead of TABLE. "
        "Pre-fix baseline: 150-479ms; post-fix baseline: ~5ms."
    )


# ---------------------------------------------------------------------------
# Parametric (dynamic-axis) query benchmarks — the EAV-join path the explore
# UI uses. Seeded via the REAL write+ingest path (save_test_run ->
# notify_new_run -> daemon UNNEST), NOT an in-memory store, so the benchmark
# measures the production measurements_dynamic join under a unique part.
# ---------------------------------------------------------------------------

_PARAM_RUNS = int(os.environ.get("LITMUS_PERF_RUNS", "30"))
_PARAM_STEPS = int(os.environ.get("LITMUS_PERF_STEPS", "10"))  # one vector per step
_PARAM_MEAS = int(os.environ.get("LITMUS_PERF_MEAS", "10"))
_PARAM_EXPECTED = _PARAM_RUNS * _PARAM_STEPS * _PARAM_MEAS  # nested measurements


@pytest.fixture(scope="module")
def parametric_dataset() -> str:
    """Seed a controlled dynamic-axis dataset; return its part_id.

    Each vector carries conditions (``temperature`` / ``vin``) so the
    parametric query exercises the ``measurements_dynamic`` EAV join.
    Written through the production path (``save_test_run`` ->
    ``notify_new_run`` -> daemon ingest); scoped to a unique part so
    ambient canonical data doesn't dilute the measurement.
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.models import UUT, Measurement, Outcome, TestRun, TestStep, TestVector
    from litmus.data.run_store import RunStore

    part = f"perf-param-{uuid4().hex[:8]}"
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 6, 1, 12, 1, 0, tzinfo=UTC)
    backend = ParquetBackend(data_dir=resolve_data_dir())
    notifier = RunStore()
    try:
        for r in range(_PARAM_RUNS):
            run = TestRun(
                id=uuid4(),
                started_at=t0,
                ended_at=t1,
                uut=UUT(serial=f"PERF-{r:04d}"),
                part_id=part,
                outcome=Outcome.PASSED,
                steps=[
                    TestStep(
                        name=f"step_{s}",
                        outcome=Outcome.PASSED,
                        started_at=t0,
                        ended_at=t1,
                        vectors=[
                            TestVector(
                                outcome=Outcome.PASSED,
                                params={
                                    "temperature": float(25 + (s % 5) * 10),
                                    "vin": round(3.2 + 0.1 * (r % 3), 2),
                                },
                                measurements=[
                                    Measurement(
                                        name="vout",
                                        value=3.3 + 0.001 * (s * _PARAM_MEAS + m),
                                        outcome=Outcome.PASSED,
                                    )
                                    for m in range(_PARAM_MEAS)
                                ],
                            )
                        ],
                    )
                    for s in range(_PARAM_STEPS)
                ],
            )
            notifier.notify_new_run(backend.save_test_run(run))
    finally:
        notifier.close()

    # Wait for the async daemon ingest to catch up before benchmarking.
    filters = FilterSet(string_filters={"part_id": [part]})
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        with MeasurementsQuery() as q:
            n = len(
                q.parametric(
                    y="measurement_value",
                    x="in_temperature",
                    filters=filters,
                    limit=_PARAM_EXPECTED + 100,
                )
            )
        if n >= _PARAM_EXPECTED:
            break
        time.sleep(0.5)
    return part


@pytest.mark.benchmark(group="daemon-parametric", warmup=True, min_rounds=20, disable_gc=True)
def test_parametric_scatter_by_condition(benchmark, parametric_dataset):
    """Scatter: measurement value vs a dynamic condition axis (one EAV join)."""
    filters = FilterSet(string_filters={"part_id": [parametric_dataset]})

    def _q():
        with MeasurementsQuery() as q:
            return q.parametric(y="measurement_value", x="in_temperature", filters=filters)

    rows = benchmark(_q)
    # Tolerate async-ingest lag, and the scatter's own row limit (5000) at
    # large seed scales — the latency, not the exact count, is the measurement.
    assert len(rows) >= min(_PARAM_EXPECTED, 5000) * 0.8


@pytest.mark.benchmark(group="daemon-parametric", warmup=True, min_rounds=20, disable_gc=True)
def test_parametric_group_by_condition(benchmark, parametric_dataset):
    """Scatter split by a second dynamic condition — two EAV joins."""
    filters = FilterSet(string_filters={"part_id": [parametric_dataset]})

    def _q():
        with MeasurementsQuery() as q:
            return q.parametric(
                y="measurement_value", x="in_temperature", group_by="in_vin", filters=filters
            )

    rows = benchmark(_q)
    assert rows


@pytest.mark.benchmark(group="daemon-parametric", warmup=True, min_rounds=20, disable_gc=True)
def test_parametric_histogram(benchmark, parametric_dataset):
    """Histogram of a measurement value distribution."""
    filters = FilterSet(string_filters={"part_id": [parametric_dataset]})

    def _q():
        with MeasurementsQuery() as q:
            return q.parametric(
                y="measurement_value", x="in_temperature", chart_type="histogram", filters=filters
            )

    rows = benchmark(_q)
    assert rows


# ---------------------------------------------------------------------------
# Dropdown-enumeration benchmarks — distinct_values populates the metrics /
# explore filter dropdowns; the cross-filtered form (exclude_self) narrows
# one facet to the values still valid given the other selections.
# ---------------------------------------------------------------------------


@pytest.mark.benchmark(group="daemon-enumerate", warmup=True, min_rounds=20, disable_gc=True)
def test_distinct_values_enumeration(benchmark, parametric_dataset):
    """Enumerate one facet's values (GROUP BY over the scoped fact)."""
    filters = FilterSet(string_filters={"part_id": [parametric_dataset]})

    def _q():
        with MeasurementsQuery() as q:
            return q.distinct_values("uut_serial", filters=filters)

    rows = benchmark(_q)
    assert rows


@pytest.mark.benchmark(group="daemon-enumerate", warmup=True, min_rounds=20, disable_gc=True)
def test_distinct_values_cross_filter(benchmark, parametric_dataset):
    """Cross-filtered enumeration — valid values of one facet given another
    selection (Tableau-style ``exclude_self``)."""
    filters = FilterSet(
        string_filters={"part_id": [parametric_dataset], "uut_serial": ["PERF-0001"]}
    )

    def _q():
        with MeasurementsQuery() as q:
            return q.distinct_values("measurement_name", filters=filters)

    rows = benchmark(_q)
    assert rows


# ---------------------------------------------------------------------------
# Ingest throughput — where the at-rest type change actually spends. Times the
# full materialization pipeline (save_test_run -> notify_new_run -> daemon
# UNNEST + projection) for N runs, from first notify to all-queryable.
# Consistent across commits so v2 (nested + EAV build) compares to before-EAV
# (flat fact + MAP, no EAV build). Run: pytest -m benchmark -k ingest -s
# ---------------------------------------------------------------------------

_INGEST_RUNS = int(os.environ.get("LITMUS_INGEST_RUNS", "40"))
_INGEST_STEPS = int(os.environ.get("LITMUS_INGEST_STEPS", "10"))
_INGEST_MEAS = int(os.environ.get("LITMUS_INGEST_MEAS", "10"))


@pytest.mark.benchmark(group="daemon-ingest")
def test_ingest_throughput():
    """End-to-end ingest throughput: save+notify N runs, time until all
    queryable. Prints runs/s and ms/run (use -s)."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.models import UUT, Measurement, Outcome, TestRun, TestStep, TestVector
    from litmus.data.run_store import RunStore

    _ensure_daemon_live()
    part = f"perf-ingest-{uuid4().hex[:8]}"
    t0d = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1d = datetime(2026, 6, 1, 12, 1, 0, tzinfo=UTC)
    backend = ParquetBackend(data_dir=resolve_data_dir())

    runs = [
        TestRun(
            id=uuid4(),
            started_at=t0d,
            ended_at=t1d,
            uut=UUT(serial=f"ING-{r:04d}"),
            part_id=part,
            outcome=Outcome.PASSED,
            steps=[
                TestStep(
                    name=f"step_{s}",
                    outcome=Outcome.PASSED,
                    started_at=t0d,
                    ended_at=t1d,
                    vectors=[
                        TestVector(
                            outcome=Outcome.PASSED,
                            params={"temperature": float(25 + (s % 5) * 10), "vin": 3.3},
                            measurements=[
                                Measurement(
                                    name="vout", value=3.3 + 0.001 * m, outcome=Outcome.PASSED
                                )
                                for m in range(_INGEST_MEAS)
                            ],
                        )
                    ],
                )
                for s in range(_INGEST_STEPS)
            ],
        )
        for r in range(_INGEST_RUNS)
    ]

    notifier = RunStore()
    filters = FilterSet(string_filters={"part_id": [part]})
    seen = 0
    try:
        t0 = time.perf_counter()
        for run in runs:
            notifier.notify_new_run(backend.save_test_run(run))
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            with MeasurementsQuery() as q:
                seen = len(q.distinct_values("uut_serial", filters=filters))
            if seen >= _INGEST_RUNS:
                break
            time.sleep(0.05)
        elapsed = time.perf_counter() - t0
    finally:
        notifier.close()

    per_run = elapsed / _INGEST_RUNS * 1000
    print(
        f"\nINGEST: {_INGEST_RUNS} runs x {_INGEST_STEPS}x{_INGEST_MEAS} meas in "
        f"{elapsed:.2f}s = {_INGEST_RUNS / elapsed:.1f} runs/s, {per_run:.0f} ms/run "
        f"(seen {seen}/{_INGEST_RUNS})"
    )
    assert seen >= _INGEST_RUNS


@pytest.mark.benchmark(group="daemon-ingest")
def test_ingest_phase_breakdown(tmp_path):
    """Time each ingest phase for one parquet on a fresh, uncontended DB —
    isolates where the projection SQL spends. Run: pytest -m benchmark -k
    phase_breakdown -s. Scale via LITMUS_PHASE_VEC / LITMUS_PHASE_MEAS."""
    import time as _t
    from datetime import UTC, datetime
    from uuid import uuid4

    from litmus.data._runs_duckdb_daemon import (
        _bulk_insert_measurement_rows,
        _bulk_insert_measurements,
        _bulk_insert_runs,
        _bulk_insert_steps,
        _ensure_schema,
        _index_io_and_refs,
        _open_index,
    )
    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.models import UUT, Measurement, Outcome, TestRun, TestStep, TestVector

    n_vec = int(os.environ.get("LITMUS_PHASE_VEC", "100"))
    n_meas = int(os.environ.get("LITMUS_PHASE_MEAS", "50"))
    t0d = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1d = datetime(2026, 6, 1, 12, 1, 0, tzinfo=UTC)
    run = TestRun(
        id=uuid4(),
        started_at=t0d,
        ended_at=t1d,
        uut=UUT(serial="PHASE-001"),
        part_id="phase",
        outcome=Outcome.PASSED,
        steps=[
            TestStep(
                name=f"s{s}",
                outcome=Outcome.PASSED,
                started_at=t0d,
                ended_at=t1d,
                vectors=[
                    TestVector(
                        outcome=Outcome.PASSED,
                        params={"temperature": float((s % 5) * 10), "vin": 3.3},
                        measurements=[
                            Measurement(name="vout", value=3.3 + 0.001 * m, outcome=Outcome.PASSED)
                            for m in range(n_meas)
                        ],
                    )
                ],
            )
            for s in range(n_vec)
        ],
    )
    backend = ParquetBackend(data_dir=tmp_path)
    pq = str(backend.save_test_run(run))

    t = _t.perf_counter()
    conn, _ = _open_index(tmp_path / "idx.duckdb")
    _ensure_schema(conn)
    schema_ms = (_t.perf_counter() - t) * 1000

    phases = [
        ("runs", lambda: _bulk_insert_runs(conn, [pq])),
        ("steps", lambda: _bulk_insert_steps(conn, [pq])),
        ("measurement_stats", lambda: _bulk_insert_measurements(conn, [pq])),
        ("meas_rows (fact+EAV)", lambda: _bulk_insert_measurement_rows(conn, pq)),
        ("io+refs", lambda: _index_io_and_refs(conn, pq)),
    ]
    print(f"\nINGEST PHASE BREAKDOWN  ({n_vec} vectors x {n_meas} meas = {n_vec * n_meas} meas):")
    print(f"  {'schema init':24s} {schema_ms:7.1f} ms")
    total = schema_ms
    for name, fn in phases:
        t = _t.perf_counter()
        fn()
        ms = (_t.perf_counter() - t) * 1000
        total += ms
        print(f"  {name:24s} {ms:7.1f} ms")
    print(f"  {'TOTAL':24s} {total:7.1f} ms")
    conn.close()


def _seed_phase_parquet(tmp_path, n_vec: int, n_meas: int, serial: str = "PHASE-001") -> str:
    from datetime import UTC, datetime
    from uuid import uuid4

    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.models import UUT, Measurement, Outcome, TestRun, TestStep, TestVector

    t0d = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1d = datetime(2026, 6, 1, 12, 1, 0, tzinfo=UTC)
    run = TestRun(
        id=uuid4(),
        started_at=t0d,
        ended_at=t1d,
        uut=UUT(serial=serial),
        part_id="phase",
        outcome=Outcome.PASSED,
        steps=[
            TestStep(
                name=f"s{s}",
                outcome=Outcome.PASSED,
                started_at=t0d,
                ended_at=t1d,
                vectors=[
                    TestVector(
                        outcome=Outcome.PASSED,
                        params={"temperature": float((s % 5) * 10), "vin": 3.3},
                        measurements=[
                            Measurement(name="vout", value=3.3 + 0.001 * m, outcome=Outcome.PASSED)
                            for m in range(n_meas)
                        ],
                    )
                ],
            )
            for s in range(n_vec)
        ],
    )
    return str(ParquetBackend(data_dir=tmp_path).save_test_run(run))


@pytest.mark.benchmark(group="daemon-ingest")
def test_meas_rows_split(tmp_path):
    """Split meas_rows into fact-without-MAP / fact-with-MAP / EAV. The MAP
    build cost is (with - without). Run: -m benchmark -k meas_rows_split -s."""
    import time as _t

    from litmus.data._runs_duckdb_daemon import (
        _dynamic_attrs_map_expr,
        _dynamic_unnest_union,
        _ensure_schema,
        _measurement_unnest_insert,
        _open_index,
        _sql_escape,
    )

    n_vec = int(os.environ.get("LITMUS_PHASE_VEC", "100"))
    n_meas = int(os.environ.get("LITMUS_PHASE_MEAS", "50"))
    pq = _seed_phase_parquet(tmp_path, n_vec, n_meas)
    escaped = _sql_escape(pq)
    src = f"read_parquet('{escaped}', union_by_name=true)"

    conn, _ = _open_index(tmp_path / "idx.duckdb")
    _ensure_schema(conn)

    with_map = _measurement_unnest_insert(src, file_path_expr=f"'{escaped}'")
    no_map = with_map.replace(
        _dynamic_attrs_map_expr(), "MAP(ARRAY[]::VARCHAR[], ARRAY[]::VARCHAR[])"
    )
    union_sql = _dynamic_unnest_union(src, where="record_type IN ('step', 'vector')")
    eav = (
        f"INSERT INTO measurements_dynamic SELECT DISTINCT "
        f"'{escaped}' AS file_path, run_id, step_index, vector_index, vector_retry, "
        f"side, name, kind, value_int, value_double, value_bool, value_text, "
        f"value_timestamp, value_json, unit, uut_pin FROM ({union_sql})"
    )

    def _ms(sql: str) -> float:
        t = _t.perf_counter()
        conn.execute(sql)
        return (_t.perf_counter() - t) * 1000

    base = _ms(no_map)
    conn.execute("DELETE FROM measurements_materialized")
    withmap = _ms(with_map)
    eav_ms = _ms(eav)
    conn.close()

    print(f"\nMEAS_ROWS SPLIT ({n_vec} vec x {n_meas} meas = {n_vec * n_meas} meas):")
    print(f"  fact insert (no MAP)       {base:7.1f} ms")
    print(f"  fact insert (with MAP)     {withmap:7.1f} ms")
    print(f"  -> MAP build cost (delta)  {withmap - base:7.1f} ms")
    print(f"  EAV insert (meas_dynamic)  {eav_ms:7.1f} ms")


def test_batch_io_refs_matches_per_file(tmp_path):
    """The batched io+refs path must produce identical measurement_io_schema /
    measurement_refs rows as the per-file path — catchup correctness guard for
    the filename-as-file_path + PARTITION BY filename rewrite. Deterministic,
    in-process (no daemon); runs in the normal suite."""
    from litmus.data._runs_duckdb_daemon import (
        _batch_index_io_and_refs,
        _ensure_schema,
        _index_io_and_refs,
        _open_index,
    )

    paths = [
        # Same serial on purpose — distinct files come from the run_id in the
        # filename, so this also guards that the run_id keeps them apart.
        _seed_phase_parquet(tmp_path, n_vec=4, n_meas=2)
        for _ in range(3)
    ]

    cb, _ = _open_index(tmp_path / "batch.duckdb")
    _ensure_schema(cb)
    _batch_index_io_and_refs(cb, paths)
    io_b = cb.execute("SELECT * FROM measurement_io_schema ORDER BY ALL").fetchall()
    refs_b = cb.execute("SELECT * FROM measurement_refs ORDER BY ALL").fetchall()
    cb.close()

    cp, _ = _open_index(tmp_path / "perfile.duckdb")
    _ensure_schema(cp)
    for p in paths:
        _index_io_and_refs(cp, p)
    io_p = cp.execute("SELECT * FROM measurement_io_schema ORDER BY ALL").fetchall()
    refs_p = cp.execute("SELECT * FROM measurement_refs ORDER BY ALL").fetchall()
    cp.close()

    assert io_b == io_p
    assert refs_b == refs_p


@pytest.mark.benchmark(group="daemon-catchup")
def test_catchup_throughput(tmp_path):
    """Cold batch-ingest of a backlog of N on-disk parquets — the catchup path
    (``_ingest_file_batch``) a daemon runs on startup/recovery. Fresh DB, no
    daemon process. Run: -m benchmark -k catchup -s. Scale via
    LITMUS_CATCHUP_RUNS / LITMUS_CATCHUP_VEC / LITMUS_CATCHUP_MEAS."""
    import time as _t

    from litmus.data._runs_duckdb_daemon import (
        _batch_index_io_and_refs,
        _batch_insert_measurement_rows,
        _bulk_insert_measurements,
        _bulk_insert_runs,
        _bulk_insert_steps,
        _ensure_schema,
        _open_index,
    )

    n = int(os.environ.get("LITMUS_CATCHUP_RUNS", "50"))
    n_vec = int(os.environ.get("LITMUS_CATCHUP_VEC", "10"))
    n_meas = int(os.environ.get("LITMUS_CATCHUP_MEAS", "10"))
    # Same serial — distinct files come from the run_id in the filename.
    paths = [_seed_phase_parquet(tmp_path, n_vec, n_meas) for _ in range(n)]

    conn, _ = _open_index(tmp_path / "idx.duckdb")
    _ensure_schema(conn)

    phases = [
        ("runs", lambda: _bulk_insert_runs(conn, paths)),
        ("steps", lambda: _bulk_insert_steps(conn, paths)),
        ("measurement_stats", lambda: _bulk_insert_measurements(conn, paths)),
        ("meas_rows (fact+EAV)", lambda: _batch_insert_measurement_rows(conn, paths)),
        ("io+refs (batched)", lambda: _batch_index_io_and_refs(conn, paths)),
    ]
    conn.execute("BEGIN")
    total = 0.0
    print(f"\nCATCHUP: {n} files x {n_vec}x{n_meas} = {n * n_vec * n_meas} meas")
    for label, fn in phases:
        t = _t.perf_counter()
        fn()
        ms = (_t.perf_counter() - t) * 1000
        total += ms
        print(f"  {label:26s} {ms:7.1f} ms")
    conn.execute("COMMIT")
    res = conn.execute("SELECT COUNT(*) FROM measurements_materialized").fetchone()
    rows = res[0] if res else 0
    conn.close()
    print(
        f"  {'TOTAL':26s} {total:7.1f} ms  =  {n / (total / 1000):.1f} runs/s, "
        f"{total / n:.1f} ms/run"
    )
    assert rows >= n * n_vec * n_meas * 0.9
