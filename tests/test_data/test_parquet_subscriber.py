"""Tests for ParquetSubscriber."""

from datetime import UTC, datetime
from uuid import uuid4

import pyarrow.parquet as pq

from litmus.data.backends.parquet import ParquetSubscriber, read_step_results
from litmus.data.events import (
    InstrumentConnected,
    MeasurementRecorded,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
)


class TestParquetSubscriber:
    def test_accumulates_and_writes(self, tmp_path):
        sub = ParquetSubscriber(tmp_path / "results")
        sub.open()

        run_id = uuid4()
        session_id = uuid4()

        sub.on_event(
            RunStarted(
                session_id=session_id,
                run_id=run_id,
                station_id="st1",
                dut_serial="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )

        sub.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_voltage",
                step_index=0,
                measurement_name="vout",
                value=3.3,
                units="V",
                outcome="passed",
                limit_low=3.0,
                limit_high=3.6,
            )
        )

        sub.on_event(
            RunEnded(
                session_id=session_id,
                run_id=run_id,
                outcome="passed",
            )
        )

        # Parquet file should be written
        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        assert len(pq_files) == 1

        table = pq.read_table(pq_files[0])
        # Run row + measurement row + step row (auto-emitted for the
        # measurement-bearing step). No StepStarted/StepEnded events
        # were emitted, so only one (step, vector_index=0) pair exists.
        rows_by_kind = {r["record_type"]: r for r in table.to_pylist()}
        assert "run" in rows_by_kind
        assert "measurement" in rows_by_kind
        meas_row = rows_by_kind["measurement"]
        assert meas_row["measurement_name"] == "vout"
        assert meas_row["measurement_value"] == 3.3
        assert meas_row["station_id"] == "st1"
        assert meas_row["dut_serial"] == "SN001"
        assert meas_row["run_outcome"] == "passed"

    def test_instruments_cached(self, tmp_path):
        sub = ParquetSubscriber(tmp_path / "results")
        sub.open()

        run_id = uuid4()
        session_id = uuid4()

        sub.on_event(
            RunStarted(
                session_id=session_id,
                run_id=run_id,
                station_id="st1",
                dut_serial="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )

        sub.on_event(
            InstrumentConnected(
                session_id=session_id,
                run_id=run_id,
                role="dmm",
                instrument_id="keithley_001",
                resource="GPIB::16",
                manufacturer="Keithley",
                model="2000",
            )
        )

        sub.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="step1",
                step_index=0,
                measurement_name="v",
                value=1.0,
                outcome="passed",
            )
        )

        sub.close()

        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        table = pq.read_table(pq_files[0])
        row = table.to_pylist()[0]
        assert row["step_instruments_name"] == ["dmm"]
        assert row["step_instruments_manufacturer"] == ["Keithley"]

    def test_close_without_run_ended(self, tmp_path):
        """close() writes even if RunEnded was not received (crash recovery)."""
        sub = ParquetSubscriber(tmp_path / "results")
        sub.open()

        run_id = uuid4()
        sub.on_event(
            RunStarted(
                run_id=run_id,
                station_id="st1",
                dut_serial="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )
        sub.on_event(
            MeasurementRecorded(
                run_id=run_id,
                step_name="s",
                step_index=0,
                measurement_name="v",
                value=1.0,
                outcome="passed",
            )
        )

        sub.close()

        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        assert len(pq_files) == 1

    def test_no_measurements_writes_run_row_only(self, tmp_path):
        """Run with no measurements still writes a parquet — the run row alone.

        With ``record_type='run'`` always emitted, even an empty run produces
        a single-row parquet that records the run identity. Lakehouse adopters
        can ``WHERE record_type = 'run'`` and find every run, regardless of
        whether it produced any measurements.
        """
        sub = ParquetSubscriber(tmp_path / "results")
        sub.open()

        sub.on_event(
            RunStarted(
                run_id=uuid4(),
                station_id="st1",
                dut_serial="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )

        sub.close()

        runs_dir = tmp_path / "results" / "runs"
        pq_files = list(runs_dir.rglob("*.parquet")) if runs_dir.exists() else []
        assert len(pq_files) == 1
        table = pq.read_table(pq_files[0])
        rows = table.to_pylist()
        assert len(rows) == 1
        assert rows[0]["record_type"] == "run"
        assert rows[0]["station_id"] == "st1"
        assert rows[0]["dut_serial"] == "SN001"

    def test_step_identity_columns(self, tmp_path):
        """Step code identity fields appear in Parquet rows."""
        sub = ParquetSubscriber(tmp_path / "results")
        sub.open()

        run_id = uuid4()
        session_id = uuid4()

        sub.on_event(
            RunStarted(
                session_id=session_id,
                run_id=run_id,
                station_id="st1",
                dut_serial="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )
        sub.on_event(
            StepStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="test_5v_rail",
                step_index=0,
                node_id="tests/test_power.py::TestPower::test_5v_rail",
                file="tests/test_power.py",
                module="tests.test_power",
                class_name="TestPower",
                function="test_5v_rail",
            )
        )
        sub.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_5v_rail",
                step_index=0,
                measurement_name="vout",
                value=5.01,
                outcome="passed",
            )
        )
        sub.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_5v_rail",
                step_index=0,
                outcome="passed",
            )
        )
        sub.on_event(
            RunEnded(
                session_id=session_id,
                run_id=run_id,
                outcome="passed",
            )
        )

        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        table = pq.read_table(pq_files[0])
        # Pick the measurement row — step identity columns are denormalized
        # onto it. The run row carries no step identity (NULL columns).
        rows = [r for r in table.to_pylist() if r["record_type"] == "measurement"]
        assert len(rows) == 1
        row = rows[0]
        assert row["step_node_id"] == "tests/test_power.py::TestPower::test_5v_rail"
        assert row["step_file"] == "tests/test_power.py"
        assert row["step_module"] == "tests.test_power"
        assert row["step_class"] == "TestPower"
        assert row["step_function"] == "test_5v_rail"

    def test_step_results_metadata(self, tmp_path):
        """Step results are written to Parquet file-level metadata."""
        sub = ParquetSubscriber(tmp_path / "results")
        sub.open()

        run_id = uuid4()
        session_id = uuid4()

        sub.on_event(
            RunStarted(
                session_id=session_id,
                run_id=run_id,
                station_id="st1",
                dut_serial="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )
        # Step 0: has measurement
        sub.on_event(
            StepStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="test_voltage",
                step_index=0,
                node_id="tests/test_hw.py::test_voltage",
                file="tests/test_hw.py",
                function="test_voltage",
            )
        )
        sub.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_voltage",
                step_index=0,
                measurement_name="vout",
                value=3.3,
                outcome="passed",
            )
        )
        sub.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_voltage",
                step_index=0,
                outcome="passed",
            )
        )
        # Step 1: no measurements (action step)
        sub.on_event(
            StepStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="configure_dut",
                step_index=1,
                node_id="tests/test_hw.py::configure_dut",
                file="tests/test_hw.py",
                function="configure_dut",
            )
        )
        sub.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="configure_dut",
                step_index=1,
                outcome="passed",
            )
        )
        sub.on_event(
            RunEnded(
                session_id=session_id,
                run_id=run_id,
                outcome="passed",
            )
        )

        pq_files = [
            f
            for f in (tmp_path / "results" / "runs").rglob("*.parquet")
            if not f.stem.endswith("_steps")
        ]
        manifest = read_step_results(pq_files[0])
        assert len(manifest) == 2
        assert manifest[0]["name"] == "test_voltage"
        assert manifest[0]["has_measurements"] is True
        assert manifest[0]["measurement_count"] == 1
        assert manifest[1]["name"] == "configure_dut"
        assert manifest[1]["has_measurements"] is False
        assert manifest[1]["measurement_count"] == 0
        assert manifest[1]["node_id"] == "tests/test_hw.py::configure_dut"

    def test_measurement_before_run_started(self, tmp_path):
        """Graceful fallback when RunStarted never arrives (crash recovery)."""
        sub = ParquetSubscriber(tmp_path / "results")
        sub.open()

        # Measurement arrives without a preceding RunStarted
        sub.on_event(
            MeasurementRecorded(
                run_id=uuid4(),
                step_name="s",
                step_index=0,
                measurement_name="v",
                value=1.0,
                outcome="passed",
            )
        )

        # close() should not crash — no RunStarted means no parquet written
        sub.close()

        runs_dir = tmp_path / "results" / "runs"
        pq_files = list(runs_dir.rglob("*.parquet")) if runs_dir.exists() else []
        assert len(pq_files) == 0

    def test_on_output_callback(self, tmp_path):
        """on_output callback is called with OutputFile after write."""
        from litmus.data.subscribers._output_file import OutputFile

        outputs: list[OutputFile] = []
        sub = ParquetSubscriber(tmp_path / "results", on_output=outputs.append)
        sub.open()

        run_id = uuid4()
        session_id = uuid4()

        sub.on_event(
            RunStarted(
                session_id=session_id,
                run_id=run_id,
                station_id="st1",
                dut_serial="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )
        sub.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_v",
                step_index=0,
                measurement_name="vout",
                value=3.3,
                outcome="passed",
            )
        )
        sub.on_event(
            RunEnded(
                session_id=session_id,
                run_id=run_id,
                outcome="passed",
            )
        )

        assert len(outputs) == 1
        assert outputs[0].format == "parquet"
        assert outputs[0].path.exists()
        assert outputs[0].run_id == str(run_id)
