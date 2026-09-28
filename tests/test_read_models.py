"""Parity tests for `testerkit.data.read_models` (cloud alignment P1).

Builds ONE real run through testerkit's own event pipeline (`EventAccumulator`
+ `materialize_run_to_parquet` — the same helper `tests/test_data/
test_vector_grained_records.py` uses to get a real per-run Parquet, never a
hand-rolled row), covering a plain step, a swept step (vectors), and a
retried step. That same run is fed to the canonical singleton runs daemon
(`RunStore().notify_new_run`, the pattern `tests/test_steps_query/
test_steps_query.py` and `tests/test_measurements_query/
test_measurements_query_sql.py` use — never a per-test daemon / `tmp_path`
daemon, per CLAUDE.md's Test Storage Convention) so the LOCAL public Query
API (`RunsQuery`, `StepsQuery`, `RunStore.get_measurements`) can be compared
field-by-field against `read_models.run_detail()` over the SAME Parquet file.

`detail.inputs`/`.outputs` are compared against the daemon's OWN ``inputs``/
``outputs`` tables directly (docs/44 §1 parity — no public Query API wraps
these tables yet), via the same raw-Flight-query pattern
`tests/test_data/test_observation_pin.py::_query_eav` uses (never a per-test
daemon).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from testerkit.analysis.runs_query import RunsQuery
from testerkit.analysis.steps_query import StepsQuery
from testerkit.data import measurement_projection as mp
from testerkit.data import read_models
from testerkit.data.backends._event_accumulator import EventAccumulator
from testerkit.data.backends.parquet import materialize_run_to_parquet
from testerkit.data.data_dir import resolve_data_dir
from testerkit.data.events import (
    MeasurementRecorded,
    RunStarted,
    StepEnded,
    StepStarted,
    VectorEnded,
    VectorStarted,
)
from testerkit.data.run_store import RunStore
from testerkit.data.schemas import RUN_ROW_SCHEMA

_T0 = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)

PLAIN_STEP_PATH = "power/vout"
SWEPT_STEP_PATH = "sweep/rail"
RETRY_STEP_PATH = "calib/offset"


def _run_started(run_id: Any, session_id: Any) -> RunStarted:
    return RunStarted(
        session_id=session_id,
        run_id=run_id,
        occurred_at=_T0,
        station_id="STA-RM-1",
        station_hostname="bench-rm",
        station_name="Read Models Bench",
        station_type="functional",
        uut_serial_number="SN-RM-1",
        uut_part_number="PN-RM-1",
        part_id="PART-RM-1",
        part_name="Read Models Widget",
        fixture_id="FIX-RM-1",
        test_phase="production",
    )


def _build_scenario() -> tuple[EventAccumulator, str, str]:
    """One run: a plain (unswept) step, a swept step (2 vectors, each with
    one input + one measurement), and a retried step (2 attempts).
    Event sequences mirror `tests/test_data/test_vector_grained_records.py`
    Scenarios 1, 2, and 5 (mode 1) exactly — proven-correct event shapes,
    not hand-guessed."""
    acc = EventAccumulator()
    run_id, session_id = uuid4(), uuid4()
    acc.on_event(_run_started(run_id, session_id))

    # Plain step (Scenario 1 shape): one step-scope measurement.
    acc.on_event(
        StepStarted(
            session_id=session_id,
            run_id=run_id,
            step_name="vout",
            step_index=0,
            step_path=PLAIN_STEP_PATH,
        )
    )
    acc.on_event(
        MeasurementRecorded(
            session_id=session_id,
            run_id=run_id,
            step_name="vout",
            step_index=0,
            step_path=PLAIN_STEP_PATH,
            measurement_name="v_out",
            value=5.01,
            unit="V",
            outcome="passed",
            limit_low=4.9,
            limit_high=5.1,
        )
    )
    acc.on_event(
        StepEnded(
            session_id=session_id,
            run_id=run_id,
            step_name="vout",
            step_index=0,
            step_path=PLAIN_STEP_PATH,
            outcome="passed",
        )
    )

    # Swept step (Scenario 2 shape): two variants, each its own
    # StepStarted/VectorStarted/MeasurementRecorded/VectorEnded/StepEnded —
    # the variants collapse onto ONE logical step row; each variant is its
    # own vector row carrying an input ("vin") and a measurement ("vout").
    for vi, vin in ((0, 3.3), (1, 5.0)):
        node_id = f"test_rail[{vin}]"
        acc.on_event(
            StepStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="rail",
                step_index=1,
                step_path=SWEPT_STEP_PATH,
                vector_index=0,
                node_id=node_id,
            )
        )
        acc.on_event(
            VectorStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="rail",
                step_index=1,
                step_path=SWEPT_STEP_PATH,
                vector_index=vi,
                inputs={"vin": vin},
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="rail",
                step_index=1,
                step_path=SWEPT_STEP_PATH,
                vector_index=vi,
                measurement_name="vout",
                value=round(vin * 0.98, 4),
                unit="V",
                outcome="passed",
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="rail",
                step_index=1,
                step_path=SWEPT_STEP_PATH,
                vector_index=vi,
                outcome="passed",
                inputs={"vin": vin},
            )
        )
        acc.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="rail",
                step_index=1,
                step_path=SWEPT_STEP_PATH,
                vector_index=0,
                outcome="passed",
                node_id=node_id,
            )
        )

    # Retried step (Scenario 5 mode-1 shape): two distinct step-execution
    # rows sharing one step_path, step_retry 0 (fails) then 1 (passes).
    for retry, outcome, value in ((0, "failed", 0.05), (1, "passed", 0.01)):
        acc.on_event(
            StepStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="offset",
                step_index=2,
                step_path=RETRY_STEP_PATH,
                retry=retry,
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="offset",
                step_index=2,
                step_path=RETRY_STEP_PATH,
                step_retry=retry,
                measurement_name="offset",
                value=value,
                unit="V",
                outcome=outcome,
                limit_low=-0.02,
                limit_high=0.02,
            )
        )
        acc.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="offset",
                step_index=2,
                step_path=RETRY_STEP_PATH,
                retry=retry,
                outcome=outcome,
            )
        )

    return acc, str(run_id), str(session_id)


@dataclasses.dataclass
class _Scenario:
    run_id: str
    session_id: str
    path: Path
    table: pa.Table


@pytest.fixture(scope="module")
def scenario() -> _Scenario:
    """Materializes the run into the CANONICAL singleton runs daemon's data
    dir (never `tmp_path` — see CLAUDE.md's Test Storage Convention: a
    per-test daemon is forbidden) and notifies it, so the LOCAL Query API can
    see it. One acquire/release of the canonical daemon for the whole
    module."""
    acc, run_id, session_id = _build_scenario()
    canonical_runs = resolve_data_dir() / "runs" / "test-read-models"
    path = materialize_run_to_parquet(acc, canonical_runs, outcome="passed")
    assert path is not None

    notifier = RunStore()
    try:
        notifier.notify_new_run(path)
    finally:
        notifier.close()

    return _Scenario(run_id=run_id, session_id=session_id, path=path, table=pq.read_table(path))


def _query_io_table(table: str, run_id: str) -> list[dict]:
    """Raw Flight query of the daemon's own ``inputs``/``outputs`` table —
    same pattern as `test_observation_pin.py::_query_eav` (no public Query
    API wraps these tables yet)."""
    from testerkit.data import runs_duckdb_manager
    from testerkit.data._flight_query import FlightQueryClient

    runs_dir = resolve_data_dir() / "runs"
    location = runs_duckdb_manager.acquire(runs_dir)
    client = FlightQueryClient(location, "runs")
    return client.query(
        f"""
        SELECT file_path, run_id, step_index, step_path, step_retry,
               vector_index, vector_outer_index, vector_retry, ordinal, index,
               name, value_type, value_int, value_double, value_bool,
               value_text, value_timestamp, value_json, unit, uut_pin
        FROM {table}
        WHERE run_id = '{run_id}'
        ORDER BY step_index, COALESCE(vector_index, -1), ordinal
        """
    )


def _assert_shared_fields_equal(
    local: dict[str, Any], cloud: dict[str, Any], *, ignore: frozenset[str] = frozenset()
) -> None:
    """Assert every field name common to both dicts (minus `ignore`) agrees.
    Datetimes/floats compare by value, not object identity."""
    shared = (set(local) & set(cloud)) - ignore
    assert shared, "no shared fields to compare — key sets diverged entirely"
    for key in shared:
        lv, cv = local[key], cloud[key]
        if isinstance(lv, float) and isinstance(cv, float):
            assert lv == pytest.approx(cv), f"field {key!r}: local={lv!r} cloud={cv!r}"
        else:
            assert lv == cv, f"field {key!r}: local={lv!r} cloud={cv!r}"


# --------------------------------------------------------------------------- #
# Column drift guards — every hand-declared column tuple in read_models.py   #
# (run_projection.py publishes no such tuple for the run header, and the     #
# catalog/slim tuples are new here) must match its builder's ACTUAL output.  #
# --------------------------------------------------------------------------- #


def _live_columns(sql: str) -> tuple[str, ...]:
    empty = pa.Table.from_pylist([], schema=RUN_ROW_SCHEMA)
    con = duckdb.connect()
    try:
        con.register("run_src", empty)
        rel = con.execute(sql)
        return tuple(d[0] for d in rel.description)
    finally:
        con.close()


_RAW_SOURCE = "(SELECT *, CAST(NULL AS VARCHAR) AS filename FROM run_src)"


@pytest.mark.parametrize(
    ("sql", "columns"),
    [
        (read_models.runs_select(_RAW_SOURCE), read_models.RUNS_COLUMNS),
        (read_models.runs_slim_select(_RAW_SOURCE), read_models.RUNS_SLIM_COLUMNS),
        (
            read_models.measurements_slim_select(_RAW_SOURCE),
            read_models.MEASUREMENTS_SLIM_COLUMNS,
        ),
        (read_models.steps_slim_select(_RAW_SOURCE), read_models.STEPS_SLIM_COLUMNS),
        (read_models.steps_catalog_select(_RAW_SOURCE), read_models.STEPS_CATALOG_COLUMNS),
        (
            read_models.measurements_catalog_select(_RAW_SOURCE),
            read_models.MEASUREMENTS_CATALOG_COLUMNS,
        ),
        (read_models.inputs_catalog_select(_RAW_SOURCE), read_models.INPUTS_CATALOG_COLUMNS),
        (read_models.outputs_catalog_select(_RAW_SOURCE), read_models.OUTPUTS_CATALOG_COLUMNS),
        (read_models.runs_catalog_select(_RAW_SOURCE), read_models.RUNS_CATALOG_COLUMNS),
        (mp.inputs_projection_select(_RAW_SOURCE), mp.IO_TABLE_COLUMNS),
        (mp.outputs_projection_select(_RAW_SOURCE), mp.IO_TABLE_COLUMNS),
        (
            # inputs_measurements_catalog_select's two args must already be
            # projected relations (inputs_projection_select /
            # measurements_projection_select output), never the raw
            # run-shaped source — it joins on `L.name`/`M.measurement_name`,
            # which only exist post-projection.
            read_models.inputs_measurements_catalog_select(
                mp.inputs_projection_select(_RAW_SOURCE),
                mp.measurements_projection_select(_RAW_SOURCE),
            ),
            read_models.INPUTS_MEASUREMENTS_CATALOG_COLUMNS,
        ),
    ],
)
def test_declared_columns_match_live_builder_output(sql, columns) -> None:
    live = _live_columns(sql)
    assert live == tuple(name for name, _ in columns)


# --------------------------------------------------------------------------- #
# run_detail() vs the LOCAL public Query API                                 #
# --------------------------------------------------------------------------- #


def test_run_detail_run_matches_runs_query(scenario: _Scenario) -> None:
    detail = read_models.run_detail(scenario.path)
    assert detail.run.run_id == scenario.run_id

    with RunsQuery() as q:
        local_run = q.get(scenario.run_id)
    assert local_run is not None

    _assert_shared_fields_equal(
        local_run.model_dump(),
        detail.run.model_dump(),
        ignore=frozenset({"file_path", "duration_s"}),
    )


def test_run_detail_step_rows_cover_plain_swept_and_retried(scenario: _Scenario) -> None:
    """4 logical-step rows: plain (1) + swept-and-collapsed (1) + retried
    step's two distinct execution rows (step_retry 0, 1)."""
    detail = read_models.run_detail(scenario.path)
    with StepsQuery() as q:
        local_steps = q.list_for_run(scenario.run_id)

    assert len(local_steps) == len(detail.steps) == 4
    by_key_local = {(s.step_path, s.step_retry): s for s in local_steps}
    by_key_cloud = {(s.step_path, s.step_retry): s for s in detail.steps}
    assert set(by_key_local) == set(by_key_cloud)

    # The retried step produced two distinct rows (retry 0 failed, retry 1 passed).
    retried_local = {k: v for k, v in by_key_local.items() if k[0] == RETRY_STEP_PATH}
    assert {k[1] for k in retried_local} == {0, 1}
    assert by_key_cloud[(RETRY_STEP_PATH, 0)].outcome == "failed"
    assert by_key_cloud[(RETRY_STEP_PATH, 1)].outcome == "passed"

    for key, local_step in by_key_local.items():
        cloud_step = by_key_cloud[key]
        _assert_shared_fields_equal(
            local_step.model_dump(),
            cloud_step.model_dump(),
            ignore=frozenset({"file_path", "parent_path", "inputs", "outputs"}),
        )


