"""v2 record-spec scenario tests — uniform vectors + vector-as-carrier.

Each test feeds an event stream into an :class:`EventAccumulator`, then
materializes it (:func:`materialize_run_to_parquet`) and asserts the exact
per-scenario records of the v2 model
(``docs/_internal/explorations/runs-execution-model.md``, the "v2 model"
section).

v2 grain: ``record_type in {run, step, vector}``, keyed
``(step_path, parent_path, vector_index, retry)``.

* **Every step execution materializes a ``vector`` row** — the scope
  vector, synthesized by the materializer (Decision A). A non-looping
  step (single / parametrize / class container) has exactly one; a Mode-2
  self-loop has one per in-body iteration instead.
* The ``step`` record carries ONLY code identity + timing + rolled-up
  outcome — it **sheds** ``inputs`` / ``outputs`` onto its scope vector.
* **Measurements are nested** in the vector row's ``measurements`` list;
  their conditions are the enclosing vector's ``inputs`` / ``outputs``.
  The daemon UNNESTs them into the flat measurement fact for queries.

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
from litmus.data.backends.parquet import materialize_run_to_parquet, read_step_results
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
        uut_serial="SN001",
        occurred_at=_T0,
    )


def _lane_units(entries):
    """name → unit for a lane-struct list (only entries that carry a unit)."""
    return {e["name"]: e["unit"] for e in (entries or []) if e.get("unit") is not None}


# ---------------------------------------------------------------------------
# Scenario 1 — single / unswept: run + step + ONE synthesized scope vector +
#   measurement. The step sheds inputs; the scope vector carries them.
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
    # v2: every execution materializes a scope vector — uniform.
    assert len(kinds["vector"]) == 1
    v = kinds["vector"][0]
    assert v["step_path"] == "test_v"
    assert v["vector_index"] == 0
    assert v["vector_retry"] == 0
    assert len(kinds["step"]) == 1
    step = kinds["step"][0]
    assert step["step_path"] == "test_v"
    assert step["vector_index"] == 0
    # The step sheds inputs/outputs onto the scope vector.
    assert decode_lane_structs(step["inputs"]) == {}
    assert decode_lane_structs(step["outputs"]) == {}
    # The measurement is nested on the scope vector, not a separate row.
    assert "measurement" not in kinds
    meas = v["measurements"]
    assert len(meas) == 1
    assert meas[0]["name"] == "vout"
    assert meas[0]["value"] == 3.3


# ---------------------------------------------------------------------------
# Scenario 2 — parametrize (Mode 1): one step per item + one scope vector per
#   item, vector_index 0/1; conditions on the scope vectors. NO in-body vectors.
# ---------------------------------------------------------------------------


def test_scenario_2_parametrize_mode1(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    for vi, vin in ((0, 3.3), (1, 5.0)):
        node_id = f"test_p.py::test_rail[{vin}]"
        acc.on_event(
            StepStarted(
                session_id=sid,
                run_id=rid,
                step_name="test_rail",
                step_index=0,
                step_path="test_rail",
                vector_index=vi,
                inputs={"vin": vin},
                node_id=node_id,
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
            StepEnded(
                session_id=sid,
                run_id=rid,
                step_name="test_rail",
                step_index=0,
                step_path="test_rail",
                vector_index=vi,
                outcome="passed",
                node_id=node_id,
            )
        )

    kinds = _by_kind(_materialize(acc, tmp_path))
    # One step + one scope vector per parametrize item; no in-body iteration.
    steps = sorted(kinds["step"], key=lambda r: r["vector_index"])
    assert [s["vector_index"] for s in steps] == [0, 1]
    assert all(s["step_path"] == "test_rail" for s in steps)
    assert all(decode_lane_structs(s["inputs"]) == {} for s in steps)
    vrows = sorted(kinds["vector"], key=lambda r: r["vector_index"])
    assert [v["vector_index"] for v in vrows] == [0, 1]
    assert {decode_lane_structs(v["inputs"])["vin"] for v in vrows} == {3.3, 5.0}
    # One measurement nested on each scope vector.
    assert "measurement" not in kinds
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
# Scenario 4 — class-vectorized (outer × inner nesting): container TestC at
#   temp=25 ⊃ method TestC::test_m at vin=3.3. Each gets its own scope vector;
#   measurement condition = {temp, vin} merged up parent_path.
# ---------------------------------------------------------------------------


def test_scenario_4_class_container_x_method(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    # Outer class container at temp=25.
    acc.on_event(
        StepStarted(
            session_id=sid,
            run_id=rid,
            step_name="TestC",
            step_index=0,
            step_path="TestC",
            vector_index=0,
            inputs={"temp": 25},
            class_name="TestC",
        )
    )
    # Inner method, parented to the container, at vin=3.3.
    acc.on_event(
        StepStarted(
            session_id=sid,
            run_id=rid,
            step_name="test_m",
            step_index=0,
            step_path="TestC::test_m",
            parent_path="TestC",
            vector_index=0,
            inputs={"vin": 3.3},
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
            step_path="TestC::test_m",
            vector_index=0,
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
            step_path="TestC::test_m",
            parent_path="TestC",
            vector_index=0,
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
    assert set(steps) == {"TestC", "TestC::test_m"}
    # Two scope vectors — one per step execution (outer container + inner method).
    vectors = {v["step_path"]: v for v in kinds["vector"]}
    assert set(vectors) == {"TestC", "TestC::test_m"}
    container_v = vectors["TestC"]
    method_v = vectors["TestC::test_m"]
    # Conditions live on the scope vectors; steps shed them.
    assert decode_lane_structs(steps["TestC"]["inputs"]) == {}
    assert decode_lane_structs(steps["TestC::test_m"]["inputs"]) == {}
    assert container_v["parent_path"] == ""
    assert decode_lane_structs(container_v["inputs"]) == {"temp": 25}
    assert method_v["parent_path"] == "TestC"
    assert decode_lane_structs(method_v["inputs"]) == {"vin": 3.3}
    # The measurement is nested on the method's scope vector.
    assert "measurement" not in kinds
    assert [m["name"] for m in method_v["measurements"]] == ["vout"]
    assert method_v["step_path"] == "TestC::test_m"
    # Measurement's full condition = inputs merged up the parent_path chain.
    merged = {
        **decode_lane_structs(container_v["inputs"]),
        **decode_lane_structs(method_v["inputs"]),
    }
    assert merged == {"temp": 25, "vin": 3.3}


# ---------------------------------------------------------------------------
# Scenario 5 — retry. Mode 1 → a second step + scope vector, retry counted.
#   Mode 2 → a second in-body vector row, same vector_index, retry=1.
# ---------------------------------------------------------------------------


def test_scenario_5_retry_mode1(tmp_path):
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(_run_started(rid, sid))
    # First attempt (retry=0) fails, second (retry=1) passes — Mode 1 fused.
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
    # StepStarted/StepEnded key on (step_path, vector_index); the retry rides
    # on the event. The manifest's retry_count reflects the re-execution.
    assert len(kinds["step"]) == 1
    # One scope vector (the step's execution, retry=0).
    assert len(kinds["vector"]) == 1

    path = materialize_run_to_parquet(acc, tmp_path / "results2", outcome="passed")
    assert path is not None
    manifest = read_step_results(path)
    assert manifest[0]["retry_count"] == 1


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
    vrows = sorted(kinds["vector"], key=lambda r: r["vector_retry"])
    assert len(vrows) == 2
    assert [v["vector_index"] for v in vrows] == [0, 0]
    assert [v["vector_retry"] for v in vrows] == [0, 1]
    assert [v["vector_outcome"] for v in vrows] == ["failed", "passed"]


# ---------------------------------------------------------------------------
# Scenario 6 — measurement-less. assert-only → step/scope-vector outcome=FAIL,
#   ZERO measurement rows, NO name="assert" row. observation-only → outputs on
#   the vector, zero measurements, NO NULL-named DONE row.
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
            vector_outcome="failed",
        )
    )

    kinds = _by_kind(_materialize(acc, tmp_path, outcome="failed"))
    assert "measurement" not in kinds  # no fabricated name="assert" row
    assert len(kinds["step"]) == 1
    assert kinds["step"][0]["step_outcome"] == "failed"
    # The execution is still represented by its (synthesized) scope vector.
    assert len(kinds["vector"]) == 1
    assert kinds["vector"][0]["vector_outcome"] == "failed"


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
#   (via an Observation outside any in-body loop) homes on the step's
#   synthesized scope vector, not on a measurement.
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
    assert len(kinds["step"]) == 1
    # The step-scope data homes on the synthesized scope vector.
    assert len(kinds["vector"]) == 1
    v = kinds["vector"][0]
    assert v["step_path"] == "test_setup"
    assert decode_lane_structs(v["inputs"]) == {"vin": 3.3}
    assert decode_lane_structs(v["outputs"]) == {"ambient_temp": 22.5}
    # The step record sheds both.
    assert decode_lane_structs(kinds["step"][0]["inputs"]) == {}
    assert decode_lane_structs(kinds["step"][0]["outputs"]) == {}


# ---------------------------------------------------------------------------
# Scenario 8 — a vector with a unit: an input/output carrying an engineering
#   unit flows into the lane's ``unit`` field on the scope vector.
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
    assert len(kinds["vector"]) == 1
    v = kinds["vector"][0]
    # Symmetric unit: an input unit AND an output unit, both on the lanes.
    assert _lane_units(v["inputs"]) == {"vin": "V"}
    assert _lane_units(v["outputs"]) == {"temp": "°C"}
    assert decode_lane_structs(v["inputs"]) == {"vin": 3.3}
    assert decode_lane_structs(v["outputs"]) == {"temp": 24.8}
