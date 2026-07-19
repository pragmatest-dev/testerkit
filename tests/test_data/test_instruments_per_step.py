from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pyarrow.parquet as pq

from testerkit.data.backends._event_accumulator import EventAccumulator
from testerkit.data.backends.parquet import materialize_run_to_parquet
from testerkit.data.events import (
    InstrumentConnected,
    InstrumentReserved,
    MeasurementRecorded,
    RunStarted,
    StepEnded,
    StepStarted,
    VectorEnded,
    VectorStarted,
)

_DMM_RECORD = {
    "name": "dmm",
    "id": "dmm-001",
    "driver": "Keithley2000",
    "resource": "GPIB::16",
    "protocol": None,
    "manufacturer": "Keithley",
    "model": "2000",
    "serial_number": "SN-DMM",
    "firmware": None,
    "cal_due": None,
    "cal_last": None,
    "cal_certificate": None,
    "cal_lab": None,
    "mocked": False,
}


def _build_acc(run_id, session_id):
    acc = EventAccumulator()
    acc.on_event(
        RunStarted(
            session_id=session_id,
            run_id=run_id,
            station_id="st1",
            uut_serial_number="SN001",
            occurred_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        )
    )
    acc.on_event(
        InstrumentConnected(
            session_id=session_id,
            run_id=run_id,
            role="dmm",
            instrument_id="dmm-001",
            resource="GPIB::16",
            manufacturer="Keithley",
            model="2000",
            serial="SN-DMM",
            driver="Keithley2000",
        )
    )
    acc.on_event(
        InstrumentConnected(
            session_id=session_id,
            run_id=run_id,
            role="psu",
            instrument_id="psu-001",
            resource="GPIB::5",
            manufacturer="Keysight",
            model="E3631A",
            serial="SN-PSU",
            driver="KeysightE3631A",
        )
    )
    return acc


