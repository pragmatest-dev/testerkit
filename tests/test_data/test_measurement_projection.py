"""Steps / measurement_facts / vectors projection SQL (docs/15 §5.1, §6.2, P1;
docs/36 P1a).

Three things proven here, no cloud creds, no object storage:

1. Drift guard (docs/15 §4.1 C6): `STEPS_COLUMNS`/`MEASUREMENT_FACTS_COLUMNS`/
   `VECTORS_COLUMNS` (the hand-maintained BigQuery schema tuples) must never
   drift from what the projection SQL actually emits — same discipline as
   `test_runs_backend.py::test_projection_columns_match_live_projection`.
2. Projection **parity vs. a DuckDB oracle**: build a REAL per-run
   measurement-grain table via testerkit's own accumulator/derive engine (never
   hand-rolled rows), the same at-rest shape `object_derive.py` produces, then
   assert the flat rows this module's SQL produces match by hand-computed
   expectation. This is the "projection parity vs a DuckDB oracle" the P1 brief
   asks for — the oracle IS testerkit's real event → accumulator → unified-rows
   pipeline, and this module's SQL is checked against its actual output.
3. Projection **parity vs. the daemon's own materialize path** (docs/36 P1a's
   "Parity test (byte/number-level)"): feed ONE real parquet (built the same
   way `object_derive.py`/the bench writer does, via `materialize_run_to_parquet`)
   through BOTH the daemon's `_bulk_insert_steps` (populating
   `steps_materialized`/`vectors_materialized`) and `steps_projection_select`/
   `vectors_projection_select`, then diff the own-grain columns row-for-row —
   proving the MIN/MAX timing rollup and the vectors-summed `measurement_count`
   fix land identically in both places (they are two SQL implementations of the
   same rule, kept in lockstep, not a shared import — see each function's
   docstring).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from testerkit.data._accumulator_pool import AccumulatorPool
from testerkit.data._runs_duckdb_daemon import _bulk_insert_steps, _ensure_schema
from testerkit.data.backends._event_accumulator import EventAccumulator
from testerkit.data.backends._row_helpers import encode_lane_structs
from testerkit.data.backends.parquet import _build_unified_rows_from_acc, materialize_run_to_parquet
from testerkit.data.event_store import _parse_event_row
from testerkit.data.events import (
    MeasurementRecorded,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
    VectorEnded,
    VectorStarted,
)
from testerkit.data.measurement_projection import (
    LANE_ROW_COLUMNS,
    MEASUREMENT_FACTS_COLUMNS,
    STEPS_COLUMNS,
    VECTORS_COLUMNS,
    lanes_projection_select,
    measurement_facts_projection_select,
    steps_projection_select,
    vectors_projection_select,
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


def test_vectors_columns_match_projection() -> None:
    empty = pa.Table.from_pylist([], schema=RUN_ROW_SCHEMA)
    con, source = _source(empty)
    try:
        rel = con.execute(vectors_projection_select(source))
        live_columns = tuple(d[0] for d in rel.description)
    finally:
        con.close()
    assert live_columns == tuple(name for name, _ in VECTORS_COLUMNS), (
        "measurement_projection.VECTORS_COLUMNS has drifted from "
        "vectors_projection_select's actual output columns."
    )


def test_lane_columns_match_projection() -> None:
    empty = pa.Table.from_pylist([], schema=RUN_ROW_SCHEMA)
    con, source = _source(empty)
    try:
        rel = con.execute(lanes_projection_select(source))
        live_columns = tuple(d[0] for d in rel.description)
    finally:
        con.close()
    assert live_columns == tuple(name for name, _ in LANE_ROW_COLUMNS), (
        "measurement_projection.LANE_ROW_COLUMNS has drifted from "
        "lanes_projection_select's actual output columns."
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


def _step_row(
    *,
    run_id: str,
    step_path: str,
    step_index: int,
    step_name: str,
    outcome: str | None,
) -> dict:
    """One ``record_type='step'`` row at the steps grain key (``run_id,
    step_path, step_retry, vector_outer_index, step_index, step_name``).
    Multiple rows built with the SAME grain key simulate a swept step's
    variant executions — the case `steps_projection_select`'s GROUP BY
    collapses into one served row (docs/31 band-aid; see the worst-wins
    test below). Unpopulated ``RUN_ROW_SCHEMA`` fields default to None,
    same convention as ``_lane_vector_row``."""
    populated: dict = {f.name: None for f in RUN_ROW_SCHEMA}
    populated.update(
        {
            "record_type": "step",
            "run_id": run_id,
            "step_path": step_path,
            "step_index": step_index,
            "step_name": step_name,
            "step_retry": 0,
            "vector_outer_index": None,
            "step_outcome": outcome,
            "measurements": [],
        }
    )
    return populated


def test_steps_projection_worst_wins_across_swept_variants() -> None:
    """A swept step can emit multiple `record_type='step'` rows sharing the
    same grain key (one per sweep variant). The OLD collapse used
    `ANY_VALUE(step_outcome)`, which could pick an arbitrary variant's
    outcome — a PASSED variant could hide a FAILED (or ERRORED) one. This
    asserts the collapse is worst-wins (severity-escalating, matching
    `models.escalate_outcome`) instead: FAILED/ERRORED variants are never
    hidden, a fully-PASSED sweep stays PASSED, and a non-swept (single-row)
    step is unaffected."""
    run_id = str(uuid.uuid4())
    rows = [
        # Mixed PASSED/FAILED variants of the same swept step -> FAILED wins.
        _step_row(
            run_id=run_id,
            step_path="sweep/mixed",
            step_index=0,
            step_name="mixed",
            outcome="passed",
        ),
        _step_row(
            run_id=run_id,
            step_path="sweep/mixed",
            step_index=0,
            step_name="mixed",
            outcome="failed",
        ),
        _step_row(
            run_id=run_id,
            step_path="sweep/mixed",
            step_index=0,
            step_name="mixed",
            outcome="passed",
        ),
        # An ERRORED variant among PASSED ones -> ERRORED wins (outranks FAILED too).
        _step_row(
            run_id=run_id,
            step_path="sweep/errored",
            step_index=1,
            step_name="errored",
            outcome="passed",
        ),
        _step_row(
            run_id=run_id,
            step_path="sweep/errored",
            step_index=1,
            step_name="errored",
            outcome="errored",
        ),
        # A fully-passed swept step stays PASSED.
        _step_row(
            run_id=run_id,
            step_path="sweep/allpass",
            step_index=2,
            step_name="allpass",
            outcome="passed",
        ),
        _step_row(
            run_id=run_id,
            step_path="sweep/allpass",
            step_index=2,
            step_name="allpass",
            outcome="passed",
        ),
        # A non-swept step (single variant, one row) is unaffected.
        _step_row(
            run_id=run_id, step_path="plain", step_index=3, step_name="plain", outcome="passed"
        ),
    ]
    table = _table_from_rows(rows)
    con, source = _source(table)
    try:
        result = con.execute(steps_projection_select(source)).fetchall()
        cols = [d[0] for d in con.description]
    finally:
        con.close()
    outcome_by_path = {
        dict(zip(cols, r, strict=True))["step_path"]: dict(zip(cols, r, strict=True))["outcome"]
        for r in result
    }
    assert outcome_by_path["sweep/mixed"] == "failed"
    assert outcome_by_path["sweep/errored"] == "errored"
    assert outcome_by_path["sweep/allpass"] == "passed"
    assert outcome_by_path["plain"] == "passed"


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


# --------------------------------------------------------------------------- #
# lanes_projection_select — behavioral (inputs/outputs EAV)                   #
# --------------------------------------------------------------------------- #


def _lane_vector_row(*, run_id: str, session_id: str) -> dict:
    """One ``record_type='vector'`` row carrying real encoded lane structs —
    built via ``encode_lane_structs`` (the actual at-rest encoder), not
    hand-rolled dicts. Unpopulated ``RUN_ROW_SCHEMA`` fields default to None,
    same convention as ``test_observation_pin.py``'s ``_make_vector_row``."""
    populated: dict = {f.name: None for f in RUN_ROW_SCHEMA}
    populated.update(
        {
            "record_type": "vector",
            "run_id": run_id,
            "session_id": session_id,
            "uut_serial_number": "SN-LANE",
            "step_name": "sweep_vin",
            "step_index": 0,
            "step_path": "sweep/vin",
            "step_retry": 0,
            "vector_index": 2,
            "vector_outer_index": None,
            "vector_retry": 0,
            "inputs": encode_lane_structs({"vin": 5.5, "note": "sweep-point"}, units={"vin": "V"}),
            "outputs": encode_lane_structs({"vout": 3.3}, units={"vout": "V"}),
            "measurements": [],
        }
    )
    return populated


