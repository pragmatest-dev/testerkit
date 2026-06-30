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

from litmus.data._process import process_uuid
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


# Identifier and name fields promoted from the JSON payload into typed
# DuckDB columns so the daemon can push WHERE filters down instead of
# round-tripping rows to Python for post-filtering. The user-facing
# rule: "all ids and names" — enough to uniquify any event and walk
# its follow-ons.
#
# Three groups, by reason:
#
# * **Pairing IDs** — an open-event ``file_id`` / ``dialog_id`` /
#   ``channel_id`` / ``slot_id`` has a matching close-event with the
#   same value. The pushdown answers "did this open ever close?"
# * **Operator-facing identifiers** — ``uut_serial_number`` /
#   ``station_hostname`` are how operators name what they're querying
#   ("everything that happened to SN001 on bench-3").
# * **Names + roles + enums** — recognition fields and the small set
#   of kind/state enums that drive routine filters (``outcome``,
#   ``reason``, ``format``, ``dialog_type``, ``response_type``).
#
# Adding a column is two edits — extend this tuple and the parallel
# ``_EVENTS_COLUMNS`` in ``_duckdb_daemon.py``. The daemon's
# ``ALTER TABLE ADD COLUMN IF NOT EXISTS`` auto-migrates existing DBs.
TYPED_PAYLOAD_COLUMNS: tuple[str, ...] = (
    # Pairing IDs
    "file_id",
    "dialog_id",
    "channel_id",
    "slot_id",
    # Operator-facing identifiers
    "uut_serial_number",
    "station_hostname",
    # Other IDs
    "instrument_id",
    "node_id",
    "step_path",
    "fixture_id",
    "operator_id",
    "station_id",
    # Names
    "step_name",
    "measurement_name",
    "name",
    # Instrument roles (two field names for legacy reasons — both
    # promoted so the role= filter pushes down via SQL OR).
    "role",
    "instrument_role",
    # Kind/state enums
    "outcome",
    "reason",
    "format",
    "dialog_type",
    "response_type",
)


class EventBase(BaseModel):
    """Base for all event log events.

    Subclasses must define ``event_type`` as a ``Literal`` field.
    """

    id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=_utcnow)
    received_at: datetime | None = None  # Set by EventLog.emit()
    session_id: UUID = Field(default_factory=uuid4)
    run_id: UUID | None = None
    # ``True`` for events the spine itself derives (the reaper's synthetic closes,
    # a materializer's ``RunMaterialized``) vs. a producer observation. The
    # terminal fence rejects post-seal PRODUCER writes (revival) but lets derived
    # completions through — so a run's async ``RunMaterialized`` still lands after
    # its session has sealed. Producers never set it.
    derived: bool = False

    def typed_payload_values(self) -> dict[str, str | None]:
        """Return promoted column values for this event as ``{col: str | None}``.

        Reads each column in :data:`TYPED_PAYLOAD_COLUMNS` via
        ``getattr``, stringifying UUIDs and coercing empty strings to
        ``None`` so the columns stay sparse (``WHERE col IS NOT NULL``
        cleanly distinguishes "field absent" from "field set to "").
        Events that don't declare a given column return ``None`` for
        it — every event produces every column slot.
        """
        out: dict[str, str | None] = {}
        for col in TYPED_PAYLOAD_COLUMNS:
            val = getattr(self, col, None)
            if val is None:
                out[col] = None
            elif isinstance(val, str):
                out[col] = val or None
            else:
                # UUID stringifies; ints/floats coerce.
                out[col] = str(val)
        return out


# ---------------------------------------------------------------------------
# Session events
# ---------------------------------------------------------------------------


class SessionStarted(EventBase):
    """Emitted once at the start of a session (interactive or test orchestrator).

    Contains session-wide metadata only. Run-level fields (UUT, config snapshots)
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

    # The will — the owner's liveness policy, read by the reaper off this event
    # (never config). ``process_uuid`` pairs with pid + station_hostname as the
    # producer identity (disambiguates a recycled pid). ``idle_lease_seconds`` /
    # ``abandon_grace_seconds`` / ``abandon_reason`` are resolved producer-side
    # from ``SessionOptions`` and stamped here at open.
    process_uuid: str | None = None
    idle_lease_seconds: float | None = None
    abandon_grace_seconds: float | None = None
    abandon_reason: str | None = None

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
        idle_lease_seconds: float | None = None,
        abandon_grace_seconds: float | None = None,
        abandon_reason: str | None = None,
    ) -> SessionStarted:
        """Build a SessionStarted with common station fields.

        Shared by plugin.py (pytest) and connect.py (interactive).
        If ``slot_count`` is None, reads ``_LITMUS_SLOT_COUNT`` env var
        (defaults to 1). The will fields (``idle_lease_seconds`` /
        ``abandon_grace_seconds`` / ``abandon_reason``) are resolved
        producer-side from ``SessionOptions`` by the caller; ``process_uuid``
        is stamped automatically, like ``pid``.
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
            process_uuid=process_uuid(),
            idle_lease_seconds=idle_lease_seconds,
            abandon_grace_seconds=abandon_grace_seconds,
            abandon_reason=abandon_reason,
        )


