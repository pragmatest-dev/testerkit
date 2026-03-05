"""Test run logging for accumulating measurements."""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from litmus.data.models import DUT, Measurement, Outcome, TestRun, TestStep, TestVector, _utcnow

# Module-level contextvars for concurrency-safe step/vector resolution.
# All execution paths (harness, decorator, fixture) set these; log_measurement() reads them.
_current_step_var: ContextVar[TestStep | None] = ContextVar("_current_step", default=None)
_current_vector_var: ContextVar[TestVector | None] = ContextVar("_current_vector", default=None)

if TYPE_CHECKING:
    from litmus.data.backends.journal import JournalWriter
    from litmus.data.exporters._base import StreamingDestination
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


def _get_run_id() -> UUID:
    """Get run ID from environment or generate new one."""
    from uuid import uuid4

    env_id = os.environ.get("LITMUS_RUN_ID")
    if env_id:
        try:
            return UUID(env_id)
        except ValueError:
            # Not a valid UUID - generate deterministic one from the string
            import hashlib

            h = hashlib.md5(env_id.encode()).hexdigest()
            return UUID(h)
    return uuid4()


class RunContext:
    """Context for adding custom metadata during test execution.

    Note: This class is deprecated. Use Context from litmus.execution.harness
    for new code. This class remains for backwards compatibility with existing
    code that uses the run_context fixture.

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

    # -------------------------------------------------------------------------
    # Context API compatibility (for use as unified context)
    # -------------------------------------------------------------------------

    def configure(self, key: str, value: Any) -> None:
        """Alias for set() - Context API compatibility."""
        self.set(key, value)

    def observe(self, key: str, value: Any) -> None:
        """Alias for set() - Context API compatibility."""
        self.set(key, value)

    def set_in(self, key: str, value: Any) -> None:
        """Alias for set() - Context API compatibility."""
        self.set(key, value)

    def set_out(self, key: str, value: Any) -> None:
        """Alias for set() - Context API compatibility."""
        self.set(key, value)

    def get_in(self, key: str, default: Any = None) -> Any:
        """Alias for get() - Context API compatibility."""
        return self.get(key, default)

    def get_out(self, key: str, default: Any = None) -> Any:
        """Alias for get() - Context API compatibility."""
        return self.get(key, default)

    @property
    def inputs(self) -> dict[str, Any]:
        """Alias for metadata - Context API compatibility."""
        return self.metadata

    @property
    def outputs(self) -> dict[str, Any]:
        """Alias for metadata - Context API compatibility."""
        return self.metadata


class TestRunLogger:
    """Accumulates measurements during test run, produces TestRun.

    Optionally streams measurements to a JSONL journal for live observability
    and crash recovery. When results_dir is provided, measurements are written
    to the journal as they happen.
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
        operator_id: str | None = None,
        operator_name: str | None = None,
        test_phase: str = "production",
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
        if run_id is not None:
            if isinstance(run_id, str):
                try:
                    run_id = UUID(run_id)
                except ValueError:
                    import hashlib

                    h = hashlib.md5(run_id.encode()).hexdigest()
                    run_id = UUID(h)
        else:
            run_id = _get_run_id()

        self.test_run = TestRun(
            id=run_id,
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
        # Serialize environment eagerly so every journal row has it
        if environment is not None:
            self.test_run.environment_json = environment.model_dump_json()
        self._current_step_index: int = -1
        self._step_token: Token[TestStep | None] | None = None
        self._vector_token: Token[TestVector | None] | None = None
        # Clear contextvars — each logger owns its execution context
        _current_step_var.set(None)
        _current_vector_var.set(None)
        self._run_context = RunContext(self.test_run)
        self._instruments: dict[str, InstrumentRecord] = instruments or {}
        self._step_instrument_arrays: dict[str, list] | None = None

        # Journal streaming for live observability
        # Start with empty instrument arrays; populated per-step via set_step_instruments()
        self._journal: JournalWriter | None = None
        if results_dir is not None:
            from litmus.data.backends.journal import JournalWriter

            self._journal = JournalWriter(
                results_dir,
                self.test_run,
            )
            self._journal.__enter__()

        # Additional streaming destinations (STDF, TDMS, PostgreSQL, etc.)
        self._streaming_destinations: list[StreamingDestination] = []
        self._failed_destinations: set[StreamingDestination] = set()

    def add_streaming_destination(self, dest: StreamingDestination) -> None:
        """Register an additional streaming destination.

        Streaming destinations receive each measurement row as it is recorded,
        in the same denormalized dict format as the JSONL journal. Use this to
        wire up real-time STDF, TDMS, or database streaming alongside the
        built-in journal.

        Args:
            dest: An object implementing the StreamingDestination protocol.
                  Must already be open (call dest.open() before adding).
        """
        self._streaming_destinations.append(dest)

    @property
    def run_context(self) -> RunContext:
        """Get the run context for adding custom metadata."""
        return self._run_context

    @property
    def journal_dir(self) -> Path | None:
        """Get the journal directory path, if journaling is enabled."""
        if self._journal is not None:
            return self._journal.journal_dir
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
        names: list[str] = []
        ids: list[str | None] = []
        drivers: list[str | None] = []
        resources: list[str | None] = []
        protocols: list[str] = []
        manufacturers: list[str | None] = []
        models: list[str | None] = []
        serials: list[str | None] = []
        firmwares: list[str | None] = []
        cal_dues: list[str | None] = []
        cal_lasts: list[str | None] = []
        cal_certs: list[str | None] = []
        cal_labs: list[str | None] = []
        mocked: list[bool] = []

        for role, record in self._instruments.items():
            if roles is not None and role not in roles:
                continue
            names.append(role)
            mocked.append(record.mocked)
            ids.append(record.instrument_id)
            drivers.append(record.driver)
            resources.append(record.resource)
            protocols.append(record.protocol)
            manufacturers.append(record.info.manufacturer if record.info else None)
            models.append(record.info.model if record.info else None)
            serials.append(record.info.serial if record.info else None)
            firmwares.append(record.info.firmware if record.info else None)
            cal_dues.append(
                record.calibration.due_date.isoformat()
                if record.calibration and record.calibration.due_date
                else None
            )
            cal_lasts.append(
                record.calibration.last_cal.isoformat()
                if record.calibration and record.calibration.last_cal
                else None
            )
            cal_certs.append(
                record.calibration.certificate if record.calibration else None
            )
            cal_labs.append(record.calibration.lab if record.calibration else None)

        return {
            "instr_name": names,
            "instr_id": ids,
            "instr_driver": drivers,
            "instr_resource": resources,
            "instr_protocol": protocols,
            "instr_manufacturer": manufacturers,
            "instr_model": models,
            "instr_serial": serials,
            "instr_firmware": firmwares,
            "instr_cal_due": cal_dues,
            "instr_cal_last": cal_lasts,
            "instr_cal_certificate": cal_certs,
            "instr_cal_lab": cal_labs,
            "instr_mocked": mocked,
        }

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

    def start_step(self, name: str, description: str | None = None):
        """Begin a new test step."""
        # Auto-close any prior step that wasn't explicitly ended
        if _current_step_var.get() is not None:
            self.end_step()
        step = TestStep(name=name, description=description)
        self._current_step_index += 1
        self.test_run.steps.append(step)
        # Create a default vector for this step (for simple logging without harness)
        vector = TestVector()
        step.vectors.append(vector)
        # Token-based set for proper reset in end_step()
        self._step_token = _current_step_var.set(step)
        self._vector_token = _current_vector_var.set(vector)

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

        Resolves step/vector from contextvars first (concurrency-safe),
        falling back to instance state. If no step exists, one is auto-created.
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

        # Update vector outcome — ERROR overrides everything (untrusted state)
        if measurement.outcome == Outcome.ERROR:
            vector.outcome = Outcome.ERROR
        elif measurement.outcome == Outcome.FAIL and vector.outcome != Outcome.ERROR:
            vector.outcome = Outcome.FAIL

        # Update step/run outcome — ERROR overrides everything
        if measurement.outcome == Outcome.ERROR:
            step.outcome = Outcome.ERROR
            self.test_run.outcome = Outcome.ERROR
        elif measurement.outcome == Outcome.FAIL:
            if step.outcome != Outcome.ERROR:
                step.outcome = Outcome.FAIL
            if self.test_run.outcome != Outcome.ERROR:
                self.test_run.outcome = Outcome.FAIL

        # Use stored index directly instead of O(n) scan.
        # Only valid for the most recently started/registered step.
        step_index = self._current_step_index

        # Build row once — works with or without journal
        from litmus.data.backends._row_helpers import build_row

        ref_saver = self._journal.save_ref if self._journal else None
        row = build_row(
            self.test_run,
            measurement,
            step.name,
            step_index,
            vector,
            self._step_instrument_arrays or {},
            ref_saver=ref_saver,
            step_started_at=step.started_at,
            step_ended_at=step.ended_at,
        )

        # Stream to journal for live observability
        if self._journal is not None:
            self._journal.append_row(row)

        # Fan out to additional streaming destinations
        for dest in self._streaming_destinations:
            if dest in self._failed_destinations:
                continue
            try:
                dest.append_row(row)
            except Exception as exc:
                import warnings

                self._failed_destinations.add(dest)
                warnings.warn(
                    f"Streaming to '{type(dest).__name__}' failed"
                    f" (disabled for rest of run): {exc}",
                    stacklevel=2,
                )

    def end_step(self):
        """Finalize current step."""
        step = _current_step_var.get()
        if step is not None:
            step.ended_at = _utcnow()
        vector = _current_vector_var.get()
        if vector is not None:
            vector.ended_at = _utcnow()
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
        limit: Any | None = None,  # Limit object from litmus.config.models
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

    def finalize(self) -> TestRun:
        """Complete test run and return result.

        Closes the journal writer if journaling is enabled.
        """
        # Close any unclosed step before finalizing
        if _current_step_var.get() is not None:
            self.end_step()

        self.test_run.ended_at = _utcnow()

        # Close journal writer
        if self._journal is not None:
            self._journal.close()

        # Notify streaming destinations that this run is complete, then close.
        # Destinations are opened by the plugin (_attach_streaming_destinations)
        # and closed here so the logger owns the full append→boundary→close tail.
        run_id_str = str(self.test_run.id)
        for dest in self._streaming_destinations:
            try:
                dest.mark_run_boundary(run_id_str)
            except Exception as exc:
                import warnings

                warnings.warn(
                    f"mark_run_boundary on '{type(dest).__name__}' failed: {exc}",
                    stacklevel=2,
                )
            try:
                dest.close()
            except Exception as exc:
                import warnings

                warnings.warn(
                    f"Closing streaming destination failed: {exc}",
                    stacklevel=2,
                )

        return self.test_run