def _table_from_rows(rows: list[dict]) -> pa.Table:
    cols = {f.name: [row.get(f.name) for row in rows] for f in RUN_ROW_SCHEMA}
    return pa.table(cols, schema=RUN_ROW_SCHEMA)


def test_lanes_projection_one_row_per_lane_entry() -> None:
    run_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    table = _table_from_rows([_lane_vector_row(run_id=run_id, session_id=session_id)])

    con, source = _source(table)
    try:
        result = con.execute(lanes_projection_select(source)).fetchall()
        cols = [d[0] for d in con.description]
    finally:
        con.close()

    dicts = [dict(zip(cols, r, strict=True)) for r in result]
    assert len(dicts) == 3  # 2 input entries + 1 output entry

    by_role_name = {(d["role"], d["name"]): d for d in dicts}
    assert set(by_role_name) == {("input", "vin"), ("input", "note"), ("output", "vout")}

    vin = by_role_name[("input", "vin")]
    assert vin["value_json"] == "5.5"
    assert vin["value"] == pytest.approx(5.5)
    assert vin["unit"] == "V"
    assert vin["step_path"] == "sweep/vin"
    assert vin["vector_index"] == 2
    assert vin["uut_serial_number"] == "SN-LANE"

    note = by_role_name[("input", "note")]
    assert note["value_json"] == '"sweep-point"'
    assert note["value"] is None  # non-numeric — TRY_CAST yields NULL
    assert note["unit"] is None

    vout = by_role_name[("output", "vout")]
    assert vout["value_json"] == "3.3"
    assert vout["value"] == pytest.approx(3.3)
    assert vout["unit"] == "V"
    assert vout["run_id"] == run_id


