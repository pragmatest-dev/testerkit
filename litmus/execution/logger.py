"""Test run logging for accumulating measurements."""

from __future__ import annotations

import hashlib
import os
import socket
from collections.abc import Callable
from contextvars import ContextVar, Token
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

# Module-level contextvars for concurrency-safe step/vector resolution.
# All execution paths (harness, decorator, fixture) set these; log_measurement() reads them.
_current_step_var: ContextVar[TestStep | None] = ContextVar("_current_step", default=None)
_current_vector_var: ContextVar[TestVector | None] = ContextVar("_current_vector", default=None)

if TYPE_CHECKING:
    from litmus.config.models import Limit
    from litmus.data.event_log import EventLog
    from litmus.environment import EnvironmentSnapshot
    from litmus.instruments.models import InstrumentRecord


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
        # Config snapshots
        station_config_yaml: str | None = None,
        product_spec_yaml: str | None = None,
        fixture_config_yaml: str | None = None,
        test_config_yaml: str | None = None,
        # Code traceability
        git_commit: str | None = None,
        # Journal streaming
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
            product_id=product_id,
            product_name=product_name,
            product_revision=product_revision,
            fixture_id=fixture_id,
            git_commit=git_commit,
            station_config_yaml=station_config_yaml,
            product_spec_yaml=product_spec_yaml,
            fixture_config_yaml=fixture_config_yaml,
            test_config_yaml=test_config_yaml,
        )
        # Serialize environment eagerly so every event has it
        if environment is not None:
            self.test_run.environment_json = environment.model_dump_json()
        self._current_step_index: int = -1
        self._step_stack: list[str] = []  # Path components for nested steps
        self._step_token: Token[TestStep | None] | None = None
        # _vector_token tracks current vector context for this step.
        # Both start_step() and log_measurement() may set it; reset in end_step().
        self._vector_token: Token[TestVector | None] | None = None
        # Clear contextvars — each logger owns its execution context
        _current_step_var.set(None)
        _current_vector_var.set(None)
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

    def build_instrument_arrays(
        self, roles: list[str] | None = None
    ) -> dict[str, list]:
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
        _INSTR_FIELDS: list[tuple[str, Callable[[str, InstrumentRecord], Any]]] = [
            ("instr_name", lambda role, rec: role),
            ("instr_id", lambda role, rec: rec.instrument_id),
            ("instr_driver", lambda role, rec: rec.driver),
            ("instr_resource", lambda role, rec: rec.resource),
            ("instr_protocol", lambda role, rec: rec.protocol),
            ("instr_manufacturer", lambda role, rec: rec.info.manufacturer if rec.info else None),
            ("instr_model", lambda role, rec: rec.info.model if rec.info else None),
            ("instr_serial", lambda role, rec: rec.info.serial if rec.info else None),
            ("instr_firmware", lambda role, rec: rec.info.firmware if rec.info else None),
            ("instr_cal_due", lambda role, rec: (
                rec.calibration.due_date.isoformat()
                if rec.calibration and rec.calibration.due_date else None
            )),
            ("instr_cal_last", lambda role, rec: (
                rec.calibration.last_cal.isoformat()
                if rec.calibration and rec.calibration.last_cal else None
            )),
            ("instr_cal_certificate", lambda role, rec: (
                rec.calibration.certificate if rec.calibration else None
            )),
            ("instr_cal_lab", lambda role, rec: rec.calibration.lab if rec.calibration else None),
            ("instr_mocked", lambda role, rec: rec.mocked),
        ]

        arrays: dict[str, list] = {key: [] for key, _ in _INSTR_FIELDS}
        for role, record in self._instruments.items():
            if roles is not None and role not in roles:
                continue
            for key, extract in _INSTR_FIELDS:
                arrays[key].append(extract(role, record))
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
        if _current_step_var.get() is not None:
            self.end_step()
        # Clear per-step instrument arrays so they don't leak between steps
        self._step_instrument_arrays = None

        # Build hierarchy path
        self._step_stack.append(name)
        step_path = "/".join(self._step_stack)
        parent_path = "/".join(self._step_stack[:-1])

        step = TestStep(
            name=name, description=description,
            step_path=step_path, parent_path=parent_path,
            node_id=node_id, file=file, module=module,
            class_name=class_name, function=function, markers=markers,
        )
        self._current_step_index += 1
        self.test_run.steps.append(step)
        # Create a default vector for this step (for simple logging without harness)
        vector = TestVector()
        step.vectors.append(vector)
        # Token-based set for proper reset in end_step()
        self._step_token = _current_step_var.set(step)
        self._vector_token = _current_vector_var.set(vector)

        if self._event_log is not None:
            self._event_log.emit(StepStarted(
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
            ))

    def register_step(self, step: TestStep) -> int:
        """Register an externally-created step. Returns step index.

        Used by TestHarness to register steps it creates, so that
        log_measurement() can find the correct step via contextvars.
        """
        self.test_run.steps.append(step)
        self._current_step_index += 1
        return self._current_step_index

    def log_measurement(self, measurement: Measurement):
        """Add measurement to current step.

        Resolves step/vector from contextvars. If no step exists, one is
        auto-created from the measurement name.
        """
        # Resolve step: contextvar only → auto-create
        step = _current_step_var.get()
        if step is None:
            self.start_step(measurement.name)
            step = _current_step_var.get()
        assert step is not None

        # Resolve vector: contextvar only → auto-create
        vector = _current_vector_var.get()
        if vector is None:
            vector = TestVector()
            step.vectors.append(vector)
            self._vector_token = _current_vector_var.set(vector)

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
                    vector, ref_saver=self._event_log.save_ref,
                ),
                custom=dict(self.test_run.custom_metadata),
            )
            self._event_log.emit(event)

    def end_step(self):
        """Finalize current step."""
        step = _current_step_var.get()
        if step is not None:
            step.ended_at = _utcnow()
        vector = _current_vector_var.get()
        if vector is not None:
            vector.ended_at = _utcnow()

        if self._event_log is not None and step is not None:
            self._event_log.emit(StepEnded(
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
            ))

        # Pop step from hierarchy stack
        if self._step_stack:
            self._step_stack.pop()

        # Reset via tokens for proper contextvar hygiene
        if self._step_token is not None:
            _current_step_var.reset(self._step_token)
            self._step_token = None
        if self._vector_token is not None:
            _current_vector_var.reset(self._vector_token)
            self._vector_token = None

    def measure(
        self,
        name: str,
        value: float | int | None,
        limit: Limit | None = None,
        units: str | None = None,
        dut_pin: str | None = None,
        instrument_name: str | None = None,
        instrument_resource: str | None = None,
        instrument_channel: str | None = None,
        fixture_point: str | None = None,
        spec_ref: str | None = None,
    ) -> Measurement:
        """Log a measurement with optional limit checking.

        This is a convenience method that creates a Measurement and logs it.
        Use this instead of constructing Measurement objects manually.

        Args:
            name: Measurement name (e.g., "output_voltage")
            value: Measured value
            limit: Optional Limit object with low/high/nominal/units/spec_ref
            units: Units (overrides limit.units if provided)
            dut_pin: DUT pin measured (e.g., "TP_VOUT")
            instrument_name: Station config instrument name (e.g., "dmm_main")
            instrument_resource: VISA address or connection string
            instrument_channel: Channel on instrument (e.g., "CH1")
            fixture_point: Fixture routing point name
            spec_ref: Spec reference (overrides limit.spec_ref if provided)

        Returns:
            The Measurement object created and logged.

        Example:
            litmus_logger.measure(
                name="output_voltage",
                value=3.31,
                limit=Limit(low=3.2, high=3.4, units="V"),
                dut_pin="TP_VOUT",
            )
        """
        # Extract limit fields if provided
        low_limit = None
        high_limit = None
        nominal = None
        comparator = None

        if limit is not None:
            low_limit = limit.low
            high_limit = limit.high
            nominal = limit.nominal
            if units is None:
                units = limit.units
            if spec_ref is None:
                spec_ref = limit.spec_ref
            comparator = getattr(limit, "comparator", None)
            if comparator is not None:
                comparator = (
                    str(comparator.value) if hasattr(comparator, "value") else str(comparator)
                )

        # Create measurement
        measurement = Measurement(
            name=name,
            value=float(value) if value is not None else None,
            units=units,
            low_limit=low_limit,
            high_limit=high_limit,
            nominal=nominal,
            comparator=comparator,
            spec_ref=spec_ref,
            dut_pin=dut_pin,
            instrument_name=instrument_name,
            instrument_resource=instrument_resource,
            instrument_channel=instrument_channel,
            fixture_point=fixture_point,
        )

        # Check limits and set outcome
        measurement.check_limit()

        # Log it
        self.log_measurement(measurement)

        return measurement

    def record(self, key: str, value: Any) -> None:
        """Emit a key/value record event to the event log.

        Args:
            key: Record key (e.g., "firmware_version", "calibration_date").
            value: Record value (must be JSON-serializable).
        """
        step = _current_step_var.get()
        step_name = step.name if step else ""
        step_index = self._current_step_index if step else -1
        if self._event_log is not None:
            self._event_log.emit(RecordEvent(
                session_id=self._session_id,
                run_id=self.test_run.id,
                step_name=step_name,
                step_index=step_index,
                key=key,
                value=value,
            ))

    def finalize(self) -> TestRun:
        """Complete test run and return result.

        Emits RunEnded event. Does NOT close the event log — caller is
        responsible for emitting SessionEnded and closing the log.
        """
        # Close any unclosed step before finalizing
        if _current_step_var.get() is not None:
            self.end_step()

        self.test_run.ended_at = _utcnow()

        if self._event_log is not None:
            self._event_log.emit(RunEnded(
                session_id=self._session_id,
                run_id=self.test_run.id,
                outcome=self.test_run.outcome.value,
            ))

        return self.test_run
