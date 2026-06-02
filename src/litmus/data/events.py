"""Typed event hierarchy for the Litmus event log.

Events are normalized: ``SessionStarted`` captures run metadata once,
``MeasurementRecorded`` carries only measurement-specific fields.
Subscribers (e.g. ParquetSubscriber) denormalize at write time.
"""

from __future__ import annotations

import os
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from litmus.data.models import _utcnow


def _detect_client() -> str:
    """Derive a human-readable client name from the running process."""
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
    """Emitted once at the start of a session (interactive or test orchestrator).

    Contains session-wide metadata only. Run-level fields (DUT, config snapshots)
    live in ``RunStarted``. Session events must NOT carry run_id.
    """

    event_type: Literal["session.started"] = "session.started"
    session_type: str = "test_run"

    # Station — id is None for bringup tier (no station YAML loaded)
    station_id: str | None = None
    station_name: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    station_hostname: str | None = None

    # Process
    pid: int | None = None
    client: str = Field(default_factory=_detect_client)

    # Operator
    operator_id: str | None = None
    operator_name: str | None = None

    # Fixture & slot
    fixture_id: str | None = None
    slot_count: int = 1

    @model_validator(mode="after")
    def _reject_run_id(self) -> SessionStarted:
        if self.run_id is not None:
            raise ValueError("SessionStarted must not have run_id; use RunStarted for run context")
        return self

    @classmethod
    def from_station(
        cls,
        *,
        session_id: UUID,
        station_id: str | None,
        station_name: str | None = None,
        station_type: str | None = None,
        station_location: str | None = None,
        station_hostname: str | None = None,
        operator_id: str | None = None,
        operator_name: str | None = None,
        fixture_id: str | None = None,
        slot_count: int | None = None,
        session_type: str = "test_run",
    ) -> SessionStarted:
        """Build a SessionStarted with common station fields.

        Shared by plugin.py (pytest) and connect.py (interactive).
        If ``slot_count`` is None, reads ``_LITMUS_SLOT_COUNT`` env var
        (defaults to 1).
        """
        if slot_count is None:
            slot_count = int(os.environ.get("_LITMUS_SLOT_COUNT", "1"))

        return cls(
            session_id=session_id,
            station_id=station_id,
            station_name=station_name,
            station_type=station_type,
            station_location=station_location,
            station_hostname=station_hostname or socket.gethostname(),
            operator_id=operator_id,
            operator_name=operator_name,
            fixture_id=fixture_id,
            slot_count=slot_count,
            session_type=session_type,
            pid=os.getpid(),
        )


class SessionEnded(EventBase):
    """Emitted at the end of a session. Must NOT carry run_id."""

    event_type: Literal["session.ended"] = "session.ended"
    outcome: str | None = None

    @model_validator(mode="after")
    def _reject_run_id(self) -> SessionEnded:
        if self.run_id is not None:
            raise ValueError("SessionEnded must not have run_id; use RunEnded for run lifecycle")
        return self


# ---------------------------------------------------------------------------
# Run events (test run lifecycle)
# ---------------------------------------------------------------------------


class RunStarted(EventBase):
    """Emitted once per test run. Contains full run context.

    In single-DUT mode, one RunStarted follows SessionStarted.
    In multi-DUT mode, each worker emits its own RunStarted.
    """

    event_type: Literal["run.started"] = "run.started"

    # Station — id is None for bringup tier (no station YAML loaded)
    station_id: str | None = None
    station_name: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    station_hostname: str | None = None
    slot_id: str | None = None
    slot_index: int | None = None

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
    test_phase: str | None = None
    project_name: str | None = None
    git_commit: str | None = None
    git_branch: str | None = None
    git_remote: str | None = None

    # Environment snapshot (small — python/litmus versions + fingerprint)
    environment_json: str | None = None

    # Custom metadata
    custom_metadata: dict[str, Any] = Field(default_factory=dict)

    # Channel references for infrastructure correlation
    channel_refs: list[str] = Field(default_factory=list)


class RunEnded(EventBase):
    """Emitted at the end of a test run."""

    event_type: Literal["run.ended"] = "run.ended"
    outcome: str | None = None


