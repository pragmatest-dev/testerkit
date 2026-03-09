"""Tests for event type hierarchy."""

from uuid import uuid4

from litmus.data.events import (
    ALL_EVENTS,
    DIALOG_EVENTS,
    DIAGNOSTIC_EVENTS,
    FIXTURE_EVENTS,
    INSTRUMENT_EVENTS,
    SESSION_EVENTS,
    STREAM_EVENTS,
    TEST_EVENTS,
    InstrumentConnected,
    MeasurementRecorded,
    RecordEvent,
    RunEnded,
    SessionStarted,
    StepEnded,
    StepStarted,
)


class TestEventModels:
    def test_session_started_defaults(self):
        e = SessionStarted(
            station_id="st1",
            dut_serial="SN001",
        )
        assert e.event_type == "session.started"
        assert e.station_id == "st1"
        assert e.dut_serial == "SN001"
        assert e.test_phase == "production"
        assert e.id is not None
        assert e.occurred_at is not None

    def test_measurement_recorded_fields(self):
        run_id = uuid4()
        e = MeasurementRecorded(
            run_id=run_id,
            step_name="test_voltage",
            step_index=0,
            measurement_name="vout",
            value=3.3,
            units="V",
            outcome="pass",
            low_limit=3.0,
            high_limit=3.6,
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
        e = StepEnded(step_name="step1", step_index=0, outcome="pass")
        assert e.event_type == "test.step_ended"

    def test_run_ended(self):
        e = RunEnded(outcome="fail")
        assert e.event_type == "test.run_ended"
        assert e.outcome == "fail"

    def test_serialization_roundtrip(self):
        e = SessionStarted(
            station_id="st1",
            dut_serial="SN001",
            custom_metadata={"badge": "EMP-123"},
        )
        json_str = e.model_dump_json()
        restored = SessionStarted.model_validate_json(json_str)
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
        e = SessionStarted(station_id="st1", dut_serial="SN001")
        assert e.session_type == "test_run"
        e2 = SessionStarted(
            station_id="st1", dut_serial="SN001", session_type="characterization"
        )
        assert e2.session_type == "characterization"

    def test_category_grouping(self):
        assert SessionStarted in SESSION_EVENTS
        assert InstrumentConnected in FIXTURE_EVENTS
        assert MeasurementRecorded in TEST_EVENTS
        assert len(ALL_EVENTS) == (
            len(SESSION_EVENTS) + len(FIXTURE_EVENTS) + len(TEST_EVENTS)
            + len(INSTRUMENT_EVENTS) + len(DIAGNOSTIC_EVENTS) + len(STREAM_EVENTS)
            + len(DIALOG_EVENTS)
        )
