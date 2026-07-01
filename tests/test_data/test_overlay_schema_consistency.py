"""No-drift EQUIVALENCE test: in-flight overlay vs materialized parquet.

THE INVARIANT (no-drift): for one identical event stream, the rows the
:class:`EventAccumulator` produces for the in-flight overlay
(``snapshot_run_row`` / ``snapshot_step_rows`` /
``snapshot_measurement_rows``) must equal the rows the materialization
path produces (write parquet -> ingest -> ``*_materialized`` tables),
column-for-column, EXCEPT for genuinely materialization-only columns
(``file_path`` -- there is no parquet file in-flight).

This REPLACES an earlier synthetic-NULL guard that injected fake values
into the overlay tables and asserted no materialized column came back
NULL. That guard had false positives because the injected values were
not produced by the real projection. This test feeds ONE real event
stream through both paths and compares the VALUES the two paths produce
for the same logical rows.

Matching keys:
  * runs by ``run_id``
  * steps by ``(step_path, vector_index)``
  * measurements by ``(step_path, vector_index, measurement_name)``

Normalization: type-only differences (enum vs string, int32 vs int64,
duckdb timestamp vs python datetime, MAP vs dict) are converted to a
canonical value before comparison so the test only reports columns whose
VALUE genuinely diverges. A value divergence is a FINDING, asserted on.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import duckdb
import pytest

from litmus.data._runs_duckdb_daemon import (
    _MEASUREMENTS_PERSISTED_COLUMNS,
    _RUNS_PERSISTED_COLUMNS,
    _STEPS_PERSISTED_COLUMNS,
    _bulk_insert_measurement_rows,
    _bulk_insert_runs,
    _bulk_insert_steps,
    _ensure_schema,
)
from litmus.data.backends._event_accumulator import EventAccumulator
from litmus.data.backends.parquet import materialize_run_to_parquet
from litmus.data.events import (
    InstrumentConnected,
    MeasurementRecorded,
    Observation,
    RunEnded,
    RunStarted,
    StepEnded,
    StepsDiscovered,
    StepStarted,
    VectorEnded,
    VectorStarted,
)

# Fixed identifiers + timestamps — never datetime.now().
_RUN_ID = UUID("11111111-1111-1111-1111-111111111111")
_SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
_T0 = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)


def _ts(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 6, 9, 12, minute, second, tzinfo=UTC)


def _build_accumulator() -> EventAccumulator:
    """One representative event stream covering the bug-exposing cases.

    - 3 logical steps (a swept step, a verify-less step, a plain step)
    - swept step with >1 vector (vector_index 0 and 1)
    - a MeasurementRecorded with a non-zero retry (retry=2)
    - a verify-less vector with an Observation (exercises promoted DONE rows)
    - an InstrumentConnected
    - a RunEnded with a real outcome
    """
    acc = EventAccumulator()

    acc.on_event(
        RunStarted(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            station_id="st1",
            station_name="Station One",
            station_hostname="host-1",
            slot_id="slotA",
            uut_serial_number="SN001",
            uut_part_number="PN-100",
            uut_lot_number="LOT-9",
            fixture_id="fix1",
            test_phase="production",
            part_id="prod-1",
            operator_id="op-1",
            project_name="proj-1",
            occurred_at=_T0,
        )
    )

    acc.on_event(
        InstrumentConnected(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            role="dmm",
            instrument_id="keithley_001",
            resource="GPIB::16",
            manufacturer="Keithley",
            model="2000",
        )
    )

    # Steps manifest with planned vector counts + markers (drives
    # vector_count / markers on the materialized + inflight sides).
    acc.on_event(
        StepsDiscovered(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            items=[
                {
                    "node_id": "tests/test_hw.py::test_sweep",
                    "step_index": 0,
                    "step_path": "test_sweep",
                    "markers": "slow",
                    "vector_count_planned": 2,
                },
                {
                    "node_id": "tests/test_hw.py::test_action",
                    "step_index": 1,
                    "step_path": "test_action",
                    "markers": None,
                    "vector_count_planned": 1,
                },
                {
                    "node_id": "tests/test_hw.py::test_plain",
                    "step_index": 2,
                    "step_path": "test_plain",
                    "markers": None,
                    "vector_count_planned": 1,
                },
            ],
        )
    )

    # ── Step 0: swept, 2 vectors (one logical step; a VectorStarted/Ended
    #    per sweep point). Vector 1's occurrence ordinal is 2 (retry=2). ──
    acc.on_event(
        StepStarted(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            step_name="test_sweep",
            step_index=0,
            step_path="test_sweep",
            vector_index=0,
            node_id="tests/test_hw.py::test_sweep",
            file="tests/test_hw.py",
            module="tests.test_hw",
            function="test_sweep",
            occurred_at=_ts(1, 0),
        )
    )
    for vec, vin, vretry in ((0, 2.0, 0), (1, 3.0, 2)):
        acc.on_event(
            VectorStarted(
                session_id=_SESSION_ID,
                run_id=_RUN_ID,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=vec,
                retry=vretry,
                inputs={"vin": vin},
                occurred_at=_ts(1, vec),
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=_SESSION_ID,
                run_id=_RUN_ID,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=vec,
                retry=vretry,
                measurement_name="vout",
                value=2.01 if vec == 0 else 3.02,
                unit="V",
                outcome="passed",
                limit_low=1.9 if vec == 0 else 2.9,
                limit_high=2.1 if vec == 0 else 3.1,
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=_SESSION_ID,
                run_id=_RUN_ID,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=vec,
                retry=vretry,
                outcome="passed",
                inputs={"vin": vin},
            )
        )
    acc.on_event(
        StepEnded(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            step_name="test_sweep",
            step_index=0,
            step_path="test_sweep",
            vector_index=0,
            outcome="passed",
            occurred_at=_ts(2, 1),
        )
    )

    # ── Step 1: verify-less vector with an Observation (promoted DONE). ─
    acc.on_event(
        StepStarted(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            step_name="test_action",
            step_index=1,
            step_path="test_action",
            vector_index=0,
            node_id="tests/test_hw.py::test_action",
            file="tests/test_hw.py",
            module="tests.test_hw",
            function="test_action",
            occurred_at=_ts(3),
        )
    )
    acc.on_event(
        Observation(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            step_name="test_action",
            step_index=1,
            step_path="test_action",
            vector_index=0,
            name="temp",
            value=25.0,
        )
    )
    acc.on_event(
        StepEnded(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            step_name="test_action",
            step_index=1,
            step_path="test_action",
            vector_index=0,
            outcome="passed",
            occurred_at=_ts(4),
        )
    )

    # ── Step 2: plain single measurement step. ─────────────────────────
    acc.on_event(
        StepStarted(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            step_name="test_plain",
            step_index=2,
            step_path="test_plain",
            vector_index=0,
            node_id="tests/test_hw.py::test_plain",
            file="tests/test_hw.py",
            module="tests.test_hw",
            function="test_plain",
            occurred_at=_ts(5),
        )
    )
    acc.on_event(
        MeasurementRecorded(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            step_name="test_plain",
            step_index=2,
            step_path="test_plain",
            vector_index=0,
            measurement_name="iout",
            value=0.5,
            unit="A",
            outcome="failed",
            limit_low=0.0,
            limit_high=0.4,
        )
    )
    acc.on_event(
        StepEnded(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            step_name="test_plain",
            step_index=2,
            step_path="test_plain",
            vector_index=0,
            outcome="failed",
            occurred_at=_ts(6),
        )
    )

    acc.on_event(
        RunEnded(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            outcome="failed",
            occurred_at=_ts(7),
        )
    )
    return acc


# ── Normalization ───────────────────────────────────────────────────


def _norm(value: Any) -> Any:
    """Canonicalize a value so type-only differences don't read as drift.

    - datetime -> ISO string in UTC (duckdb TIMESTAMPTZ vs python datetime)
    - enum/StrEnum -> its string value
    - dict / MAP -> sorted (k, str(v)) tuples
    - bool stays bool; int/float compare by value
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return tuple(sorted((str(k), _norm(v)) for k, v in value.items()))
    # duckdb returns MAP as a {'key': [...], 'value': [...]} dict in some
    # builds, or as a python dict in others; the dict branch above covers
    # the python-dict case. A list-of-pairs also normalizes here.
    if isinstance(value, (list, tuple)):
        return tuple(_norm(v) for v in value)
    # StrEnum is a str subclass; str(value) collapses enum -> value.
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return str(value)