def test_run_detail_vectors_match_steps_query_vectors(scenario: _Scenario) -> None:
    detail = read_models.run_detail(scenario.path)
    with StepsQuery() as q:
        local_vectors = q.list_vectors_for_run(scenario.run_id)

    assert len(local_vectors) == 2
    assert len(detail.vectors) == 2

    by_key_local = {(v.step_path, v.vector_index): v for v in local_vectors}
    by_key_cloud = {(v.step_path, v.vector_index): v for v in detail.vectors}
    assert set(by_key_local) == set(by_key_cloud) == {(SWEPT_STEP_PATH, 0), (SWEPT_STEP_PATH, 1)}

    for key, local_vector in by_key_local.items():
        cloud_vector = by_key_cloud[key]
        _assert_shared_fields_equal(
            local_vector.model_dump(),
            cloud_vector.model_dump(),
            ignore=frozenset({"file_path", "parent_path", "inputs", "outputs", "step_index"}),
        )


def test_run_detail_measurements_match_local_measurements(scenario: _Scenario) -> None:
    detail = read_models.run_detail(scenario.path)
    with RunStore() as store:
        local_facts = store.get_measurements(scenario.run_id)

    # 1 plain + 2 vector (swept) + 2 retry attempts (failed, then passed).
    assert len(local_facts) == len(detail.measurements) == 5

    def _key(row: dict[str, Any]) -> tuple:
        return (row["step_path"], row.get("step_retry", 0), row.get("vector_index"), row["ordinal"])

    by_key_local = {_key(r): r for r in local_facts}
    by_key_cloud = {_key(m.model_dump()): m.model_dump() for m in detail.measurements}
    assert set(by_key_local) == set(by_key_cloud)

    for key, local_fact in by_key_local.items():
        cloud_fact = by_key_cloud[key]
        _assert_shared_fields_equal(
            local_fact,
            cloud_fact,
            ignore=frozenset({"file_path", "inputs", "outputs"}),
        )


