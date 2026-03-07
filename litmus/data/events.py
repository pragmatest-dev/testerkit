"""Typed event hierarchy for the Litmus event log.

Events are normalized: ``SessionStarted`` captures run metadata once,
``MeasurementRecorded`` carries only measurement-specific fields.
Subscribers (e.g. ParquetSubscriber) denormalize at write time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class EventBase(BaseModel):
    """Base for all event log events.

    Subclasses must define ``event_type`` as a ``Literal`` field.
    """

    id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=_utcnow)
    received_at: datetime | None = None  # Set by EventLog.emit()
    session_id: UUID = Field(default_factory=uuid4)
    run_id: UUID | None = None


# ---------------------------------------------------------------------------
# Session events
# ---------------------------------------------------------------------------

class SessionStarted(EventBase):
    """Emitted once at the start of a run. Contains full run context."""

    event_type: Literal["session.started"] = "session.started"

    # Station
    station_id: str
    station_name: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    slot_id: str | None = None

    # DUT
    dut_serial: str
    dut_part_number: str | None = None
    dut_revision: str | None = None
    dut_lot_number: str | None = None

    # Product
    product_id: str | None = None
    product_name: str | None = None
    product_revision: str | None = None

    # Operator
    operator_id: str | None = None
    operator_name: str | None = None

    # Test context
    fixture_id: str | None = None
    sequence_id: str | None = None
    test_phase: str = "production"
    git_commit: str | None = None

    # Environment & config snapshots
    environment_json: str | None = None
    station_config_yaml: str | None = None
    product_spec_yaml: str | None = None
    fixture_config_yaml: str | None = None
    test_config_yaml: str | None = None

    # Custom metadata
    custom_metadata: dict[str, Any] = Field(default_factory=dict)

    # Channel references for infrastructure correlation
    channel_refs: list[str] = Field(default_factory=list)


class SessionEnded(EventBase):
    """Emitted at the end of a run."""

    event_type: Literal["session.ended"] = "session.ended"
    outcome: str = "pass"


# ---------------------------------------------------------------------------
# Fixture events
# ---------------------------------------------------------------------------

class InstrumentConnected(EventBase):
    """Emitted when an instrument is connected and identified."""

    event_type: Literal["fixture.instrument_connected"] = "fixture.instrument_connected"

    role: str
    instrument_id: str
    driver: str | None = None
    resource: str
    protocol: str = "visa"
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None
    cal_due: str | None = None
    cal_last: str | None = None
    cal_certificate: str | None = None
    cal_lab: str | None = None
    mocked: bool = False


class IdentityVerified(EventBase):
    event_type: Literal["fixture.identity_verified"] = "fixture.identity_verified"
    role: str
    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    matches: bool = True
    mismatches: list[str] = Field(default_factory=list)


class CalibrationWarning(EventBase):
    event_type: Literal["fixture.calibration_warning"] = "fixture.calibration_warning"
    role: str
    instrument_id: str
    days_until_due: int | None = None
    message: str = ""


class DutScanned(EventBase):
    event_type: Literal["fixture.dut_scanned"] = "fixture.dut_scanned"
    dut_serial: str
    scan_source: str | None = None


class FixtureTeardown(EventBase):
    event_type: Literal["fixture.teardown"] = "fixture.teardown"
    reason: str | None = None


# ---------------------------------------------------------------------------
# Test events
# ---------------------------------------------------------------------------

class StepStarted(EventBase):
    event_type: Literal["test.step_started"] = "test.step_started"
    step_name: str
    step_index: int
    description: str | None = None


class MeasurementRecorded(EventBase):
    """A single measurement. Normalized: carries only measurement-specific fields.

    Run metadata (station, DUT, operator, etc.) lives in ``SessionStarted``.
    Instrument arrays live in ``InstrumentConnected`` events.
    Subscribers denormalize at write time.
    """

    event_type: Literal["test.measurement"] = "test.measurement"

    # Step/vector context
    step_name: str
    step_index: int
    vector_index: int | None = None
    attempt: int | None = None

    # Measurement fields
    measurement_name: str
    measurement_timestamp: datetime | None = None
    value: float | None = None
    units: str | None = None
    outcome: str | None = None
    low_limit: float | None = None
    high_limit: float | None = None
    nominal: float | None = None
    comparator: str | None = None
    spec_id: str | None = None
    spec_ref: str | None = None

    # Signal path
    meas_dut_pin: str | None = None
    meas_fixture_point: str | None = None
    meas_instrument: str | None = None
    meas_instrument_resource: str | None = None
    meas_instrument_channel: str | None = None

    # Dynamic columns (vector-specific, not available elsewhere)
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    custom: dict[str, Any] = Field(default_factory=dict)


class RecordEvent(EventBase):
    """A raw record (non-measurement data row)."""

    event_type: Literal["test.record"] = "test.record"
    step_name: str
    step_index: int
    data: dict[str, Any] = Field(default_factory=dict)


class StepEnded(EventBase):
    event_type: Literal["test.step_ended"] = "test.step_ended"
    step_name: str
    step_index: int
    outcome: str = "pass"


class RunEnded(EventBase):
    event_type: Literal["test.run_ended"] = "test.run_ended"
    outcome: str = "pass"


# ---------------------------------------------------------------------------
# Diagnostic events
# ---------------------------------------------------------------------------

class DiagnosticWarning(EventBase):
    event_type: Literal["diagnostic.warning"] = "diagnostic.warning"
    source: str = ""
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class DiagnosticError(EventBase):
    event_type: Literal["diagnostic.error"] = "diagnostic.error"
    source: str = ""
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stream events (Phase 2+)
# ---------------------------------------------------------------------------

class StreamStarted(EventBase):
    event_type: Literal["stream.started"] = "stream.started"
    stream_id: UUID = Field(default_factory=uuid4)
    format: str = ""
    path: str | None = None


class StreamEnded(EventBase):
    event_type: Literal["stream.ended"] = "stream.ended"
    stream_id: UUID = Field(default_factory=uuid4)


class StreamFrameIndex(EventBase):
    event_type: Literal["stream.frame_index"] = "stream.frame_index"
    stream_id: UUID = Field(default_factory=uuid4)
    frame_count: int = 0


# ---------------------------------------------------------------------------
# Category grouping constants
# ---------------------------------------------------------------------------

SESSION_EVENTS = {SessionStarted, SessionEnded}
FIXTURE_EVENTS = {
    InstrumentConnected, IdentityVerified, CalibrationWarning, DutScanned, FixtureTeardown,
}
TEST_EVENTS = {StepStarted, MeasurementRecorded, RecordEvent, StepEnded, RunEnded}
DIAGNOSTIC_EVENTS = {DiagnosticWarning, DiagnosticError}
STREAM_EVENTS = {StreamStarted, StreamEnded, StreamFrameIndex}
ALL_EVENTS = SESSION_EVENTS | FIXTURE_EVENTS | TEST_EVENTS | DIAGNOSTIC_EVENTS | STREAM_EVENTS

# Discriminated union type for deserialization
Event = Annotated[
    SessionStarted | SessionEnded
    | InstrumentConnected | IdentityVerified | CalibrationWarning | DutScanned | FixtureTeardown
    | StepStarted | MeasurementRecorded | RecordEvent | StepEnded | RunEnded
    | DiagnosticWarning | DiagnosticError
    | StreamStarted | StreamEnded | StreamFrameIndex,
    Field(discriminator="event_type"),
]