def _table_rows(conn: duckdb.DuckDBPyConnection, table: str) -> list[dict[str, Any]]:
    cols = [c[0] for c in conn.execute(f"DESCRIBE {table}").fetchall()]
    out: list[dict[str, Any]] = []
    for row in conn.execute(f"SELECT * FROM {table}").fetchall():
        out.append(dict(zip(cols, row, strict=True)))
    return out


# Columns that exist only on the materialized side, not the overlay:
# ``file_path`` (no parquet file in-flight) and ``vector_index_key`` (an
# internal COALESCE(vector_index,-1) dedup key for the PK, not data — the
# ``steps`` view EXCLUDEs it; the overlay never carries it).
_MATERIALIZATION_ONLY = {"file_path", "vector_index_key", "vector_outer_index_key"}

# Sentinel: a materialized column with no corresponding inflight key.
_MISSING = object()


def _drop_null_map_entries(value: Any) -> Any:
    """For a ``dynamic_attrs`` MAP, drop keys whose value is None.

    The materialized side packs a file-wide UNION of dynamic columns and
    pads keys absent from a given row with NULL. A NULL MAP entry is
    semantically identical to the key being absent, so dropping NULLs on
    both sides removes that padding noise without hiding a real value
    (a non-NULL entry present on one side only still shows as drift)."""
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if v is not None}
    return value


