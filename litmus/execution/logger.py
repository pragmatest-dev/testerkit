"""Test run logging for accumulating measurements."""

from __future__ import annotations

import hashlib
import os
import socket
from contextvars import Token
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from litmus.data.backends._row_helpers import build_input_columns, build_output_columns
from litmus.data.events import (
    MeasurementRecorded,
    RecordEvent,
    RunEnded,
    StepEnded,
    StepStarted,
)
from litmus.data.models import (
    DUT,
    Measurement,
    TestRun,
    TestStep,
    TestVector,
    _utcnow,
    escalate_outcome,
)
from litmus.execution._state import current_step_var, current_vector_var

if TYPE_CHECKING:
    from litmus.data.event_log import EventLog
    from litmus.environment import EnvironmentSnapshot
    from litmus.models.config import Limit
    from litmus.models.instrument import InstrumentRecord


# Canonical list of instrument identity array keys.
# Used by build_instrument_arrays() and _build_empty_row() for schema consistency.
INSTRUMENT_ARRAY_KEYS = (
    "instr_name",
    "instr_id",
    "instr_driver",
    "instr_resource",
    "instr_protocol",
    "instr_manufacturer",
    "instr_model",
    "instr_serial",
    "instr_firmware",
    "instr_cal_due",
    "instr_cal_last",
    "instr_cal_certificate",
    "instr_cal_lab",
    "instr_mocked",
)


def instrument_info_fields(rec: InstrumentRecord) -> dict[str, Any]:
    """Return ``{manufacturer, model, serial, firmware}`` from a record.

    Shared by :meth:`TestRunLogger.build_instrument_arrays` and the
    plugin's ``InstrumentConnected`` event emitter — keeps the ``if
    rec.info else None`` dance in one place.
    """
    info = rec.info
    return {
        "manufacturer": info.manufacturer if info else None,
        "model": info.model if info else None,
        "serial": info.serial if info else None,
        "firmware": info.firmware if info else None,
    }


def _stringify_comparator(cmp_raw: Any) -> str | None:
    """Render a comparator value for the ``Measurement.comparator`` string field.

    Accepts a :class:`Comparator` enum, a raw string, or ``None``. Enums
    return their ``.value`` attribute; other non-``None`` values are
    coerced via ``str(...)``. Shared by :meth:`TestRunLogger.measure` and
    the ``@measure`` decorator so both paths produce the same row shape.
    """
    if cmp_raw is None:
        return None
    return str(cmp_raw.value) if hasattr(cmp_raw, "value") else str(cmp_raw)


def _normalize_comparator(val: Any) -> Any:
    """Coerce a comparator value (str / enum / None) to a :class:`Comparator`.

    Shared by inline-kwarg resolution (:func:`_resolve_measurement_limit`)
    and sidecar parsing (``plugin._parse_limits_block``) so both paths
    produce identical enum values. ``None`` maps to the default ``GELE``.
    """
    # Inline import: breaks runtime cycle with plugin (which imports this module).
    from litmus.config.enums import Comparator

    if val is None:
        return Comparator.GELE
    if isinstance(val, Comparator):
        return val
    return Comparator(val)


def _limit_from_dict(spec: Any, *, units_override: str | None = None) -> Limit:
    """Build a :class:`Limit` from a mapping of low/high/nominal/units/comparator.

    Shared by sidecar parsing (``plugin._parse_limits_block``) and any
    future dict-shaped limit source. The ``units_override`` lets callers
    prefer a caller-supplied unit when the dict itself has no ``units``.
    """
    from litmus.config.test_config import Limit as LimitModel

    return LimitModel(
        low=spec.get("low"),
        high=spec.get("high"),
        nominal=spec.get("nominal"),
        units=spec.get("units", units_override or ""),
        spec_ref=spec.get("spec_ref"),
        comparator=_normalize_comparator(spec.get("comparator")),
    )


