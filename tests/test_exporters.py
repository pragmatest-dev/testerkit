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
from litmus.data.exporters.csv_exporter import CsvSubscriber
from litmus.data.exporters.json_exporter import JsonSubscriber
from litmus.data.models import UUT, Measurement, Outcome, TestRun, TestStep, TestVector
from litmus.data.subscribers._base import get_subscriber_class, list_subscribers
from tests.test_data.conftest import _replay_events


@pytest.fixture
def sample_test_run() -> TestRun:
    """Create a minimal TestRun for testing."""
    return TestRun(
        id=uuid4(),
        started_at=datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 3, 4, 10, 5, 0, tzinfo=UTC),
        uut=UUT(serial="UUT001", part_number="PN-100", revision="A"),
        station_id="station_001",
        test_phase="development",
        outcome=Outcome.PASSED,
        custom_metadata={"operator_badge": "EMP-123"},
        steps=[
            TestStep(
                name="test_voltage",
                started_at=datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC),
                ended_at=datetime(2026, 3, 4, 10, 1, 0, tzinfo=UTC),
                outcome=Outcome.PASSED,
                instrument_arrays={
                    "step_instruments_name": ["DMM_01"],
                    "step_instruments_resource": ["TCPIP::192.168.1.10"],
                    "step_instruments_driver": ["Keysight34465A"],
                },
                vectors=[
                    TestVector(
                        index=0,
                        retry=0,
                        params={"vin": 5.0},
                        outcome=Outcome.PASSED,
                        measurements=[
                            Measurement(
                                name="vout",
                                value=3.3,
                                units="V",
                                limit_low=3.0,
                                limit_high=3.6,
                                outcome=Outcome.PASSED,
                            ),
                            Measurement(
                                name="iout",
                                value=0.5,
                                units="A",
                                limit_low=0.0,
                                limit_high=1.0,
                                outcome=Outcome.PASSED,
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


class TestSubscriberRegistry:
    def test_csv_registered(self):
        """CSV subscriber auto-registered via __init_subclass__."""
        cls = get_subscriber_class("csv")
        assert cls is not None
        assert cls.format_name == "csv"

    def test_json_registered(self):
        """JSON subscriber auto-registered via __init_subclass__."""
        cls = get_subscriber_class("json")
        assert cls is not None
        assert cls.format_name == "json"

    def test_unknown_format_returns_none(self):
        assert get_subscriber_class("nonexistent_format_xyz") is None

    def test_register_custom_subscriber(self):
        from litmus.data.event_log import EventSubscriber

        class FakeSubscriber(EventSubscriber):
            format_name = "fake"
            event_types: set[type] = set()

            def open(self) -> None:
                pass

            def on_event(self, event: object) -> None:
                pass

            def close(self) -> None:
                pass

        assert "fake" in list_subscribers()
        EventSubscriber._registry.pop("fake")  # cleanup

    def test_list_subscribers(self):
        names = list_subscribers()
        assert "csv" in names
        assert "json" in names


class TestCsvSubscriber:
    def test_creates_file(self, sample_test_run: TestRun, tmp_path: Path):
        sub = CsvSubscriber(tmp_path)
        sub.open()
        _replay_events(sample_test_run, sub)
        sub.close()
        csv_dir = tmp_path
        files = list(csv_dir.glob("*.csv"))
        assert len(files) == 1

    def test_content(self, sample_test_run: TestRun, tmp_path: Path):
        sub = CsvSubscriber(tmp_path)
        sub.open()
        _replay_events(sample_test_run, sub)
        sub.close()
        csv_dir = tmp_path
        csv_file = next(csv_dir.glob("*.csv"))

        with csv_file.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2  # Two measurements
        assert rows[0]["measurement_name"] == "vout"
        assert rows[0]["value"] == "3.3"
        assert rows[0]["units"] == "V"
        assert rows[1]["measurement_name"] == "iout"

    def test_includes_dynamic_columns(self, sample_test_run: TestRun, tmp_path: Path):
        """CSV includes in_* columns from event inputs."""
        sub = CsvSubscriber(tmp_path)
        sub.open()
        _replay_events(sample_test_run, sub)
        sub.close()
        csv_dir = tmp_path
        csv_file = next(csv_dir.glob("*.csv"))

        with csv_file.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert "in_vin" in rows[0]
        assert rows[0]["in_vin"] == "5.0"

    def test_includes_custom_metadata(self, sample_test_run: TestRun, tmp_path: Path):
        """CSV includes custom_* columns from RunStarted."""
        sub = CsvSubscriber(tmp_path)
        sub.open()
        _replay_events(sample_test_run, sub)
        sub.close()
        csv_dir = tmp_path
        csv_file = next(csv_dir.glob("*.csv"))

        with csv_file.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert "custom_operator_badge" in rows[0]
        assert rows[0]["custom_operator_badge"] == "EMP-123"


class TestJsonSubscriber:
    def test_creates_file(self, sample_test_run: TestRun, tmp_path: Path):
        sub = JsonSubscriber(tmp_path)
        sub.open()
        _replay_events(sample_test_run, sub)
        sub.close()
        json_dir = tmp_path
        files = list(json_dir.glob("*.json"))
        assert len(files) == 1

    def test_content(self, sample_test_run: TestRun, tmp_path: Path):
        sub = JsonSubscriber(tmp_path)
        sub.open()
        _replay_events(sample_test_run, sub)
        sub.close()
        json_dir = tmp_path
        json_file = next(json_dir.glob("*.json"))

        data = json.loads(json_file.read_text())
        assert data["station_id"] == "station_001"
        assert data["uut"]["serial"] == "UUT001"
        assert len(data["steps"]) == 1
        assert len(data["steps"][0]["vectors"][0]["measurements"]) == 2


def _reject_constant(token: str) -> float:
    # json.loads is lenient and parses NaN/Infinity (invalid JSON) back to
    # floats; firing here on those tokens turns json.loads into a strict
    # validity check.
    raise ValueError(f"non-JSON constant in export output: {token}")


class TestExporterRobustness:
    """Regression: exporters survive NaN values and non-primitive metadata.

    Previously a NaN measurement emitted the bare ``NaN`` token (invalid JSON)
    and a ``datetime``/``UUID`` in ``custom_metadata`` raised ``TypeError``
    mid-export (JSON) or on attribute write (HDF5).
    """

    def _edge_run(self) -> TestRun:
        ts = datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC)
        return TestRun(
            id=uuid4(),
            started_at=ts,
            ended_at=ts,
            uut=UUT(serial="UUT001"),
            station_id="s1",
            test_phase="development",
            outcome=Outcome.ERRORED,
            custom_metadata={"captured_at": ts, "trace_id": uuid4()},
            steps=[
                TestStep(
                    name="t",
                    started_at=ts,
                    ended_at=ts,
                    outcome=Outcome.ERRORED,
                    vectors=[
                        TestVector(
                            index=0,
                            retry=0,
                            params={},
                            outcome=Outcome.ERRORED,
                            measurements=[
                                Measurement(
                                    name="v",
                                    value=float("nan"),
                                    units="V",
                                    outcome=Outcome.ERRORED,
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )

    def test_json_nan_and_nonprimitive_metadata(self, tmp_path: Path):
        sub = JsonSubscriber(tmp_path)
        sub.open()
        _replay_events(self._edge_run(), sub)
        sub.close()
        text = next(tmp_path.glob("*.json")).read_text()
        data = json.loads(text, parse_constant=_reject_constant)  # raises on NaN/Inf
        meas = data["steps"][0]["vectors"][0]["measurements"][0]
        assert meas["value"] is None  # NaN -> null
        assert "custom_metadata" in data  # datetime/UUID didn't crash the dump

    def test_hdf5_nonprimitive_metadata_does_not_crash(self, tmp_path: Path):
        pytest.importorskip("h5py")
        from litmus.data.exporters.hdf5 import Hdf5Subscriber

        sub = Hdf5Subscriber(tmp_path)
        sub.open()
        _replay_events(self._edge_run(), sub)
        sub.close()
        assert list(tmp_path.iterdir())  # a file was written => no crash


class TestPluginWarnings:
    def test_event_subscriber_warns_on_error(self, tmp_path):
        """Event subscriber failure emits a warning and disables the subscriber."""
        from litmus.data.event_log import EventLog, EventSubscriber
        from litmus.data.events import MeasurementRecorded

        event_log = EventLog(tmp_path / "events", uuid4())

        call_count = 0

        class BadSubscriber(EventSubscriber):
            format_name = "bad-warns"
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
            outcome="passed",
        )

        with w.catch_warnings(record=True) as caught:
            w.simplefilter("always")
            event_log.emit(event)

        assert any("EventSubscriber 'bad-warns' failed" in str(c.message) for c in caught)

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
            sample_test_run,
            measurement,
            step.name,
            0,
            vector,
            {},
        )

        assert isinstance(row, MeasurementRow)
        assert row.run_id == str(sample_test_run.id)
        assert row.uut_serial == "UUT001"
        assert row.station_id == "station_001"
        assert row.step_name == "test_voltage"
        assert row.measurement_name == "vout"
        assert row.measurement_value == 3.3
        assert row.measurement_units == "V"
        assert row.measurement_outcome == "passed"

    def test_to_flat_dict(self, sample_test_run: TestRun):
        """Roundtrip: build → flatten → verify in_*/out_* keys present."""
        step = sample_test_run.steps[0]
        vector = step.vectors[0]
        measurement = vector.measurements[0]

        row = build_row(
            sample_test_run,
            measurement,
            step.name,
            0,
            vector,
            {},
        )
        flat = row.to_flat_dict()

        assert isinstance(flat, dict)
        assert flat["run_id"] == str(sample_test_run.id)
        assert flat["measurement_name"] == "vout"
        # Vector had params={"vin": 5.0} → encoded into the nested inputs lanes.
        from litmus.data.backends._row_helpers import decode_lane_structs

        assert decode_lane_structs(flat["inputs"])["vin"] == 5.0

    def test_iter_rows(self, sample_test_run: TestRun):
        """``iter_rows(test_run)`` yields a flat row dict per measurement."""
        from litmus.data.backends._row_helpers import iter_rows

        rows = list(iter_rows(sample_test_run))
        assert len(rows) == 2
        assert all(isinstance(r, dict) for r in rows)
        assert rows[0]["measurement_name"] == "vout"
        assert rows[1]["measurement_name"] == "iout"
        assert rows[0]["uut_serial"] == "UUT001"


class TestEventSubscriberLifecycle:
    def test_subscriber_receives_events(self, tmp_path):
        """EventSubscriber receives events when wired to EventLog."""
        from litmus.data.event_log import EventLog, EventSubscriber
        from litmus.data.events import MeasurementRecorded

        event_log = EventLog(tmp_path / "events", uuid4())

        received = []

        class RecordingSub(EventSubscriber):
            format_name = "recording-lifecycle"
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
            outcome="passed",
        )
        event_log.emit(event)

        assert len(received) == 1
        assert received[0].measurement_name == "v"
        assert received[0].value == 1.0

        event_log.close()

    def test_subscriber_close_warns(self, tmp_path):
        """EventLog.close() warns when subscriber close() raises."""
        from litmus.data.event_log import EventLog, EventSubscriber
        from litmus.data.events import MeasurementRecorded

        event_log = EventLog(tmp_path / "events", uuid4())

        class BadCloseSub(EventSubscriber):
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
            "bad_close" in str(c.message) and "close failed" in str(c.message) for c in caught
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
        assert ref == "file://_ref/abc_trace.csv"
        assert (ref_dir / "abc_trace.csv").read_text() == "data"

    def test_bytes(self, tmp_path: Path):
        from litmus.data.backends._row_helpers import save_ref_to_dir

        ref_dir = tmp_path / "_ref"
        ref_dir.mkdir()

        ref = save_ref_to_dir(ref_dir, "abc", "blob", b"\x00\x01\x02")
        assert ref == "file://_ref/abc_blob.bin"
        assert (ref_dir / "abc_blob.bin").read_bytes() == b"\x00\x01\x02"

    def test_waveform_json_fallback(self, tmp_path: Path):
        from litmus.data.backends._row_helpers import save_ref_to_dir
        from litmus.data.models import Waveform

        ref_dir = tmp_path / "_ref"
        ref_dir.mkdir()

        wfm = Waveform(t0=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC), dt=0.001, Y=[1.0, 2.0, 3.0])
        ref = save_ref_to_dir(ref_dir, "abc", "wfm", wfm)
        # npz if numpy available, json otherwise — either is valid
        assert ref.startswith("file://_ref/abc_wfm.")
        assert (ref_dir / ref.removeprefix("file://_ref/")).exists()

    def test_pydantic_model(self, tmp_path: Path):
        from litmus.data.backends._row_helpers import save_ref_to_dir

        ref_dir = tmp_path / "_ref"
        ref_dir.mkdir()

        model = UUT(serial="UUT001", part_number="PN-100")
        ref = save_ref_to_dir(ref_dir, "abc", "uut", model)
        assert ref == "file://_ref/abc_uut.json"
        content = json.loads((ref_dir / "abc_uut.json").read_text())
        assert content["serial"] == "UUT001"


class TestInstrumentArrayKeys:
    def test_keys_match_build_output(self):
        """INSTRUMENT_ARRAY_KEYS stays in sync with build_instrument_arrays()."""
        from litmus.execution.logger import INSTRUMENT_ARRAY_KEYS, RunScope

        logger = RunScope(
            uut_serial="UUT001",
            station_id="station_001",
        )
        arrays = logger.build_instrument_arrays()
        assert set(INSTRUMENT_ARRAY_KEYS) == set(arrays.keys())


class TestReconstructTestRun:
    """Roundtrip: build TestRun → save to Parquet → reconstruct → compare."""

    def test_roundtrip(self, sample_test_run: TestRun, tmp_path: Path):
        from litmus.data.backends.parquet import ParquetBackend, reconstruct_test_run_from_file

        backend = ParquetBackend(data_dir=tmp_path)
        pq_file = backend.save_test_run(sample_test_run)

        rebuilt = reconstruct_test_run_from_file(pq_file)

        # Compare key fields
        assert rebuilt.id == sample_test_run.id
        assert rebuilt.uut.serial == sample_test_run.uut.serial
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
            assert rebuilt_m.limit_low == orig_m.limit_low
            assert rebuilt_m.limit_high == orig_m.limit_high

    def test_roundtrip_custom_metadata(self, sample_test_run: TestRun, tmp_path: Path):
        """custom_metadata survives Parquet save → reconstruct."""
        from litmus.data.backends.parquet import ParquetBackend, reconstruct_test_run_from_file

        backend = ParquetBackend(data_dir=tmp_path)
        pq_file = backend.save_test_run(sample_test_run)
        rebuilt = reconstruct_test_run_from_file(pq_file)

        assert rebuilt.custom_metadata == {"operator_badge": "EMP-123"}

    def test_roundtrip_instrument_arrays(self, sample_test_run: TestRun, tmp_path: Path):
        """instrument_arrays survives Parquet save → reconstruct."""
        from litmus.data.backends.parquet import ParquetBackend, reconstruct_test_run_from_file

        backend = ParquetBackend(data_dir=tmp_path)
        pq_file = backend.save_test_run(sample_test_run)
        rebuilt = reconstruct_test_run_from_file(pq_file)

        step = rebuilt.steps[0]
        assert step.instrument_arrays is not None
        assert step.instrument_arrays["step_instruments_name"] == ["DMM_01"]
        assert step.instrument_arrays["step_instruments_resource"] == ["TCPIP::192.168.1.10"]
        assert step.instrument_arrays["step_instruments_driver"] == ["Keysight34465A"]

    def test_csv_subscriber_includes_custom_columns(self, sample_test_run: TestRun, tmp_path: Path):
        """CSV subscriber includes custom_* columns from RunStarted."""
        sub = CsvSubscriber(tmp_path)
        sub.open()
        _replay_events(sample_test_run, sub)
        sub.close()
        csv_dir = tmp_path
        csv_file = next(csv_dir.glob("*.csv"))

        with csv_file.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert "custom_operator_badge" in rows[0]
        assert rows[0]["custom_operator_badge"] == "EMP-123"


class TestHarnessLoggerIntegration:
    """Verify harness.measure() streams through logger via event log."""

    def test_harness_measure_emits_event(self, tmp_path):
        """harness.measure() emits MeasurementRecorded to event log."""
        from litmus.data.event_log import EventLog, EventSubscriber
        from litmus.data.events import MeasurementRecorded
        from litmus.execution.harness import TestHarness
        from litmus.execution.logger import RunScope

        logger = RunScope(
            uut_serial="UUT001",
            station_id="station_001",
        )

        received = []

        class RecordingSub(EventSubscriber):
            format_name = "recording-harness"
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
        from litmus.execution.logger import RunScope

        logger = RunScope(
            uut_serial="UUT001",
            station_id="station_001",
        )
        harness = TestHarness(logger=logger, step_name="test_voltage")
        with harness.step("test_voltage") as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    harness.measure("vout", 3.3)

        # Only one measurement in the vector
        assert len(step.vectors[0].measurements) == 1
        assert step.vectors[0].measurements[0].name == "vout"