# --------------------------------------------------------------------------- #
# vectors_projection_select — behavioral, real accumulator pipeline (P1a)     #
# --------------------------------------------------------------------------- #


def _build_swept_run_table() -> tuple[pa.Table, str]:
    """One real run with a swept (in-body ``vectors``-loop) step — 2 vectors,
    each with its own measurement — through testerkit's ACTUAL accumulator/
    unified-row pipeline. Also carries git/UUT context so
    `measurement_facts_projection_select`'s denormalized step_outcome /
    step_started_at / step_ended_at / vector_outcome / git_* / env columns
    (docs/36 P1a) have something real to assert on."""
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    t0 = datetime(2026, 9, 13, 10, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 9, 13, 10, 0, 10, tzinfo=UTC)

    events = [
        RunStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            station_hostname="bench-a",
            uut_serial_number="SN-9",
            uut_part_number="P-9",
            test_phase="production",
            git_commit="deadbeef",
            git_branch="main",
            git_remote="origin",
        ),
        StepStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_path="power/sweep",
            step_name="sweep",
            step_index=0,
        ),
        VectorStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_name="sweep",
            step_index=0,
            step_path="power/sweep",
            vector_index=0,
            retry=0,
            inputs={"vin": 2.0},
        ),
        MeasurementRecorded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_name="sweep",
            step_index=0,
            step_path="power/sweep",
            vector_index=0,
            retry=0,
            measurement_name="vout",
            value=2.01,
            unit="V",
            outcome="passed",
            limit_low=1.9,
            limit_high=2.1,
        ),
        VectorEnded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=datetime(2026, 9, 13, 10, 0, 3, tzinfo=UTC),
            step_name="sweep",
            step_index=0,
            step_path="power/sweep",
            vector_index=0,
            retry=0,
            outcome="passed",
            inputs={"vin": 2.0},
        ),
        VectorStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=datetime(2026, 9, 13, 10, 0, 4, tzinfo=UTC),
            step_name="sweep",
            step_index=0,
            step_path="power/sweep",
            vector_index=1,
            retry=0,
            inputs={"vin": 3.0},
        ),
        MeasurementRecorded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=datetime(2026, 9, 13, 10, 0, 4, tzinfo=UTC),
            step_name="sweep",
            step_index=0,
            step_path="power/sweep",
            vector_index=1,
            retry=0,
            measurement_name="vout",
            value=3.02,
            unit="V",
            outcome="failed",
            limit_low=2.9,
            limit_high=3.1,
        ),
        VectorEnded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=datetime(2026, 9, 13, 10, 0, 7, tzinfo=UTC),
            step_name="sweep",
            step_index=0,
            step_path="power/sweep",
            vector_index=1,
            retry=0,
            outcome="failed",
            inputs={"vin": 3.0},
        ),
        StepEnded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=datetime(2026, 9, 13, 10, 0, 8, tzinfo=UTC),
            step_name="sweep",
            step_index=0,
            step_path="power/sweep",
            outcome="failed",
        ),
        RunEnded(session_id=session_id, run_id=run_id, occurred_at=t1, outcome="failed"),
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
    rows = _build_unified_rows_from_acc(acc, t1, "failed")
    return table_from_rows(rows, _build_write_schema(rows)), str(run_id)


