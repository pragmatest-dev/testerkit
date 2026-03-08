"""Tests for EventLog writer and subscriber dispatch."""

import json
from uuid import uuid4

from litmus.data.event_log import EventLog
from litmus.data.events import MeasurementRecorded, RunEnded, SessionStarted


class TestEventLog:
    def test_emit_writes_jsonl(self, tmp_path):
        run_id = uuid4()
        log = EventLog(tmp_path / "events", run_id)

        event = SessionStarted(
            run_id=run_id,
            station_id="st1",
            dut_serial="SN001",
        )
        log.emit(event)
        log.close()

        lines = log.path.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["event_type"] == "session.started"
        assert data["station_id"] == "st1"
        assert data["received_at"] is not None

    def test_emit_sets_received_at(self, tmp_path):
        log = EventLog(tmp_path / "events", uuid4())
        event = RunEnded(outcome="pass")
        assert event.received_at is None

        log.emit(event)
        assert event.received_at is not None
        log.close()

    def test_subscriber_dispatch(self, tmp_path):
        log = EventLog(tmp_path / "events", uuid4())
        received = []

        class Sub:
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
        log.emit(SessionStarted(station_id="st1", dut_serial="SN001"))
        assert len(received) == 0

        # Should dispatch
        log.emit(MeasurementRecorded(
            step_name="s", step_index=0, measurement_name="v", value=1.0,
        ))
        assert len(received) == 1
        log.close()

    def test_failed_subscriber_disabled(self, tmp_path):
        import warnings as w

        log = EventLog(tmp_path / "events", uuid4())
        call_count = 0

        class BadSub:
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
            log.emit(MeasurementRecorded(
                step_name="s", step_index=0, measurement_name="m1", value=1.0,
            ))
            log.emit(MeasurementRecorded(
                step_name="s", step_index=0, measurement_name="m2", value=2.0,
            ))

        assert call_count == 1
        log.close()

    def test_save_ref(self, tmp_path):
        session_id = uuid4()
        log = EventLog(tmp_path / "events", session_id)

        ref = log.save_ref("abc12345", "trace", b"\x00\x01\x02")
        assert ref == "file://_ref/abc12345_trace.bin"
        # Ref dir lives alongside the JSONL in the date partition
        assert (log.path.parent / f"{session_id}_ref" / "abc12345_trace.bin").exists()
        log.close()

    def test_multiple_events_in_one_file(self, tmp_path):
        run_id = uuid4()
        log = EventLog(tmp_path / "events", run_id)

        log.emit(SessionStarted(run_id=run_id, station_id="st1", dut_serial="SN001"))
        log.emit(MeasurementRecorded(
            run_id=run_id, step_name="s", step_index=0,
            measurement_name="v", value=1.0,
        ))
        log.emit(RunEnded(run_id=run_id, outcome="pass"))
        log.close()

        lines = log.path.read_text().strip().splitlines()
        assert len(lines) == 3
        types = [json.loads(line)["event_type"] for line in lines]
        assert types == ["session.started", "test.measurement", "test.run_ended"]