class RunMaterialized(EventBase):
    """Emitted by a materializer after a run's state has been written to a
    durable, query-optimized backend.

    Today the only materializer is the runs daemon writing parquet +
    ingesting into ``runs_materialized`` / ``steps_materialized`` /
    ``measurements_materialized``. Future materializers (Postgres,
    Snowflake, Delta Lake, etc.) emit the same event with their own
    ``materializer`` name and ``destination`` URI/path.

    Lifecycle handshake:

    * Tells the live materializer pool (in the runs daemon) to evict
      the run — its in-memory accumulator is no longer the source of
      truth for it; the materialized view is.
    * Tells retention the run's event cohort is safe to retire from
      the EventStore. Events remain persisted in the EventStore until
      retention prunes them; the run is *materialized* when this
      event lands.

    Distinct from ``RunEnded``: a run can be RunEnded (the test
    finished) without yet being RunMaterialized (no durable read
    model exists). The bigger refactor closes this window to ~ms.
    """

    event_type: Literal["run.materialized"] = "run.materialized"
    materializer: str
    destination: str
    materialized_at: datetime = Field(default_factory=_utcnow)
    row_counts: dict[str, int] | None = None


# ---------------------------------------------------------------------------
# Slot events (multi-DUT)
# ---------------------------------------------------------------------------


class SlotStarted(EventBase):
    """Emitted when a DUT slot begins execution."""

    event_type: Literal["slot.started"] = "slot.started"
    slot_id: str
    dut_serial: str


class SlotCompleted(EventBase):
    """Emitted when a DUT slot finishes execution."""

    event_type: Literal["slot.completed"] = "slot.completed"
    slot_id: str
    outcome: str  # "passed", "failed", "errored", etc — see Outcome
    error_message: str | None = None


class SyncArrived(EventBase):
    """Emitted by a child process when it reaches a named sync point."""

    event_type: Literal["sync.arrived"] = "sync.arrived"
    slot_id: str
    name: str  # Sync point name (e.g., "thermal_soak")


class SyncRelease(EventBase):
    """Emitted by the orchestrator to unblock all slots at a sync point."""

    event_type: Literal["sync.release"] = "sync.release"
    name: str  # Sync point name


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

    # Vector context — which sweep condition this execution is.
    # vector_index 0 (the default) is the natural value for non-swept steps;
    # for sweep variants it identifies the specific condition. ``inputs``
    # carries the commanded sweep parameters (in_*) for this vector — what
    # subscribers need to disambiguate "test_efficiency starting" from
    # "test_efficiency starting at vin=2.0V".
    vector_index: int = 0
    inputs: dict[str, Any] = Field(default_factory=dict)

    # Code identity
    node_id: str | None = None
    file: str | None = None
    module: str | None = None
    class_name: str | None = None
    function: str | None = None


class MeasurementRecorded(EventBase):
    """A single measurement. Normalized: carries only measurement-specific fields.

    Run metadata (station, DUT, operator, etc.) lives in ``RunStarted``.
    Instrument arrays live in ``InstrumentConnected`` events.
    Subscribers denormalize at write time.
    """

    event_type: Literal["test.measurement"] = "test.measurement"

    # Step/vector context
    step_name: str
    step_index: int
    step_path: str = ""
    vector_index: int = 0
    retry: int = 0  # 0 for first execution, N for Nth retry

    # Measurement fields
    measurement_name: str
    measurement_timestamp: datetime | None = None
    value: float | None = None
    units: str | None = None
    outcome: str | None = None
    limit_low: float | None = None
    limit_high: float | None = None
    limit_nominal: float | None = None
    limit_comparator: str | None = None
    characteristic_id: str | None = None
    spec_ref: str | None = None

    # Signal path
    dut_pin: str | None = None
    fixture_connection: str | None = None
    instrument_name: str | None = None
    instrument_resource: str | None = None
    instrument_channel: str | None = None

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


class Observation(EventBase):
    """Emitted by ``Context.observe(key, value)``.

    Carries the observation that landed in the vector's ``out_<name>``
    column. Value is the scalar inline when scalar, or the claim URI
    string (``channel://…`` or ``file://…``) when the value was
    routed to a store.

    Item 4 in the v0.2.0 data-architecture lift — closes the gap
    where ``observe()`` calls were silent on the event timeline.
    """

    event_type: Literal["test.observation"] = "test.observation"

    # Step/vector context (matches MeasurementRecorded shape)
    step_name: str = ""
    step_index: int = 0
    step_path: str = ""
    vector_index: int = 0
    retry: int = 0

    # Observation
    name: str
    value: Any = None