def _compare(
    label: str,
    inflight: dict[str, Any],
    materialized: dict[str, Any],
) -> list[tuple[str, Any, Any]]:
    """Return [(column, inflight_value, materialized_value)] for every
    materialized column whose normalized VALUE differs from the inflight
    row. ``file_path`` is skipped (materialization-only)."""
    diffs: list[tuple[str, Any, Any]] = []
    for col, mat_val in materialized.items():
        if col in _MATERIALIZATION_ONLY:
            continue
        in_val = inflight.get(col, _MISSING)
        if in_val is _MISSING:
            diffs.append((col, "<absent from inflight row>", mat_val))
            continue
        a, b = in_val, mat_val
        if col == "dynamic_attrs":
            a = _drop_null_map_entries(a)
            b = _drop_null_map_entries(b)
        if _norm(a) != _norm(b):
            diffs.append((col, a, b))
    return diffs


def _run_equivalence() -> dict[str, list[tuple[str, Any, Any]]]:
    """Drive both paths and return divergences per table."""
    acc = _build_accumulator()

    inflight_run = acc.snapshot_run_row()
    assert inflight_run is not None
    inflight_steps = acc.snapshot_step_rows()
    inflight_meas = acc.snapshot_measurement_rows()

    findings: dict[str, list[tuple[str, Any, Any]]] = {}

    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td) / "results"
        # Feed the SAME ended_at the RunEnded event carries — otherwise
        # materialize_run_to_parquet defaults run_ended_at to now() and the
        # ended_at columns trivially differ (a harness artifact, not drift).
        parquet_path = materialize_run_to_parquet(
            acc, out_dir, outcome="failed", run_ended_at=_ts(7)
        )
        assert parquet_path is not None

        conn = duckdb.connect()
        try:
            _ensure_schema(conn)
            fkey = str(parquet_path)
            # Populate the three *_materialized tables exactly as the daemon's
            # ingest does. (We call the inserts directly rather than
            # _index_unified_parquet so the unrelated io/refs index — which
            # we don't compare — doesn't run.) measurements_materialized is
            # filled by _bulk_insert_measurement_rows (the batch path's
            # _batch_insert_measurement_rows is the same INSERT).
            _bulk_insert_runs(conn, [fkey])
            _bulk_insert_steps(conn, [fkey])
            _bulk_insert_measurement_rows(conn, fkey)

            mat_runs = _table_rows(conn, "runs_materialized")
            mat_steps = _table_rows(conn, "steps_materialized")
            mat_meas = _table_rows(conn, "measurements_materialized")
        finally:
            conn.close()

    # ── runs: match by run_id ──────────────────────────────────────────
    assert len(mat_runs) == 1
    findings["runs"] = _compare("runs", inflight_run, mat_runs[0])

    # ── steps: match by (step_path, vector_index) ──────────────────────
    findings["steps"] = _match_and_compare(
        "steps",
        inflight_steps,
        mat_steps,
        key=lambda r: (r["step_path"], r["vector_index"]),
    )

    # ── measurements: match by (step_path, vector_index, name) ─────────
    findings["measurements"] = _match_and_compare(
        "measurements",
        inflight_meas,
        mat_meas,
        key=lambda r: (r["step_path"], r["vector_index"], r["measurement_name"]),
    )

    return findings