def test_vectors_projection_matches_real_derive_output() -> None:
    table, run_id = _build_swept_run_table()
    con, source = _source(table)
    try:
        rows = con.execute(vectors_projection_select(source)).fetchall()
        cols = [d[0] for d in con.description]
    finally:
        con.close()
    by_index = {
        dict(zip(cols, r, strict=True))["vector_index"]: dict(zip(cols, r, strict=True))
        for r in rows
    }
    assert set(by_index) == {0, 1}

    v0 = by_index[0]
    assert v0["run_id"] == run_id
    assert v0["step_path"] == "power/sweep"
    assert v0["step_name"] == "sweep"  # denormalized off the vector's own carrier row
    assert v0["step_index"] == 0
    assert v0["outcome"] == "passed"
    assert v0["measurement_count"] == 1
    assert v0["uut_serial_number"] == "SN-9"

    v1 = by_index[1]
    assert v1["outcome"] == "failed"
    assert v1["measurement_count"] == 1

    # Two distinct condition-point rows — the load_regulation-shape assertion
    # (docs/36 P1b done-when): a swept step's variants come through as
    # DISTINCT rows, not collapsed into one.
    assert v0["started_at"] != v1["started_at"]


def test_steps_projection_measurement_count_sums_vectors() -> None:
    """A swept step's own `record_type='step'` row carries zero nested
    measurements (they ride the vector rows) — `steps_projection_select`'s
    `measurement_count` must be the step's own count PLUS its vectors'
    summed count (docs/36 P1a: this landed 0 before the fix)."""
    table, run_id = _build_swept_run_table()
    con, source = _source(table)
    try:
        rows = con.execute(steps_projection_select(source)).fetchall()
        cols = [d[0] for d in con.description]
    finally:
        con.close()
    by_path = {
        dict(zip(cols, r, strict=True))["step_path"]: dict(zip(cols, r, strict=True)) for r in rows
    }
    step = by_path["power/sweep"]
    assert step["run_id"] == run_id
    assert step["measurement_count"] == 2  # 0 own + 1 (vector 0) + 1 (vector 1)
    # MIN/MAX rollup (not ANY_VALUE): the step's execution window spans both
    # vectors' timing, anchored on the StepStarted/StepEnded occurred_at.
    assert step["started_at"] == datetime(2026, 9, 13, 10, 0, 0, tzinfo=UTC)
    assert step["ended_at"] == datetime(2026, 9, 13, 10, 0, 8, tzinfo=UTC)
    # Worst-wins step outcome still holds (a FAILED vector escalates the step).
    assert step["outcome"] == "failed"


