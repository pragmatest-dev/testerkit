"""Steps / measurement_facts projection SQL (docs/15 §5.1, §6.2; P1).

Two things proven here, no cloud creds, no object storage:

1. Drift guard (docs/15 §4.1 C6): `STEPS_COLUMNS`/`MEASUREMENT_FACTS_COLUMNS`
   (the hand-maintained BigQuery schema tuples) must never drift from what the
   projection SQL actually emits — same discipline as
   `test_runs_backend.py::test_projection_columns_match_live_projection`.
2. Projection **parity vs. a DuckDB oracle**: build a REAL per-run
   measurement-grain table via testerkit's own accumulator/derive engine (never
   hand-rolled rows), the same at-rest shape `object_derive.py` produces, then
   assert the flat rows this module's SQL produces match by hand-computed
   expectation. This is the "projection parity vs a DuckDB oracle" the P1 brief
   asks for — the oracle IS testerkit's real event → accumulator → unified-rows
   pipeline, and this module's SQL is checked against its actual output.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import duckdb
import pyarrow as pa
import pytest

from testerkit.data._accumulator_pool import AccumulatorPool
from testerkit.data.backends.parquet import _build_unified_rows_from_acc
from testerkit.data.event_store import _parse_event_row
from testerkit.data.events import (
    MeasurementRecorded,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
)
from testerkit.data.measurement_projection import (
    MEASUREMENT_FACTS_COLUMNS,
    STEPS_COLUMNS,
    measurement_facts_projection_select,
    steps_projection_select,
)
from testerkit.data.schemas import RUN_ROW_SCHEMA, _build_write_schema, table_from_rows


def _source(table: pa.Table) -> tuple[duckdb.DuckDBPyConnection, str]:
    con = duckdb.connect()
    con.register("measurement_rows", table)
    source = "(SELECT *, CAST(NULL AS VARCHAR) AS filename FROM measurement_rows)"
    return con, source


# --------------------------------------------------------------------------- #
# Drift guards (C6)                                                            #
# --------------------------------------------------------------------------- #


def test_steps_columns_match_projection() -> None:
    empty = pa.Table.from_pylist([], schema=RUN_ROW_SCHEMA)
    con, source = _source(empty)
    try:
        rel = con.execute(steps_projection_select(source))
        live_columns = tuple(d[0] for d in rel.description)
    finally:
        con.close()
    assert live_columns == tuple(name for name, _ in STEPS_COLUMNS), (
        "measurement_projection.STEPS_COLUMNS has drifted from "
        "steps_projection_select's actual output columns."
    )


def test_measurement_facts_columns_match_projection() -> None:
    empty = pa.Table.from_pylist([], schema=RUN_ROW_SCHEMA)
    con, source = _source(empty)
    try:
        rel = con.execute(measurement_facts_projection_select(source))
        live_columns = tuple(d[0] for d in rel.description)
    finally:
        con.close()
    assert live_columns == tuple(name for name, _ in MEASUREMENT_FACTS_COLUMNS), (
        "measurement_projection.MEASUREMENT_FACTS_COLUMNS has drifted from "
        "measurement_facts_projection_select's actual output columns."
    )


# --------------------------------------------------------------------------- #
# Parity vs. testerkit's REAL derive engine (accumulator → unified rows)       #
# --------------------------------------------------------------------------- #


def _build_run_table() -> pa.Table:
    """One real run — RunStarted, a step with one measurement, a second
    (unswept) step, a swept step (two vectors) — through testerkit's ACTUAL
    accumulator/unified-row pipeline. Never a hand-rolled row: this table is
    exactly what `object_derive.py` writes to object storage."""
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    t0 = datetime(2026, 9, 13, 10, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 9, 13, 10, 0, 5, tzinfo=UTC)

    events = [
        RunStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            station_hostname="bench-a",
            uut_serial_number="SN-1",
            uut_part_number="P-1",
            test_phase="production",
        ),
        StepStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_path="power/vout",
            step_name="vout",
            step_index=0,
        ),
        MeasurementRecorded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_name="vout",
            step_index=0,
            step_path="power/vout",
            measurement_name="v_out",
            value=5.01,
            unit="V",
            outcome="passed",
            limit_low=4.9,
            limit_high=5.1,
        ),
        StepEnded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_name="vout",
            step_index=0,
            step_path="power/vout",
            outcome="passed",
        ),
        RunEnded(session_id=session_id, run_id=run_id, occurred_at=t1, outcome="passed"),
    ]

    pool = AccumulatorPool()
    for i, event in enumerate(events):
        row = {
            "id": str(event.id),
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "session_id": str(session_id),
            "run_id": str(run_id),
            "json": event.model_dump_json(),
        }
        pool.dispatch(_parse_event_row(row))

    acc = pool._accs[str(run_id)]
    rows = _build_unified_rows_from_acc(acc, t1, "passed")
    return table_from_rows(rows, _build_write_schema(rows)), str(run_id)


def test_steps_projection_matches_real_derive_output() -> None:
    table, run_id = _build_run_table()
    con, source = _source(table)
    try:
        rows = con.execute(steps_projection_select(source)).fetchall()
        cols = [d[0] for d in con.description]
    finally:
        con.close()
    by_name = {
        dict(zip(cols, r, strict=True))["step_name"]: dict(zip(cols, r, strict=True)) for r in rows
    }
    assert set(by_name) == {"vout"}
    step = by_name["vout"]
    assert step["run_id"] == run_id
    assert step["step_path"] == "power/vout"
    assert step["outcome"] == "passed"
    assert step["measurement_count"] == 1
    assert step["uut_serial_number"] == "SN-1"
    assert step["uut_part_number"] == "P-1"
    assert step["station_hostname"] == "bench-a"


def test_measurement_facts_projection_matches_real_derive_output() -> None:
    table, run_id = _build_run_table()
    con, source = _source(table)
    try:
        rows = con.execute(measurement_facts_projection_select(source)).fetchall()
        cols = [d[0] for d in con.description]
    finally:
        con.close()
    dicts = [dict(zip(cols, r, strict=True)) for r in rows]
    assert len(dicts) == 1
    fact = dicts[0]
    assert fact["run_id"] == run_id
    assert fact["step_path"] == "power/vout"
    assert fact["step_name"] == "vout"
    assert fact["measurement_name"] == "v_out"
    assert fact["measurement_value"] == pytest.approx(5.01)
    assert fact["measurement_outcome"] == "passed"
    assert fact["limit_low"] == pytest.approx(4.9)
    assert fact["limit_high"] == pytest.approx(5.1)
    assert fact["vector_index"] is None  # step-scope measurement, not a vector
    assert fact["uut_part_number"] == "P-1"


def test_measurement_facts_occurrence_index_discriminates_repeats() -> None:
    """Two measurements of the SAME name at different execution positions get
    distinct `occurrence_index` values (the daemon's `_occurrence_index_expr`
    formula, reproduced verbatim — see module docstring's promotion flag)."""
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    t0 = datetime(2026, 9, 13, 10, 0, 0, tzinfo=UTC)

    events = [
        RunStarted(session_id=session_id, run_id=run_id, occurred_at=t0, uut_serial_number="SN-2"),
        StepStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_path="a",
            step_name="a",
            step_index=0,
        ),
        MeasurementRecorded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_name="a",
            step_index=0,
            step_path="a",
            measurement_name="v",
            value=1.0,
            outcome="passed",
        ),
        StepEnded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_name="a",
            step_index=0,
            step_path="a",
            outcome="passed",
        ),
        StepStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_path="b",
            step_name="b",
            step_index=1,
        ),
        MeasurementRecorded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_name="b",
            step_index=1,
            step_path="b",
            measurement_name="v",
            value=2.0,
            outcome="passed",
        ),
        StepEnded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_name="b",
            step_index=1,
            step_path="b",
            outcome="passed",
        ),
        RunEnded(session_id=session_id, run_id=run_id, occurred_at=t0, outcome="passed"),
    ]
    pool = AccumulatorPool()
    for event in events:
        row = {
            "id": str(event.id),
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "session_id": str(session_id),
            "run_id": str(run_id),
            "json": event.model_dump_json(),
        }
        pool.dispatch(_parse_event_row(row))
    acc = pool._accs[str(run_id)]
    rows = _build_unified_rows_from_acc(acc, t0, "passed")
    table = table_from_rows(rows, _build_write_schema(rows))

    con, source = _source(table)
    try:
        result = con.execute(measurement_facts_projection_select(source)).fetchall()
        cols = [d[0] for d in con.description]
    finally:
        con.close()
    dicts = sorted((dict(zip(cols, r, strict=True)) for r in result), key=lambda d: d["step_index"])
    assert [d["occurrence_index"] for d in dicts] == [0, 1]
    assert [d["measurement_value"] for d in dicts] == [1.0, 2.0]
