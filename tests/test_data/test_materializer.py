"""Tests for the parquet materializer (events → parquet on disk).

The materializer is :func:`materialize_run_to_parquet` — feed events
into an :class:`EventAccumulator`, then materialize. Mirrors what the
runs daemon does internally on ``RunEnded``.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pyarrow.parquet as pq

from litmus.data.backends._event_accumulator import EventAccumulator
from litmus.data.backends.parquet import (
    materialize_run_to_parquet,
    read_step_results,
    reconstruct_test_run_from_file,
)
from litmus.data.events import (
    InstrumentConnected,
    MeasurementRecorded,
    RunStarted,
    StepEnded,
    StepStarted,
)


class TestMaterializer:
    def test_accumulates_and_writes(self, tmp_path):
        acc = EventAccumulator()
        run_id = uuid4()
        session_id = uuid4()

        acc.on_event(
            RunStarted(
                session_id=session_id,
                run_id=run_id,
                station_id="st1",
                uut_serial_number="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )

        acc.on_event(
            MeasurementRecorded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_voltage",
                step_index=0,
                measurement_name="vout",
                value=3.3,
                unit="V",
                outcome="passed",
                limit_low=3.0,
                limit_high=3.6,
            )
        )

        parquet_path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")

        assert parquet_path is not None
        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        assert len(pq_files) == 1

        table = pq.read_table(pq_files[0])
        # Run row + vector row (carrying the nested measurement) + step row.
        # No StepStarted/StepEnded events were emitted, so only one
        # (step, vector_index=0) pair exists.
        rows_by_kind = {r["record_type"]: r for r in table.to_pylist()}
        assert "run" in rows_by_kind
        assert "vector" in rows_by_kind
        vec_row = rows_by_kind["vector"]
        assert [m["name"] for m in vec_row["measurements"]] == ["vout"]
        assert vec_row["measurements"][0]["value"] == 3.3
        assert vec_row["station_id"] == "st1"
        assert vec_row["uut_serial_number"] == "SN001"
        assert vec_row["run_outcome"] == "passed"

    def test_instruments_cached(self, tmp_path):
        acc = EventAccumulator()
        run_id = uuid4()
        session_id = uuid4()

        acc.on_event(
            RunStarted(
                session_id=session_id,
                run_id=run_id,
                station_id="st1",
                uut_serial_number="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )

        acc.on_event(
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

        acc.on_event(
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

        materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")

        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        table = pq.read_table(pq_files[0])
        row = table.to_pylist()[0]
        instruments = row["instruments"]
        assert len(instruments) == 1
        assert instruments[0]["name"] == "dmm"
        assert instruments[0]["manufacturer"] == "Keithley"

    def test_materialize_without_outcome_falls_back_to_aborted(self, tmp_path):
        """``outcome=None`` falls back to ``"aborted"`` — the orphan-sweep semantic."""
        acc = EventAccumulator()
        run_id = uuid4()
        acc.on_event(
            RunStarted(
                run_id=run_id,
                station_id="st1",
                uut_serial_number="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )
        acc.on_event(
            MeasurementRecorded(
                run_id=run_id,
                step_name="s",
                step_index=0,
                measurement_name="v",
                value=1.0,
                outcome="passed",
            )
        )

        parquet_path = materialize_run_to_parquet(acc, tmp_path / "results")

        assert parquet_path is not None
        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        assert len(pq_files) == 1
        table = pq.read_table(pq_files[0])
        run_row = next(r for r in table.to_pylist() if r["record_type"] == "run")
        assert run_row["run_outcome"] == "aborted"

    def test_no_measurements_writes_run_row_only(self, tmp_path):
        """Run with no measurements still writes a parquet — the run row alone.

        With ``record_type='run'`` always emitted, even an empty run produces
        a single-row parquet that records the run identity. Lakehouse adopters
        can ``WHERE record_type = 'run'`` and find every run, regardless of
        whether it produced any measurements.
        """
        acc = EventAccumulator()
        acc.on_event(
            RunStarted(
                run_id=uuid4(),
                station_id="st1",
                uut_serial_number="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )

        materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")

        runs_dir = tmp_path / "results" / "runs"
        pq_files = list(runs_dir.rglob("*.parquet")) if runs_dir.exists() else []
        assert len(pq_files) == 1
        table = pq.read_table(pq_files[0])
        rows = table.to_pylist()
        assert len(rows) == 1
        assert rows[0]["record_type"] == "run"
        assert rows[0]["station_id"] == "st1"
        assert rows[0]["uut_serial_number"] == "SN001"

    def test_step_identity_columns(self, tmp_path):
        """Step code identity fields appear in Parquet rows."""
        acc = EventAccumulator()
        run_id = uuid4()
        session_id = uuid4()

        acc.on_event(
            RunStarted(
                session_id=session_id,
                run_id=run_id,
                station_id="st1",
                uut_serial_number="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )
        acc.on_event(
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
        acc.on_event(
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
        acc.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_5v_rail",
                step_index=0,
                outcome="passed",
            )
        )

        materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")

        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        table = pq.read_table(pq_files[0])
        # Pick the vector row — step identity columns ride on it (it carries
        # the nested measurement). The run row carries no step identity.
        rows = [r for r in table.to_pylist() if r["record_type"] == "vector"]
        assert len(rows) == 1
        row = rows[0]
        assert row["step_node_id"] == "tests/test_power.py::TestPower::test_5v_rail"
        assert row["step_file"] == "tests/test_power.py"
        assert row["step_module"] == "tests.test_power"
        assert row["step_class"] == "TestPower"
        assert row["step_function"] == "test_5v_rail"

    def test_step_results_metadata(self, tmp_path):
        """Step results are written to Parquet file-level metadata."""
        acc = EventAccumulator()
        run_id = uuid4()
        session_id = uuid4()

        acc.on_event(
            RunStarted(
                session_id=session_id,
                run_id=run_id,
                station_id="st1",
                uut_serial_number="SN001",
                occurred_at=datetime(2026, 3, 6, 14, 0, 0, tzinfo=UTC),
            )
        )
        # Step 0: has measurement
        acc.on_event(
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
        acc.on_event(
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
        acc.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="test_voltage",
                step_index=0,
                outcome="passed",
            )
        )
        # Step 1: no measurements (action step)
        acc.on_event(
            StepStarted(
                session_id=session_id,
                run_id=run_id,
                step_name="configure_uut",
                step_index=1,
                node_id="tests/test_hw.py::configure_uut",
                file="tests/test_hw.py",
                function="configure_uut",
            )
        )
        acc.on_event(
            StepEnded(
                session_id=session_id,
                run_id=run_id,
                step_name="configure_uut",
                step_index=1,
                outcome="passed",
            )
        )

        materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")

        pq_files = list((tmp_path / "results" / "runs").rglob("*.parquet"))
        manifest = read_step_results(pq_files[0])
        assert len(manifest) == 2
        assert manifest[0]["name"] == "test_voltage"
        assert manifest[0]["measurement_count"] > 0
        assert manifest[0]["measurement_count"] == 1
        assert manifest[1]["name"] == "configure_uut"
        assert manifest[1]["measurement_count"] == 0
        assert manifest[1]["node_id"] == "tests/test_hw.py::configure_uut"

    def test_measurement_before_run_started(self, tmp_path):
        """Graceful fallback when RunStarted never arrives.

        Materializer returns ``None`` and writes nothing — accumulator
        has no cached RunStarted, so there's no run identity to record.
        """
        acc = EventAccumulator()
        acc.on_event(
            MeasurementRecorded(
                run_id=uuid4(),
                step_name="s",
                step_index=0,
                measurement_name="v",
                value=1.0,
                outcome="passed",
            )
        )

        result = materialize_run_to_parquet(acc, tmp_path / "results")

        assert result is None
        runs_dir = tmp_path / "results" / "runs"
        pq_files = list(runs_dir.rglob("*.parquet")) if runs_dir.exists() else []
        assert len(pq_files) == 0

    def test_custom_metadata_roundtrip_materializer_path(self, tmp_path):
        """custom_metadata on RunStarted survives materializer → reconstruct."""
        acc = EventAccumulator()
        run_id = uuid4()
        session_id = uuid4()

        acc.on_event(
            RunStarted(
                session_id=session_id,
                run_id=run_id,
                uut_serial_number="SN-CUSTOM",
                occurred_at=datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC),
                custom_metadata={"badge": "EMP-999", "batch": "Q2-2026"},
            )
        )
        acc.on_event(
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

        parquet_path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")

        assert parquet_path is not None
        rebuilt = reconstruct_test_run_from_file(parquet_path)
        assert rebuilt.custom_metadata == {"badge": "EMP-999", "batch": "Q2-2026"}
