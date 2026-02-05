"""Test run logging for accumulating measurements."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from litmus.data.models import DUT, Measurement, Outcome, TestRun, TestStep

if TYPE_CHECKING:
    from litmus.data.backends.journal import JournalWriter
    from litmus.instruments.base import Instrument
    from litmus.instruments.models import InstrumentRecord


def _utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(UTC)


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
        # Instrument identity for traceability
        instruments: dict[str, Instrument] | None = None,
        # Instrument records (new format with calibration)
        instrument_records: dict[str, InstrumentRecord] | None = None,
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
        self._current_step: TestStep | None = None
        self._current_step_index: int = -1
        self._current_vector = None  # For simple logging without harness
        self._run_context = RunContext(self.test_run)
        self._instruments = instruments or {}
        self._instrument_records: dict[str, InstrumentRecord] = instrument_records or {}

        # Journal streaming for live observability
        self._journal: JournalWriter | None = None
        if results_dir is not None:
            from litmus.data.backends.journal import JournalWriter

            self._journal = JournalWriter(
                results_dir,
                self.test_run,
                instrument_arrays=self.build_instrument_arrays(),
            )
            self._journal.__enter__()

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

    @property
    def instruments(self) -> dict[str, Instrument]:
        """Get the instruments dict for identity tracking."""
        return self._instruments

    def build_instrument_arrays(self) -> dict[str, list]:
        """Build parallel arrays for instrument identity and calibration.

        Returns dict with keys:
        - instr_name: List of instrument names/roles (e.g., ["dmm", "psu"])
        - instr_id: List of instrument IDs (e.g., ["keithley_dmm_001", "keysight_psu_001"])
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

        # Prefer instrument records (new format with full info)
        if self._instrument_records:
            for role, record in self._instrument_records.items():
                names.append(role)
                ids.append(record.instrument_id)
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
        else:
            # Fall back to legacy instruments dict
            for name, inst in self._instruments.items():
                names.append(name)
                ids.append(None)
                resources.append(getattr(inst, "resource", None))
                protocols.append("visa")  # Default assumption

                # Get instrument type from class name (e.g., "DMM" -> "dmm")
                # This is legacy behavior, kept for backwards compatibility

                # Get identity from attributes (set by plugin from station config)
                manufacturers.append(getattr(inst, "manufacturer", None))
                models.append(getattr(inst, "model", None))
                serials.append(getattr(inst, "serial", None))
                firmwares.append(getattr(inst, "firmware", None))

                # No calibration info in legacy format
                cal_dues.append(None)
                cal_lasts.append(None)
                cal_certs.append(None)
                cal_labs.append(None)

        return {
            "instr_name": names,
            "instr_id": ids,
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
        }

    def start_step(self, name: str, description: str | None = None):
        """Begin a new test step."""
        from litmus.data.models import TestVector

        self._current_step = TestStep(name=name, description=description)
        self._current_step_index += 1
        self.test_run.steps.append(self._current_step)
        # Create a default vector for this step (for simple logging without harness)
        self._current_vector = TestVector()
        self._current_step.vectors.append(self._current_vector)

    def log_measurement(self, measurement: Measurement):
        """Add measurement to current step.

        Measurements are stored in TestVectors within the step.
        If no step exists, one is auto-created.
        """
        from litmus.data.models import TestVector

        if self._current_step is None:
            # Auto-create step if none exists
            self.start_step(measurement.name)

        # Ensure we have a current vector
        if not hasattr(self, "_current_vector") or self._current_vector is None:
            self._current_vector = TestVector()
            self._current_step.vectors.append(self._current_vector)

        self._current_vector.measurements.append(measurement)

        # Update vector outcome
        if measurement.outcome == Outcome.FAIL:
            self._current_vector.outcome = Outcome.FAIL
        elif measurement.outcome == Outcome.ERROR:
            if self._current_vector.outcome != Outcome.FAIL:
                self._current_vector.outcome = Outcome.ERROR

        # Update step pass/fail
        if measurement.outcome == Outcome.FAIL:
            self._current_step.outcome = Outcome.FAIL
            self.test_run.outcome = Outcome.FAIL
        elif measurement.outcome == Outcome.ERROR:
            if self._current_step.outcome != Outcome.FAIL:
                self._current_step.outcome = Outcome.ERROR
            if self.test_run.outcome != Outcome.FAIL:
                self.test_run.outcome = Outcome.ERROR

        # Stream to journal for live observability
        if self._journal is not None:
            self._journal.append(
                measurement,
                self._current_step.name,
                self._current_step_index,
                self._current_vector,
            )

    def end_step(self):
        """Finalize current step."""
        if self._current_step:
            self._current_step.ended_at = _utcnow()
            # Also end the current vector
            if self._current_vector:
                self._current_vector.ended_at = _utcnow()
        self._current_step = None
        self._current_vector = None

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
        self.test_run.ended_at = _utcnow()

        # Close journal writer
        if self._journal is not None:
            self._journal.close()

        return self.test_run
