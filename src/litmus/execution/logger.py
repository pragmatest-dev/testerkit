"""Test run logging for accumulating measurements."""

from __future__ import annotations

import hashlib
import os
import socket
from contextvars import Token
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from litmus.data._json_safe import coerce_dict
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
    Outcome,
    TestRun,
    TestStep,
    TestVector,
    _utcnow,
    escalate_outcome,
)
from litmus.execution._state import (
    get_active_characteristic,
    get_active_connection,
    get_active_instruments,
    get_active_limits,
    get_active_product_context,
    get_active_vector_params,
    get_current_step,
    get_current_vector,
    push_current_step,
    push_current_vector,
    reset_current_step,
    reset_current_vector,
)
from litmus.execution.sidecar import resolve_limit
from litmus.models.test_config import Limit

if TYPE_CHECKING:
    from litmus.data.event_log import EventLog
    from litmus.environment import EnvironmentSnapshot
    from litmus.models.instrument import InstrumentRecord


# Re-exported from the data layer — lives there to avoid importing the
# execution framework just for a tuple of column name strings.
from litmus.data.backends._row_helpers import (
    INSTRUMENT_ARRAY_KEYS as INSTRUMENT_ARRAY_KEYS,  # noqa: F401
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
    coerced via ``str(...)``. Used by :meth:`TestRunLogger.measure` to
    produce the canonical row shape.
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
    from litmus.models.enums import Comparator

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
    return Limit(
        low=spec.get("low"),
        high=spec.get("high"),
        nominal=spec.get("nominal"),
        units=spec.get("units", units_override or ""),
        spec_ref=spec.get("spec_ref"),
        comparator=_normalize_comparator(spec.get("comparator")),
    )


def _auto_traceability(name: str) -> dict[str, Any]:
    """Resolve traceability fields for a measurement ``name``.

    Resolution order:

    1. **Active :class:`FixtureConnection`** (from ``_active_connection_var``,
       pushed by ``ConnectionIterator`` while the test iterates
       ``ctx.connections``). When set, this is the authoritative source
       for ``dut_pin`` / ``net`` / ``fixture_connection`` /
       ``instrument_name`` / ``instrument_channel`` /
       ``instrument_terminal`` — the connection IS the row's pin.
    2. **Legacy name-match against the active ProductContext**: when no
       connection is active, fall back to ``spec.get_pin_info(name)``
       for rows whose measurement label happens to equal a characteristic
       id. This branch exists for the transition period and will be
       dropped once demos and tests have moved to the spec/connections markers.

    Returns a dict with any of ``dut_pin``, ``net``, ``fixture_connection``,
    ``instrument_name``, ``instrument_resource``, ``instrument_channel``,
    ``instrument_terminal``, ``characteristic_id``, ``spec_ref`` — callers use
    ``.get(...)`` so pure-pytest runs (no spec, no connections) fall through
    silently.
    """
    result: dict[str, Any] = {}

    conn = get_active_connection()
    if conn is not None:
        if conn.dut_pin is not None:
            result["dut_pin"] = conn.dut_pin
        if conn.net is not None:
            result["net"] = conn.net
        result["fixture_connection"] = conn.name
        result["instrument_name"] = conn.instrument
        if conn.instrument_channel is not None:
            result["instrument_channel"] = conn.instrument_channel
        if conn.instrument_terminal is not None:
            result["instrument_terminal"] = conn.instrument_terminal

        instruments = get_active_instruments()
        inst = instruments.get(conn.instrument)
        resource = getattr(inst, "_resource", None) or getattr(inst, "resource", None)
        if resource:
            result["instrument_resource"] = str(resource)
        return result

    spec = get_active_product_context()
    if spec is None:
        return result

    try:
        pin_info = spec.get_pin_info(name)
    except KeyError:
        return result
    if not pin_info:
        return result

    result["dut_pin"] = pin_info.get("dut_pin")
    result["fixture_connection"] = pin_info.get("fixture_connection")
    result["instrument_channel"] = pin_info.get("instrument_channel")
    result["characteristic_id"] = name

    fc_name = pin_info.get("fixture_connection")
    if fc_name and spec.fixture is not None:
        fc = spec.fixture.connections.get(fc_name)
        if fc is not None:
            result["instrument_name"] = fc.instrument
            instruments = get_active_instruments()
            inst = instruments.get(fc.instrument)
            resource = getattr(inst, "_resource", None) or getattr(inst, "resource", None)
            if resource:
                result["instrument_resource"] = str(resource)

    return result


def _snapshot_active_vector_params() -> dict[str, Any]:
    """Return a copy of the active vector params ContextVar.

    Used by ``start_step`` / ``log_measurement`` to stamp ``TestVector.params``
    so parquet rows carry parametrize values. Returns an empty dict when no
    pytest plugin is active (direct / legacy callers). Values are JSON-coerced
    so events serialize cleanly even for class-type / bytes parametrize values.
    """
    return coerce_dict(get_active_vector_params())


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
    → active sidecar limits → active product context → unchecked (None).

    Graceful degradation: both ``get_active_limits`` (sidecar) and
    ``get_active_product_context`` (product YAML) may be empty/None in
    pure-pytest runs; in that case returns ``None`` and the measurement
    is recorded unchecked. The spec read is a one-way ContextVar snapshot
    at write time — not a runtime call on the spec module — so the
    ``test → spec → logger`` data-flow rule from the plugin plan still
    holds. Lives at module scope so it can be tested in isolation from
    ``TestRunLogger`` instance state.
    """
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

    cfg = get_active_limits().get(name)
    if cfg is not None:
        # cfg is a MeasurementLimitConfig — resolve against active state
        # (vector params, spec context, profile guardband) at measurement
        # time, including band matching with sibling-as-catch-all fallback.
        return resolve_limit(cfg, test_char=get_active_characteristic())

    spec = get_active_product_context()
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
    env_id = os.environ.get("_LITMUS_RUN_ID")
    if env_id:
        return _parse_uuid(env_id)
    return uuid4()


class RunContext:
    """Run-level context for adding custom metadata during test execution.

    Unlike ``Context`` (which is per-vector and scoped to a single test step),
    ``RunContext`` persists for the entire session and stores metadata that
    applies to the whole run (operator badge, fixture serial, etc.).

    Allows test architects to add custom fields that become columns in Parquet:

        def test_output_voltage(context, psu, dmm, verify, run_context):
            # Add custom fields - become columns in Parquet
            run_context.set("operator_badge", badge_id)
            run_context.set("operator_shift", "day")
            run_context.set("chamber_humidity", 45.2)
            run_context.set("fixture_serial", "FIX-001")

            # Normal test...
            psu.set_voltage(context.get_param("vin"))
            verify("output_voltage", float(dmm.measure_dc_voltage()))

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
    (the runs daemon's materializer is the canonical consumer).
    """

    __test__ = False  # Prevent pytest collection

    def __init__(
        self,
        dut_serial: str,
        station_id: str | None,
        station_name: str | None = None,
        station_type: str | None = None,
        station_location: str | None = None,
        station_hostname: str | None = None,
        operator_id: str | None = None,
        operator_name: str | None = None,
        test_phase: str | None = None,
        profile: str | None = None,
        profile_facets: dict[str, str] | None = None,
        session_inputs: dict[str, str] | None = None,
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
        data_dir: str | Path | None = None,
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
            test_phase=test_phase,
            profile=profile,
            profile_facets=profile_facets or {},
            session_inputs=session_inputs or {},
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
        self._step_seen_names: set[tuple[str, str | None, int | None, int | None]] = set()
        self._step_seen_repeatable: set[tuple[str, str | None, int | None, int | None]] = set()
        self._step_token: Token[TestStep | None] | None = None
        # _vector_token tracks current vector context for this step.
        # Both start_step() and log_measurement() may set it; reset in end_step().
        self._vector_token: Token[TestVector | None] | None = None
        # Clear contextvars — each logger owns its execution context
        push_current_step(None)
        push_current_vector(None)
        self._run_context = RunContext(self.test_run)
        self._instruments: dict[str, InstrumentRecord] = instruments or {}
        self._step_instrument_arrays: dict[str, list] | None = None

        # Event log for typed event streaming
        self._event_log: EventLog | None = None
        self._session_id: UUID = self.test_run.session_id
        self._data_dir = Path(data_dir) if data_dir is not None else None

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
        - step_instruments_name: List of instrument names/roles (e.g., ["dmm", "psu"])
        - step_instruments_id: List of instrument IDs
          (e.g., ["keithley_dmm_001", "keysight_psu_001"])
        - step_instruments_driver: List of driver class paths
          (e.g., ["drivers.Keithley2000"])
        - step_instruments_resource: List of resources
          (e.g., ["GPIB::16::INSTR", "GPIB::17::INSTR"])
        - step_instruments_protocol: List of protocols (e.g., ["visa", "visa"])
        - step_instruments_manufacturer: List of manufacturers
        - step_instruments_model: List of models
        - step_instruments_serial: List of serial numbers
        - step_instruments_firmware: List of firmware versions
        - step_instruments_cal_due: List of calibration due dates (ISO format)
        - step_instruments_cal_last: List of last calibration dates (ISO format)
        - step_instruments_cal_certificate: List of certificate numbers
        - step_instruments_cal_lab: List of calibration labs

        All arrays are the same length and in the same order.
        """
        arrays: dict[str, list] = {key: [] for key in INSTRUMENT_ARRAY_KEYS}
        for role, record in self._instruments.items():
            if roles is not None and role not in roles:
                continue
            info = instrument_info_fields(record)
            cal = instrument_cal_fields(record)
            arrays["step_instruments_name"].append(role)
            arrays["step_instruments_id"].append(record.instrument_id)
            arrays["step_instruments_driver"].append(record.driver)
            arrays["step_instruments_resource"].append(record.resource)
            arrays["step_instruments_protocol"].append(record.protocol)
            arrays["step_instruments_manufacturer"].append(info["manufacturer"])
            arrays["step_instruments_model"].append(info["model"])
            arrays["step_instruments_serial"].append(info["serial"])
            arrays["step_instruments_firmware"].append(info["firmware"])
            arrays["step_instruments_cal_due"].append(cal["cal_due"])
            arrays["step_instruments_cal_last"].append(cal["cal_last"])
            arrays["step_instruments_cal_certificate"].append(cal["cal_certificate"])
            arrays["step_instruments_cal_lab"].append(cal["cal_lab"])
            arrays["step_instruments_mocked"].append(record.mocked)
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
        step_index: int | None = None,
        vector_index: int | None = None,
        inputs: dict[str, Any] | None = None,
        node_id: str | None = None,
        file: str | None = None,
        module: str | None = None,
        class_name: str | None = None,
        function: str | None = None,
        markers: str | None = None,
    ):
        """Begin a new test step. Supports nesting via step_path.

        ``step_index`` / ``vector_index`` / ``inputs`` are pre-assigned by the
        pytest plugin during collection so that:

        * sweep variants of the same logical step share one ``step_index``
          (sequence-relative position) and differ only by ``vector_index``;
        * the commanded sweep parameters (``in_*``) reach the StepStarted event
          and the step row directly, without subscribers having to wait for a
          measurement to arrive.

        When the kwargs are omitted (manual nested ``with harness.step(...)``
        usage, legacy paths), ``step_index`` falls back to the auto-incrementing
        counter and ``vector_index`` defaults to 0.
        """
        # Auto-close any prior step that wasn't explicitly ended
        if get_current_step() is not None:
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
        self._next_step_index(step_index)
        self.test_run.steps.append(step)
        # Create a default vector for this step. Plugin-supplied inputs are the
        # commanded sweep params; fall back to the active-vector snapshot for
        # manual/in-test vector iteration paths.  Coerce to JSON-safe values so
        # StepStarted/StepEnded serialize cleanly when parametrize values are
        # bytes or class types.
        vec_params = coerce_dict(inputs) if inputs is not None else _snapshot_active_vector_params()
        vector = TestVector(index=vector_index or 0, params=vec_params)
        step.vectors.append(vector)
        # Token-based set for proper reset in end_step()
        self._step_token = push_current_step(step)
        self._vector_token = push_current_vector(vector)

        self._emit_step_event(step, is_start=True)

    def register_step(self, step: TestStep, step_index: int | None = None) -> int:
        """Register an externally-created step. Returns step index.

        Used by TestHarness to register steps it creates, so that
        log_measurement() can find the correct step via contextvars.
        ``step_index`` mirrors :meth:`start_step` — explicit value sets the
        counter; ``None`` auto-increments. Symmetry matters for sweep variants
        that share a sequence-relative index assigned at collection time.
        """
        self.test_run.steps.append(step)
        return self._next_step_index(step_index)

    def _next_step_index(self, provided: int | None) -> int:
        """Set or auto-increment ``_current_step_index`` and return the result.

        Single source of truth for the counter so :meth:`start_step` and
        :meth:`register_step` can't drift out of sync. Sequence-relative
        values pre-assigned by the pytest plugin override; ``None`` falls
        back to the running counter for legacy / manual paths.
        """
        if provided is not None:
            self._current_step_index = provided
        else:
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
        """Emit a StepStarted event for an externally-managed step.

        Used by ``TestHarness.step(...)`` when it owns step lifecycle and
        only delegates event emission to the logger.  ``step_index`` is the
        caller's authoritative position; we sync the logger's counter to it
        so subsequent measurements stamp the right index.
        """
        self._current_step_index = step_index
        self._emit_step_event(step, is_start=True)

    def emit_step_ended(self, step: TestStep, step_index: int) -> None:
        """Emit a StepEnded event for an externally-managed step.

        Counterpart to :meth:`emit_step_started`.  Same caller-owns-lifecycle
        contract — ``step_index`` is authoritative.
        """
        self._current_step_index = step_index
        self._emit_step_event(step, is_start=False)

    def _emit_step_event(self, step: TestStep, *, is_start: bool) -> None:
        """Emit a StepStarted or StepEnded event for ``step``.

        Single helper used by :meth:`start_step` and :meth:`end_step` so the
        payload shape stays in sync.  Reads the active vector from the step
        (assumed to be the last one appended for this iteration) and applies
        :func:`coerce_dict` on params/observations so non-JSON-safe parametrize
        values don't break ``model_dump_json``.
        """
        if self._event_log is None:
            return
        vec = step.vectors[-1] if step.vectors else None
        vec_index = vec.index if vec is not None else 0
        inputs = coerce_dict(vec.params) if vec is not None else {}
        if is_start:
            event: StepStarted | StepEnded = StepStarted(
                session_id=self._session_id,
                run_id=self.test_run.id,
                step_name=step.name,
                step_index=self._current_step_index,
                step_path=step.step_path,
                parent_path=step.parent_path or "",
                description=step.description,
                vector_index=vec_index,
                inputs=inputs,
                node_id=step.node_id,
                file=step.file,
                module=step.module,
                class_name=step.class_name,
                function=step.function,
            )
        else:
            vec_outcome = vec.outcome.value if vec is not None and vec.outcome is not None else None
            event = StepEnded(
                session_id=self._session_id,
                run_id=self.test_run.id,
                step_name=step.name,
                step_index=self._current_step_index,
                step_path=step.step_path,
                parent_path=step.parent_path or "",
                outcome=step.outcome.value if step.outcome else None,
                vector_index=vec_index,
                vector_outcome=vec_outcome,
                inputs=inputs,
                outputs=coerce_dict(vec.observations) if vec is not None else {},
                node_id=step.node_id,
                file=step.file,
                module=step.module,
                class_name=step.class_name,
                function=step.function,
            )
        self._event_log.emit(event)

    def log_measurement(self, measurement: Measurement):
        """Add measurement to current step.

        Resolves step/vector from contextvars. If no step exists, one is
        auto-created from the measurement name.
        """
        # Resolve step: contextvar only → auto-create
        step = get_current_step()
        if step is None:
            self.start_step(measurement.name)
            step = get_current_step()
        assert step is not None

        # Resolve vector: contextvar only → auto-create
        vector = get_current_vector()
        if vector is None:
            vector = TestVector(params=_snapshot_active_vector_params())
            step.vectors.append(vector)
            self._vector_token = push_current_vector(vector)

        # Stamp step_path from the resolved step so downstream consumers
        # (traceability audit, parquet projection) don't see empty strings.
        if not measurement.step_path:
            measurement.step_path = step.step_path

        # Guard against double-append (harness.measure() appends before calling us)
        if measurement not in vector.measurements:
            vector.measurements.append(measurement)

        # Outcome is set by every caller before reaching here:
        # ``logger.measure`` stamps via the ``outcome=`` kwarg (default
        # DONE; verify passes PASSED / FAILED / ERRORED). ``harness.measure``
        # stamps via ``check_limit()``. Direct ``log_measurement`` callers
        # (assertion path) set it explicitly. RuntimeError instead of
        # ``assert`` so the contract holds under ``python -O``.
        if measurement.outcome is None:
            raise RuntimeError(
                f"log_measurement requires measurement.outcome to be set "
                f"before this call (got None for {measurement.name!r}). "
                "Stamp via logger.measure(outcome=...), check_limit(), or "
                "set measurement.outcome explicitly before calling."
            )
        # Cascade outcome up the test run hierarchy via the severity
        # ladder (ABORTED > TERMINATED > ERRORED > FAILED > PASSED >
        # DONE > SKIPPED). ``None`` ranks below everything.
        vector.outcome = escalate_outcome(vector.outcome, measurement.outcome)
        step.outcome = escalate_outcome(step.outcome, measurement.outcome)
        self.test_run.outcome = escalate_outcome(self.test_run.outcome, measurement.outcome)

        # A measurement carrying limits is structurally equivalent
        # to a passing assert — the test code declared an intent to
        # judge. Flag the step so a clean exit lands as PASSED
        # rather than DONE. (See pytest_plugin.hooks.pytest_assertion_pass
        # for the assert-side counterpart.)
        if measurement.limit_low is not None or measurement.limit_high is not None:
            try:
                from litmus.pytest_plugin.hooks import mark_step_judgment_intent

                mark_step_judgment_intent(str(step.id))
            except ImportError:
                pass  # non-pytest path; flag is unused

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
                retry=vector.retry,
                # Measurement fields
                measurement_name=measurement.name,
                measurement_timestamp=measurement.timestamp,
                value=measurement.value,
                units=measurement.units,
                outcome=measurement.outcome.value if measurement.outcome else None,
                limit_low=measurement.limit_low,
                limit_high=measurement.limit_high,
                limit_nominal=measurement.limit_nominal,
                limit_comparator=measurement.limit_comparator,
                characteristic_id=measurement.characteristic_id,
                spec_ref=measurement.spec_ref,
                dut_pin=measurement.dut_pin,
                fixture_connection=measurement.fixture_connection,
                instrument_name=measurement.instrument_name,
                instrument_resource=measurement.instrument_resource,
                instrument_channel=measurement.instrument_channel,
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
        step = get_current_step()
        if step is not None:
            step.ended_at = _utcnow()
        vector = get_current_vector()
        if vector is not None:
            vector.ended_at = _utcnow()

        if step is not None:
            self._emit_step_event(step, is_start=False)

        # Pop step from hierarchy stack
        if self._step_stack:
            self._step_stack.pop()

        # Reset via tokens for proper contextvar hygiene
        if self._step_token is not None:
            reset_current_step(self._step_token)
            self._step_token = None
        if self._vector_token is not None:
            reset_current_vector(self._vector_token)
            self._vector_token = None

    def measure(
        self,
        name: str,
        value: float | int | None,
        *,
        limit: Limit | None = None,
        outcome: Outcome = Outcome.DONE,
        allow_repeat: bool = False,
    ) -> Measurement:
        """Record one measurement row.

        Default ``outcome=Outcome.DONE`` — recorder semantic ("ran, no
        judgment"). :func:`litmus.execution.verify.verify` resolves
        the limit upfront, computes the post-judgment outcome, and
        passes it via ``outcome=`` so the cascade and event fire ONCE
        with the final value (PASSED / FAILED / ERRORED).

        **Limit resolution chain** — fields copied onto the row so
        analysis sees what limit was active, even when nobody evaluated:

        1. ``limit=Limit(...)`` passed by the caller.
        2. :func:`get_active_limits` — sidecar + marker + profile merge.
        3. :func:`get_active_product_context` — product characteristic by name.
        4. None — row records no limit fields.

        **Auto-traceability** — ``dut_pin`` / ``instrument_*`` /
        ``fixture_connection`` / ``characteristic_id`` / ``spec_ref`` are pulled
        from the active :class:`ProductContext` by measurement name when
        available. Callers never pass these.

        **Duplicate-name dedup**: two writes with the same name in one
        step raise :class:`DuplicateMeasurementError` unless both opt in
        via ``allow_repeat=True``.

        Returns:
            The persisted :class:`Measurement` with the requested
            ``outcome`` (default DONE).
        """
        resolved_limit = _resolve_measurement_limit(
            name,
            inline_any=False,
            low=None,
            high=None,
            nominal=None,
            comparator=None,
            limit=limit,
            units=None,
        )

        # Ensure a step exists *before* the dedup check — otherwise the
        # check runs against stale state and ``start_step`` (auto-called
        # from ``log_measurement``) would then reset ``_step_seen_names``,
        # silently swallowing a real duplicate. Pytest always opens a step
        # around the test body; this guard is for non-pytest callers.
        if get_current_step() is None:
            self.start_step(name)

        # Dedup check against per-step seen_names
        self._guard_duplicate(name, allow_repeat)

        # Extract limit fields for the Measurement row
        limit_low: float | None = None
        limit_high: float | None = None
        nom: float | None = None
        cmp_str: str | None = None
        meas_units: str | None = None
        meas_char_id: str | None = None
        meas_spec_ref: str | None = None

        if resolved_limit is not None:
            limit_low = resolved_limit.low
            limit_high = resolved_limit.high
            nom = resolved_limit.nominal
            meas_units = resolved_limit.units
            meas_char_id = resolved_limit.characteristic_id
            meas_spec_ref = resolved_limit.spec_ref
            cmp_str = _stringify_comparator(resolved_limit.comparator)

        # Auto-fill traceability from the active ProductContext. A caller
        # (ProductContext.check / legacy harness) may have pre-populated a
        # Measurement; here we only set fields from the spec when they
        # would otherwise be blank.
        trace = _auto_traceability(name)

        measurement = Measurement(
            name=name,
            value=float(value) if value is not None else None,
            units=meas_units,
            limit_low=limit_low,
            limit_high=limit_high,
            limit_nominal=nom,
            limit_comparator=cmp_str,
            characteristic_id=meas_char_id or trace.get("characteristic_id"),
            spec_ref=meas_spec_ref or trace.get("spec_ref"),
            dut_pin=trace.get("dut_pin"),
            instrument_name=trace.get("instrument_name"),
            instrument_resource=trace.get("instrument_resource"),
            instrument_channel=trace.get("instrument_channel"),
            outcome=outcome,
            fixture_connection=trace.get("fixture_connection"),
        )

        # Cascade + emit fire ONCE with the final outcome the caller
        # passed. ``verify`` resolves limit + computes outcome upfront
        # and passes PASSED / FAILED here; ``logger.measure`` callers
        # use the DONE default.
        self.log_measurement(measurement)
        return measurement

    def _guard_duplicate(self, name: str, allow_repeat: bool) -> None:
        """Raise :class:`DuplicateMeasurementError` on same-name double-write.

        Each step tracks
        ``(name, active_connection, vector_index, vector_retry)``
        tuples that have been written. A second write with the same
        tuple is an error unless both calls opt in via
        ``allow_repeat=True``.

        The four components scope the dedup so the common patterns
        all work without ``allow_repeat``:

        * **Connection.** Multi-pin characteristics iterate
          ``ctx.connections`` and emit the same measurement name once
          per pin — each iteration has a different ``connection``, so
          the keys are distinct.
        * **Vector index.** In-test iteration via the ``vectors``
          fixture (``for v in vectors: verify(...)``) pushes a new
          ``TestVector`` per row with a distinct ``index``. The same
          name on different vector rows is not a duplicate. Native
          ``@pytest.mark.parametrize`` doesn't need this — each item
          opens its own step — but the ``vectors`` fixture runs
          inside one step and would otherwise trip on the second
          iteration.
        * **Vector retry.** :class:`~litmus.execution.harness.TestHarness`
          retries (``litmus_retry`` markers running through the
          harness path) re-run the same vector with the same index
          but an incremented ``retry``. The retry re-emits the same
          measurement name; including ``retry`` in the key keeps
          each retry distinct. (Pytest-side retries via
          pytest-rerunfailures open a fresh step per attempt and
          don't need this dimension.)

        Typical causes when this fires:

        - Two independent ``logger.measure`` calls accidentally
          sharing a name within the same vector; rename one.
        - An inner-loop streaming pattern that forgot
          ``allow_repeat=True``.
        - ``logger.measure(name)`` followed by ``verify(name)`` for
          the same measurement — verify already records, so the
          first call is redundant.
        """
        conn = get_active_connection()
        vector = get_current_vector()
        key = (
            name,
            conn.name if conn is not None else None,
            vector.index if vector is not None else None,
            vector.retry if vector is not None else None,
        )
        if key in self._step_seen_names:
            first_was_repeatable = key in self._step_seen_repeatable
            if not (allow_repeat and first_was_repeatable):
                step = get_current_step()
                step_label = step.name if step else "<no-step>"
                raise DuplicateMeasurementError(
                    f"Measurement {name!r} already recorded in step {step_label!r}. "
                    "Each measurement name must be unique within a vector; pass "
                    "allow_repeat=True on every call when streaming samples."
                )
        else:
            self._step_seen_names.add(key)
            if allow_repeat:
                self._step_seen_repeatable.add(key)

    def record(self, key: str, value: Any) -> None:
        """Emit a key/value record event to the event log.

        Args:
            key: Record key (e.g., "firmware_version", "calibration_date").
            value: Record value (must be JSON-serializable).
        """
        step = get_current_step()
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
        if get_current_step() is not None:
            self.end_step()

        self.test_run.ended_at = _utcnow()

        if self._event_log is not None:
            self._event_log.emit(
                RunEnded(
                    session_id=self._session_id,
                    run_id=self.test_run.id,
                    outcome=self.test_run.outcome.value if self.test_run.outcome else None,
                )
            )

        return self.test_run