def _match_and_compare(
    label: str,
    inflight: list[dict[str, Any]],
    materialized: list[dict[str, Any]],
    *,
    key: Callable[[dict[str, Any]], tuple[Any, ...]],
) -> list[tuple[str, Any, Any]]:
    """Join two row lists on a natural key and diff matched pairs.

    Unmatched rows on either side are reported as row-existence
    divergences (a row the one path produced and the other did not) —
    a finding, not a crash.
    """
    in_by_key = {key(r): r for r in inflight}
    mat_by_key = {key(r): r for r in materialized}
    diffs: list[tuple[str, Any, Any]] = []
    for k in sorted(set(in_by_key) | set(mat_by_key), key=str):
        irow = in_by_key.get(k)
        mrow = mat_by_key.get(k)
        if mrow is None:
            diffs.append((f"{k}:<row>", "<inflight-only row>", None))
            continue
        if irow is None:
            diffs.append((f"{k}:<row>", None, "<materialized-only row>"))
            continue
        for col, iv, mv in _compare(label, irow, mrow):
            diffs.append((f"{k}:{col}", iv, mv))
    return diffs


def test_inflight_overlay_matches_materialized_for_same_events() -> None:
    """For one identical event stream, the in-flight overlay rows and the
    materialized parquet rows must agree column-for-column (except
    ``file_path``).

    Type-only differences are normalized away. Every remaining divergence
    is a real drift FINDING. The set below catalogs the divergences that
    exist TODAY (each with its assessment). The test asserts the ACTUAL
    divergences equal this catalog — so a NEW divergence (regression) OR a
    FIXED one (the in-flight plumbing caught up) both fail here loudly,
    while the suite stays green at the documented baseline.

    Already-fixed fields ``retry_count`` / ``record_type`` are NOT in the
    catalog — they MATCH. If they reappear here, that is a regression.
    """
    findings = _run_equivalence()

    actual = {
        (table, _signature(col)) for table, diffs in findings.items() for col, _i, _m in diffs
    }

    detail: list[str] = []
    for table, diffs in findings.items():
        for col, in_val, mat_val in diffs:
            detail.append(f"  [{table}] {col}: inflight={in_val!r} materialized={mat_val!r}")
    detail_str = "\n".join(detail)

    new_drift = actual - _KNOWN_DIVERGENCES
    fixed = _KNOWN_DIVERGENCES - actual
    assert not new_drift, (
        "NEW in-flight↔materialized drift not in the documented catalog "
        f"(real findings — fix the in-flight plumbing or update the catalog):\n{detail_str}\n"
        f"new: {sorted(new_drift)}"
    )
    assert not fixed, (
        "A documented divergence no longer reproduces — the in-flight plumbing "
        f"caught up. Remove it from _KNOWN_DIVERGENCES:\n  fixed: {sorted(fixed)}"
    )


def test_step_scope_measurement_reaches_materialized_with_null_vector_index() -> None:
    """A plain ``def test(): measure(...)`` step-scope measurement MUST reach
    ``measurements_materialized`` with ``vector_index IS NULL``.

    Regression: before the step-row UNNEST landed, the measurement projection
    sourced ``record_type = 'vector'`` only, so a step-scope measurement showed
    in the in-flight overlay then VANISHED at finalize (real data loss). It must
    now materialize, carrying NULL as its leaf coordinate (it belongs to the
    step itself, not a vector).
    """
    acc = EventAccumulator()
    acc.on_event(
        RunStarted(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            station_id="st1",
            uut_serial_number="SN001",
            occurred_at=_T0,
        )
    )
    acc.on_event(
        StepStarted(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            step_name="test_plain",
            step_index=0,
            step_path="test_plain",
            vector_index=0,
            occurred_at=_ts(1),
        )
    )
    acc.on_event(
        MeasurementRecorded(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            step_name="test_plain",
            step_index=0,
            step_path="test_plain",
            vector_index=0,
            measurement_name="vout",
            value=3.3,
            outcome="passed",
        )
    )
    acc.on_event(
        StepEnded(
            session_id=_SESSION_ID,
            run_id=_RUN_ID,
            step_name="test_plain",
            step_index=0,
            step_path="test_plain",
            vector_index=0,
            outcome="passed",
            occurred_at=_ts(2),
        )
    )
    acc.on_event(
        RunEnded(session_id=_SESSION_ID, run_id=_RUN_ID, outcome="passed", occurred_at=_ts(3))
    )

    with tempfile.TemporaryDirectory() as td:
        parquet_path = materialize_run_to_parquet(
            acc, Path(td) / "results", outcome="passed", run_ended_at=_ts(3)
        )
        assert parquet_path is not None
        conn = duckdb.connect()
        try:
            _ensure_schema(conn)
            _bulk_insert_measurement_rows(conn, str(parquet_path))
            rows = conn.execute(
                "SELECT measurement_name, vector_index FROM measurements_materialized"
            ).fetchall()
        finally:
            conn.close()

    assert rows == [("vout", None)]