def test_run_detail_inputs_match_local_inputs_table(scenario: _Scenario) -> None:
    """`detail.inputs` must equal the LOCAL daemon's own ``inputs`` table rows
    for this run, byte-for-byte, including ``ordinal``/``index`` (docs/44 §1:
    no `role` column, no collapsed `value` — the honestly-named shape)."""
    detail = read_models.run_detail(scenario.path)
    assert len(detail.inputs) == 2
    assert {row.name for row in detail.inputs} == {"vin"}

    local_rows = _query_io_table("inputs", scenario.run_id)
    assert len(local_rows) == len(detail.inputs) == 2

    by_key_local = {(r["step_path"], r["ordinal"]): r for r in local_rows}
    by_key_cloud = {(r.step_path, r.ordinal): r.model_dump() for r in detail.inputs}
    assert set(by_key_local) == set(by_key_cloud)

    for key, local_row in by_key_local.items():
        _assert_shared_fields_equal(local_row, by_key_cloud[key], ignore=frozenset({"file_path"}))

    # The swept step's two inputs get distinct, 0-based ordinals — the
    # UNNEST-WITH-ORDINALITY position within their own carrier row.
    swept_inputs = {r["ordinal"] for r in local_rows if r["step_path"] == SWEPT_STEP_PATH}
    assert swept_inputs == {0}, f"expected ordinal 0 on each swept-step carrier row: {local_rows}"
    swept_indices = sorted(r["index"] for r in local_rows if r["step_path"] == SWEPT_STEP_PATH)
    assert swept_indices == [0, 1], f"expected occurrence indices 0, 1: {local_rows}"