class TestInstrumentsPerStep:
    def test_step_with_dmm_only_excludes_psu(self, tmp_path):
        run_id = uuid4()
        session_id = uuid4()
        acc = _build_acc(run_id, session_id)

        acc.on_event(
            InstrumentReserved(
                session_id=session_id,
                run_id=run_id,
                role="dmm",
                instrument_id="dmm-001",
                resource="GPIB::16",
                waited_ms=0.0,
                step_index=1,
                step_retry=0,
            )
        )
        acc.on_event(
            StepStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="test_voltage",
                step_index=1,
                step_path="test_voltage",
                vector_index=0,
                occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_voltage",
                step_index=1,
                step_path="test_voltage",
                vector_index=0,
                measurement_name="vout",
                value=3.3,
                outcome="passed",
            )
        )
        acc.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_voltage",
                step_index=1,
                step_path="test_voltage",
                vector_index=0,
                outcome="passed",
                occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
            )
        )

        path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")
        assert path is not None

        table = pq.read_table(next((tmp_path / "results" / "runs").rglob("*.parquet")))
        rows = {r["record_type"]: r for r in table.to_pylist()}

        run_names = sorted(r["name"] for r in rows["run"]["instruments"])
        assert run_names == ["dmm", "psu"], "run row must have full inventory"

        step_names = sorted(r["name"] for r in rows["step"]["instruments"])
        assert step_names == ["dmm"], "step row must carry only the reserved dmm"

        # Non-looping step → no vector row; the reserved instrument rides on
        # the step record.
        assert "vector" not in rows

    def test_step_with_no_instruments_produces_empty_set(self, tmp_path):
        run_id = uuid4()
        session_id = uuid4()
        acc = _build_acc(run_id, session_id)

        acc.on_event(
            StepStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="test_self_check",
                step_index=1,
                step_path="test_self_check",
                vector_index=0,
                occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_self_check",
                step_index=1,
                step_path="test_self_check",
                vector_index=0,
                measurement_name="flag",
                value=1.0,
                outcome="passed",
            )
        )
        acc.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_self_check",
                step_index=1,
                step_path="test_self_check",
                vector_index=0,
                outcome="passed",
                occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
            )
        )

        path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")
        assert path is not None

        table = pq.read_table(next((tmp_path / "results" / "runs").rglob("*.parquet")))
        rows = {r["record_type"]: r for r in table.to_pylist()}

        run_names = sorted(r["name"] for r in rows["run"]["instruments"])
        assert run_names == ["dmm", "psu"], "run row must have full inventory"

        assert rows["step"]["instruments"] == [], "step with no reservations must be empty"
        # Non-looping step → no vector row.
        assert "vector" not in rows

    def test_mode2_inbody_vectors_inherit_step_instruments(self, tmp_path):
        run_id = uuid4()
        session_id = uuid4()
        acc = _build_acc(run_id, session_id)

        acc.on_event(
            InstrumentReserved(
                session_id=session_id,
                run_id=run_id,
                role="dmm",
                instrument_id="dmm-001",
                resource="GPIB::16",
                waited_ms=0.0,
                step_index=1,
                step_retry=0,
            )
        )
        acc.on_event(
            StepStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="test_sweep",
                step_index=1,
                step_path="test_sweep",
                vector_index=0,
                occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            )
        )
        acc.on_event(
            VectorStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="test_sweep",
                step_index=1,
                step_path="test_sweep",
                vector_index=0,
                retry=0,
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_sweep",
                step_index=1,
                step_path="test_sweep",
                vector_index=0,
                measurement_name="vout",
                value=3.3,
                outcome="passed",
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_sweep",
                step_index=1,
                step_path="test_sweep",
                vector_index=0,
                retry=0,
                outcome="passed",
            )
        )
        acc.on_event(
            VectorStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="test_sweep",
                step_index=1,
                step_path="test_sweep",
                vector_index=1,
                retry=0,
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_sweep",
                step_index=1,
                step_path="test_sweep",
                vector_index=1,
                measurement_name="vout",
                value=3.4,
                outcome="passed",
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_sweep",
                step_index=1,
                step_path="test_sweep",
                vector_index=1,
                retry=0,
                outcome="passed",
            )
        )
        acc.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_sweep",
                step_index=1,
                step_path="test_sweep",
                vector_index=0,
                outcome="passed",
                occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
            )
        )

        path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")
        assert path is not None

        table = pq.read_table(next((tmp_path / "results" / "runs").rglob("*.parquet")))
        all_rows = table.to_pylist()
        run_rows = [r for r in all_rows if r["record_type"] == "run"]
        step_rows = [r for r in all_rows if r["record_type"] == "step"]
        vector_rows = [r for r in all_rows if r["record_type"] == "vector"]

        assert len(run_rows) == 1
        assert sorted(r["name"] for r in run_rows[0]["instruments"]) == ["dmm", "psu"]

        assert len(step_rows) == 1
        assert sorted(r["name"] for r in step_rows[0]["instruments"]) == ["dmm"]

        assert len(vector_rows) == 2, "Mode-2 step must produce exactly two in-body vector rows"
        for vr in vector_rows:
            assert sorted(r["name"] for r in vr["instruments"]) == ["dmm"]

    def test_mock_run_reserved_instruments_populate_step_row(self, tmp_path):
        run_id = uuid4()
        session_id = uuid4()
        acc = EventAccumulator()
        acc.on_event(
            RunStarted(
                session_id=session_id,
                run_id=run_id,
                station_id="st1",
                uut_serial_number="SN001",
                occurred_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
            )
        )
        acc.on_event(
            InstrumentConnected(
                session_id=session_id,
                run_id=run_id,
                role="dmm",
                instrument_id="mock-dmm-001",
                resource="mock://dmm",
                mocked=True,
            )
        )
        acc.on_event(
            InstrumentReserved(
                session_id=session_id,
                run_id=run_id,
                role="dmm",
                instrument_id="mock-dmm-001",
                resource="mock://dmm",
                waited_ms=0.0,
                step_index=0,
                step_retry=0,
            )
        )
        acc.on_event(
            StepStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="test_mock_step",
                step_index=0,
                step_path="test_mock_step",
                vector_index=0,
                occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_mock_step",
                step_index=0,
                step_path="test_mock_step",
                vector_index=0,
                measurement_name="reading",
                value=1.0,
                outcome="passed",
            )
        )
        acc.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_mock_step",
                step_index=0,
                step_path="test_mock_step",
                vector_index=0,
                outcome="passed",
                occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
            )
        )

        path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")
        assert path is not None

        table = pq.read_table(next((tmp_path / "results" / "runs").rglob("*.parquet")))
        all_rows = table.to_pylist()
        run_rows = [r for r in all_rows if r["record_type"] == "run"]
        step_rows = [r for r in all_rows if r["record_type"] == "step"]
        vector_rows = [r for r in all_rows if r["record_type"] == "vector"]

        assert len(run_rows) == 1
        run_instr = run_rows[0]["instruments"]
        assert len(run_instr) == 1
        assert run_instr[0]["name"] == "dmm"
        assert run_instr[0]["mocked"] is True

        assert len(step_rows) == 1
        step_instr = step_rows[0]["instruments"]
        assert len(step_instr) == 1
        assert step_instr[0]["name"] == "dmm"
        assert step_instr[0]["mocked"] is True

        # Non-looping step → the reserved instrument rides on the step record;
        # no vector row is produced.
        assert vector_rows == []
