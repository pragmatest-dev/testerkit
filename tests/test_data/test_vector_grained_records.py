"""Record-spec scenario tests — step-carries-own-data + vector-as-leaf-carrier.

Each test feeds an event stream into an :class:`EventAccumulator`, then
materializes it (:func:`materialize_run_to_parquet`) and asserts the exact
per-scenario records of the grain-reshape model
(``docs/_internal/explorations/step-vector-grain-reshape.md``).

Grain: ``record_type in {run, step, vector}``, keyed
``(step_path, step_retry, vector_index)``.

* **A vector row is emitted ONLY for an actual sweep point** — a Mode-1
  parametrize variant, a class-outer iteration, or a Mode-2 in-body loop.
  Each is a ``VectorStarted``/``VectorEnded`` pair on the wire. There is
  NO synthesized scope vector — a non-looping step emits ZERO vectors.
* A **non-looping step carries its OWN data** — ``inputs`` / ``outputs`` /
  ``measurements`` ride on the ``step`` record. Measurements with no active
  vector loop are step-scope.
* A step's at-rest ``vector_index`` is the **enclosing** iteration: NULL
  for a top-level / non-swept step, an int for a step nested under a swept
  parent. A vector row's ``vector_index`` is its own leaf coordinate.
* **Measurements are nested** in their carrier's ``measurements`` list (the
  vector row for vector-scope, the step row for step-scope). The daemon
  UNNESTs them into the flat measurement fact for queries.

All scenarios drive the :class:`EventAccumulator` directly — the exact
projection the runs daemon runs on the live event stream — so the records
asserted are the ones the daemon writes. The plugin-via-pytester path
additionally needs a running daemon, omitted here to avoid spawning
per-test daemons.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pyarrow.parquet as pq

from litmus.data.backends._event_accumulator import EventAccumulator
from litmus.data.backends._row_helpers import decode_lane_structs
from litmus.data.backends.parquet import materialize_run_to_parquet
from litmus.data.events import (
    MeasurementRecorded,
    Observation,
    RunStarted,
    StepEnded,
    StepStarted,
    VectorEnded,
    VectorStarted,
)

_T0 = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _materialize(acc: EventAccumulator, tmp_path, *, outcome="passed"):
    path = materialize_run_to_parquet(acc, tmp_path / "results", outcome=outcome)
    assert path is not None
    return pq.read_table(path).to_pylist()


def _by_kind(rows):
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["record_type"], []).append(r)
    return out


def _run_started(run_id, session_id):
    return RunStarted(
        session_id=session_id,
        run_id=run_id,
        station_id="st1",
        uut_serial_number="SN001",
        occurred_at=_T0,
    )


def _lane_units(entries):
    """name → unit for a lane-struct list (only entries that carry a unit)."""
    return {e["name"]: e["unit"] for e in (entries or []) if e.get("unit") is not None}


# ---------------------------------------------------------------------------
# Scenario 1 — single / unswept: run + step, ZERO vectors. The measurement is
#   step-scope (no vector loop ran) and rides on the step record.
# ---------------------------------------------------------------------------


def test_scenario_1_single_unswept(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    acc.on_event(
        StepStarted(
            session_id=sid, run_id=rid, step_name="test_v", step_index=0, step_path="test_v"
        )
    )
    acc.on_event(
        MeasurementRecorded(
            session_id=sid,
            run_id=rid,
            step_name="test_v",
            step_index=0,
            step_path="test_v",
            measurement_name="vout",
            value=3.3,
            outcome="passed",
        )
    )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_v",
            step_index=0,
            step_path="test_v",
            outcome="passed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path))

    assert len(kinds["run"]) == 1
    # No vector loop ran → ZERO vector rows.
    assert "vector" not in kinds
    assert len(kinds["step"]) == 1
    step = kinds["step"][0]
    assert step["step_path"] == "test_v"
    # Top-level step → enclosing vector_index is NULL.
    assert step["vector_index"] is None
    assert step["step_retry"] == 0
    # The measurement is step-scope: nested on the step record, not a
    # separate row, not a synthesized vector.
    assert "measurement" not in kinds
    meas = step["measurements"]
    assert len(meas) == 1
    assert meas[0]["name"] == "vout"
    assert meas[0]["value"] == 3.3


# ---------------------------------------------------------------------------
# Scenario 2 — parametrize (Mode 1): every variant emits a VectorStarted/Ended
#   (vector_index 0/1) carrying its condition; the variants share ONE logical
#   step (step_path test_rail, enclosing vector_index NULL at top level).
# ---------------------------------------------------------------------------


def test_scenario_2_parametrize_mode1(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    for vi, vin in ((0, 3.3), (1, 5.0)):
        node_id = f"test_p.py::test_rail[{vin}]"
        # Each variant is its own pytest item: a StepStarted/StepEnded around
        # a VectorStarted/VectorEnded carrying that variant's condition. The
        # step's enclosing vector_index is 0 (top level, no outer sweep); the
        # variants share step_path so they collapse to one logical step.
        acc.on_event(
            StepStarted(
                session_id=sid,
                run_id=rid,
                step_name="test_rail",
                step_index=0,
                step_path="test_rail",
                vector_index=0,
                node_id=node_id,
            )
        )
        acc.on_event(
            VectorStarted(
                session_id=sid,
                run_id=rid,
                step_name="test_rail",
                step_index=0,
                step_path="test_rail",
                vector_index=vi,
                inputs={"vin": vin},
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=sid,
                run_id=rid,
                step_name="test_rail",
                step_index=0,
                step_path="test_rail",
                vector_index=vi,
                measurement_name="vout",
                value=vin,
                outcome="passed",
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=sid,
                run_id=rid,
                step_name="test_rail",
                step_index=0,
                step_path="test_rail",
                vector_index=vi,
                outcome="passed",
                inputs={"vin": vin},
            )
        )
        acc.on_event(
            StepEnded(
                session_id=sid,
                run_id=rid,
                step_name="test_rail",
                step_index=0,
                step_path="test_rail",
                vector_index=0,
                outcome="passed",
                node_id=node_id,
            )
        )

    kinds = _by_kind(_materialize(acc, tmp_path))
    # The two variants collapse to ONE logical step (top level → vector_index
    # NULL); each variant is a leaf vector row carrying its condition.
    assert len(kinds["step"]) == 1
    step = kinds["step"][0]
    assert step["step_path"] == "test_rail"
    assert step["vector_index"] is None
    vrows = sorted(kinds["vector"], key=lambda r: r["vector_index"])
    assert [v["vector_index"] for v in vrows] == [0, 1]
    assert {decode_lane_structs(v["inputs"])["vin"] for v in vrows} == {3.3, 5.0}
    # One measurement nested on each variant vector; none step-scope.
    assert "measurement" not in kinds
    assert not step["measurements"]
    assert [len(v["measurements"]) for v in vrows] == [1, 1]
    assert [v["measurements"][0]["name"] for v in vrows] == ["vout", "vout"]
    assert {v["measurements"][0]["value"] for v in vrows} == {3.3, 5.0}


# ---------------------------------------------------------------------------
# Scenario 3 — self-loop (Mode 2): ONE step + 3 in-body vector rows (vec 0/1/2)
#   + measurements under each. NO extra scope vector (iterations are the
#   carriers).
# ---------------------------------------------------------------------------


def test_scenario_3_self_loop_mode2(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    acc.on_event(
        StepStarted(
            session_id=sid, run_id=rid, step_name="test_sweep", step_index=0, step_path="test_sweep"
        )
    )
    for vi in (0, 1, 2):
        acc.on_event(
            VectorStarted(
                session_id=sid,
                run_id=rid,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=vi,
                inputs={"vin": float(vi)},
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=sid,
                run_id=rid,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=vi,
                measurement_name="vout",
                value=float(vi),
                outcome="passed",
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=sid,
                run_id=rid,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=vi,
                outcome="passed",
                inputs={"vin": float(vi)},
            )
        )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_sweep",
            step_index=0,
            step_path="test_sweep",
            outcome="passed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path))

    assert len(kinds["run"]) == 1
    # ONE step row (the leaf step span), 3 in-body vector rows — NO scope vector
    # is synthesized for a looped step (the iterations are the carriers).
    assert len(kinds["step"]) == 1
    vrows = sorted(kinds["vector"], key=lambda r: r["vector_index"])
    assert [v["vector_index"] for v in vrows] == [0, 1, 2]
    for v in vrows:
        assert v["step_path"] == "test_sweep"
        assert v["vector_retry"] == 0
        assert v["vector_outcome"] == "passed"
        assert decode_lane_structs(v["inputs"]) == {"vin": float(v["vector_index"])}
    # One measurement nested under each in-body vector.
    assert "measurement" not in kinds
    assert [len(v["measurements"]) for v in vrows] == [1, 1, 1]
    assert [v["measurements"][0]["value"] for v in vrows] == [0.0, 1.0, 2.0]


# ---------------------------------------------------------------------------
# Scenario 4 — class-vectorized (outer × inner nesting): container TestC sweeps
#   temp=25 (a class-outer VectorStarted/Ended), method TestC/test_m runs under
#   it and adds vin=3.3 (step-scope configure). The container emits ONE leaf
#   vector; the method is step-scope and its step row carries the merged
#   {temp, vin} condition (pre-merge invariant: every row's inputs already
#   contains all enclosing conditions).
# ---------------------------------------------------------------------------


def test_scenario_4_class_container_x_method(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    # Outer class container — opens a step and emits its class-outer vector at
    # temp=25.
    acc.on_event(
        StepStarted(
            session_id=sid,
            run_id=rid,
            step_name="TestC",
            step_index=0,
            step_path="TestC",
            vector_index=0,
            class_name="TestC",
        )
    )
    acc.on_event(
        VectorStarted(
            session_id=sid,
            run_id=rid,
            step_name="TestC",
            step_index=0,
            step_path="TestC",
            vector_index=0,
            inputs={"temp": 25},
        )
    )
    # Inner method, parented to the container; runs inside the container's
    # vector 0 → vector_outer_index=0. StepStarted carries the inherited temp;
    # the method's own vin lands on StepEnded (step-scope configure).
    acc.on_event(
        StepStarted(
            session_id=sid,
            run_id=rid,
            step_name="test_m",
            step_index=0,
            step_path="TestC/test_m",
            vector_index=0,
            vector_outer_index=0,
            inputs={"temp": 25},
            class_name="TestC",
            function="test_m",
        )
    )
    acc.on_event(
        MeasurementRecorded(
            session_id=sid,
            run_id=rid,
            step_name="test_m",
            step_index=0,
            step_path="TestC/test_m",
            vector_index=0,
            vector_outer_index=0,
            measurement_name="vout",
            value=3.3,
            outcome="passed",
        )
    )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_m",
            step_index=0,
            step_path="TestC/test_m",
            vector_index=0,
            vector_outer_index=0,
            inputs={"temp": 25, "vin": 3.3},
            outcome="passed",
        )
    )
    acc.on_event(
        VectorEnded(
            session_id=sid,
            run_id=rid,
            step_name="TestC",
            step_index=0,
            step_path="TestC",
            vector_index=0,
            inputs={"temp": 25},
            outcome="passed",
        )
    )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="TestC",
            step_index=0,
            step_path="TestC",
            vector_index=0,
            outcome="passed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path))
    steps = {s["step_path"]: s for s in kinds["step"]}
    assert set(steps) == {"TestC", "TestC/test_m"}
    # All step rows carry vector_index=NULL; the enclosing outer coordinate is
    # vector_outer_index. Container is top-level → NULL; method is nested under
    # the container's vector 0 → 0.
    assert steps["TestC"]["vector_index"] is None
    assert steps["TestC"]["vector_outer_index"] is None
    assert steps["TestC/test_m"]["vector_index"] is None
    assert steps["TestC/test_m"]["vector_outer_index"] == 0
    # ONE leaf vector — the container's class-outer iteration at temp=25.
    vectors = {v["step_path"]: v for v in kinds["vector"]}
    assert set(vectors) == {"TestC"}
    assert decode_lane_structs(vectors["TestC"]["inputs"]) == {"temp": 25}
    # The measurement is step-scope on the method's step row.
    assert "measurement" not in kinds
    assert [m["name"] for m in steps["TestC/test_m"]["measurements"]] == ["vout"]
    # Pre-merge invariant: the method's step row already carries the full
    # merged condition {temp, vin} — no chain-walk needed at query time.
    assert decode_lane_structs(steps["TestC/test_m"]["inputs"]) == {"temp": 25, "vin": 3.3}


# ---------------------------------------------------------------------------
# Scenario 5 — retry. Mode 1 → the rerun is a second STEP row (step_retry 1),
#   ZERO vectors (non-looping). Mode 2 → a second in-body vector row, same
#   vector_index, retry=1.
# ---------------------------------------------------------------------------


def test_scenario_5_retry_mode1(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    # First attempt (retry=0) fails, second (retry=1) passes — non-looping
    # step, so the rerun is a distinct step row, no vector.
    for retry, outcome in ((0, "failed"), (1, "passed")):
        acc.on_event(
            StepStarted(
                session_id=sid,
                run_id=rid,
                step_name="test_v",
                step_index=0,
                step_path="test_v",
                retry=retry,
            )
        )
        acc.on_event(
            StepEnded(
                session_id=sid,
                run_id=rid,
                step_name="test_v",
                step_index=0,
                step_path="test_v",
                retry=retry,
                outcome=outcome,
            )
        )

    kinds = _by_kind(_materialize(acc, tmp_path))
    # De-fuse: StepStarted/StepEnded key on (step_path, step_retry,
    # vector_index), so the two attempts (step_retry 0 and 1) are TWO distinct
    # step execution rows — never fused. No vectors (non-looping step).
    assert "vector" not in kinds
    steps = sorted(kinds["step"], key=lambda r: r["step_retry"])
    assert len(steps) == 2
    assert [s["step_retry"] for s in steps] == [0, 1]
    assert [s["step_outcome"] for s in steps] == ["failed", "passed"]
    # Top-level step → enclosing vector_index NULL on both attempts.
    assert [s["vector_index"] for s in steps] == [None, None]

    path = materialize_run_to_parquet(acc, tmp_path / "results2", outcome="passed")
    assert path is not None
    step_rows = [r for r in pq.read_table(path).to_pylist() if r["record_type"] == "step"]
    # One step row per execution; no derived retry_count rollup column.
    assert "retry_count" not in step_rows[0]
    assert {r["step_retry"] for r in step_rows} == {0, 1}


def test_scenario_5_retry_mode2(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    acc.on_event(
        StepStarted(
            session_id=sid, run_id=rid, step_name="test_sweep", step_index=0, step_path="test_sweep"
        )
    )
    # Same vector_index=0, retry 0 (fail) then retry 1 (pass) — Mode 2.
    for retry, outcome in ((0, "failed"), (1, "passed")):
        acc.on_event(
            VectorStarted(
                session_id=sid,
                run_id=rid,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=0,
                retry=retry,
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=sid,
                run_id=rid,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=0,
                retry=retry,
                outcome=outcome,
            )
        )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_sweep",
            step_index=0,
            step_path="test_sweep",
            outcome="passed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path))
    # Two in-body vector rows (retries); NO extra scope vector (looped step).
    # vector_retry = the (step_path, vector_index) occurrence ordinal — vec 0
    # ran twice, so the two rows are ordinals 0 and 1.
    vrows = sorted(kinds["vector"], key=lambda r: r["vector_retry"])
    assert len(vrows) == 2
    assert [v["vector_index"] for v in vrows] == [0, 0]
    assert [v["vector_retry"] for v in vrows] == [0, 1]
    assert [v["vector_outcome"] for v in vrows] == ["failed", "passed"]


def test_scenario_5_retry_mode2_per_vector_ordinal(tmp_path):
    """vector_retry is the occurrence ordinal PER (step_path, vector_index).

    A 2-point in-body loop where only vec 1 retries in-body: vec 0 and vec 2
    each ran once (ordinal 0); vec 1 ran twice (ordinals 0 and 1). The ordinal
    is counted independently per vector point, not as a step-wide retry.
    """
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    acc.on_event(
        StepStarted(
            session_id=sid, run_id=rid, step_name="test_sweep", step_index=0, step_path="test_sweep"
        )
    )
    # Execution order: vec0(r0), vec1(r0 fail), vec1(r1 pass), vec2(r0).
    plan = ((0, 0, "passed"), (1, 0, "failed"), (1, 1, "passed"), (2, 0, "passed"))
    for tick, (vi, retry, outcome) in enumerate(plan):
        ts = datetime(2026, 6, 18, 12, 0, tick, tzinfo=UTC)
        acc.on_event(
            VectorStarted(
                session_id=sid,
                run_id=rid,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=vi,
                retry=retry,
                occurred_at=ts,
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=sid,
                run_id=rid,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=vi,
                retry=retry,
                outcome=outcome,
            )
        )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_sweep",
            step_index=0,
            step_path="test_sweep",
            outcome="passed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path))
    by_vec_retry = sorted(
        ((v["vector_index"], v["vector_retry"]) for v in kinds["vector"]),
    )
    # vec0→ord0, vec1→ord0 & ord1, vec2→ord0. The ordinal is per-vector.
    assert by_vec_retry == [(0, 0), (1, 0), (1, 1), (2, 0)]


# ---------------------------------------------------------------------------
# Scenario 6 — measurement-less. assert-only → step outcome=FAIL, ZERO
#   measurement rows, ZERO vectors, NO name="assert" row. observation-only →
#   outputs on the vector, zero measurements, NO NULL-named DONE row.
# ---------------------------------------------------------------------------


def test_scenario_6_assert_only_no_assert_row(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    acc.on_event(
        StepStarted(
            session_id=sid, run_id=rid, step_name="test_a", step_index=0, step_path="test_a"
        )
    )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_a",
            step_index=0,
            step_path="test_a",
            outcome="failed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path, outcome="failed"))
    assert "measurement" not in kinds  # no fabricated name="assert" row
    # Non-looping step → the execution is the step row itself; no vector.
    assert "vector" not in kinds
    assert len(kinds["step"]) == 1
    assert kinds["step"][0]["step_outcome"] == "failed"


def test_scenario_6_observation_only_no_null_done_row(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    acc.on_event(
        StepStarted(
            session_id=sid, run_id=rid, step_name="test_o", step_index=0, step_path="test_o"
        )
    )
    # observation-only Mode-2 vector — observation rides on the vector record's
    # outputs lanes; no measurement.
    acc.on_event(
        VectorStarted(
            session_id=sid,
            run_id=rid,
            step_name="test_o",
            step_index=0,
            step_path="test_o",
            vector_index=0,
        )
    )
    acc.on_event(
        VectorEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_o",
            step_index=0,
            step_path="test_o",
            vector_index=0,
            outcome="done",
            outputs={"temperature": 24.8},
        )
    )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_o",
            step_index=0,
            step_path="test_o",
            outcome="done",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path, outcome="passed"))
    assert "measurement" not in kinds  # no NULL-named DONE row
    assert len(kinds["vector"]) == 1
    v = kinds["vector"][0]
    assert decode_lane_structs(v["outputs"]) == {"temperature": 24.8}
    assert v["vector_outcome"] == "done"


# ---------------------------------------------------------------------------
# Scenario 7 — outside-loop step-scope data: data recorded in the step body
#   (via an Observation outside any in-body loop) homes on the step record's
#   own outputs lanes — no vector, no fabricated measurement.
# ---------------------------------------------------------------------------


def test_scenario_7_outside_loop_step_scope_data(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    acc.on_event(
        StepStarted(
            session_id=sid,
            run_id=rid,
            step_name="test_setup",
            step_index=0,
            step_path="test_setup",
            inputs={"vin": 3.3},
        )
    )
    # An observation recorded in the step body, outside any inner loop.
    acc.on_event(
        Observation(
            session_id=sid,
            run_id=rid,
            step_name="test_setup",
            step_index=0,
            step_path="test_setup",
            vector_index=0,
            name="ambient_temp",
            value=22.5,
        )
    )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_setup",
            step_index=0,
            step_path="test_setup",
            outcome="passed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path))
    # No measurement — the step-scope observation is NOT fabricated as one.
    assert "measurement" not in kinds
    # No vector loop ran → ZERO vector rows; the step-scope data homes on the
    # step record's own inputs/outputs lanes.
    assert "vector" not in kinds
    assert len(kinds["step"]) == 1
    step = kinds["step"][0]
    assert step["step_path"] == "test_setup"
    assert step["vector_index"] is None
    assert decode_lane_structs(step["inputs"]) == {"vin": 3.3}
    assert decode_lane_structs(step["outputs"]) == {"ambient_temp": 22.5}


# ---------------------------------------------------------------------------
# Scenario 8 — units on a step-scope row: an input/output carrying an
#   engineering unit flows into the lane's ``unit`` field on the step record
#   (no vector loop ran).
# ---------------------------------------------------------------------------


def test_scenario_8_vector_with_unit(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    acc.on_event(
        StepStarted(
            session_id=sid,
            run_id=rid,
            step_name="test_u",
            step_index=0,
            step_path="test_u",
            inputs={"vin": 3.3},
            input_units={"vin": "V"},
        )
    )
    acc.on_event(
        Observation(
            session_id=sid,
            run_id=rid,
            step_name="test_u",
            step_index=0,
            step_path="test_u",
            vector_index=0,
            name="temp",
            value=24.8,
            unit="°C",
        )
    )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_u",
            step_index=0,
            step_path="test_u",
            outcome="passed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path))
    # No vector loop ran → the data (with units) homes on the step row.
    assert "vector" not in kinds
    assert len(kinds["step"]) == 1
    step = kinds["step"][0]
    # Symmetric unit: an input unit AND an output unit, both on the lanes.
    assert _lane_units(step["inputs"]) == {"vin": "V"}
    assert _lane_units(step["outputs"]) == {"temp": "°C"}
    assert decode_lane_structs(step["inputs"]) == {"vin": 3.3}
    assert decode_lane_structs(step["outputs"]) == {"temp": 24.8}


# ---------------------------------------------------------------------------
# Permutation-table rows C/D — in-body loop with step-scope data on the step row.
#
# Row C: `def t(ctx, vectors)` — step carries setup inputs + N vector rows.
# The step's StepEnded.inputs (configure before the loop) and StepEnded.outputs
# (observe after the loop) ride on the step record; vectors carry the
# loop-specific measurements. Verifies steps-carry-own-data when an in-body
# loop also runs.
# ---------------------------------------------------------------------------


def test_row_c_inbody_loop_step_carries_setup_data(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    # Step with setup inputs (configure before loop) and teardown outputs
    # (observe after loop). The loop runs 2 in-body iterations.
    acc.on_event(
        StepStarted(
            session_id=sid,
            run_id=rid,
            step_name="test_sweep",
            step_index=0,
            step_path="test_sweep",
            inputs={"vin": 3.3},
        )
    )
    for vi, x in ((0, 10.0), (1, 20.0)):
        acc.on_event(
            VectorStarted(
                session_id=sid,
                run_id=rid,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=vi,
                inputs={"vin": 3.3, "x": x},
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=sid,
                run_id=rid,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=vi,
                measurement_name="vout",
                value=x * 1.1,
                outcome="passed",
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=sid,
                run_id=rid,
                step_name="test_sweep",
                step_index=0,
                step_path="test_sweep",
                vector_index=vi,
                outcome="passed",
                inputs={"vin": 3.3, "x": x},
            )
        )
    # StepEnded carries the step's own configure inputs + observe outputs.
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_sweep",
            step_index=0,
            step_path="test_sweep",
            inputs={"vin": 3.3},
            outputs={"ambient": 22.5},
            outcome="passed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path))

    # ONE step row — the loop step itself.
    assert len(kinds["step"]) == 1
    step = kinds["step"][0]
    assert step["step_path"] == "test_sweep"
    # Top-level step → enclosing vector_index is NULL (null-vs-0 reconstruction:
    # no parent emitted vectors, so _parent_emitted_vectors is False).
    assert step["vector_index"] is None

    # The step row carries its own inputs (from StepEnded) and outputs (observe
    # after the loop) — steps-carry-own-data, not shed to a synthesized vector.
    assert decode_lane_structs(step["inputs"]) == {"vin": 3.3}
    assert decode_lane_structs(step["outputs"]) == {"ambient": 22.5}

    # N in-body vector rows (vi=0, vi=1) carry the loop-specific measurements.
    vrows = sorted(kinds["vector"], key=lambda r: r["vector_index"])
    assert [v["vector_index"] for v in vrows] == [0, 1]
    assert [len(v["measurements"]) for v in vrows] == [1, 1]
    assert [v["measurements"][0]["name"] for v in vrows] == ["vout", "vout"]

    # Step row carries NO measurements (all inside the vectors); no fabricated scope vector.
    assert step["measurements"] == []
    assert "measurement" not in kinds


# ---------------------------------------------------------------------------
# Permutation-table row G — nested method in a NON-swept class.
#
# Row G: `class C: def m(ctx)` — C plain (no litmus_sweeps), m has no own
# sweep → at-rest: C step vi=NULL, m step vi=NULL, ZERO vector rows for
# either. Verifies null-vs-0 reconstruction: _parent_emitted_vectors("C/m")
# is False (C emitted no VectorStarted), so m's at-rest vector_index is NULL.
# ---------------------------------------------------------------------------


def test_row_g_nested_method_unswept_class_vector_index_null(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    # Container step for class C — no VectorStarted (C is not swept).
    acc.on_event(
        StepStarted(
            session_id=sid,
            run_id=rid,
            step_name="TestC",
            step_index=0,
            step_path="TestC",
            class_name="TestC",
        )
    )
    # Method m nested under C — no own vectors (m is not parametrized).
    acc.on_event(
        StepStarted(
            session_id=sid,
            run_id=rid,
            step_name="test_m",
            step_index=0,
            step_path="TestC/test_m",
            class_name="TestC",
            function="test_m",
        )
    )
    acc.on_event(
        MeasurementRecorded(
            session_id=sid,
            run_id=rid,
            step_name="test_m",
            step_index=0,
            step_path="TestC/test_m",
            vector_index=0,
            measurement_name="reading",
            value=1.0,
            outcome="passed",
        )
    )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="test_m",
            step_index=0,
            step_path="TestC/test_m",
            outcome="passed",
        )
    )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="TestC",
            step_index=0,
            step_path="TestC",
            outcome="passed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path))

    steps = {s["step_path"]: s for s in kinds["step"]}
    assert set(steps) == {"TestC", "TestC/test_m"}

    # Neither step is swept → both get vector_index=NULL at rest.
    # _parent_emitted_vectors("TestC") = False (no "/" in "TestC").
    assert steps["TestC"]["vector_index"] is None
    # _parent_emitted_vectors("TestC/test_m") = False (parent "TestC" has no VectorStarted).
    assert steps["TestC/test_m"]["vector_index"] is None

    # ZERO vector rows for either step — no loops ran.
    assert "vector" not in kinds

    # The measurement is step-scope on m's step row (no vector to carry it).
    assert [m["name"] for m in steps["TestC/test_m"]["measurements"]] == ["reading"]


# ---------------------------------------------------------------------------
# Permutation-table row J — @parametrize method in a plain (unswept) class.
#
# Row J: `class C: @parametrize(v=[0,1]) def m(ctx)` — C is plain (not swept
# with litmus_sweeps), m has its own Mode-1 parametrize variants → m emits
# one VectorStarted/Ended per variant (vi=0/1). At rest:
#   - C step row: vector_index=NULL (top-level, no enclosing loop)
#   - C has NO vector rows (C is not swept)
#   - m (logical step, two variants sharing step_path) step row: vector_index=NULL
#     (parent C has no VectorStarted → _parent_emitted_vectors("C/m")=False)
#   - m has 2 vector rows (vi=0/1 from parametrize)
# ---------------------------------------------------------------------------


def test_row_j_parametrize_method_in_plain_class(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))

    # Container step for plain class C (no VectorStarted — not swept).
    acc.on_event(
        StepStarted(
            session_id=sid,
            run_id=rid,
            step_name="TestC",
            step_index=0,
            step_path="TestC",
            class_name="TestC",
        )
    )
    # Mode-1 parametrize: each variant is a separate pytest item sharing step_path.
    # Both variants carry StepStarted.vector_index=0 (enclosing=None for top-level C).
    for vi, v in ((0, "a"), (1, "b")):
        node_id = f"test_file.py::TestC::test_m[{v}]"
        acc.on_event(
            StepStarted(
                session_id=sid,
                run_id=rid,
                step_name="test_m",
                step_index=0,
                step_path="TestC/test_m",
                vector_index=0,
                class_name="TestC",
                function="test_m",
                node_id=node_id,
            )
        )
        acc.on_event(
            VectorStarted(
                session_id=sid,
                run_id=rid,
                step_name="test_m",
                step_index=0,
                step_path="TestC/test_m",
                vector_index=vi,
                inputs={"v": v},
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=sid,
                run_id=rid,
                step_name="test_m",
                step_index=0,
                step_path="TestC/test_m",
                vector_index=vi,
                measurement_name="result",
                value=float(vi),
                outcome="passed",
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=sid,
                run_id=rid,
                step_name="test_m",
                step_index=0,
                step_path="TestC/test_m",
                vector_index=vi,
                outcome="passed",
                inputs={"v": v},
            )
        )
        acc.on_event(
            StepEnded(
                session_id=sid,
                run_id=rid,
                step_name="test_m",
                step_index=0,
                step_path="TestC/test_m",
                vector_index=0,
                outcome="passed",
                node_id=node_id,
            )
        )
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name="TestC",
            step_index=0,
            step_path="TestC",
            outcome="passed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path))
    steps = {s["step_path"]: s for s in kinds["step"]}
    assert set(steps) == {"TestC", "TestC/test_m"}

    # C is plain (not swept) → top-level, no enclosing loop → vector_index=NULL.
    assert steps["TestC"]["vector_index"] is None
    # m's parent C has no VectorStarted → _parent_emitted_vectors("TestC/test_m")=False
    # → m step row vector_index=NULL even though m DOES have its own vectors below.
    assert steps["TestC/test_m"]["vector_index"] is None

    # C has NO vector rows (C is not swept with litmus_sweeps).
    c_vectors = [v for v in kinds.get("vector", []) if v["step_path"] == "TestC"]
    assert c_vectors == []

    # m has 2 vector rows (the @parametrize variants), each with own vi and measurement.
    m_vectors = sorted(
        [v for v in kinds.get("vector", []) if v["step_path"] == "TestC/test_m"],
        key=lambda v: v["vector_index"],
    )
    assert [v["vector_index"] for v in m_vectors] == [0, 1]
    assert [decode_lane_structs(v["inputs"])["v"] for v in m_vectors] == ["a", "b"]
    assert [v["measurements"][0]["name"] for v in m_vectors] == ["result", "result"]

    # Step row for m carries NO measurements (all in the variant vector rows).
    assert steps["TestC/test_m"]["measurements"] == []
