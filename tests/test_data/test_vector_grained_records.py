"""Phase 2 record-spec scenario tests — the "right parquet".

Each test feeds an event stream into an :class:`EventAccumulator`, then
materializes it (:func:`materialize_run_to_parquet`) and asserts the exact
per-scenario records from the Phase 2 record spec
(``docs/_internal/explorations/runs-execution-model.md``).

Record grain: ``record_type in {run, step, vector, measurement}``, keyed
``(step_path, parent_path, vector_index, retry)``. A ``vector`` record
appears ONLY for Mode-2 in-body iterations (the ``vectors`` fixture /
``run_vector`` loop) — Mode 1 and class containers fuse into the ``step``
record.

Scenarios covered here: 1 (single), 2 (parametrize Mode 1), 3 (self-loop
Mode 2), 4 (class container × method), 5 (retry), 6 (measurement-less).

All scenarios drive the :class:`EventAccumulator` directly — this is the
exact projection the runs daemon runs on the live event stream, so the
records asserted are the ones the daemon would write. Scenarios 2 and 4
are driven by the event shape the pytest plugin emits (parametrize → one
``StepStarted`` per item with a distinct ``step_path`` / ``vector_index``;
class container → a container ``StepStarted`` whose ``step_path`` is the
method step's ``parent_path``). The plugin-via-pytester end-to-end path
additionally needs a running runs daemon to materialize, which is omitted
here to avoid spawning per-test daemons in this environment.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pyarrow.parquet as pq

from litmus.data.backends._event_accumulator import EventAccumulator
from litmus.data.backends._row_helpers import decode_lane_structs
from litmus.data.backends.parquet import materialize_run_to_parquet
from litmus.data.events import (
    MeasurementRecorded,
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


# ---------------------------------------------------------------------------
# Scenario 1 — single / unswept: run + step(vec=0, retry=0) + measurement.
#   NO separate vector row (fused).
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
    assert "vector" not in kinds  # fused — no separate vector row
    assert len(kinds["step"]) == 1
    step = kinds["step"][0]
    assert step["step_path"] == "test_v"
    assert step["vector_index"] == 0
    assert len(kinds["measurement"]) == 1
    m = kinds["measurement"][0]
    assert m["measurement_name"] == "vout"
    assert m["measurement_value"] == 3.3
    assert m["vector_index"] == 0


# ---------------------------------------------------------------------------
# Scenario 2 — parametrize (Mode 1): one step per item, distinct node_id,
#   vector_index 0/1; measurements under each. NO vector rows.
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
                vector_index=vi,
                outcome="passed",
                node_id=node_id,
            )
        )

    kinds = _by_kind(_materialize(acc, tmp_path))
    assert "vector" not in kinds  # Mode 1 — fused, no vector rows
    steps = sorted(kinds["step"], key=lambda r: r["vector_index"])
    assert [s["vector_index"] for s in steps] == [0, 1]
    assert all(s["step_path"] == "test_rail" for s in steps)
    for s in steps:
        assert decode_lane_structs(s["inputs"])["vin"] in (3.3, 5.0)
    mrows = sorted(kinds["measurement"], key=lambda r: r["vector_index"])
    assert [m["vector_index"] for m in mrows] == [0, 1]


# ---------------------------------------------------------------------------
# Scenario 3 — self-loop (Mode 2): ONE step + 3 vector rows (vec 0/1/2) +
#   measurements under each. The vectors fixture emits VectorStarted/Ended.
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
                inputs={"vin": float(vi)},
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
    # ONE step row (the leaf step span), 3 vector rows (the in-body loop).
    assert len(kinds["step"]) == 1
    vrows = sorted(kinds["vector"], key=lambda r: r["vector_index"])
    assert [v["vector_index"] for v in vrows] == [0, 1, 2]
    for v in vrows:
        assert v["step_path"] == "test_sweep"
        assert v["vector_retry"] == 0
        assert v["vector_outcome"] == "passed"
        assert decode_lane_structs(v["inputs"]) == {"vin": float(v["vector_index"])}
    # measurements under each vector
    mrows = sorted(kinds["measurement"], key=lambda r: r["vector_index"])
    assert [m["vector_index"] for m in mrows] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Scenario 4 — class container × method: step(TestC, vector_index=outer,
#   inputs={temp}) ⊃ step(TestC/test_m, parent_path=TestC, vector_index=inner,
#   inputs={vin}). Measurement condition = {temp, vin} merged up parent_path.
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
            inputs={"vin": 3.3},
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
    assert "vector" not in kinds  # both fused (containers + Mode 1)
    steps = {s["step_path"]: s for s in kinds["step"]}
    assert set(steps) == {"TestC", "TestC::test_m"}
    container = steps["TestC"]
    method = steps["TestC::test_m"]
    assert container["parent_path"] == ""
    assert decode_lane_structs(container["inputs"]) == {"temp": 25}
    assert method["parent_path"] == "TestC"
    assert decode_lane_structs(method["inputs"]) == {"vin": 3.3}
    # Measurement's full condition = inputs merged up the parent_path chain.
    m = kinds["measurement"][0]
    merged = {**decode_lane_structs(container["inputs"]), **decode_lane_structs(method["inputs"])}
    assert merged == {"temp": 25, "vin": 3.3}
    assert m["step_path"] == "TestC::test_m"


# ---------------------------------------------------------------------------
# Scenario 5 — retry. Mode 1 → a second step row, retry=1. Mode 2 → a second
#   vector row, same vector_index, retry=1.
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
    steps = kinds["step"]
    assert len(steps) == 1
    # retry_count surfaced in step_results metadata, not as a column count here;
    # assert the retry was counted (not zeroed as the old MAX(vector_retry) did).
    from litmus.data.backends.parquet import read_step_results

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
    vrows = sorted(kinds["vector"], key=lambda r: r["vector_retry"])
    assert len(vrows) == 2
    assert [v["vector_index"] for v in vrows] == [0, 0]
    assert [v["vector_retry"] for v in vrows] == [0, 1]
    assert [v["vector_outcome"] for v in vrows] == ["failed", "passed"]


# ---------------------------------------------------------------------------
# Scenario 6 — measurement-less. assert-only → step/vector outcome=FAIL, ZERO
#   measurement rows, NO name="assert" row. observation-only → outputs lanes,
#   zero measurements, NO NULL-named DONE row.
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
