"""Tests for ParquetSubscriber."""

from datetime import UTC, datetime
from uuid import uuid4

import pyarrow.parquet as pq

from litmus.data.backends.parquet import ParquetBackend, ParquetSubscriber
from litmus.data.backends.parquet import read_step_manifest
from litmus.data.events import (
    InstrumentConnected,
    MeasurementRecorded,
    RunEnded,
    SessionStarted,
    StepEnded,
    StepStarted,
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

    def test_step_identity_columns(self, tmp_path):
        """Step code identity fields appear in Parquet rows."""
        backend = ParquetBackend(results_dir=str(tmp_path / "results"))
        sub = ParquetSubscriber(backend)
        sub.open()

        run_id = uuid4()
        session_id = uuid4()

        sub.on_event(SessionStarted(
            session_id=session_id, run_id=run_id,
            station_id="st1", dut_serial="SN001",
            occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
        ))
        sub.on_event(StepStarted(
            session_id=session_id, run_id=run_id,
            step_name="test_5v_rail", step_index=0,
            node_id="tests/test_power.py::TestPower::test_5v_rail",
            file="tests/test_power.py",
            module="tests.test_power",
            class_name="TestPower",
            function="test_5v_rail",
        ))
        sub.on_event(MeasurementRecorded(
            session_id=session_id, run_id=run_id,
            step_name="test_5v_rail", step_index=0,
            measurement_name="vout", value=5.01, outcome="pass",
        ))
        sub.on_event(StepEnded(
            session_id=session_id, run_id=run_id,
            step_name="test_5v_rail", step_index=0, outcome="pass",
        ))
        sub.on_event(RunEnded(
            session_id=session_id, run_id=run_id, outcome="pass",
        ))

        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        table = pq.read_table(pq_files[0])
        row = table.to_pylist()[0]
        assert row["step_node_id"] == "tests/test_power.py::TestPower::test_5v_rail"
        assert row["step_file"] == "tests/test_power.py"
        assert row["step_module"] == "tests.test_power"
        assert row["step_class"] == "TestPower"
        assert row["step_function"] == "test_5v_rail"

    def test_step_manifest_metadata(self, tmp_path):
        """Step manifest is written to Parquet file-level metadata."""
        backend = ParquetBackend(results_dir=str(tmp_path / "results"))
        sub = ParquetSubscriber(backend)
        sub.open()

        run_id = uuid4()
        session_id = uuid4()

        sub.on_event(SessionStarted(
            session_id=session_id, run_id=run_id,
            station_id="st1", dut_serial="SN001",
            occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
        ))
        # Step 0: has measurement
        sub.on_event(StepStarted(
            session_id=session_id, run_id=run_id,
            step_name="test_voltage", step_index=0,
            node_id="tests/test_hw.py::test_voltage",
            file="tests/test_hw.py", function="test_voltage",
        ))
        sub.on_event(MeasurementRecorded(
            session_id=session_id, run_id=run_id,
            step_name="test_voltage", step_index=0,
            measurement_name="vout", value=3.3, outcome="pass",
        ))
        sub.on_event(StepEnded(
            session_id=session_id, run_id=run_id,
            step_name="test_voltage", step_index=0, outcome="pass",
        ))
        # Step 1: no measurements (action step)
        sub.on_event(StepStarted(
            session_id=session_id, run_id=run_id,
            step_name="configure_dut", step_index=1,
            node_id="tests/test_hw.py::configure_dut",
            file="tests/test_hw.py", function="configure_dut",
        ))
        sub.on_event(StepEnded(
            session_id=session_id, run_id=run_id,
            step_name="configure_dut", step_index=1, outcome="pass",
        ))
        sub.on_event(RunEnded(
            session_id=session_id, run_id=run_id, outcome="pass",
        ))

        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        manifest = read_step_manifest(pq_files[0])
        assert len(manifest) == 2
        assert manifest[0]["name"] == "test_voltage"
        assert manifest[0]["has_measurements"] is True
        assert manifest[0]["measurement_count"] == 1
        assert manifest[1]["name"] == "configure_dut"
        assert manifest[1]["has_measurements"] is False
        assert manifest[1]["measurement_count"] == 0
        assert manifest[1]["node_id"] == "tests/test_hw.py::configure_dut"