class SessionEnded(EventBase):
    """Emitted at the end of a session. Must NOT carry run_id.

    A clean close (owner leaving) carries the defaults. The reaper emits a
    *derived* close for an abandoned session — ``derived=True`` and ``reason``
    from the will (e.g. ``"abandoned"``) — so the synthetic seal is
    operator-visible and never confused with a cooperative end.
    """

    event_type: Literal["session.ended"] = "session.ended"
    reason: str | None = None

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

    In single-UUT mode, one RunStarted follows SessionStarted.
    In multi-UUT mode, each worker emits its own RunStarted.
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

    # UUT
    uut_serial_number: str = ""
    uut_part_number: str | None = None
    uut_revision: str | None = None
    uut_lot_number: str | None = None

    # Part
    part_id: str | None = None
    part_name: str | None = None
    part_revision: str | None = None

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
# Slot events (multi-UUT)
# ---------------------------------------------------------------------------


class SlotStarted(EventBase):
    """Emitted when a UUT slot begins execution."""

    event_type: Literal["slot.started"] = "slot.started"
    slot_id: str
    uut_serial_number: str


class SlotCompleted(EventBase):
    """Emitted when a UUT slot finishes execution."""

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


class UutScanned(EventBase):
    event_type: Literal["fixture.uut_scanned"] = "fixture.uut_scanned"
    uut_serial_number: str
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
    description: str | None = None

    # Vector context — which sweep condition this execution is.
    # vector_index 0 (the default) is the natural value for non-swept steps;
    # for sweep variants it identifies the specific condition. ``inputs``
    # carries the commanded sweep parameters for this vector — what
    # subscribers need to disambiguate "test_efficiency starting" from
    # "test_efficiency starting at vin=2.0V".
    vector_index: int = 0
    # 0-based retry of this execution. 0 for first run, N for the Nth retry.
    # Meaningful for the Mode-1 fused step-execution≡vector boundary (parametrize
    # item rerun, or class-container re-execution).
    retry: int = 0
    inputs: dict[str, Any] = Field(default_factory=dict)
    # Optional engineering unit per input name (``{"vin": "V"}``) — rides into
    # the lane's ``unit`` field → the EAV ``unit`` column.
    input_units: dict[str, str] = Field(default_factory=dict)

    # Code identity
    node_id: str | None = None
    file: str | None = None
    module: str | None = None
    class_name: str | None = None
    function: str | None = None

    instrument_records: list[dict[str, Any]] = Field(default_factory=list)


class MeasurementRecorded(EventBase):
    """A single measurement. Normalized: carries only measurement-specific fields.

    Run metadata (station, UUT, operator, etc.) lives in ``RunStarted``.
    Instrument arrays live in ``InstrumentConnected`` events.
    Subscribers denormalize at write time.
    """

    event_type: Literal["test.measurement"] = "test.measurement"

    # Step/vector context
    step_name: str
    step_index: int
    step_path: str = ""
    vector_index: int = 0
    step_retry: int = 0  # outer item-attempt axis (de-fuse identity)
    retry: int = 0  # inner vector retry — 0 for first execution, N for Nth

    # Measurement fields
    measurement_name: str
    measurement_timestamp: datetime | None = None
    value: float | None = None
    unit: str | None = None
    outcome: str | None = None
    limit_low: float | None = None
    limit_high: float | None = None
    limit_nominal: float | None = None
    limit_comparator: str | None = None
    characteristic_id: str | None = None
    spec_ref: str | None = None

    # Signal path
    uut_pin: str | None = None
    fixture_connection: str | None = None
    instrument_name: str | None = None
    instrument_resource: str | None = None
    instrument_channel: str | None = None

    # Carried by in-flight events (live daemon path). At rest, parquet
    # reconstruction reads conditions from the enclosing vector record.
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)