def test_measurement_facts_projection_denormalizes_step_and_vector_outcome() -> None:
    """docs/36 P1a: `measurement_facts_projection_select` gained
    `step_outcome`/`step_started_at`/`step_ended_at`/`vector_outcome` (present
    on local's `measurements` view via its steps/vectors joins, absent here
    before this fix) and the git_*/env columns (present on every row —
    `run_context_from_run_started(..., include_env=True)` — so ANY_VALUE per
    carrier row is exact)."""
    table, run_id = _build_swept_run_table()
    con, source = _source(table)
    try:
        rows = con.execute(measurement_facts_projection_select(source)).fetchall()
        cols = [d[0] for d in con.description]
    finally:
        con.close()
    dicts = [dict(zip(cols, r, strict=True)) for r in rows]
    assert len(dicts) == 2
    by_vector = {d["vector_index"]: d for d in dicts}

    v0 = by_vector[0]
    assert v0["run_id"] == run_id
    # step_outcome is the WORST-WINS collapse across the step's variant rows
    # (here just one 'step' row, so it's simply that row's own outcome) — NOT
    # v.step_outcome directly (which is NULL on a vector-sourced fact at rest).
    assert v0["step_outcome"] == "failed"
    assert v0["step_started_at"] == datetime(2026, 9, 13, 10, 0, 0, tzinfo=UTC)
    assert v0["step_ended_at"] == datetime(2026, 9, 13, 10, 0, 8, tzinfo=UTC)
    assert v0["vector_outcome"] == "passed"  # this vector's own outcome, not the step's
    assert v0["git_commit"] == "deadbeef"
    assert v0["git_branch"] == "main"
    assert v0["git_remote"] == "origin"
    # No environment_json was set on RunStarted in this fixture -> None, not
    # a crash or a silently-dropped column.
    assert v0["python_version"] is None
    assert v0["testerkit_version"] is None
    assert v0["env_fingerprint"] is None

    v1 = by_vector[1]
    assert v1["step_outcome"] == "failed"
    assert v1["vector_outcome"] == "failed"


# --------------------------------------------------------------------------- #
# Daemon-materialize parity (docs/36 P1a's "byte/number-level" parity test)   #
# --------------------------------------------------------------------------- #