class StepEnded(EventBase):
    event_type: Literal["test.step_ended"] = "test.step_ended"
    step_name: str
    step_index: int
    step_path: str = ""
    # parent_path mirrors the StepStarted field so subscribers reconstructing
    # the run hierarchy can walk parent → children without joining against
    # other event types.
    parent_path: str = ""
    # ``None`` is a valid value: a step that opened but never recorded
    # a measurement (and never had an outcome cascaded into it) ends
    # with no outcome stamped. The unified parquet preserves that signal —
    # measurement rows, by construction, only exist for steps that recorded
    # measurements; step-summary rows fill the gap.
    outcome: str | None = None

    # Vector context for this specific execution.
    # ``vector_outcome`` is the per-vector verdict (the step-level ``outcome``
    # is the aggregate across vectors).  ``inputs`` repeat the commanded sweep
    # parameters for completeness; ``outputs`` carries vector-level
    # observations not tied to any specific measurement.
    vector_index: int = 0
    vector_outcome: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)

    # Code identity
    node_id: str | None = None
    file: str | None = None
    module: str | None = None
    class_name: str | None = None
    function: str | None = None


class StepsDiscovered(EventBase):
    """Emitted after instruments connect, before steps execute.

    Carries the full list of pytest-collected items so subscribers can
    build a complete step manifest (including ``not_started`` entries
    for steps that never ran due to abort / ``--maxfail``).

    One event per run, bounded by test count (not vectors).
    The DuckDB index caps the json column at 4K; the arrow file keeps
    the full list for replay.
    """

    event_type: Literal["test.steps_discovered"] = "test.steps_discovered"
    # Mixed string/int payload — strings for code identity (node_id, file,
    # module, class_name, function, markers), ints for the
    # collection-time-assigned step_index, vector_index, vector_count_planned.
    items: list[dict[str, str | int | None]] = Field(default_factory=list)


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
# Route events (signal switching)
# ---------------------------------------------------------------------------


class RouteClosed(EventBase):
    """Emitted when switch channels are closed to activate a route."""

    event_type: Literal["route.closed"] = "route.closed"
    connection_name: str
    switch_role: str
    channels: list[str]


class RouteOpened(EventBase):
    """Emitted when switch channels are opened to deactivate a route."""

    event_type: Literal["route.opened"] = "route.opened"
    connection_name: str
    switch_role: str
    channels: list[str]


# ---------------------------------------------------------------------------
# Instrument events
# ---------------------------------------------------------------------------


class ChannelStarted(EventBase):
    """A channel received its first sample in this session.

    Position 2 lifecycle event: emitted once per ``(channel_id, session_id)``
    pair. Replaces the per-sample ``InstrumentRead`` event (retired in
    v0.2.0). Sample data lives in ChannelStore — subscribers wanting
    per-sample access subscribe to ChannelStore via Flight ``do_get``.

    Carries enough context to let consumers find + subscribe to the
    channel without further discovery. Instrument fields are populated
    when the source is an instrument observer; null otherwise (for
    ``stream(...)`` / ``channels.write`` / daemon-driven writes).
    """

    event_type: Literal["channel.started"] = "channel.started"
    channel_id: str
    units: str | None = None
    # Instrument source fields — populated for observer.read; null otherwise.
    instrument_role: str | None = None
    method: str | None = None
    resource: str | None = None


class ChannelClosed(EventBase):
    """A channel was sealed for this session.

    Position 2 lifecycle event. Fires when a session ends (all of its
    channels close) or when retention pruning removes a channel.
    Consumers tracking "still being written to" vs "no more data
    coming" key off this event.

    Emission wiring lives downstream — for v0.2.0 initial cut the
    class exists; SessionEnded-tied emission lands in a follow-up.
    """

    event_type: Literal["channel.closed"] = "channel.closed"
    channel_id: str
    reason: str  # e.g., "session_ended" | "retention_prune"


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
    """Emitted when a FileStore streaming sink opens.

    Once per ``stream_id``. Carries the on-disk path so live consumers
    can begin a range-read or library-decode against the still-growing
    file before any chunks land. ``path`` is the absolute filesystem
    path; the final ``file://`` URI is announced via :class:`StreamEnded`
    at close (it can't be known until the sink resolves a collision-free
    name).

    **Stream events are lifecycle-only** (the FileStore parallel of
    Position 2 for channels). Per-chunk events would flood the
    EventStore at high write rates (kHz captures, 30 fps video, etc.)
    for no real subscriber gain. Live consumers subscribe to the
    stream directly — range-read the file, decode via the format
    library, or watch the underlying transport — using EventStore only
    for discovery ("what streams are open / done").
    """

    event_type: Literal["stream.started"] = "stream.started"
    stream_id: UUID
    name: str = ""
    format: str = ""
    path: str | None = None


