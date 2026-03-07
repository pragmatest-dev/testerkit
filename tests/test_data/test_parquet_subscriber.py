"""Tests for ParquetSubscriber."""

from datetime import UTC, datetime
from uuid import uuid4

import pyarrow.parquet as pq

from litmus.data.backends.parquet import ParquetBackend, ParquetSubscriber
from litmus.data.events import (
    InstrumentConnected,
    MeasurementRecorded,
    RunEnded,
    SessionStarted,
)


class TestParquetSubscriber:
    def test_accumulates_and_writes(self, tmp_path):
        backend = ParquetBackend(results_dir=str(tmp_path / "results"))
        sub = ParquetSubscriber(backend)
        sub.open()

        run_id = uuid4()
        session_id = uuid4()

        sub.on_event(SessionStarted(
            session_id=session_id,
            run_id=run_id,
            station_id="st1",
            dut_serial="SN001",
            occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
        ))

        sub.on_event(MeasurementRecorded(
            session_id=session_id,
            run_id=run_id,
            step_name="test_voltage",
            step_index=0,
            measurement_name="vout",
            value=3.3,
            units="V",
            outcome="pass",
            low_limit=3.0,
            high_limit=3.6,
        ))

        sub.on_event(RunEnded(
            session_id=session_id,
            run_id=run_id,
            outcome="pass",
        ))

        # Parquet file should be written
        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        assert len(pq_files) == 1

        table = pq.read_table(pq_files[0])
        assert table.num_rows == 1
        row = table.to_pylist()[0]
        assert row["measurement_name"] == "vout"
        assert row["value"] == 3.3
        assert row["station_id"] == "st1"
        assert row["dut_serial"] == "SN001"
        assert row["run_outcome"] == "pass"

    def test_instruments_cached(self, tmp_path):
        backend = ParquetBackend(results_dir=str(tmp_path / "results"))
        sub = ParquetSubscriber(backend)
        sub.open()

        run_id = uuid4()
        session_id = uuid4()

        sub.on_event(SessionStarted(
            session_id=session_id,
            run_id=run_id,
            station_id="st1",
            dut_serial="SN001",
            occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
        ))

        sub.on_event(InstrumentConnected(
            session_id=session_id,
            run_id=run_id,
            role="dmm",
            instrument_id="keithley_001",
            resource="GPIB::16",
            manufacturer="Keithley",
            model="2000",
        ))

        sub.on_event(MeasurementRecorded(
            session_id=session_id,
            run_id=run_id,
            step_name="step1",
            step_index=0,
            measurement_name="v",
            value=1.0,
            outcome="pass",
        ))

        sub.close()

        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        table = pq.read_table(pq_files[0])
        row = table.to_pylist()[0]
        assert row["instr_name"] == ["dmm"]
        assert row["instr_manufacturer"] == ["Keithley"]

    def test_close_without_run_ended(self, tmp_path):
        """close() writes even if RunEnded was not emitted (crash recovery)."""
        backend = ParquetBackend(results_dir=str(tmp_path / "results"))
        sub = ParquetSubscriber(backend)
        sub.open()

        run_id = uuid4()
        sub.on_event(SessionStarted(
            run_id=run_id,
            station_id="st1",
            dut_serial="SN001",
            occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
        ))
        sub.on_event(MeasurementRecorded(
            run_id=run_id,
            step_name="s",
            step_index=0,
            measurement_name="v",
            value=1.0,
            outcome="pass",
        ))

        sub.close()

        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        assert len(pq_files) == 1

    def test_no_measurements_no_file(self, tmp_path):
        """No Parquet written when no measurements accumulated."""
        backend = ParquetBackend(results_dir=str(tmp_path / "results"))
        sub = ParquetSubscriber(backend)
        sub.open()

        sub.on_event(SessionStarted(
            run_id=uuid4(),
            station_id="st1",
            dut_serial="SN001",
            occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
        ))

        sub.close()

        runs_dir = tmp_path / "results" / "runs"
        pq_files = list(runs_dir.rglob("*.parquet")) if runs_dir.exists() else []
        assert len(pq_files) == 0