def test_run_detail_outputs_match_local_outputs_table(scenario: _Scenario) -> None:
    """`detail.outputs` must equal the LOCAL daemon's own ``outputs`` table
    rows for this run (no fixture output rows are recorded here, but the
    shape/emptiness must still agree)."""
    detail = read_models.run_detail(scenario.path)
    local_rows = _query_io_table("outputs", scenario.run_id)
    assert len(local_rows) == len(detail.outputs)


# --------------------------------------------------------------------------- #
# derive_run()                                                                #
# --------------------------------------------------------------------------- #


def test_derive_run_header_and_slim_facts(scenario: _Scenario) -> None:
    derived = read_models.derive_run(scenario.path)
    assert derived.run.run_id == scenario.run_id
    assert derived.run.uut_serial_number == "SN-RM-1"
    assert derived.run.part_id == "PART-RM-1"
    assert derived.run.station_id == "STA-RM-1"

    assert len(derived.measurements) == 5
    slim_names = {name for name, _ in read_models.MEASUREMENTS_SLIM_COLUMNS}
    for fact in derived.measurements:
        assert set(fact.model_dump()) == slim_names

    # Both retry attempts' measurement occurrences are distinct rows (each
    # attempt is its own execution) — neither is dropped or fused.
    retry_facts = [f for f in derived.measurements if f.step_path == RETRY_STEP_PATH]
    assert len(retry_facts) == 2
    assert sorted(f.step_retry for f in retry_facts if f.step_retry is not None) == [0, 1]
    assert sorted(
        f.measurement_outcome for f in retry_facts if f.measurement_outcome is not None
    ) == ["failed", "passed"]


