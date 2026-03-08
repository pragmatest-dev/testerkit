"""Typed event hierarchy for the Litmus event log.

Events are normalized: ``SessionStarted`` captures run metadata once,
``MeasurementRecorded`` carries only measurement-specific fields.
Subscribers (e.g. ParquetSubscriber) denormalize at write time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_serializer

from litmus.data.ref import classify_value, make_channel_uri


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _detect_client() -> str:
    """Derive a human-readable client name from the running process."""
    import sys
    from pathlib import Path

    if not sys.argv:
        return "unknown"
    name = Path(sys.argv[0]).name
    # Common runners → friendly names
    if name in ("pytest", "py.test") or "pytest" in name:
        return "pytest"
    if name in ("jupyter", "ipykernel_launcher"):
        return "jupyter"
    return name


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
    session_type: str = "test_run"

    # Station
    station_id: str
    station_name: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    slot_id: str | None = None

    # Process
    pid: int | None = None
    client: str = Field(default_factory=_detect_client)

    # DUT
    dut_serial: str = ""
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


class InstrumentDisconnected(EventBase):
    """Emitted when an instrument is disconnected during teardown."""

    event_type: Literal["fixture.instrument_disconnected"] = "fixture.instrument_disconnected"
    role: str
    instrument_id: str


# ---------------------------------------------------------------------------
# Test events
# ---------------------------------------------------------------------------

class StepStarted(EventBase):
    event_type: Literal["test.step_started"] = "test.step_started"
    step_name: str
    step_index: int
    step_path: str = ""
    parent_path: str = ""
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
    step_path: str = ""
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
    """A key/value record emitted by harness.record()."""

    event_type: Literal["test.record"] = "test.record"
    step_name: str
    step_index: int
    key: str
    value: Any


class StepEnded(EventBase):
    event_type: Literal["test.step_ended"] = "test.step_ended"
    step_name: str
    step_index: int
    step_path: str = ""
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
# Instrument events
# ---------------------------------------------------------------------------

class InstrumentRead(EventBase):
    """Emitted when a driver read method is called via proxy.

    For array/waveform data, ``value`` holds the full Python object in memory
    (subscribers like ChannelStore get the real data), but JSON serialization
    replaces it with a claim-check summary to keep JSONL compact.
    """

    event_type: Literal["instrument.read"] = "instrument.read"
    instrument_role: str
    channel_id: str
    method: str
    value: Any = None
    units: str | None = None
    resource: str = ""

    @model_serializer(mode="wrap")
    def _serialize_with_claim_check(self, handler: Any) -> dict[str, Any]:
        """Scalars and URIs inline; raw arrays → ``channel://`` URI claim-check.

        When proxy writes to ChannelStore directly, value is already a URI
        string — pass through. Fallback for no-channel-store case still
        builds a claim-check reference.
        """
        from litmus.data.ref import is_ref

        data = handler(self)
        v = self.value

        # Already a URI from proxy writing to ChannelStore
        if is_ref(v):
            return data

        vtype = classify_value(v)

        if vtype == "scalar":
            return data

        if vtype in ("numeric_array", "channel"):
            uri = make_channel_uri(self.channel_id, str(self.session_id))
            ref: dict[str, Any] = {
                "_ref": uri,
                "channel_id": self.channel_id,
                "type": "array" if vtype == "numeric_array" else "struct",
            }
            if isinstance(v, (list, tuple)) and len(v) >= 1:
                first = v[0]
                if isinstance(first, (list, tuple)):
                    samples = first
                    dt = v[1] if len(v) > 1 else None
                    ref["length"] = len(samples)
                    if dt is not None:
                        ref["sample_interval"] = dt
                    if samples:
                        ref["min"] = min(samples)
                        ref["max"] = max(samples)
            elif hasattr(v, "tolist"):
                ref["length"] = len(v)  # type: ignore[arg-type]
            data["value"] = ref
            return data

        # blob — repr for JSONL
        data["value"] = repr(v)
        return data


class InstrumentSet(EventBase):
    """Emitted when a driver set method is called via proxy."""

    event_type: Literal["instrument.set"] = "instrument.set"
    instrument_role: str
    channel_id: str
    attribute: str
    value: Any = None
    units: str | None = None
    resource: str = ""


class InstrumentConfigure(EventBase):
    """Emitted when a driver configure method is called via proxy."""

    event_type: Literal["instrument.configure"] = "instrument.configure"
    instrument_role: str
    method: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    resource: str = ""


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
    InstrumentConnected, IdentityVerified, CalibrationWarning, DutScanned, InstrumentDisconnected,
}
TEST_EVENTS = {StepStarted, MeasurementRecorded, RecordEvent, StepEnded, RunEnded}
INSTRUMENT_EVENTS = {InstrumentRead, InstrumentSet, InstrumentConfigure}
DIAGNOSTIC_EVENTS = {DiagnosticWarning, DiagnosticError}
STREAM_EVENTS = {StreamStarted, StreamEnded, StreamFrameIndex}
ALL_EVENTS = (
    SESSION_EVENTS | FIXTURE_EVENTS | TEST_EVENTS
    | INSTRUMENT_EVENTS | DIAGNOSTIC_EVENTS | STREAM_EVENTS
)

# Discriminated union type for deserialization
Event = Annotated[
    SessionStarted | SessionEnded
    | InstrumentConnected | IdentityVerified | CalibrationWarning
    | DutScanned | InstrumentDisconnected
    | StepStarted | MeasurementRecorded | RecordEvent | StepEnded | RunEnded
    | InstrumentRead | InstrumentSet | InstrumentConfigure
    | DiagnosticWarning | DiagnosticError
    | StreamStarted | StreamEnded | StreamFrameIndex,
    Field(discriminator="event_type"),
]
