"""Tests for pluggable export infrastructure."""

from __future__ import annotations

import csv
import json
import warnings as w
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from litmus.data.backends._row_helpers import MeasurementRow, build_row
from litmus.data.exporters import get_exporter, list_exporters, register_exporter
from litmus.data.exporters._registry import _REGISTRY, get_exporter_class
from litmus.data.exporters.csv_exporter import CsvExporter
from litmus.data.exporters.json_exporter import JsonExporter
from litmus.data.models import DUT, Measurement, Outcome, TestRun, TestStep, TestVector
from litmus.data.transports import get_transport, register_transport
from litmus.data.transports._registry import _REGISTRY as _TRANSPORT_REGISTRY
from litmus.data.transports.file_transport import FileTransport
from litmus.schemas import OutputConfig, ProjectConfig


@pytest.fixture
def sample_test_run() -> TestRun:
    """Create a minimal TestRun for testing."""
    return TestRun(
        id=uuid4(),
        started_at=datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 3, 4, 10, 5, 0, tzinfo=UTC),
        dut=DUT(serial="DUT001", part_number="PN-100", revision="A"),
        station_id="station_001",
        test_sequence_id="test_seq",
        test_phase="development",
        outcome=Outcome.PASS,
        custom_metadata={"operator_badge": "EMP-123"},
        steps=[
            TestStep(
                name="test_voltage",
                started_at=datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC),
                ended_at=datetime(2026, 3, 4, 10, 1, 0, tzinfo=UTC),
                outcome=Outcome.PASS,
                instrument_arrays={
                    "instr_name": ["DMM_01"],
                    "instr_resource": ["TCPIP::192.168.1.10"],
                    "instr_driver": ["Keysight34465A"],
                },
                vectors=[
                    TestVector(
                        index=0,
                        attempt=1,
                        params={"vin": 5.0},
                        outcome=Outcome.PASS,
                        measurements=[
                            Measurement(
                                name="vout",
                                value=3.3,
                                units="V",
                                low_limit=3.0,
                                high_limit=3.6,
                                outcome=Outcome.PASS,
                            ),
                            Measurement(
                                name="iout",
                                value=0.5,
                                units="A",
                                low_limit=0.0,
                                high_limit=1.0,
                                outcome=Outcome.PASS,
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


class TestExporterRegistry:
    def test_csv_lazy_load(self):
        """CSV exporter loads lazily on first access."""
        # Clear registry to test lazy loading
        _REGISTRY.pop("csv", None)
        exporter = get_exporter("csv")
        assert exporter.format_name == "csv"

    def test_json_lazy_load(self):
        """JSON exporter loads lazily on first access."""
        _REGISTRY.pop("json", None)
        exporter = get_exporter("json")
        assert exporter.format_name == "json"

    def test_unknown_format_raises(self):
        with pytest.raises(KeyError, match="No exporter registered"):
            get_exporter("nonexistent_format_xyz")

    def test_register_custom_exporter(self):
        class FakeExporter:
            format_name = "fake"
            def export(self, test_run, output_path):
                return output_path / "fake.dat"

        register_exporter(FakeExporter())
        assert "fake" in list_exporters()
        _REGISTRY.pop("fake")  # cleanup

    def test_get_exporter_class_returns_class(self):
        cls = get_exporter_class("csv")
        assert cls is not None
        assert isinstance(cls(), object)
        assert cls().format_name == "csv"

    def test_get_exporter_class_unknown_returns_none(self):
        assert get_exporter_class("nonexistent_format_xyz") is None

    def test_list_exporters(self):
        # Ensure at least csv is loadable
        get_exporter("csv")
        names = list_exporters()
        assert "csv" in names


class TestCsvExporter:
    def test_export_creates_file(self, sample_test_run: TestRun, tmp_path: Path):
        exporter = CsvExporter()
        result = exporter.export(sample_test_run, tmp_path)
        assert result.exists()
        assert result.suffix == ".csv"

    def test_export_content(self, sample_test_run: TestRun, tmp_path: Path):
        exporter = CsvExporter()
        result = exporter.export(sample_test_run, tmp_path)

        with result.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2  # Two measurements
        assert rows[0]["measurement_name"] == "vout"
        assert rows[0]["value"] == "3.3"
        assert rows[0]["units"] == "V"
        assert rows[1]["measurement_name"] == "iout"

    def test_export_includes_dynamic_columns(self, sample_test_run: TestRun, tmp_path: Path):
        """CSV export includes in_* columns from vector params."""
        exporter = CsvExporter()
        result = exporter.export(sample_test_run, tmp_path)

        with result.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # Vector had params={"vin": 5.0} → should produce in_vin column
        assert "in_vin" in rows[0]
        assert rows[0]["in_vin"] == "5.0"


class TestJsonExporter:
    def test_export_creates_file(self, sample_test_run: TestRun, tmp_path: Path):
        exporter = JsonExporter()
        result = exporter.export(sample_test_run, tmp_path)
        assert result.exists()
        assert result.suffix == ".json"

    def test_export_content(self, sample_test_run: TestRun, tmp_path: Path):
        exporter = JsonExporter()
        result = exporter.export(sample_test_run, tmp_path)

        data = json.loads(result.read_text())
        assert data["station_id"] == "station_001"
        assert data["dut"]["serial"] == "DUT001"
        assert len(data["steps"]) == 1
        assert len(data["steps"][0]["vectors"][0]["measurements"]) == 2


class TestTransportRegistry:
    def test_file_transport_lazy_load(self):
        _TRANSPORT_REGISTRY.pop("file", None)
        transport = get_transport("file")
        assert transport.transport_name == "file"

    def test_unknown_transport_raises(self):
        with pytest.raises(KeyError, match="No transport registered"):
            get_transport("nonexistent_transport_xyz")

    def test_register_custom_transport(self):
        class FakeTransport:
            transport_name = "fake_t"
            def send(self, local_path, config):
                return "sent"

        register_transport(FakeTransport())
        _TRANSPORT_REGISTRY.pop("fake_t")


class TestFileTransport:
    def test_send_copies_file(self, tmp_path: Path):
        # Create source file
        src = tmp_path / "source.csv"
        src.write_text("data")

        dest_dir = tmp_path / "dest"
        transport = FileTransport()
        cfg = OutputConfig(format="csv", output_dir=str(dest_dir))
        result = transport.send(src, cfg)

        assert Path(result).exists()
        assert Path(result).read_text() == "data"


class TestOutputConfig:
    def test_default_output_dir_html(self):
        cfg = OutputConfig(format="html")
        assert cfg.default_output_dir() == "reports"

    def test_default_output_dir_csv(self):
        cfg = OutputConfig(format="csv")
        assert cfg.default_output_dir() == "results/exports/csv"

    def test_default_output_dir_override(self):
        cfg = OutputConfig(format="csv", output_dir="/custom")
        assert cfg.default_output_dir() == "/custom"

    def test_extras_collection(self):
        cfg = OutputConfig.model_validate({
            "format": "stdf",
            "transport": "s3",
            "bucket": "my-bucket",
            "prefix": "stdf/",
        })
        assert cfg.extras["bucket"] == "my-bucket"
        assert cfg.extras["prefix"] == "stdf/"

    def test_requires_format_or_transport(self):
        with pytest.raises(ValueError, match="at least one of"):
            OutputConfig.model_validate({})

    def test_project_config_empty_outputs(self):
        config = ProjectConfig(name="test")
        assert config.outputs == []

    def test_project_config_with_outputs(self):
        config = ProjectConfig(
            name="test",
            outputs=[OutputConfig(format="csv")],
        )
        assert len(config.outputs) == 1
        assert config.outputs[0].format == "csv"


class TestPluginWarnings:
    def test_run_configured_outputs_warns_on_error(self):
        """_run_configured_outputs emits a warning instead of silently swallowing."""
        from unittest.mock import patch

        from litmus.execution.plugin import _run_configured_outputs

        with patch(
            "litmus.execution.plugin.run_outputs",
            side_effect=RuntimeError("boom"),
            create=True,
        ):
            # Patch the import inside the function
            with patch(
                "litmus.data.output_runner.run_outputs",
                side_effect=RuntimeError("boom"),
            ):
                with w.catch_warnings(record=True) as caught:
                    w.simplefilter("always")
                    _run_configured_outputs(None, "run123", "results")

                assert any("Output processing failed" in str(c.message) for c in caught)

    def test_event_subscriber_warns_on_error(self, tmp_path):
        """Event subscriber failure emits a warning and disables the subscriber."""
        from litmus.data.event_log import EventLog
        from litmus.data.events import MeasurementRecorded

        event_log = EventLog(tmp_path / "events", uuid4())

        call_count = 0

        class BadSubscriber:
            format_name = "bad"
            event_types = {MeasurementRecorded}
            def open(self):
                pass
            def on_event(self, event):
                nonlocal call_count
                call_count += 1
                raise RuntimeError("boom")
            def close(self):
                pass

        event_log.add_subscriber(BadSubscriber())

        event = MeasurementRecorded(
            run_id=uuid4(),
            step_name="step1",
            step_index=0,
            measurement_name="v",
            value=1.0,
            outcome="pass",
        )

        with w.catch_warnings(record=True) as caught:
            w.simplefilter("always")
            event_log.emit(event)

        assert any("EventSubscriber 'bad' failed" in str(c.message) for c in caught)

        # Second emit should not call the subscriber (disabled after failure)
        event_log.emit(event)
        assert call_count == 1

        event_log.close()


class TestMeasurementRow:
    def test_build_row_standalone(self, sample_test_run: TestRun):
        """build_row() returns MeasurementRow with expected typed fields."""
        step = sample_test_run.steps[0]
        vector = step.vectors[0]
        measurement = vector.measurements[0]

        row = build_row(
            sample_test_run, measurement,
            step.name, 0, vector, {},
        )

        assert isinstance(row, MeasurementRow)
        assert row.run_id == str(sample_test_run.id)
        assert row.dut_serial == "DUT001"
        assert row.station_id == "station_001"
        assert row.step_name == "test_voltage"
        assert row.measurement_name == "vout"
        assert row.value == 3.3
        assert row.units == "V"
        assert row.outcome == "pass"

    def test_to_flat_dict(self, sample_test_run: TestRun):
        """Roundtrip: build → flatten → verify in_*/out_* keys present."""
        step = sample_test_run.steps[0]
        vector = step.vectors[0]
        measurement = vector.measurements[0]

        row = build_row(
            sample_test_run, measurement,
            step.name, 0, vector, {},
        )
        flat = row.to_flat_dict()

        assert isinstance(flat, dict)
        assert flat["run_id"] == str(sample_test_run.id)
        assert flat["measurement_name"] == "vout"
        # Vector had params={"vin": 5.0} → should produce in_vin
        assert flat["in_vin"] == 5.0

    def test_iter_rows(self, sample_test_run: TestRun):
        """TestRun.iter_rows() yields MeasurementRow for each measurement."""
        rows = list(sample_test_run.iter_rows())
        assert len(rows) == 2
        assert all(isinstance(r, MeasurementRow) for r in rows)
        assert rows[0].measurement_name == "vout"
        assert rows[1].measurement_name == "iout"
        assert rows[0].dut_serial == "DUT001"


class TestEventSubscriberLifecycle:
    def test_subscriber_receives_events(self, tmp_path):
        """EventSubscriber receives events when wired to EventLog."""
        from litmus.data.event_log import EventLog
        from litmus.data.events import MeasurementRecorded

        event_log = EventLog(tmp_path / "events", uuid4())

        received = []

        class RecordingSub:
            format_name = "recording"
            event_types = {MeasurementRecorded}
            def open(self):
                pass
            def on_event(self, event):
                received.append(event)
            def close(self):
                pass

        event_log.add_subscriber(RecordingSub())

        event = MeasurementRecorded(
            run_id=uuid4(),
            step_name="step1",
            step_index=0,
            measurement_name="v",
            value=1.0,
            outcome="pass",
        )
        event_log.emit(event)

        assert len(received) == 1
        assert received[0].measurement_name == "v"
        assert received[0].value == 1.0

        event_log.close()

    def test_subscriber_close_warns(self, tmp_path):
        """EventLog.close() warns when subscriber close() raises."""
        from litmus.data.event_log import EventLog
        from litmus.data.events import MeasurementRecorded

        event_log = EventLog(tmp_path / "events", uuid4())

        class BadCloseSub:
            format_name = "bad_close"
            event_types = {MeasurementRecorded}
            def open(self):
                pass
            def on_event(self, event):
                pass
            def close(self):
                raise RuntimeError("close failed")

        event_log.add_subscriber(BadCloseSub())

        with w.catch_warnings(record=True) as caught:
            w.simplefilter("always")
            event_log.close()

        assert any(
            "bad_close" in str(c.message) and "close failed" in str(c.message)
            for c in caught
        )


class TestSaveRefToDir:
    """Direct tests for save_ref_to_dir() value-type dispatch."""

    def test_path_copy(self, tmp_path: Path):
        from litmus.data.backends._row_helpers import save_ref_to_dir

        src = tmp_path / "source.csv"
        src.write_text("data")
        ref_dir = tmp_path / "_ref"
        ref_dir.mkdir()

        ref = save_ref_to_dir(ref_dir, "abc", "trace", src)
        assert ref == "_ref/abc_trace.csv"
        assert (ref_dir / "abc_trace.csv").read_text() == "data"

    def test_bytes(self, tmp_path: Path):
        from litmus.data.backends._row_helpers import save_ref_to_dir

        ref_dir = tmp_path / "_ref"
        ref_dir.mkdir()

        ref = save_ref_to_dir(ref_dir, "abc", "blob", b"\x00\x01\x02")
        assert ref == "_ref/abc_blob.bin"
        assert (ref_dir / "abc_blob.bin").read_bytes() == b"\x00\x01\x02"

    def test_waveform_json_fallback(self, tmp_path: Path):
        from litmus.data.backends._row_helpers import save_ref_to_dir
        from litmus.data.models import Waveform

        ref_dir = tmp_path / "_ref"
        ref_dir.mkdir()

        wfm = Waveform(t0=0.0, dt=0.001, Y=[1.0, 2.0, 3.0])
        ref = save_ref_to_dir(ref_dir, "abc", "wfm", wfm)
        # npz if numpy available, json otherwise — either is valid
        assert ref.startswith("_ref/abc_wfm.")
        assert (ref_dir / ref.removeprefix("_ref/")).exists()

    def test_pydantic_model(self, tmp_path: Path):
        from litmus.data.backends._row_helpers import save_ref_to_dir

        ref_dir = tmp_path / "_ref"
        ref_dir.mkdir()

        model = DUT(serial="DUT001", part_number="PN-100")
        ref = save_ref_to_dir(ref_dir, "abc", "dut", model)
        assert ref == "_ref/abc_dut.json"
        content = json.loads((ref_dir / "abc_dut.json").read_text())
        assert content["serial"] == "DUT001"


class TestInstrumentArrayKeys:
    def test_keys_match_build_output(self):
        """INSTRUMENT_ARRAY_KEYS stays in sync with build_instrument_arrays()."""
        from litmus.execution.logger import INSTRUMENT_ARRAY_KEYS, TestRunLogger

        logger = TestRunLogger(
            dut_serial="DUT001",
            station_id="station_001",
            test_sequence_id="seq",
        )
        arrays = logger.build_instrument_arrays()
        assert set(INSTRUMENT_ARRAY_KEYS) == set(arrays.keys())


class TestReconstructTestRun:
    """Roundtrip: build TestRun → save to Parquet → reconstruct → compare."""

    def test_roundtrip(self, sample_test_run: TestRun, tmp_path: Path):
        from litmus.data.backends.parquet import ParquetBackend, reconstruct_test_run_from_file

        results_dir = tmp_path / "results"
        backend = ParquetBackend(results_dir=str(results_dir))
        backend.save_test_run(sample_test_run)

        # Find the saved file
        pq_file = backend.find_run_file(str(sample_test_run.id))
        assert pq_file is not None

        # Reconstruct
        rebuilt = reconstruct_test_run_from_file(pq_file)

        # Compare key fields
        assert rebuilt.id == sample_test_run.id
        assert rebuilt.dut.serial == sample_test_run.dut.serial
        assert rebuilt.station_id == sample_test_run.station_id
        assert rebuilt.outcome == sample_test_run.outcome
        assert len(rebuilt.steps) == len(sample_test_run.steps)

        orig_step = sample_test_run.steps[0]
        rebuilt_step = rebuilt.steps[0]
        assert rebuilt_step.name == orig_step.name
        assert len(rebuilt_step.vectors) == len(orig_step.vectors)

        orig_vec = orig_step.vectors[0]
        rebuilt_vec = rebuilt_step.vectors[0]
        assert len(rebuilt_vec.measurements) == len(orig_vec.measurements)

        for orig_m, rebuilt_m in zip(orig_vec.measurements, rebuilt_vec.measurements):
            assert rebuilt_m.name == orig_m.name
            assert rebuilt_m.value == orig_m.value
            assert rebuilt_m.units == orig_m.units
            assert rebuilt_m.outcome == orig_m.outcome
            assert rebuilt_m.low_limit == orig_m.low_limit
            assert rebuilt_m.high_limit == orig_m.high_limit

    def test_roundtrip_custom_metadata(self, sample_test_run: TestRun, tmp_path: Path):
        """custom_metadata survives Parquet save → reconstruct."""
        from litmus.data.backends.parquet import ParquetBackend, reconstruct_test_run_from_file

        results_dir = tmp_path / "results"
        backend = ParquetBackend(results_dir=str(results_dir))
        backend.save_test_run(sample_test_run)

        pq_file = backend.find_run_file(str(sample_test_run.id))
        rebuilt = reconstruct_test_run_from_file(pq_file)

        assert rebuilt.custom_metadata == {"operator_badge": "EMP-123"}

    def test_roundtrip_instrument_arrays(self, sample_test_run: TestRun, tmp_path: Path):
        """instrument_arrays survives Parquet save → reconstruct."""
        from litmus.data.backends.parquet import ParquetBackend, reconstruct_test_run_from_file

        results_dir = tmp_path / "results"
        backend = ParquetBackend(results_dir=str(results_dir))
        backend.save_test_run(sample_test_run)

        pq_file = backend.find_run_file(str(sample_test_run.id))
        rebuilt = reconstruct_test_run_from_file(pq_file)

        step = rebuilt.steps[0]
        assert step.instrument_arrays is not None
        assert step.instrument_arrays["instr_name"] == ["DMM_01"]
        assert step.instrument_arrays["instr_resource"] == ["TCPIP::192.168.1.10"]
        assert step.instrument_arrays["instr_driver"] == ["Keysight34465A"]

    def test_csv_includes_custom_and_instr_columns(self, sample_test_run: TestRun, tmp_path: Path):
        """CSV export includes custom_* and instr_* columns."""
        exporter = CsvExporter()
        result = exporter.export(sample_test_run, tmp_path)

        with result.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert "custom_operator_badge" in rows[0]
        assert rows[0]["custom_operator_badge"] == "EMP-123"
        assert "instr_name" in rows[0]


class TestHarnessLoggerIntegration:
    """Verify harness.measure() streams through logger via event log."""

    def test_harness_measure_emits_event(self, tmp_path):
        """harness.measure() emits MeasurementRecorded to event log."""
        from litmus.data.event_log import EventLog
        from litmus.data.events import MeasurementRecorded
        from litmus.execution.harness import TestHarness
        from litmus.execution.logger import TestRunLogger

        logger = TestRunLogger(
            dut_serial="DUT001",
            station_id="station_001",
            test_sequence_id="seq",
        )

        received = []

        class RecordingSub:
            format_name = "recording"
            event_types = {MeasurementRecorded}
            def open(self):
                pass
            def on_event(self, event):
                received.append(event)
            def close(self):
                pass

        event_log = EventLog(tmp_path / "events", logger.test_run.id)
        event_log.add_subscriber(RecordingSub())
        logger.event_log = event_log

        harness = TestHarness(logger=logger, step_name="test_voltage")
        with harness.step("test_voltage"):
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    harness.measure("vout", 3.3)

        assert len(received) == 1
        assert received[0].measurement_name == "vout"
        assert received[0].value == 3.3
        assert received[0].step_name == "test_voltage"

        event_log.close()

    def test_harness_measure_no_double_append_to_vector(self):
        """Measurement should appear exactly once in the vector."""
        from litmus.execution.harness import TestHarness
        from litmus.execution.logger import TestRunLogger

        logger = TestRunLogger(
            dut_serial="DUT001",
            station_id="station_001",
            test_sequence_id="seq",
        )
        harness = TestHarness(logger=logger, step_name="test_voltage")
        with harness.step("test_voltage") as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    harness.measure("vout", 3.3)

        # Only one measurement in the vector
        assert len(step.vectors[0].measurements) == 1
        assert step.vectors[0].measurements[0].name == "vout"