def test_derive_run_catalog_deltas(scenario: _Scenario) -> None:
    derived = read_models.derive_run(scenario.path)
    catalog = derived.catalog

    assert {s.step_path for s in catalog.steps} == {
        PLAIN_STEP_PATH,
        SWEPT_STEP_PATH,
        RETRY_STEP_PATH,
    }

    measurement_keys = {(s.step_path, s.measurement_name) for s in catalog.measurements}
    assert (PLAIN_STEP_PATH, "v_out") in measurement_keys
    assert (SWEPT_STEP_PATH, "vout") in measurement_keys
    assert (RETRY_STEP_PATH, "offset") in measurement_keys

    input_names = {row.name for row in catalog.inputs}
    assert input_names == {"vin"}
    assert catalog.outputs == []

    assert {r.part_id for r in catalog.runs} == {"PART-RM-1"}
    assert {r.station_id for r in catalog.runs} == {"STA-RM-1"}
    assert {r.fixture_id for r in catalog.runs} == {"FIX-RM-1"}

    cooc_pairs = {(p.input_name, p.measurement_name) for p in catalog.inputs_measurements}
    assert ("vin", "vout") in cooc_pairs


def test_derive_run_measurements_slim_is_column_subset() -> None:
    slim_names = {name for name, _ in read_models.MEASUREMENTS_SLIM_COLUMNS}
    full_names = {name for name, _ in mp.MEASUREMENTS_COLUMNS}
    # `inputs` (docs/48 §4b track A1) is the one declared column NOT drawn
    # from `MEASUREMENTS_COLUMNS` — it comes from the separate
    # `inputs_projection_select` builder, aggregated per carrier, so the
    # strict-subset relationship holds for every OTHER slim column.
    assert slim_names - {"inputs"} <= full_names
    assert "inputs" in slim_names
    assert "org_id" not in slim_names  # a server-side concern, not in the library tuple