def test_steps_and_vectors_projection_matches_daemon_materialized(tmp_path: Path) -> None:
    """Feed ONE real parquet (built via `materialize_run_to_parquet`, the same
    writer the bench/daemon uses) through BOTH the daemon's own materialize
    path (`_bulk_insert_steps` -> `steps_materialized`/`vectors_materialized`)
    and the shared projection SQL (`steps_projection_select`/
    `vectors_projection_select`), then diff the own-grain columns.

    Scope: `steps_materialized`/`vectors_materialized` are star-schema tables
    (star schema, 0.3.1 phase 6) — they hold ONLY the step's/vector's own
    identity+timing+rollup columns, not the denormalized run/UUT/station
    context (that lives in `runs_materialized`, joined at VIEW time). So this
    compares exactly that own-grain column set — the columns the P1a rollup
    fixes (MIN/MAX timing, vectors-summed measurement_count) actually touch —
    which is also every column `steps_materialized`/`vectors_materialized`
    carries. The denormalized context columns are covered separately by the
    drift-guard + real-derive-output tests above.
    """
    acc = EventAccumulator()
    t0 = datetime(2026, 9, 14, 8, 0, 0, tzinfo=UTC)
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    acc.on_event(
        RunStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            uut_serial_number="SN-PARITY",
            station_hostname="bench-parity",
        )
    )
    acc.on_event(
        StepStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t0,
            step_name="sweep",
            step_index=0,
            step_path="power/sweep",
            node_id="tests/test_hw.py::test_sweep",
        )
    )
    for vec, vin in ((0, 2.0), (1, 3.0)):
        v_t0 = datetime(2026, 9, 14, 8, 0, 1 + vec * 3, tzinfo=UTC)
        v_t1 = datetime(2026, 9, 14, 8, 0, 2 + vec * 3, tzinfo=UTC)
        acc.on_event(
            VectorStarted(
                session_id=session_id,
                run_id=run_id,
                occurred_at=v_t0,
                step_name="sweep",
                step_index=0,
                step_path="power/sweep",
                vector_index=vec,
                retry=0,
                inputs={"vin": vin},
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                occurred_at=v_t0,
                step_name="sweep",
                step_index=0,
                step_path="power/sweep",
                vector_index=vec,
                retry=0,
                measurement_name="vout",
                value=2.0 + vec,
                unit="V",
                outcome="passed",
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=session_id,
                run_id=run_id,
                occurred_at=v_t1,
                step_name="sweep",
                step_index=0,
                step_path="power/sweep",
                vector_index=vec,
                retry=0,
                outcome="passed",
                inputs={"vin": vin},
            )
        )
    t_end = datetime(2026, 9, 14, 8, 0, 8, tzinfo=UTC)
    acc.on_event(
        StepEnded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=t_end,
            step_name="sweep",
            step_index=0,
            step_path="power/sweep",
            outcome="passed",
        )
    )
    acc.on_event(
        RunEnded(session_id=session_id, run_id=run_id, occurred_at=t_end, outcome="passed")
    )

    out_dir = tmp_path / "results"
    parquet_path = materialize_run_to_parquet(acc, out_dir, outcome="passed", run_ended_at=t_end)
    assert parquet_path is not None

    # ── daemon side: real materialize path ──────────────────────────────
    conn = duckdb.connect()
    try:
        _ensure_schema(conn)
        _bulk_insert_steps(conn, [str(parquet_path)])
        mat_steps = conn.execute(
            "SELECT run_id, step_path, step_retry, vector_outer_index, step_index, "
            "step_name, outcome, started_at, ended_at, duration_s, measurement_count, markers "
            "FROM steps_materialized"
        ).fetchall()
        mat_steps_cols = [d[0] for d in conn.description]
        mat_vectors = conn.execute(
            "SELECT run_id, step_path, step_retry, vector_outer_index, vector_index, "
            "vector_retry, outcome, started_at, ended_at, duration_s, measurement_count "
            "FROM vectors_materialized"
        ).fetchall()
        mat_vectors_cols = [d[0] for d in conn.description]
    finally:
        conn.close()

    # ── projection side: same parquet, the shared SQL ───────────────────
    con2 = duckdb.connect()
    try:
        source = f"read_parquet(['{parquet_path}'], filename=true, union_by_name=true)"
        proj_steps = con2.execute(steps_projection_select(source)).fetchall()
        proj_steps_cols = [d[0] for d in con2.description]
        proj_vectors = con2.execute(vectors_projection_select(source)).fetchall()
        proj_vectors_cols = [d[0] for d in con2.description]
    finally:
        con2.close()

    grain_cols = [
        "run_id",
        "step_path",
        "step_retry",
        "vector_outer_index",
        "step_index",
        "step_name",
        "outcome",
        "started_at",
        "ended_at",
        "duration_s",
        "measurement_count",
    ]
    mat_step_row = dict(zip(mat_steps_cols, mat_steps[0], strict=True))
    proj_step_by_path = {
        dict(zip(proj_steps_cols, r, strict=True))["step_path"]: dict(
            zip(proj_steps_cols, r, strict=True)
        )
        for r in proj_steps
    }
    proj_step_row = proj_step_by_path["power/sweep"]
    for col in grain_cols:
        assert mat_step_row[col] == proj_step_row[col], (
            f"steps parity mismatch on {col!r}: "
            f"materialized={mat_step_row[col]!r} projection={proj_step_row[col]!r}"
        )
    assert mat_step_row["measurement_count"] == 2
    assert mat_step_row["markers"] == proj_step_row["markers"]

    vec_grain_cols = [
        "run_id",
        "step_path",
        "step_retry",
        "vector_outer_index",
        "vector_index",
        "vector_retry",
        "outcome",
        "started_at",
        "ended_at",
        "duration_s",
        "measurement_count",
    ]
    mat_vec_by_index = {
        dict(zip(mat_vectors_cols, r, strict=True))["vector_index"]: dict(
            zip(mat_vectors_cols, r, strict=True)
        )
        for r in mat_vectors
    }
    proj_vec_by_index = {
        dict(zip(proj_vectors_cols, r, strict=True))["vector_index"]: dict(
            zip(proj_vectors_cols, r, strict=True)
        )
        for r in proj_vectors
    }
    assert set(mat_vec_by_index) == set(proj_vec_by_index) == {0, 1}
    for vi in (0, 1):
        mrow, prow = mat_vec_by_index[vi], proj_vec_by_index[vi]
        for col in vec_grain_cols:
            assert mrow[col] == prow[col], (
                f"vectors[{vi}] parity mismatch on {col!r}: "
                f"materialized={mrow[col]!r} projection={prow[col]!r}"
            )