def _resolve_measurement_limit(
    name: str,
    inline_any: bool,
    low: float | None,
    high: float | None,
    nominal: float | None,
    comparator: Any,
    limit: Limit | None,
    units: str | None,
) -> Limit | None:
    """Return a Limit or None per :meth:`TestRunLogger.measure`'s resolution chain.

    Chain order: inline low/high/nominal/comparator → explicit ``limit=``
    → active sidecar limits → active spec context → unchecked (None).

    Graceful degradation: both ``get_active_limits`` (sidecar) and
    ``get_active_spec_context`` (product YAML) may be empty/None in
    pure-pytest runs; in that case returns ``None`` and the measurement
    is recorded unchecked. The spec read is a one-way ContextVar snapshot
    at write time — not a runtime call on the spec module — so the
    ``test → spec → logger`` data-flow rule from the plugin plan still
    holds. Lives at module scope so it can be tested in isolation from
    ``TestRunLogger`` instance state.
    """
    from litmus.execution.plugin import get_active_limits, get_active_spec_context

    if inline_any:
        return _limit_from_dict(
            {
                "low": low,
                "high": high,
                "nominal": nominal,
                "units": units or "",
                "comparator": comparator,
            }
        )
    if limit is not None:
        return limit

    sidecar_limit = get_active_limits().get(name)
    if sidecar_limit is not None:
        return sidecar_limit

    spec = get_active_spec_context()
    if spec is not None:
        try:
            return spec.get_limit(name)
        except KeyError:
            return None

    return None


def instrument_cal_fields(rec: InstrumentRecord) -> dict[str, Any]:
    """Return ``{cal_due, cal_last, cal_certificate, cal_lab}`` from a record.

    Dates are ISO-formatted. None-safe over missing ``calibration``.
    """
    cal = rec.calibration
    return {
        "cal_due": cal.due_date.isoformat() if cal and cal.due_date else None,
        "cal_last": cal.last_cal.isoformat() if cal and cal.last_cal else None,
        "cal_certificate": cal.certificate if cal else None,
        "cal_lab": cal.lab if cal else None,
    }


class DuplicateMeasurementError(AssertionError):
    """Raised when a measurement name is recorded twice in one step.

    Subclasses :class:`AssertionError` so pytest surfaces it as a test
    failure (and the typical streaming-loop users still see a helpful
    message). The dedup rule is enforced in
    :meth:`TestRunLogger.measure`; bypass explicitly via
    ``allow_repeat=True``.
    """


def _parse_uuid(value: str) -> UUID:
    """Parse a string as UUID, falling back to deterministic md5 hash."""
    try:
        return UUID(value)
    except ValueError:
        h = hashlib.md5(value.encode()).hexdigest()
        return UUID(h)


def _get_run_id() -> UUID:
    """Get run ID from environment or generate new one."""
    env_id = os.environ.get("LITMUS_RUN_ID")
    if env_id:
        return _parse_uuid(env_id)
    return uuid4()