def test_derive_run_measurements_slim_inputs_match_local_inputs_table(scenario: _Scenario) -> None:
    """docs/44 §1 parity: every `measurements_slim` row's `inputs` must equal
    the LOCAL daemon's own `inputs` table rows for the same carrier
    (`step_path, step_retry, vector_index, vector_outer_index`), ordinal
    order included; swept rows carry a non-empty list, plain/retried rows an
    empty one."""
    derived = read_models.derive_run(scenario.path)
    local_rows = _query_io_table("inputs", scenario.run_id)

    def _carrier(row: dict[str, Any]) -> tuple:
        return (
            row["step_path"],
            row.get("step_retry", 0),
            row.get("vector_index"),
            row.get("vector_outer_index"),
        )

    local_by_carrier: dict[tuple, list[dict[str, Any]]] = {}
    for row in local_rows:
        local_by_carrier.setdefault(_carrier(row), []).append(row)
    for entries in local_by_carrier.values():
        entries.sort(key=lambda r: r["ordinal"])

    entry_field_names = {name for name, _ in read_models.MEASUREMENT_INPUT_ENTRY_COLUMNS}
    saw_swept_non_empty = False
    saw_plain_empty = False
    saw_retry_empty = False
    for fact in derived.measurements:
        carrier = (fact.step_path, fact.step_retry, fact.vector_index, fact.vector_outer_index)
        expected = local_by_carrier.get(carrier, [])
        actual = [entry.model_dump() for entry in fact.inputs]

        assert [e["ordinal"] for e in actual] == [e["ordinal"] for e in expected]
        for exp, act in zip(expected, actual, strict=True):
            assert set(act) == entry_field_names
            _assert_shared_fields_equal(exp, act)

        if fact.step_path == SWEPT_STEP_PATH:
            assert actual, f"swept carrier {carrier} must have non-empty inputs"
            saw_swept_non_empty = True
        elif fact.step_path == PLAIN_STEP_PATH:
            assert actual == [], f"plain carrier {carrier} must have empty inputs"
            saw_plain_empty = True
        elif fact.step_path == RETRY_STEP_PATH:
            assert actual == [], f"retried carrier {carrier} must have empty inputs"
            saw_retry_empty = True

    assert saw_swept_non_empty and saw_plain_empty and saw_retry_empty


# --------------------------------------------------------------------------- #
# runs_slim / steps_slim (docs/48 D19) vs the LOCAL public Query API         #
# --------------------------------------------------------------------------- #


def test_runs_slim_is_column_subset() -> None:
    slim_names = {name for name, _ in read_models.RUNS_SLIM_COLUMNS}
    full_names = {name for name, _ in read_models.RUNS_COLUMNS}
    assert slim_names <= full_names
    assert "org_id" not in slim_names  # a server-side concern, not in the library tuple


