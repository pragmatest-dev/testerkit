"""Tests for event type hierarchy."""

from uuid import uuid4

from litmus.data.events import (
    ALL_EVENTS,
    CHANNEL_EVENTS,
    DIAGNOSTIC_EVENTS,
    DIALOG_EVENTS,
    FIXTURE_EVENTS,
    INSTRUMENT_EVENTS,
    ROUTE_EVENTS,
    RUN_EVENTS,
    SESSION_EVENTS,
    SLOT_EVENTS,
    STREAM_EVENTS,
    TEST_EVENTS,
    InstrumentConnected,
    MeasurementRecorded,
    RecordEvent,
    RunEnded,
    RunStarted,
    SessionStarted,
    StepEnded,
    StepStarted,
)


class TestEventModels:
    def test_session_started_defaults(self):
        e = SessionStarted(station_id="st1")
        assert e.event_type == "session.started"
        assert e.station_id == "st1"
        assert e.slot_count == 1
        assert e.id is not None
        assert e.occurred_at is not None

    def test_run_started_defaults(self):
        e = RunStarted(
            station_id="st1",
            dut_serial="SN001",
        )
        assert e.event_type == "run.started"
        assert e.station_id == "st1"
        assert e.dut_serial == "SN001"
        assert e.test_phase is None

    def test_measurement_recorded_fields(self):
        run_id = uuid4()
        e = MeasurementRecorded(
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
        assert e.event_type == "test.measurement"
        assert e.value == 3.3
        assert e.units == "V"
        assert e.run_id == run_id

    def test_instrument_connected(self):
        e = InstrumentConnected(
            role="dmm",
            instrument_id="keithley_001",
            resource="GPIB::16::INSTR",
            mocked=True,
        )
        assert e.event_type == "fixture.instrument_connected"
        assert e.mocked is True

    def test_step_events(self):
        s = StepStarted(step_name="step1", step_index=0)
        assert s.event_type == "test.step_started"
        e = StepEnded(step_name="step1", step_index=0, outcome="passed")
        assert e.event_type == "test.step_ended"

    def test_run_ended(self):
        e = RunEnded(outcome="failed")
        assert e.event_type == "run.ended"
        assert e.outcome == "failed"

    def test_serialization_roundtrip(self):
        e = RunStarted(
            station_id="st1",
            dut_serial="SN001",
            custom_metadata={"badge": "EMP-123"},
        )
        json_str = e.model_dump_json()
        restored = RunStarted.model_validate_json(json_str)
        assert restored.station_id == "st1"
        assert restored.custom_metadata == {"badge": "EMP-123"}
        assert restored.id == e.id

    def test_record_event_key_value(self):
        e = RecordEvent(
            step_name="step1",
            step_index=0,
            key="firmware_version",
            value="1.2.3",
        )
        assert e.event_type == "test.record"
        assert e.key == "firmware_version"
        assert e.value == "1.2.3"

    def test_session_started_session_type(self):
        e = SessionStarted(station_id="st1")
        assert e.session_type == "test_run"
        e2 = SessionStarted(station_id="st1", session_type="characterization")
        assert e2.session_type == "characterization"

    def test_session_started_from_station(self):
        from uuid import uuid4 as _uuid4

        sid = _uuid4()
        e = SessionStarted.from_station(
            session_id=sid,
            station_id="st1",
            station_name="Station 1",
            slot_count=3,
            session_type="interactive",
        )
        assert e.session_id == sid
        assert e.station_id == "st1"
        assert e.slot_count == 3
        assert e.session_type == "interactive"
        assert e.pid is not None
        assert e.station_hostname is not None

    def test_session_started_from_station_reads_env(self, monkeypatch):
        monkeypatch.setenv("_LITMUS_SLOT_COUNT", "4")
        e = SessionStarted.from_station(
            session_id=uuid4(),
            station_id="st1",
        )
        assert e.slot_count == 4

    def test_session_started_rejects_run_id(self):
        import pytest as _pytest

        with _pytest.raises(ValueError, match="must not have run_id"):
            SessionStarted(station_id="st1", run_id=uuid4())

    def test_session_ended_rejects_run_id(self):
        import pytest as _pytest

        from litmus.data.events import SessionEnded

        with _pytest.raises(ValueError, match="must not have run_id"):
            SessionEnded(outcome="passed", run_id=uuid4())

    def test_session_started_has_no_run_id(self):
        e = SessionStarted(station_id="st1")
        assert e.run_id is None

    def test_session_ended_has_no_run_id(self):
        from litmus.data.events import SessionEnded

        e = SessionEnded(outcome="passed")
        assert e.run_id is None

    def test_run_started_slot_index(self):
        e = RunStarted(station_id="st1", slot_id="slot_1", slot_index=0)
        assert e.slot_index == 0

    def test_category_grouping(self):
        assert SessionStarted in SESSION_EVENTS
        assert RunStarted in RUN_EVENTS
        assert RunEnded in RUN_EVENTS
        assert InstrumentConnected in FIXTURE_EVENTS
        assert MeasurementRecorded in TEST_EVENTS
        assert len(ALL_EVENTS) == (
            len(SESSION_EVENTS)
            + len(RUN_EVENTS)
            + len(SLOT_EVENTS)
            + len(FIXTURE_EVENTS)
            + len(TEST_EVENTS)
            + len(ROUTE_EVENTS)
            + len(INSTRUMENT_EVENTS)
            + len(CHANNEL_EVENTS)
            + len(DIAGNOSTIC_EVENTS)
            + len(STREAM_EVENTS)
            + len(DIALOG_EVENTS)
        )
