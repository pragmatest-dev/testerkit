"""Measurement attribution + inputs-lane sourcing — de-fuse correctness guards.

Encodes ``runs-execution-model.md`` ("Inputs are vector-scoped and stable"):

* A Mode-1 rerun records measurements under a distinct ``step_retry``; each
  attempt's step row carries ONLY its own measurements (no retry collapse),
  and ``measurement_count`` is per-attempt — not summed across attempts.
* The inputs lane uses Start while in-flight and lets End override at finalize,
  so a ``configure()`` value added in the test body is honored at rest.
* The live overlay (``snapshot_measurement_rows``) attributes identically to the
  materialized parquet (no drift).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pyarrow.parquet as pq

from testerkit.data.backends._event_accumulator import EventAccumulator
from testerkit.data.backends.parquet import materialize_run_to_parquet
from testerkit.data.events import (
    MeasurementRecorded,
    RunStarted,
    StepEnded,
    StepStarted,
)

_T0 = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
_SP = "s"


def _new_acc():
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(
        RunStarted(
            session_id=sid, run_id=rid, station_id="st1", uut_serial_number="SN1", occurred_at=_T0
        )
    )
    return acc, rid, sid


def _step_start(acc, sid, rid, sr, inputs=None):
    acc.on_event(
        StepStarted(
            session_id=sid,
            run_id=rid,
            step_name=_SP,
            step_index=0,
            step_path=_SP,
            retry=sr,
            inputs=inputs or {},
        )
    )


def _step_end(acc, sid, rid, sr, outcome="passed", inputs=None):
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name=_SP,
            step_index=0,
            step_path=_SP,
            retry=sr,
            outcome=outcome,
            inputs=inputs or {},
        )
    )


def _measure(acc, sid, rid, *, sr, value, outcome):
    acc.on_event(
        MeasurementRecorded(
            session_id=sid,
            run_id=rid,
            step_name=_SP,
            step_index=0,
            step_path=_SP,
            vector_index=0,
            step_retry=sr,
            retry=0,
            measurement_name="m",
            value=value,
            outcome=outcome,
        )
    )


def _materialize(acc, tmp_path):
    path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")
    assert path is not None
    return pq.read_table(path).to_pylist()


def test_mode1_rerun_measurements_attributed_per_attempt(tmp_path):
    """Attempt 0 fails with m=1.0, attempt 1 passes with m=2.0 — no collision."""
    acc, rid, sid = _new_acc()
    _step_start(acc, sid, rid, 0)
    _measure(acc, sid, rid, sr=0, value=1.0, outcome="failed")
    _step_end(acc, sid, rid, 0, "failed")
    _step_start(acc, sid, rid, 1)
    _measure(acc, sid, rid, sr=1, value=2.0, outcome="passed")
    _step_end(acc, sid, rid, 1, "passed")

    rows = _materialize(acc, tmp_path)
    # No vector loop ran → the measurements are step-scope; each attempt is its
    # own step row (de-fused by step_retry), no vector rows.
    assert not [r for r in rows if r["record_type"] == "vector"]
    steps = [r for r in rows if r["record_type"] == "step"]
    assert len(steps) == 2
    # Each attempt's step row carries ONLY its own measurement (not both).
    per_attempt = {s["step_retry"]: [m["value"] for m in (s["measurements"] or [])] for s in steps}
    assert per_attempt == {0: [1.0], 1: [2.0]}


def test_mode1_rerun_measurement_count_is_per_attempt(tmp_path):
    acc, rid, sid = _new_acc()
    _step_start(acc, sid, rid, 0)
    _measure(acc, sid, rid, sr=0, value=1.0, outcome="failed")
    _step_end(acc, sid, rid, 0, "failed")
    _step_start(acc, sid, rid, 1)
    _measure(acc, sid, rid, sr=1, value=2.0, outcome="passed")
    _step_end(acc, sid, rid, 1, "passed")

    step_rows = acc.snapshot_step_rows()
    counts = {r["step_retry"]: r["measurement_count"] for r in step_rows}
    assert counts == {0: 1, 1: 1}  # NOT {0: 2, 1: 2}


def test_per_attempt_outcome_recoverable_fpy(tmp_path):
    """First-attempt outcome (FPY) and final outcome are independently recoverable."""
    acc, rid, sid = _new_acc()
    _step_start(acc, sid, rid, 0)
    _measure(acc, sid, rid, sr=0, value=1.0, outcome="failed")
    _step_end(acc, sid, rid, 0, "failed")
    _step_start(acc, sid, rid, 1)
    _measure(acc, sid, rid, sr=1, value=2.0, outcome="passed")
    _step_end(acc, sid, rid, 1, "passed")

    step_rows = acc.snapshot_step_rows()
    by_attempt = {r["step_retry"]: r["outcome"] for r in step_rows}
    assert by_attempt == {0: "failed", 1: "passed"}
    first_attempt = by_attempt[min(by_attempt)]
    assert first_attempt == "failed"  # FPY denominator sees the first failure


def test_overlay_matches_materialized_for_rerun(tmp_path):
    """The live overlay attributes rerun measurements exactly like the parquet."""
    acc, rid, sid = _new_acc()
    _step_start(acc, sid, rid, 0)
    _measure(acc, sid, rid, sr=0, value=1.0, outcome="failed")
    _step_end(acc, sid, rid, 0, "failed")
    _step_start(acc, sid, rid, 1)
    _measure(acc, sid, rid, sr=1, value=2.0, outcome="passed")
    _step_end(acc, sid, rid, 1, "passed")

    # Step-scope measurements: attribution is by step_retry (vector_retry is 0
    # for both since no vector loop ran). Overlay and at-rest must agree.
    overlay = acc.snapshot_measurement_rows()
    overlay_facts = sorted((r["measurement_value"], r["step_retry"]) for r in overlay)
    rows = _materialize(acc, tmp_path)
    at_rest = sorted(
        (m["value"], s["step_retry"])
        for s in rows
        if s["record_type"] == "step"
        for m in (s["measurements"] or [])
    )
    assert overlay_facts == at_rest == [(1.0, 0), (2.0, 1)]


def test_configure_input_end_overrides_start_at_finalize():
    """A configure()-added input present at step end lands in the stored lane."""
    acc, rid, sid = _new_acc()
    _step_start(acc, sid, rid, 0, inputs={"vin": 5.0})
    _step_end(acc, sid, rid, 0, "passed", inputs={"vin": 5.0, "extra": 9.0})

    step_rows = acc.snapshot_step_rows()
    attrs = step_rows[0]["inputs_map"]
    assert attrs.get("vin") == "5.0"
    assert attrs.get("extra") == "9.0"  # honored from the End snapshot


def test_inputs_use_start_while_in_flight():
    """Before the End event, the lane reads the Start snapshot (overlay)."""
    acc, rid, sid = _new_acc()
    _step_start(acc, sid, rid, 0, inputs={"vin": 5.0})

    step_rows = acc.snapshot_step_rows()
    attrs = step_rows[0]["inputs_map"]
    assert attrs.get("vin") == "5.0"
    assert "extra" not in attrs