def _signature(col: str) -> str:
    """Collapse a per-row diff label (``('test_sweep', 0):measurement_count``
    or ``('', 0):<row>``) down to its column / row-marker so the catalog is
    keyed by column, not by example row."""
    return col.rsplit(":", 1)[-1]


# ── Documented divergences (real findings, each assessed) ────────────
#
# (materialized_table, column-or-marker). ZERO divergences: the in-flight
# overlay and the materialized parquet produce identical rows for the same
# events. The earlier catalog (num_measurements / has_measurements /
# measurement_count count drift, the phantom run-record step row, and the
# over-packed step-identity dynamic_attrs) was eliminated by aligning the
# observation-promotion shape (one nameless DONE row per verify-less
# vector), the real-measurement count discriminator (measurement_name IS
# NOT NULL), the dynamic_attrs prefix rule (in_/out_/custom_ only), and the
# record_type<>'run' filter on the steps aggregation.
_KNOWN_DIVERGENCES: set[tuple[str, str]] = set()


# ── CREATE↔migration-tuple drift guard ───────────────────────────────
#
# Each ``*_materialized`` table is declared TWICE: the ``CREATE TABLE``
# body (full fresh schema, incl. structural PK / NOT NULL columns) and a
# ``_*_PERSISTED_COLUMNS`` tuple that drives ``ALTER TABLE ADD COLUMN IF
# NOT EXISTS`` so existing DBs migrate. The two drifted historically
# (``retry_count`` reached the tuple but not the CREATE; ``parent_path``
# the CREATE but not the tuple — the latter breaks the bulk-insert on any
# pre-existing DB that never got the column). This guard fails the moment
# they diverge again.


def _table_info(conn: duckdb.DuckDBPyConnection, table: str) -> list[tuple[str, bool, bool]]:
    """Return ``[(name, is_not_null, is_pk)]`` for a table's columns."""
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    return [(r[1], bool(r[3]), bool(r[5])) for r in rows]


@pytest.mark.parametrize(
    "table, persisted",
    [
        ("runs_materialized", _RUNS_PERSISTED_COLUMNS),
        ("steps_materialized", _STEPS_PERSISTED_COLUMNS),
        ("measurements_materialized", _MEASUREMENTS_PERSISTED_COLUMNS),
    ],
)
def test_materialized_data_columns_are_in_migration_tuple(
    table: str, persisted: tuple[tuple[str, str], ...]
) -> None:
    """Every migratable (nullable, non-PK) materialized column must be in
    its ``_*_PERSISTED_COLUMNS`` tuple.

    A data column present only in the ``CREATE`` never backfills onto an
    older DB (the ALTER loop only adds tuple columns), so its bulk-insert
    breaks there. Structural PK / NOT NULL columns are CREATE-only by
    necessity (can't be ``ALTER``-added) and are exempt.
    """
    conn = duckdb.connect()
    try:
        _ensure_schema(conn)
        info = _table_info(conn, table)
    finally:
        conn.close()

    actual = {name for name, _nn, _pk in info}
    structural = {name for name, nn, pk in info if pk or nn}
    tuple_cols = {name for name, _type in persisted}

    missing_from_tuple = (actual - structural) - tuple_cols
    assert not missing_from_tuple, (
        f"{table}: nullable data columns present in the schema but missing from its "
        f"migration tuple — existing DBs won't backfill them and the bulk-insert breaks: "
        f"{sorted(missing_from_tuple)}"
    )

    extra_in_tuple = tuple_cols - actual
    assert not extra_in_tuple, (
        f"{table}: migration tuple names columns the table lacks: {sorted(extra_in_tuple)}"
    )