class Observation(EventBase):
    """Emitted by ``Context.observe(key, value)``.

    Carries the observation that landed in the vector's outputs lane
    (role ``output``, name = the observation key). Value is the scalar
    inline when scalar, or the claim URI string (``channel://…`` or
    ``file://…``) when the value was routed to a store.

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
    unit: str | None = None
    uut_pin: str | None = None


class StepEnded(EventBase):
    event_type: Literal["test.step_ended"] = "test.step_ended"
    step_name: str
    step_index: int
    step_path: str = ""
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
    # 0-based retry of this execution (Mode-1 fused boundary). Companion to
    # ``StepStarted.retry``.
    retry: int = 0
    vector_outcome: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    # Optional engineering unit / pin per input / output name → the lane fields.
    input_units: dict[str, str] = Field(default_factory=dict)
    output_units: dict[str, str] = Field(default_factory=dict)
    output_pins: dict[str, str] = Field(default_factory=dict)

    # Code identity
    node_id: str | None = None
    file: str | None = None
    module: str | None = None
    class_name: str | None = None
    function: str | None = None


class VectorStarted(EventBase):
    """An in-body loop vector is entered (Mode 2: the ``vectors`` fixture or a
    ``run_vector`` loop). One per iteration, so every vector — including ones
    that record no measurement — announces itself, closing the data-less-vector
    gap and the offline/streaming drift.

    Mode 1 (parametrize / single) and class containers reuse ``StepStarted`` as
    the fused step-execution≡vector boundary; this event is the in-body analog,
    nested inside the enclosing leaf step. The measurement's full condition is
    the merge of this vector's ``inputs`` with the enclosing steps' inputs along
    the step hierarchy.
    """

    event_type: Literal["test.vector_started"] = "test.vector_started"
    step_name: str
    step_index: int
    step_path: str = ""
    vector_index: int = 0
    retry: int = 0
    inputs: dict[str, Any] = Field(default_factory=dict)
    input_units: dict[str, str] = Field(default_factory=dict)
    node_id: str | None = None


class VectorEnded(EventBase):
    """Completion of an in-body loop vector (Mode 2). Carries the vector's
    verdict and its observations, mirroring ``StepEnded`` at vector grain.
    """

    event_type: Literal["test.vector_ended"] = "test.vector_ended"
    step_name: str
    step_index: int
    step_path: str = ""
    vector_index: int = 0
    retry: int = 0
    outcome: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    input_units: dict[str, str] = Field(default_factory=dict)
    output_units: dict[str, str] = Field(default_factory=dict)
    output_pins: dict[str, str] = Field(default_factory=dict)
    node_id: str | None = None


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
    unit: str | None = None
    # Instrument source fields — populated for observer.read; null otherwise.
    instrument_role: str | None = None
    method: str | None = None
    resource: str | None = None


class ChannelEnded(EventBase):
    """A channel was sealed for this session.

    Position 2 lifecycle event. Fires when a session ends (all of its
    channels close) or when retention pruning removes a channel.
    Consumers tracking "still being written to" vs "no more data
    coming" key off this event.

    Emission wiring lives downstream — for v0.2.0 initial cut the
    class exists; SessionEnded-tied emission lands in a follow-up.
    """

    event_type: Literal["channel.ended"] = "channel.ended"
    channel_id: str
    reason: str  # e.g., "session_ended" | "retention_prune"


class ChannelCheckpoint(EventBase):
    """Low-rate liveness + progress marker from an active channel producer.

    A channel's samples ride the off-spine fan-out, so a long active channel
    emits nothing durable between ``ChannelStarted`` and ``ChannelEnded``. The
    producer's write path emits one of these when more than the configured
    cadence (``StreamTuning.checkpoint_cadence``, default ``lease/3``) has
    elapsed since its last spine event, carrying the sample offset reached so far.

    It renews the session lease like any spine event (so the reaper tells a live
    channel apart from a crashed one) and records resumable progress. Bounded to
    one per cadence regardless of sample rate — never per-sample.
    """

    event_type: Literal["channel.checkpoint"] = "channel.checkpoint"
    uri: str
    sample_offset: int = 0


class InstrumentSet(EventBase):
    """Emitted when a driver set method is called via proxy."""

    event_type: Literal["instrument.set"] = "instrument.set"
    instrument_role: str
    channel_id: str
    attribute: str
    value: Any = None
    unit: str | None = None
    resource: str = ""


class InstrumentConfigure(EventBase):
    """Emitted when a driver configure method is called via proxy."""

    event_type: Literal["instrument.configure"] = "instrument.configure"
    instrument_role: str
    method: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    resource: str = ""


# ---------------------------------------------------------------------------
# Reservation events
# ---------------------------------------------------------------------------


class InstrumentReserved(EventBase):
    """Emitted by the pool when an exclusive instrument reservation is acquired.

    ``waited_ms`` is the monotonic duration of the acquire call — 0.0 when
    uncontended, positive when another holder was blocking.  Hold duration is
    derivable as ``InstrumentReleased.occurred_at − InstrumentReserved.occurred_at``.
    """

    event_type: Literal["instrument.reserved"] = "instrument.reserved"
    role: str
    instrument_id: str
    resource: str
    waited_ms: float


class InstrumentReleased(EventBase):
    """Emitted by the pool when an instrument reservation is released."""

    event_type: Literal["instrument.released"] = "instrument.released"
    role: str
    instrument_id: str
    resource: str


# ---------------------------------------------------------------------------
# File events (Phase 2+)
# ---------------------------------------------------------------------------


class FileStarted(EventBase):
    """Emitted when a FileStore streaming sink opens.

    Once per ``file_id``. Announces an open stream for discovery
    ("what streams are open / done"); the final ``file://`` URI is
    announced via :class:`FileEnded` at close (it can't be known until
    the sink resolves a collision-free name).

    **File events are lifecycle-only** (the FileStore parallel of
    Position 2 for channels). Per-chunk events would flood the
    EventStore at high write rates (kHz captures, 30 fps video, etc.)
    for no real subscriber gain. Live consumers receive each chunk
    push-style via ephemeral frames (the files daemon, not the event
    log); they use EventStore only for discovery.
    """

    event_type: Literal["file.started"] = "file.started"
    file_id: UUID
    name: str = ""
    format: str = ""


class FileEnded(EventBase):
    """Emitted when a FileStore streaming sink closes.

    Once per ``file_id``. ``uri`` is the final ``file://`` claim that
    callers can stash into the vector's outputs lane or hand to the
    artifact viewer. ``size_bytes`` is the total appended-byte count
    at close.
    """

    event_type: Literal["file.ended"] = "file.ended"
    file_id: UUID
    uri: str | None = None
    size_bytes: int | None = None


class FileCheckpoint(EventBase):
    """Low-rate liveness + progress marker from an active file sink.

    A file stream's frames ride the off-spine fan-out, so a long active stream
    emits nothing durable between ``FileStarted`` and ``FileEnded``. The sink's
    write path emits one of these when more than the configured cadence
    (``StreamTuning.checkpoint_cadence``, default ``lease/3``) has elapsed since
    its last spine event, carrying the byte offset reached so far.

    It renews the session lease like any spine event (so the reaper tells a live
    stream apart from a crashed one) and records resumable progress. Bounded to
    one per cadence regardless of write rate — never per-write.
    """

    event_type: Literal["file.checkpoint"] = "file.checkpoint"
    uri: str
    byte_offset: int = 0


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
RUN_EVENTS = {RunStarted, RunEnded, RunMaterialized}
SLOT_EVENTS = {SlotStarted, SlotCompleted, SyncArrived, SyncRelease}
FIXTURE_EVENTS = {
    InstrumentConnected,
    IdentityVerified,
    CalibrationWarning,
    UutScanned,
    InstrumentDisconnected,
}
TEST_EVENTS = {
    StepStarted,
    MeasurementRecorded,
    Observation,
    StepEnded,
    VectorStarted,
    VectorEnded,
    StepsDiscovered,
}
ROUTE_EVENTS = {RouteClosed, RouteOpened}
INSTRUMENT_EVENTS = {InstrumentSet, InstrumentConfigure, InstrumentReserved, InstrumentReleased}
CHANNEL_EVENTS = {ChannelStarted, ChannelEnded, ChannelCheckpoint}
DIAGNOSTIC_EVENTS = {DiagnosticWarning, DiagnosticError}
FILE_EVENTS = {FileStarted, FileEnded, FileCheckpoint}
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
    | FILE_EVENTS
    | DIALOG_EVENTS
)

# Discriminated union type for deserialization
Event = Annotated[
    SessionStarted
    | SessionEnded
    | RunStarted
    | RunEnded
    | RunMaterialized
    | SlotStarted
    | SlotCompleted
    | SyncArrived
    | SyncRelease
    | InstrumentConnected
    | IdentityVerified
    | CalibrationWarning
    | UutScanned
    | InstrumentDisconnected
    | StepStarted
    | MeasurementRecorded
    | Observation
    | StepEnded
    | VectorStarted
    | VectorEnded
    | StepsDiscovered
    | RouteClosed
    | RouteOpened
    | InstrumentSet
    | InstrumentConfigure
    | InstrumentReserved
    | InstrumentReleased
    | ChannelStarted
    | ChannelEnded
    | ChannelCheckpoint
    | DiagnosticWarning
    | DiagnosticError
    | FileStarted
    | FileEnded
    | FileCheckpoint
    | DialogOpened
    | DialogResponded,
    Field(discriminator="event_type"),
]