def test_steps_slim_is_column_subset() -> None:
    slim_names = {name for name, _ in read_models.STEPS_SLIM_COLUMNS}
    full_names = {name for name, _ in mp.STEPS_COLUMNS}
    assert slim_names <= full_names
    assert "org_id" not in slim_names


def test_derive_run_runs_slim_matches_runs_query(scenario: _Scenario) -> None:
    """`runs_slim` is one row per run — served (non-empty) and equal, field
    by field, to `RunsQuery`'s row for the same run."""
    derived = read_models.derive_run(scenario.path)
    assert len(derived.runs) == 1
    slim = derived.runs[0]
    assert slim.run_id == scenario.run_id

    with RunsQuery() as q:
        local_run = q.get(scenario.run_id)
    assert local_run is not None

    _assert_shared_fields_equal(local_run.model_dump(), slim.model_dump())


def test_derive_run_steps_slim_matches_steps_query(scenario: _Scenario) -> None:
    """`steps_slim` must carry every logical step this run produced — plain,
    the swept step (collapsed to one row, matching `StepsQuery`), and BOTH
    the retried step's attempts (failed then passed) — with `steps_slim`'s
    own fields equal to `StepsQuery`'s. Rows are keyed by
    `(step_path, step_retry)`, the step-execution grain."""
    derived = read_models.derive_run(scenario.path)
    with StepsQuery() as q:
        local_steps = q.list_for_run(scenario.run_id)

    assert len(local_steps) == len(derived.steps) == 4

    def _key(row: dict[str, Any]) -> tuple:
        return (row["step_path"], row["step_retry"])

    by_key_local = {_key(s.model_dump()): s.model_dump() for s in local_steps}
    by_key_slim = {_key(s.model_dump()): s.model_dump() for s in derived.steps}
    assert set(by_key_local) == set(by_key_slim)

    for key, local_row in by_key_local.items():
        _assert_shared_fields_equal(local_row, by_key_slim[key])

    assert by_key_slim[(RETRY_STEP_PATH, 0)]["outcome"] == "failed"
    assert by_key_slim[(RETRY_STEP_PATH, 1)]["outcome"] == "passed"
    for row in by_key_slim.values():
        assert row["part_id"] == "PART-RM-1"
        assert row["station_id"] == "STA-RM-1"
        assert row["test_phase"] == "production"


# --------------------------------------------------------------------------- #
# Fingerprints                                                                #
# --------------------------------------------------------------------------- #


def test_fingerprint_stable_across_calls() -> None:
    for name in read_models.READ_MODELS:
        first = read_models.read_model_fingerprint(name)
        second = read_models.read_model_fingerprint(name)
        assert first == second
        assert len(first) == 64  # sha256 hex digest


def test_fingerprints_differ_across_models() -> None:
    values = {name: read_models.read_model_fingerprint(name) for name in read_models.READ_MODELS}
    assert len(set(values.values())) == len(values)


def test_fingerprint_changes_when_builder_sql_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    before = read_models.read_model_fingerprint("steps_catalog")
    original_spec = read_models.READ_MODELS["steps_catalog"]
    changed_spec = dataclasses.replace(
        original_spec,
        builder=lambda source_sql: "SELECT DISTINCT 'x' AS step_path, 'y' AS step_name",
    )
    monkeypatch.setitem(read_models.READ_MODELS, "steps_catalog", changed_spec)

    after = read_models.read_model_fingerprint("steps_catalog")
    assert before != after


def test_fingerprint_changes_when_columns_change(monkeypatch: pytest.MonkeyPatch) -> None:
    before = read_models.read_model_fingerprint("runs_catalog")
    original_spec = read_models.READ_MODELS["runs_catalog"]
    changed_spec = dataclasses.replace(
        original_spec, columns=(*original_spec.columns, ("extra_col", "STRING"))
    )
    monkeypatch.setitem(read_models.READ_MODELS, "runs_catalog", changed_spec)

    after = read_models.read_model_fingerprint("runs_catalog")
    assert before != after
