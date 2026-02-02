"""Test run logging for accumulating measurements."""

import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from litmus.data.models import DUT, Measurement, Outcome, TestRun, TestStep


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
    """Accumulates measurements during test run, produces TestRun."""

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
        self._current_vector = None  # For simple logging without harness
        self._run_context = RunContext(self.test_run)

    @property
    def run_context(self) -> RunContext:
        """Get the run context for adding custom metadata."""
        return self._run_context

    def start_step(self, name: str, description: str | None = None):
        """Begin a new test step."""
        from litmus.data.models import TestVector

        self._current_step = TestStep(name=name, description=description)
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
        """Complete test run and return result."""
        self.test_run.ended_at = _utcnow()
        return self.test_run
