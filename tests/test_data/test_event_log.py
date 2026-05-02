"""Tests for EventLog writer and subscriber dispatch."""

import json
from uuid import uuid4

import pyarrow as pa
import pyarrow.ipc as ipc

from litmus.data.event_log import EventLog, EventSubscriber
from litmus.data.events import MeasurementRecorded, RunEnded, RunStarted, SessionStarted


class TestEventLog:
    def test_emit_writes_arrow_ipc(self, tmp_path):
        run_id = uuid4()
        log = EventLog(tmp_path / "events", run_id)

        event = RunStarted(
            run_id=run_id,
            station_id="st1",
            dut_serial="SN001",
        )
        log.emit(event)
        log.close()

        reader = ipc.open_stream(pa.OSFile(str(log.path), "rb"))
        table = reader.read_all()
        assert len(table) == 1
        data = json.loads(table.column("json")[0].as_py())
        assert data["event_type"] == "run.started"
        assert data["station_id"] == "st1"
        assert data["received_at"] is not None

    def test_emit_sets_received_at(self, tmp_path):
        log = EventLog(tmp_path / "events", uuid4())
        event = RunEnded(outcome="passed")
        assert event.received_at is None

        log.emit(event)
        assert event.received_at is not None
        log.close()

    def test_subscriber_dispatch(self, tmp_path):
        log = EventLog(tmp_path / "events", uuid4())
        received = []

        class Sub(EventSubscriber):
            format_name = "test"
            event_types = {MeasurementRecorded}

            def open(self):
                pass

            def on_event(self, event):
                received.append(event)

            def close(self):
                pass

        log.add_subscriber(Sub())

        # Should NOT dispatch (wrong type)
        log.emit(SessionStarted(station_id="st1"))
        assert len(received) == 0

        # Should dispatch
        log.emit(
            MeasurementRecorded(
                step_name="s",
                step_index=0,
                measurement_name="v",
                value=1.0,
            )
        )
        assert len(received) == 1
        log.close()

    def test_failed_subscriber_disabled(self, tmp_path):
        import warnings as w

        log = EventLog(tmp_path / "events", uuid4())
        call_count = 0

        class BadSub(EventSubscriber):
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

        log.add_subscriber(BadSub())

        with w.catch_warnings(record=True):
            w.simplefilter("always")
            log.emit(
                MeasurementRecorded(
                    step_name="s",
                    step_index=0,
                    measurement_name="m1",
                    value=1.0,
                )
            )
            log.emit(
                MeasurementRecorded(
                    step_name="s",
                    step_index=0,
                    measurement_name="m2",
                    value=2.0,
                )
            )

        assert call_count == 1
        log.close()

    def test_save_ref(self, tmp_path):
        session_id = uuid4()
        log = EventLog(tmp_path / "events", session_id)

        ref = log.save_ref("abc12345", "trace", b"\x00\x01\x02")
        assert ref == "file://_ref/abc12345_trace.bin"
        # Ref dir lives alongside the Arrow IPC in the date partition
        assert (log.path.parent / f"{session_id}_ref" / "abc12345_trace.bin").exists()
        log.close()

    def test_multiple_events_in_one_file(self, tmp_path):
        run_id = uuid4()
        log = EventLog(tmp_path / "events", run_id)

        log.emit(RunStarted(run_id=run_id, station_id="st1", dut_serial="SN001"))
        log.emit(
            MeasurementRecorded(
                run_id=run_id,
                step_name="s",
                step_index=0,
                measurement_name="v",
                value=1.0,
            )
        )
        log.emit(RunEnded(run_id=run_id, outcome="passed"))
        log.close()

        reader = ipc.open_stream(pa.OSFile(str(log.path), "rb"))
        table = reader.read_all()
        types = table.column("event_type").to_pylist()
        assert types == ["run.started", "test.measurement", "run.ended"]