class StreamEnded(EventBase):
    """Emitted when a FileStore streaming sink closes.

    Once per ``stream_id``. ``uri`` is the final ``file://`` claim that
    callers can stash into vector ``out_*`` columns or hand to the
    artifact viewer. ``size_bytes`` is the total appended-byte count
    at close.
    """

    event_type: Literal["stream.ended"] = "stream.ended"
    stream_id: UUID
    uri: str | None = None
    size_bytes: int | None = None


# ---------------------------------------------------------------------------
# Dialog events
# ---------------------------------------------------------------------------


class DialogOpened(EventBase):
    """Emitted when an operator dialog is shown, pausing test execution."""

    event_type: Literal["dialog.opened"] = "dialog.opened"
    dialog_id: UUID
    dialog_type: str  # "confirm", "choice", "input", "image"
    title: str
    message: str
    step_name: str | None = None
    blocking: bool = True


class DialogResponded(EventBase):
    """Emitted when an operator dialog receives a response."""

    event_type: Literal["dialog.responded"] = "dialog.responded"
    dialog_id: UUID
    dialog_type: str
    response_type: str  # "answered", "cancelled", "timed_out"
    duration_seconds: float
    value: str | None = None
    choice: int | None = None


# ---------------------------------------------------------------------------
# Category grouping constants
# ---------------------------------------------------------------------------

SESSION_EVENTS = {SessionStarted, SessionEnded}
RUN_EVENTS = {RunStarted, RunEnded}
SLOT_EVENTS = {SlotStarted, SlotCompleted, SyncArrived, SyncRelease}
FIXTURE_EVENTS = {
    InstrumentConnected,
    IdentityVerified,
    CalibrationWarning,
    DutScanned,
    InstrumentDisconnected,
}
TEST_EVENTS = {
    StepStarted,
    MeasurementRecorded,
    RecordEvent,
    Observation,
    StepEnded,
    StepsDiscovered,
}
ROUTE_EVENTS = {RouteClosed, RouteOpened}
INSTRUMENT_EVENTS = {InstrumentSet, InstrumentConfigure}
CHANNEL_EVENTS = {ChannelStarted, ChannelClosed}
DIAGNOSTIC_EVENTS = {DiagnosticWarning, DiagnosticError}
STREAM_EVENTS = {StreamStarted, StreamEnded}
DIALOG_EVENTS = {DialogOpened, DialogResponded}
ALL_EVENTS = (
    SESSION_EVENTS
    | RUN_EVENTS
    | SLOT_EVENTS
    | FIXTURE_EVENTS
    | TEST_EVENTS
    | ROUTE_EVENTS
    | INSTRUMENT_EVENTS
    | CHANNEL_EVENTS
    | DIAGNOSTIC_EVENTS
    | STREAM_EVENTS
    | DIALOG_EVENTS
)

# Discriminated union type for deserialization
Event = Annotated[
    SessionStarted
    | SessionEnded
    | RunStarted
    | RunEnded
    | SlotStarted
    | SlotCompleted
    | SyncArrived
    | SyncRelease
    | InstrumentConnected
    | IdentityVerified
    | CalibrationWarning
    | DutScanned
    | InstrumentDisconnected
    | StepStarted
    | MeasurementRecorded
    | RecordEvent
    | Observation
    | StepEnded
    | StepsDiscovered
    | RouteClosed
    | RouteOpened
    | InstrumentSet
    | InstrumentConfigure
    | ChannelStarted
    | ChannelClosed
    | DiagnosticWarning
    | DiagnosticError
    | StreamStarted
    | StreamEnded
    | DialogOpened
    | DialogResponded,
    Field(discriminator="event_type"),
]