class RunContext:
    """Run-level context for adding custom metadata during test execution.

    Unlike ``Context`` (which is per-vector and scoped to a single test step),
    ``RunContext`` persists for the entire session and stores metadata that
    applies to the whole run (operator badge, fixture serial, etc.).

    Allows test architects to add custom fields that become columns in Parquet:

        @litmus_test
        def test_output_voltage(vector, psu, dmm, run_context):
            # Add custom fields - become columns in Parquet
            run_context.set("operator_badge", badge_id)
            run_context.set("operator_shift", "day")
            run_context.set("chamber_humidity", 45.2)
            run_context.set("fixture_serial", "FIX-001")

            # Normal test...
            psu.set_voltage(vector["vin"])
            return dmm.measure_dc_voltage()

    Custom fields are prefixed with their entity or use a `custom_` prefix:
    - `operator_badge`, `operator_shift` → grouped with operator
    - `custom_chamber_humidity` → explicit custom namespace
    """

    def __init__(self, test_run: TestRun):
        """Initialize context with reference to test run.

        Args:
            test_run: The TestRun to store custom metadata on.
        """
        self._test_run = test_run

    def set(self, key: str, value: Any) -> None:
        """Set a custom metadata field.

        Args:
            key: Field name. Will be stored as-is if it contains a prefix
                 (e.g., "operator_badge") or prefixed with "custom_" otherwise.
            value: Field value (must be JSON-serializable for Parquet).
        """
        self._test_run.custom_metadata[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a custom metadata field.

        Args:
            key: Field name.
            default: Value to return if key not found.

        Returns:
            The stored value or default.
        """
        return self._test_run.custom_metadata.get(key, default)

    def update(self, **kwargs: Any) -> None:
        """Set multiple custom metadata fields at once.

        Args:
            **kwargs: Key-value pairs to set.
        """
        self._test_run.custom_metadata.update(kwargs)

    @property
    def metadata(self) -> dict[str, Any]:
        """Access the underlying metadata dict (read-only view)."""
        return dict(self._test_run.custom_metadata)


class TestRunLogger:
    """Accumulates measurements during test run, produces TestRun.

    Optionally streams typed events to an event log (JSONL) for live
    observability and crash recovery. When an ``EventLog`` is wired,
    events are emitted as they happen and dispatched to subscribers
    (e.g. ``ParquetSubscriber``).
    """

    __test__ = False  # Prevent pytest collection

    def __init__(
        self,
        dut_serial: str,
        station_id: str,
        test_sequence_id: str,
        station_name: str | None = None,
        station_type: str | None = None,
        station_location: str | None = None,
        station_hostname: str | None = None,
        operator_id: str | None = None,
        operator_name: str | None = None,
        test_phase: str = "production",
        profile: str | None = None,
        session_id: UUID | None = None,
        run_id: UUID | str | None = None,
        # Product traceability
        product_id: str | None = None,
        product_name: str | None = None,
        product_revision: str | None = None,
        # Fixture traceability
        fixture_id: str | None = None,
        # DUT details
        dut_part_number: str | None = None,
        dut_revision: str | None = None,
        dut_lot_number: str | None = None,
        # Code traceability
        git_commit: str | None = None,
        git_branch: str | None = None,
        git_remote: str | None = None,
        project_name: str | None = None,
        # Project directory — used for auto-detection (git, etc.)
        project_dir: str | Path | None = None,
        # Results storage
        results_dir: str | Path | None = None,
        # Instrument records for identity + calibration traceability
        instruments: dict[str, InstrumentRecord] | None = None,
        # Environment snapshot for software traceability
        environment: EnvironmentSnapshot | None = None,
    ):
        # Use provided run_id, environment variable, or generate new
        if isinstance(run_id, str):
            run_id = _parse_uuid(run_id)
        elif run_id is None:
            run_id = _get_run_id()

        _session_id = session_id if session_id is not None else uuid4()

        # Auto-detect git info and project name when not provided
        if git_commit is None or git_branch is None or git_remote is None or project_name is None:
            from litmus.execution._git import get_git_info, get_project_name

            if git_commit is None or git_branch is None or git_remote is None:
                info = get_git_info(project_dir)
                if git_commit is None:
                    git_commit = info.commit
                if git_branch is None:
                    git_branch = info.branch
                if git_remote is None:
                    git_remote = info.remote

            if project_name is None:
                project_name = get_project_name(project_dir)

        self.test_run = TestRun(
            id=run_id,
            session_id=_session_id,
            dut=DUT(
                serial=dut_serial,
                part_number=dut_part_number,
                revision=dut_revision,
                lot_number=dut_lot_number,
            ),
            station_id=station_id,
            station_name=station_name,
            station_type=station_type,
            station_location=station_location,
            station_hostname=station_hostname or socket.gethostname(),
            operator_id=operator_id,
            operator_name=operator_name,
            test_sequence_id=test_sequence_id,
            test_phase=test_phase,
            profile=profile,
            product_id=product_id,
            product_name=product_name,
            product_revision=product_revision,
            fixture_id=fixture_id,
            git_commit=git_commit,
            git_branch=git_branch,
            git_remote=git_remote,
            project_name=project_name,
        )
        # Serialize environment eagerly so every event has it
        if environment is not None:
            self.test_run.environment_json = environment.model_dump_json()
        self._current_step_index: int = -1
        self._step_stack: list[str] = []  # Path components for nested steps
        # Per-step set of measurement names that have been written. Reset in
        # start_step() so each step starts with a clean slate; used by
        # ``measure()`` to raise DuplicateMeasurementError on accidental
        # double-logs within a step.
        self._step_seen_names: set[str] = set()
        self._step_seen_repeatable: set[str] = set()
        self._step_token: Token[TestStep | None] | None = None
        # _vector_token tracks current vector context for this step.
        # Both start_step() and log_measurement() may set it; reset in end_step().
        self._vector_token: Token[TestVector | None] | None = None
        # Clear contextvars — each logger owns its execution context
        current_step_var.set(None)
        current_vector_var.set(None)
        self._run_context = RunContext(self.test_run)
        self._instruments: dict[str, InstrumentRecord] = instruments or {}
        self._step_instrument_arrays: dict[str, list] | None = None

        # Event log for typed event streaming
        self._event_log: EventLog | None = None
        self._session_id: UUID = self.test_run.session_id
        self._results_dir = Path(results_dir) if results_dir is not None else None

    @property
    def event_log(self) -> EventLog | None:
        """Get the event log, if enabled."""
        return self._event_log

    @event_log.setter
    def event_log(self, log: EventLog | None) -> None:
        self._event_log = log

    @property
    def run_context(self) -> RunContext:
        """Get the run context for adding custom metadata."""
        return self._run_context

    @property
    def event_log_path(self) -> Path | None:
        """Get the event log file path, if enabled."""
        if self._event_log is not None:
            return self._event_log.path
        return None

    def build_instrument_arrays(self, roles: list[str] | None = None) -> dict[str, list]:
        """Build parallel arrays for instrument identity and calibration.

        Args:
            roles: If provided, only include instruments with these role names.
                   If None, include all instruments.

        Returns dict with keys:
        - instr_name: List of instrument names/roles (e.g., ["dmm", "psu"])
        - instr_id: List of instrument IDs (e.g., ["keithley_dmm_001", "keysight_psu_001"])
        - instr_driver: List of driver class paths (e.g., ["drivers.Keithley2000"])
        - instr_resource: List of resources (e.g., ["GPIB::16::INSTR", "GPIB::17::INSTR"])
        - instr_protocol: List of protocols (e.g., ["visa", "visa"])
        - instr_manufacturer: List of manufacturers
        - instr_model: List of models
        - instr_serial: List of serial numbers
        - instr_firmware: List of firmware versions
        - instr_cal_due: List of calibration due dates (ISO format)
        - instr_cal_last: List of last calibration dates (ISO format)
        - instr_cal_certificate: List of certificate numbers
        - instr_cal_lab: List of calibration labs

        All arrays are the same length and in the same order.
        """
        arrays: dict[str, list] = {key: [] for key in INSTRUMENT_ARRAY_KEYS}
        for role, record in self._instruments.items():
            if roles is not None and role not in roles:
                continue
            info = instrument_info_fields(record)
            cal = instrument_cal_fields(record)
            arrays["instr_name"].append(role)
            arrays["instr_id"].append(record.instrument_id)
            arrays["instr_driver"].append(record.driver)
            arrays["instr_resource"].append(record.resource)
            arrays["instr_protocol"].append(record.protocol)
            arrays["instr_manufacturer"].append(info["manufacturer"])
            arrays["instr_model"].append(info["model"])
            arrays["instr_serial"].append(info["serial"])
            arrays["instr_firmware"].append(info["firmware"])
            arrays["instr_cal_due"].append(cal["cal_due"])
            arrays["instr_cal_last"].append(cal["cal_last"])
            arrays["instr_cal_certificate"].append(cal["cal_certificate"])
            arrays["instr_cal_lab"].append(cal["cal_lab"])
            arrays["instr_mocked"].append(record.mocked)
        return arrays

    def set_step_instruments(self, roles: list[str]) -> dict[str, list]:
        """Set the instrument arrays for the current test step.

        Filters instruments to only those used by the step (detected from
        fixture parameters) and caches the result.

        Args:
            roles: List of instrument role names used by this step.

        Returns:
            The filtered instrument arrays dict.
        """
        arrays = self.build_instrument_arrays(roles=roles)
        self._step_instrument_arrays = arrays
        return arrays

    def start_step(
        self,
        name: str,
        description: str | None = None,
        *,
        node_id: str | None = None,
        file: str | None = None,
        module: str | None = None,
        class_name: str | None = None,
        function: str | None = None,
        markers: str | None = None,
    ):
        """Begin a new test step. Supports nesting via step_path."""
        # Auto-close any prior step that wasn't explicitly ended
        if current_step_var.get() is not None:
            self.end_step()
        # Clear per-step instrument arrays so they don't leak between steps
        self._step_instrument_arrays = None
        # Reset per-step dedup sets — each step starts with a clean slate.
        self._step_seen_names = set()
        self._step_seen_repeatable = set()

        # Build hierarchy path
        self._step_stack.append(name)
        step_path = "/".join(self._step_stack)
        parent_path = "/".join(self._step_stack[:-1])

        step = TestStep(
            name=name,
            description=description,
            step_path=step_path,
            parent_path=parent_path,
            node_id=node_id,
            file=file,
            module=module,
            class_name=class_name,
            function=function,
            markers=markers,
        )
        self._current_step_index += 1
        self.test_run.steps.append(step)
        # Create a default vector for this step (for simple logging without harness)
        vector = TestVector()
        step.vectors.append(vector)
        # Token-based set for proper reset in end_step()
        self._step_token = current_step_var.set(step)
        self._vector_token = current_vector_var.set(vector)

        if self._event_log is not None:
            self._event_log.emit(
                StepStarted(
                    session_id=self._session_id,
                    run_id=self.test_run.id,
                    step_name=name,
                    step_index=self._current_step_index,
                    step_path=step_path,
                    parent_path=parent_path,
                    description=description,
                    node_id=node_id,
                    file=file,
                    module=module,
                    class_name=class_name,
                    function=function,
                )
            )

    def register_step(self, step: TestStep) -> int:
        """Register an externally-created step. Returns step index.

        Used by TestHarness to register steps it creates, so that
        log_measurement() can find the correct step via contextvars.
        """
        self.test_run.steps.append(step)
        self._current_step_index += 1
        return self._current_step_index

    @property
    def session_id(self) -> UUID:
        """Session ID for event correlation."""
        return self._session_id

    @property
    def step_instrument_arrays(self) -> dict[str, list] | None:
        """Per-step instrument arrays, if set."""
        return self._step_instrument_arrays

    def emit_step_started(self, step: TestStep, step_index: int) -> None:
        """Emit a StepStarted event if an event log is wired."""
        if self._event_log is not None:
            self._event_log.emit(
                StepStarted(
                    session_id=self._session_id,
                    run_id=self.test_run.id,
                    step_name=step.name,
                    step_index=step_index,
                    step_path=step.step_path,
                    description=step.description,
                    node_id=step.node_id,
                    file=step.file,
                    module=step.module,
                    class_name=step.class_name,
                    function=step.function,
                )
            )

    def emit_step_ended(self, step: TestStep, step_index: int) -> None:
        """Emit a StepEnded event if an event log is wired."""
        if self._event_log is not None:
            self._event_log.emit(
                StepEnded(
                    session_id=self._session_id,
                    run_id=self.test_run.id,
                    step_name=step.name,
                    step_index=step_index,
                    step_path=step.step_path,
                    outcome=step.outcome.value,
                    node_id=step.node_id,
                    file=step.file,
                    module=step.module,
                    class_name=step.class_name,
                    function=step.function,
                )
            )

    def log_measurement(self, measurement: Measurement):
        """Add measurement to current step.

        Resolves step/vector from contextvars. If no step exists, one is
        auto-created from the measurement name.
        """
        # Resolve step: contextvar only → auto-create
        step = current_step_var.get()
        if step is None:
            self.start_step(measurement.name)
            step = current_step_var.get()
        assert step is not None

        # Resolve vector: contextvar only → auto-create
        vector = current_vector_var.get()
        if vector is None:
            vector = TestVector()
            step.vectors.append(vector)
            self._vector_token = current_vector_var.set(vector)

        # Stamp step_path from the resolved step so downstream consumers
        # (traceability audit, parquet projection) don't see empty strings.
        if not measurement.step_path:
            measurement.step_path = step.step_path

        # Guard against double-append (harness.measure() appends before calling us)
        if measurement not in vector.measurements:
            vector.measurements.append(measurement)

        # Cascade outcome: ERROR > FAIL > PASS
        if measurement.outcome is not None:
            vector.outcome = escalate_outcome(vector.outcome, measurement.outcome)
            step.outcome = escalate_outcome(step.outcome, measurement.outcome)
            self.test_run.outcome = escalate_outcome(self.test_run.outcome, measurement.outcome)

        # Emit event if event log is wired
        if self._event_log is not None:
            event = MeasurementRecorded(
                session_id=self._session_id,
                run_id=self.test_run.id,
                # Step/vector context
                step_name=step.name,
                step_index=self._current_step_index,
                step_path=step.step_path,
                vector_index=vector.index,
                attempt=vector.attempt,
                # Measurement fields
                measurement_name=measurement.name,
                measurement_timestamp=measurement.timestamp,
                value=measurement.value,
                units=measurement.units,
                outcome=measurement.outcome.value if measurement.outcome else None,
                low_limit=measurement.low_limit,
                high_limit=measurement.high_limit,
                nominal=measurement.nominal,
                comparator=measurement.comparator,
                spec_id=measurement.spec_id,
                spec_ref=measurement.spec_ref,
                meas_dut_pin=measurement.dut_pin,
                meas_fixture_point=measurement.fixture_point,
                meas_instrument=measurement.instrument_name,
                meas_instrument_resource=measurement.instrument_resource,
                meas_instrument_channel=measurement.instrument_channel,
                # Dynamic columns (vector-specific)
                inputs=build_input_columns(vector),
                outputs=build_output_columns(
                    vector,
                    ref_saver=self._event_log.save_ref,
                ),
                custom=dict(self.test_run.custom_metadata),
            )
            self._event_log.emit(event)

    def end_step(self):
        """Finalize current step."""
        step = current_step_var.get()
        if step is not None:
            step.ended_at = _utcnow()
        vector = current_vector_var.get()
        if vector is not None:
            vector.ended_at = _utcnow()

        if self._event_log is not None and step is not None:
            self._event_log.emit(
                StepEnded(
                    session_id=self._session_id,
                    run_id=self.test_run.id,
                    step_name=step.name,
                    step_index=self._current_step_index,
                    step_path=step.step_path,
                    outcome=step.outcome.value,
                    node_id=step.node_id,
                    file=step.file,
                    module=step.module,
                    class_name=step.class_name,
                    function=step.function,
                )
            )

        # Pop step from hierarchy stack
        if self._step_stack:
            self._step_stack.pop()

        # Reset via tokens for proper contextvar hygiene
        if self._step_token is not None:
            current_step_var.reset(self._step_token)
            self._step_token = None
        if self._vector_token is not None:
            current_vector_var.reset(self._vector_token)
            self._vector_token = None

    def measure(
        self,
        name: str,
        value: float | int | None,
        *,
        # Inline limit (terse form)
        low: float | None = None,
        high: float | None = None,
        nominal: float | None = None,
        comparator: Any = None,
        # Explicit Limit (for sidecar/spec-resolved or prebuilt limits)
        limit: Limit | None = None,
        # Units (overrides limit.units if both present)
        units: str | None = None,
        # Behavior
        allow_repeat: bool = False,
        # Traceability overrides (ambient ContextVars fill the rest)
        dut_pin: str | None = None,
        instrument_name: str | None = None,
        instrument_resource: str | None = None,
        instrument_channel: str | None = None,
        fixture_point: str | None = None,
        spec_ref: str | None = None,
    ) -> Measurement:
        """Log a measurement with optional limit checking.

        **Limit resolution chain** (first match wins):

        1. Inline ``low=/high=/nominal=/comparator=`` kwargs, if any are set.
        2. Explicit ``limit=Limit(...)`` kwarg.
        3. :func:`litmus.execution.plugin.get_active_limits` — the sidecar
           ``limits:`` block pushed by the pytest_native plugin.
        4. :func:`litmus.execution.plugin.get_active_spec_context` —
           product YAML characteristic with the same name.
        5. Record unchecked (no limit, no outcome set).

        **Duplicate-name dedup:** each step tracks names that have been
        written. A second write with the same name raises
        :class:`DuplicateMeasurementError` unless both the first and
        second call pass ``allow_repeat=True`` (e.g. inner streaming
        loop). This catches the common bug where a caller invokes
        ``logger.measure`` *and* ``spec.check`` on the same name.

        Args:
            name: Measurement name (e.g. ``"output_voltage"``).
            value: Measured value; ``None`` is recorded with no outcome.
            low, high, nominal, comparator: Inline limit fields —
                convenient terse form for ad-hoc checks.
            limit: Prebuilt Limit object. Mutually exclusive with the
                inline form — passing both raises ``ValueError``.
            units: Unit string — overrides ``limit.units`` when present.
            allow_repeat: Opt-in for naive same-name loops; both the
                first and subsequent calls must set it.
            dut_pin, instrument_name, instrument_resource,
            instrument_channel, fixture_point: Traceability metadata
                written straight onto the Measurement. No ambient
                fallback today — callers (typically ``spec.check``)
                supply these explicitly.
            spec_ref: Override for the spec reference. When omitted,
                falls back to the resolved limit's ``spec_ref`` (which
                ``derive_limit`` populates with characteristic id plus
                any condition suffix).

        Returns:
            The :class:`Measurement` created and logged.

        Raises:
            DuplicateMeasurementError: When ``name`` was already recorded
                in the current step without ``allow_repeat=True``.
        """
        inline_any = any(x is not None for x in (low, high, nominal, comparator))
        if inline_any and limit is not None:
            raise ValueError(
                "measure(): pass either inline low/high/nominal/comparator or limit=, not both"
            )

        resolved_limit = _resolve_measurement_limit(
            name, inline_any, low, high, nominal, comparator, limit, units
        )

        # Ensure a step exists *before* the dedup check — otherwise the
        # check runs against stale state and ``start_step`` (auto-called
        # from ``log_measurement``) would then reset ``_step_seen_names``,
        # silently swallowing a real duplicate. Pytest always opens a step
        # around the test body; this guard is for non-pytest callers.
        if current_step_var.get() is None:
            self.start_step(name)

        # Dedup check against per-step seen_names
        self._guard_duplicate(name, allow_repeat)

        # Extract limit fields for the Measurement row
        low_limit: float | None = None
        high_limit: float | None = None
        nom: float | None = None
        cmp_str: str | None = None
        meas_units = units
        meas_spec_ref = spec_ref

        if resolved_limit is not None:
            low_limit = resolved_limit.low
            high_limit = resolved_limit.high
            nom = resolved_limit.nominal
            if meas_units is None:
                meas_units = resolved_limit.units
            if meas_spec_ref is None:
                meas_spec_ref = resolved_limit.spec_ref
            cmp_str = _stringify_comparator(getattr(resolved_limit, "comparator", None))

        measurement = Measurement(
            name=name,
            value=float(value) if value is not None else None,
            units=meas_units,
            low_limit=low_limit,
            high_limit=high_limit,
            nominal=nom,
            comparator=cmp_str,
            spec_ref=meas_spec_ref,
            dut_pin=dut_pin,
            instrument_name=instrument_name,
            instrument_resource=instrument_resource,
            instrument_channel=instrument_channel,
            fixture_point=fixture_point,
        )

        measurement.check_limit()
        self.log_measurement(measurement)
        return measurement

    def _guard_duplicate(self, name: str, allow_repeat: bool) -> None:
        """Raise :class:`DuplicateMeasurementError` on same-name double-write.

        Each step tracks names that have been written. A second write
        with the same name is an error unless both the first and second
        call opt in via ``allow_repeat=True``.

        Typical causes when this fires:

        - ``spec.check(name, ...)`` and ``logger.measure(name, ...)`` on
          the same name — ``spec.check`` calls ``measure`` internally,
          so the two are redundant.
        - An inner-loop streaming pattern that forgot ``allow_repeat=True``.
        - Two independent ``logger.measure`` calls accidentally sharing
          a name; rename one or split into separate steps.
        """
        if name in self._step_seen_names:
            first_was_repeatable = name in self._step_seen_repeatable
            if not (allow_repeat and first_was_repeatable):
                step = current_step_var.get()
                step_label = step.name if step else "<no-step>"
                raise DuplicateMeasurementError(
                    f"Measurement {name!r} already recorded in step {step_label!r}. "
                    "Each measurement name must be unique within a step; pass "
                    "allow_repeat=True on every call when streaming samples."
                )
        else:
            self._step_seen_names.add(name)
            if allow_repeat:
                self._step_seen_repeatable.add(name)

    def record(self, key: str, value: Any) -> None:
        """Emit a key/value record event to the event log.

        Args:
            key: Record key (e.g., "firmware_version", "calibration_date").
            value: Record value (must be JSON-serializable).
        """
        step = current_step_var.get()
        step_name = step.name if step else ""
        step_index = self._current_step_index if step else -1
        if self._event_log is not None:
            self._event_log.emit(
                RecordEvent(
                    session_id=self._session_id,
                    run_id=self.test_run.id,
                    step_name=step_name,
                    step_index=step_index,
                    key=key,
                    value=value,
                )
            )

    def finalize(self) -> TestRun:
        """Complete test run and return result.

        Emits RunEnded event. Does NOT close the event log — caller is
        responsible for emitting SessionEnded and closing the log.
        """
        # Close any unclosed step before finalizing
        if current_step_var.get() is not None:
            self.end_step()

        self.test_run.ended_at = _utcnow()

        if self._event_log is not None:
            self._event_log.emit(
                RunEnded(
                    session_id=self._session_id,
                    run_id=self.test_run.id,
                    outcome=self.test_run.outcome.value,
                )
            )

        return self.test_run
