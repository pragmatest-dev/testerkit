"""Shared fixtures for data exporter/subscriber tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from litmus.data.models import DUT, Measurement, Outcome, TestRun, TestStep, TestVector


@pytest.fixture
def realistic_test_run() -> TestRun:
    """A realistic TestRun exercising all comparator types and edge cases.

    Structure:
      - 3 steps: PASS (2 vectors), FAIL (1 vector), SKIP (empty vectors)
      - All 10 comparator types spread across measurements
      - value=None measurement (error case)
      - Nested step_path ("power/output/voltage")
      - DUT with all fields, custom_metadata, instrument_arrays
    """
    return TestRun(
        id=uuid4(),
        started_at=datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 3, 4, 10, 5, 0, tzinfo=UTC),
        dut=DUT(
            serial="DUT-001",
            part_number="PN-200",
            revision="B",
            lot_number="LOT-42",
        ),
        station_id="station_alpha",
        station_name="Alpha Bench",
        station_type="power_test",
        station_location="Lab 3",
        product_id="PROD-100",
        product_name="Widget Pro",
        product_revision="2.1",
        fixture_id="FIX-007",
        test_sequence_id="seq_power_validation",
        test_phase="qualification",
        operator_id="OP-42",
        operator_name="Jane Doe",
        git_commit="abc1234",
        outcome=Outcome.FAIL,
        custom_metadata={"batch": "2026-Q1", "temperature": 25.0},
        steps=[
            # Step 0: PASS with 2 vectors, nested path, instrument arrays
            TestStep(
                name="voltage_check",
                step_path="power/output/voltage",
                description="Output voltage under load",
                started_at=datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC),
                ended_at=datetime(2026, 3, 4, 10, 1, 0, tzinfo=UTC),
                outcome=Outcome.PASS,
                instrument_arrays={
                    "instr_name": ["DMM_01", "PSU_01"],
                    "instr_resource": ["TCPIP::10.0.0.1", "TCPIP::10.0.0.2"],
                    "instr_driver": ["Keysight34465A", "KeysightE36312A"],
                },
                vectors=[
                    TestVector(
                        index=0,
                        attempt=1,
                        params={"vin": 5.0, "load": 100.0},
                        observations={"temp_ambient": 24.8},
                        outcome=Outcome.PASS,
                        started_at=datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC),
                        ended_at=datetime(2026, 3, 4, 10, 0, 30, tzinfo=UTC),
                        measurements=[
                            Measurement(
                                name="vout",
                                value=3.30,
                                units="V",
                                low_limit=3.0,
                                high_limit=3.6,
                                comparator="GELE",
                                outcome=Outcome.PASS,
                                dut_pin="VOUT",
                                instrument_name="DMM_01",
                                spec_id="SPEC-001",
                            ),
                            Measurement(
                                name="iout",
                                value=0.50,
                                units="A",
                                low_limit=0.0,
                                high_limit=1.0,
                                comparator="GELT",
                                outcome=Outcome.PASS,
                                spec_id="SPEC-002",
                            ),
                            Measurement(
                                name="vout_exclusive",
                                value=3.31,
                                units="V",
                                low_limit=3.0,
                                high_limit=3.6,
                                comparator="GTLT",
                                outcome=Outcome.PASS,
                            ),
                            Measurement(
                                name="vout_gt_low",
                                value=3.31,
                                units="V",
                                low_limit=3.0,
                                high_limit=3.6,
                                comparator="GTLE",
                                outcome=Outcome.PASS,
                            ),
                        ],
                    ),
                    TestVector(
                        index=1,
                        attempt=1,
                        params={"vin": 12.0, "load": 200.0},
                        observations={"temp_ambient": 25.1},
                        outcome=Outcome.PASS,
                        started_at=datetime(2026, 3, 4, 10, 0, 30, tzinfo=UTC),
                        ended_at=datetime(2026, 3, 4, 10, 1, 0, tzinfo=UTC),
                        measurements=[
                            Measurement(
                                name="vout",
                                value=3.29,
                                units="V",
                                low_limit=3.0,
                                high_limit=3.6,
                                comparator="GELE",
                                outcome=Outcome.PASS,
                            ),
                            Measurement(
                                name="vref_eq",
                                value=1.25,
                                units="V",
                                nominal=1.25,
                                comparator="EQ",
                                outcome=Outcome.PASS,
                            ),
                        ],
                    ),
                ],
            ),
            # Step 1: FAIL with 1 vector
            TestStep(
                name="current_limit",
                step_path="power/protection",
                description="Over-current protection test",
                started_at=datetime(2026, 3, 4, 10, 1, 0, tzinfo=UTC),
                ended_at=datetime(2026, 3, 4, 10, 2, 0, tzinfo=UTC),
                outcome=Outcome.FAIL,
                vectors=[
                    TestVector(
                        index=0,
                        attempt=1,
                        params={"vin": 5.0},
                        outcome=Outcome.FAIL,
                        started_at=datetime(2026, 3, 4, 10, 1, 0, tzinfo=UTC),
                        ended_at=datetime(2026, 3, 4, 10, 2, 0, tzinfo=UTC),
                        measurements=[
                            Measurement(
                                name="ilimit",
                                value=2.5,
                                units="A",
                                high_limit=2.0,
                                comparator="LE",
                                outcome=Outcome.FAIL,
                            ),
                            Measurement(
                                name="threshold_ne",
                                value=0.0,
                                units="V",
                                nominal=0.0,
                                comparator="NE",
                                outcome=Outcome.FAIL,
                            ),
                            Measurement(
                                name="dropout_lt",
                                value=0.3,
                                units="V",
                                high_limit=0.5,
                                comparator="LT",
                                outcome=Outcome.PASS,
                            ),
                            Measurement(
                                name="bias_ge",
                                value=1.0,
                                units="mA",
                                low_limit=0.5,
                                comparator="GE",
                                outcome=Outcome.PASS,
                            ),
                            Measurement(
                                name="leakage_gt",
                                value=0.01,
                                units="uA",
                                low_limit=0.001,
                                comparator="GT",
                                outcome=Outcome.PASS,
                            ),
                            # value=None → ERROR case
                            Measurement(
                                name="broken_sensor",
                                value=None,
                                units="V",
                                low_limit=0.0,
                                high_limit=5.0,
                                comparator="GELE",
                                outcome=Outcome.ERROR,
                            ),
                        ],
                    ),
                ],
            ),
            # Step 2: SKIP with empty vectors
            TestStep(
                name="thermal_shutdown",
                step_path="power/protection/thermal",
                description="Thermal protection (skipped)",
                started_at=datetime(2026, 3, 4, 10, 2, 0, tzinfo=UTC),
                ended_at=datetime(2026, 3, 4, 10, 2, 0, tzinfo=UTC),
                outcome=Outcome.SKIP,
                vectors=[],
            ),
        ],
    )


def _replay_events(
    test_run: TestRun,
    subscriber: Any,
) -> None:
    """Replay a TestRun as the event sequence a real run produces.

    Generates: RunStarted → StepStarted → MeasurementRecorded* →
    StepEnded → ... → RunEnded, feeding each to subscriber.on_event().
    """
    from litmus.data.events import (
        MeasurementRecorded,
        RunEnded,
        RunStarted,
        StepEnded,
        StepStarted,
    )

    session_id = test_run.session_id
    run_id = test_run.id

    # RunStarted
    subscriber.on_event(
        RunStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=test_run.started_at,
            station_id=test_run.station_id,
            station_name=test_run.station_name,
            station_type=test_run.station_type,
            station_location=test_run.station_location,
            dut_serial=test_run.dut.serial,
            dut_part_number=test_run.dut.part_number,
            dut_revision=test_run.dut.revision,
            dut_lot_number=test_run.dut.lot_number,
            product_id=test_run.product_id,
            product_name=test_run.product_name,
            product_revision=test_run.product_revision,
            operator_id=test_run.operator_id,
            operator_name=test_run.operator_name,
            fixture_id=test_run.fixture_id,
            sequence_id=test_run.test_sequence_id,
            test_phase=test_run.test_phase,
            git_commit=test_run.git_commit,
            custom_metadata=test_run.custom_metadata,
        )
    )

    # Steps
    for step_idx, step in enumerate(test_run.steps):
        subscriber.on_event(
            StepStarted(
                session_id=session_id,
                run_id=run_id,
                occurred_at=step.started_at,
                step_name=step.name,
                step_index=step_idx,
                step_path=step.step_path,
                description=step.description,
                node_id=step.node_id,
                file=step.file,
                module=step.module,
                class_name=step.class_name,
                function=step.function,
            )
        )

        for vector in step.vectors:
            for meas in vector.measurements:
                subscriber.on_event(
                    MeasurementRecorded(
                        session_id=session_id,
                        run_id=run_id,
                        step_name=step.name,
                        step_index=step_idx,
                        step_path=step.step_path,
                        vector_index=vector.index,
                        attempt=vector.attempt,
                        measurement_name=meas.name,
                        value=meas.value,
                        units=meas.units,
                        outcome=str(meas.outcome) if meas.outcome else None,
                        low_limit=meas.low_limit,
                        high_limit=meas.high_limit,
                        nominal=meas.nominal,
                        comparator=meas.comparator,
                        spec_id=meas.spec_id,
                        spec_ref=meas.spec_ref,
                        meas_dut_pin=meas.dut_pin,
                        meas_instrument=meas.instrument_name,
                        meas_instrument_resource=meas.instrument_resource,
                        meas_instrument_channel=meas.instrument_channel,
                        meas_fixture_point=meas.fixture_point,
                        inputs=vector.params,
                        outputs=vector.observations,
                    )
                )

        subscriber.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                occurred_at=step.ended_at or step.started_at,
                step_name=step.name,
                step_index=step_idx,
                step_path=step.step_path,
                outcome=str(step.outcome),
            )
        )

    # RunEnded
    subscriber.on_event(
        RunEnded(
            session_id=session_id,
            run_id=run_id,
            outcome=str(test_run.outcome),
        )
    )


@pytest.fixture
def replay_events() -> Callable[[TestRun, Any], None]:
    """Return the event replay function for subscriber tests."""
    return _replay_events
