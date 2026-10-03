"""Event-stream builders for the live-channel tests (docs/41 §8 T1-T3, M1).

Each builder returns the event dicts a bench's event WAL would deliver to the live
pusher's pool (``AccumulatorPool.dispatch`` input), in order. Fixed identifiers and
timestamps — never ``datetime.now()`` — so the streams are deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from testerkit.data.events import (
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

RUN_ID = UUID("33333333-3333-3333-3333-333333333333")
SESSION_ID = UUID("44444444-4444-4444-4444-444444444444")
T0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


def ts(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def run_started(run_id: UUID = RUN_ID) -> RunStarted:
    return RunStarted(
        session_id=SESSION_ID,
        run_id=run_id,
        station_id="st1",
        station_name="Station One",
        station_type="bench",
        station_location="lab-2",
        station_hostname="host-1",
        site_index=0,
        uut_serial_number="SN001",
        uut_part_number="PN-100",
        uut_lot_number="LOT-9",
        part_id="part-1",
        part_name="Widget",
        part_revision="B",
        fixture_id="fix1",
        test_phase="production",
        operator_id="op-1",
        operator_name="Ada",
        project_name="proj-1",
        git_commit="abc1234",
        git_branch="main",
        occurred_at=ts(0),
    )


def _step_kwargs(name: str, idx: int, run_id: UUID) -> dict[str, Any]:
    return {
        "session_id": SESSION_ID,
        "run_id": run_id,
        "step_name": name,
        "step_index": idx,
        "step_path": name,
    }


def swept_step_events(
    name: str,
    idx: int,
    *,
    vectors: int,
    t: float,
    measurements_per_vector: int = 2,
    run_id: UUID = RUN_ID,
    finish: bool = True,
) -> list[Any]:
    """One swept step: ``vectors`` in-body vectors, each with N measurements."""
    kw = _step_kwargs(name, idx, run_id)
    out: list[Any] = [
        StepStarted(
            **kw,
            vector_index=None,
            node_id=f"tests/test_hw.py::{name}",
            file="tests/test_hw.py",
            module="tests.test_hw",
            function=name,
            occurred_at=ts(t),
        )
    ]
    for vi in range(vectors):
        vt = t + 0.01 * (vi + 1)
        out.append(
            VectorStarted(
                **kw,
                vector_index=vi,
                inputs={"vin": 1.0 + vi * 0.1},
                input_units={"vin": "V"},
                occurred_at=ts(vt),
            )
        )
        for m in range(measurements_per_vector):
            out.append(
                MeasurementRecorded(
                    **kw,
                    vector_index=vi,
                    measurement_name=f"vout_{m}",
                    measurement_timestamp=ts(vt),
                    value=2.0 + vi * 0.01 + m,
                    unit="V",
                    outcome="passed",
                    limit_low=1.0,
                    limit_high=9.0,
                    limit_comparator="GELE",
                    uut_pin=f"TP{m}",
                    occurred_at=ts(vt),
                )
            )
        out.append(
            VectorEnded(
                **kw,
                vector_index=vi,
                outcome="passed",
                inputs={"vin": 1.0 + vi * 0.1},
                input_units={"vin": "V"},
                occurred_at=ts(vt),
            )
        )
    if finish:
        out.append(
            StepEnded(
                **kw, vector_index=None, outcome="passed", occurred_at=ts(t + 0.01 * (vectors + 1))
            )
        )
    return out


def sweep_point_events(
    name: str, idx: int, vi: int, *, t: float, run_id: UUID = RUN_ID, measurements: int = 2
) -> list[Any]:
    """One more sweep point (vector ``vi``) on an already-started swept step."""
    kw = _step_kwargs(name, idx, run_id)
    out: list[Any] = [VectorStarted(**kw, vector_index=vi, inputs={"vin": 1.0}, occurred_at=ts(t))]
    out += [
        MeasurementRecorded(
            **kw,
            vector_index=vi,
            measurement_name=f"vout_{m}",
            value=2.0 + m,
            unit="V",
            outcome="passed",
            occurred_at=ts(t),
        )
        for m in range(measurements)
    ]
    out.append(
        VectorEnded(**kw, vector_index=vi, outcome="passed", inputs={"vin": 1.0}, occurred_at=ts(t))
    )
    return out


def plain_step_events(name: str, idx: int, *, t: float, run_id: UUID = RUN_ID) -> list[Any]:
    """A step-scoped (ambient) measurement step with an observation."""
    kw = _step_kwargs(name, idx, run_id)
    return [
        StepStarted(**kw, vector_index=None, occurred_at=ts(t)),
        MeasurementRecorded(
            **kw,
            vector_index=None,
            measurement_name="iout",
            value=0.5,
            unit="A",
            outcome="failed",
            limit_low=0.0,
            limit_high=0.4,
            occurred_at=ts(t + 0.1),
        ),
        MeasurementRecorded(
            **kw,
            vector_index=None,
            measurement_name="vrail",
            value=3.3,
            unit="V",
            outcome="passed",
            limit_low=3.0,
            limit_high=3.6,
            occurred_at=ts(t + 0.2),
        ),
        Observation(**kw, vector_index=None, name="temp", value=25.0, unit="C"),
        StepEnded(
            **kw,
            vector_index=None,
            outcome="failed",
            outputs={"temp": 25.0},
            output_units={"temp": "C"},
            occurred_at=ts(t + 0.3),
        ),
    ]


def retried_step_events(name: str, idx: int, *, t: float, run_id: UUID = RUN_ID) -> list[Any]:
    """A step that executed twice (step_retry 0 then 1), one vector each."""
    kw = _step_kwargs(name, idx, run_id)
    out: list[Any] = []
    for sr in (0, 1):
        base = t + sr * 10
        out.append(StepStarted(**kw, retry=sr, occurred_at=ts(base)))
        out.append(
            VectorStarted(**kw, vector_index=0, retry=sr, step_retry=sr, occurred_at=ts(base + 1))
        )
        out.append(
            MeasurementRecorded(
                **kw,
                vector_index=0,
                retry=sr,
                step_retry=sr,
                measurement_name="settle",
                value=0.9 + sr,
                unit="s",
                outcome="failed" if sr == 0 else "passed",
                limit_low=0.0,
                limit_high=1.0,
                occurred_at=ts(base + 1),
            )
        )
        out.append(
            VectorEnded(
                **kw,
                vector_index=0,
                retry=sr,
                step_retry=sr,
                outcome="failed" if sr == 0 else "passed",
                occurred_at=ts(base + 1),
            )
        )
        out.append(
            StepEnded(
                **kw, retry=sr, outcome="failed" if sr == 0 else "passed", occurred_at=ts(base + 2)
            )
        )
    return out


def class_outer_events(name: str, idx: int, *, t: float, run_id: UUID = RUN_ID) -> list[Any]:
    """A step nested under a class-level sweep: vector_outer_index 0 and 1."""
    kw = _step_kwargs(name, idx, run_id)
    out: list[Any] = []
    for voi in (0, 1):
        base = t + voi * 5
        out.append(
            StepStarted(
                **kw,
                vector_outer_index=voi,
                inputs={"temp_c": 25.0 + 40 * voi},
                input_units={"temp_c": "C"},
                occurred_at=ts(base),
            )
        )
        out.append(
            MeasurementRecorded(
                **kw,
                vector_index=None,
                vector_outer_index=voi,
                measurement_name="leak",
                value=0.001 * (voi + 1),
                unit="A",
                outcome="passed",
                limit_high=0.01,
                occurred_at=ts(base + 1),
            )
        )
        out.append(
            StepEnded(
                **kw,
                vector_outer_index=voi,
                outcome="passed",
                inputs={"temp_c": 25.0 + 40 * voi},
                input_units={"temp_c": "C"},
                occurred_at=ts(base + 2),
            )
        )
    return out


def representative_events(*, ended: bool = True, run_id: UUID = RUN_ID) -> list[Any]:
    """Swept + retried + class-outer + step-scoped-measurement steps, one run."""
    out: list[Any] = [
        run_started(run_id),
        InstrumentConnected(
            session_id=SESSION_ID,
            run_id=run_id,
            role="dmm",
            instrument_id="keithley_001",
            resource="GPIB::16",
            manufacturer="Keithley",
            model="2000",
        ),
        StepsDiscovered(
            session_id=SESSION_ID,
            run_id=run_id,
            items=[
                {
                    "node_id": "tests/test_hw.py::sweep",
                    "step_index": 0,
                    "step_path": "sweep",
                    "markers": "slow",
                    "vector_count_planned": 3,
                },
                {
                    "node_id": "tests/test_hw.py::plain",
                    "step_index": 1,
                    "step_path": "plain",
                    "markers": None,
                    "vector_count_planned": 1,
                },
            ],
        ),
    ]
    out += swept_step_events("sweep", 0, vectors=3, t=1, run_id=run_id)
    out += plain_step_events("plain", 1, t=3, run_id=run_id)
    out += retried_step_events("flaky", 2, t=5, run_id=run_id)
    out += class_outer_events("thermal", 3, t=30, run_id=run_id)
    if ended:
        out.append(
            RunEnded(session_id=SESSION_ID, run_id=run_id, outcome="failed", occurred_at=ts(60))
        )
    return out


def as_dicts(events: list[Any]) -> list[dict[str, Any]]:
    """Event models -> the dicts the EventStore subscription delivers."""
    return [e.model_dump(mode="json") for e in events]
