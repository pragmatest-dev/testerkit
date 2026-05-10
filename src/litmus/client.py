"""Python client for submitting test results to Litmus.

This module provides a simple API for external tools (LabVIEW, TestStand,
custom scripts) to submit test results to the Litmus data store.

Basic usage:

    from litmus.client import LitmusClient

    client = LitmusClient()

    # Start a test run
    run = client.start_run(
        dut_serial="ABC123",
        station_id="station_001",
    )

    # Add a test step with measurements
    with run.step("measure_5v_rail") as step:
        step.measure("rail_voltage", 5.02, units="V", low=4.75, high=5.25)
        step.measure("rail_current", 0.150, units="A", high=0.5)

    # Finish and save
    run.finish()

For parametrized tests (multiple vectors per step):

    with run.step("voltage_sweep") as step:
        for voltage in [3.3, 5.0, 12.0]:
            with step.vector(input_voltage=voltage) as vec:
                output = measure_output(voltage)
                vec.measure("output_voltage", output, units="V")

The client automatically:
- Generates UUIDs for runs, steps, vectors
- Timestamps all events
- Evaluates limits and sets outcomes
- Saves to Parquet via the configured backend
"""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from litmus.data.backends.parquet import ParquetBackend
from litmus.data.models import DUT, Measurement, Outcome, RunSummary, TestRun, TestStep, TestVector


def _to_float(value: float | int | str | None) -> float | None:
    """Convert a numeric value to float."""
    if value is None:
        return None
    if isinstance(value, float):
        return value
    return float(value)


class VectorBuilder:
    """Builder for a single test vector within a step.

    Use via `StepBuilder.vector()` context manager.
    """

    def __init__(self, step: "StepBuilder", params: dict[str, Any], index: int):
        self._step = step
        self._vector = TestVector(
            test_step_id=step._test_step.id,
            index=index,
            params=params,
        )

    def measure(
        self,
        name: str,
        value: float | int | None,
        *,
        units: str | None = None,
        low: float | int | None = None,
        high: float | int | None = None,
        nominal: float | int | None = None,
        comparator: str = "GELE",
        spec_ref: str | None = None,
    ) -> Measurement:
        """Record a measurement with optional limit checking.

        Args:
            name: Measurement name (e.g., "rail_voltage")
            value: Measured value
            units: Unit of measurement (e.g., "V", "A", "ohm")
            low: Low limit (inclusive by default)
            high: High limit (inclusive by default)
            nominal: Nominal/expected value (for EQ/NE comparators)
            comparator: Limit comparison mode (default "GELE" = low <= value <= high)
                Options: EQ, NE, LT, LE, GT, GE, GELE, GELT, GTLE, GTLT
            spec_ref: Reference to specification (e.g., "SPEC-001")

        Returns:
            The created Measurement object with outcome evaluated.
        """
        m = Measurement(
            name=name,
            value=_to_float(value),
            units=units,
            limit_low=_to_float(low),
            limit_high=_to_float(high),
            limit_nominal=_to_float(nominal),
            limit_comparator=comparator,
            spec_ref=spec_ref,
        )

        # Evaluate limits
        has_limits = (
            m.limit_low is not None or m.limit_high is not None or m.limit_nominal is not None
        )
        if m.value is not None and has_limits:
            m.check_limit()
        elif m.value is not None:
            # No limit configured → recorder semantic ("ran, no judgment").
            # Matches ``logger.measure``'s default outcome.
            m.outcome = Outcome.DONE

        self._vector.measurements.append(m)

        # Update vector outcome if measurement failed
        if m.outcome == Outcome.FAILED:
            self._vector.outcome = Outcome.FAILED
        elif m.outcome == Outcome.ERRORED and self._vector.outcome != Outcome.FAILED:
            self._vector.outcome = Outcome.ERRORED

        return m

    def fail(self, message: str | None = None) -> None:
        """Mark this vector as failed."""
        self._vector.outcome = Outcome.FAILED
        if message:
            self._vector.error_message = message

    def skip(self, message: str | None = None) -> None:
        """Mark this vector as skipped."""
        self._vector.outcome = Outcome.SKIPPED
        if message:
            self._vector.error_message = message

    def _finish(self) -> TestVector:
        """Finalize the vector (called by context manager)."""
        self._vector.ended_at = datetime.now(UTC)
        return self._vector


class StepBuilder:
    """Builder for a test step.

    Use via `RunBuilder.step()` context manager.
    """

    def __init__(self, run: "RunBuilder", name: str, description: str | None = None):
        self._run = run
        self._test_step = TestStep(name=name, description=description)
        self._vector_index = 0
        self._default_vector: VectorBuilder | None = None

    @contextmanager
    def vector(self, **params: Any) -> Generator[VectorBuilder, None, None]:
        """Create a test vector with the given parameters.

        Use this for parametrized tests where you want to record
        which input values produced which measurements.

        Example:
            with step.vector(voltage=5.0, temperature=25) as vec:
                vec.measure("output", measured_value)
        """
        builder = VectorBuilder(self, params, self._vector_index)
        self._vector_index += 1
        try:
            yield builder
        finally:
            self._test_step.vectors.append(builder._finish())
            # Update step outcome based on vector
            v_outcome = builder._vector.outcome
            if v_outcome == Outcome.FAILED:
                self._test_step.outcome = Outcome.FAILED
            elif v_outcome == Outcome.ERRORED and self._test_step.outcome != Outcome.FAILED:
                self._test_step.outcome = Outcome.ERRORED

    def measure(
        self,
        name: str,
        value: float | int | None,
        *,
        units: str | None = None,
        low: float | int | None = None,
        high: float | int | None = None,
        nominal: float | int | None = None,
        comparator: str = "GELE",
        spec_ref: str | None = None,
    ) -> Measurement:
        """Record a measurement (creates default vector if needed).

        For simple tests without explicit vectors, measurements are
        collected into a single default vector.

        Args:
            name: Measurement name
            value: Measured value
            units: Unit of measurement
            low: Low limit
            high: High limit
            nominal: Nominal value
            comparator: Comparison mode (default "GELE")
            spec_ref: Specification reference

        Returns:
            The created Measurement object.
        """
        # Create default vector on first measurement
        if self._default_vector is None:
            self._default_vector = VectorBuilder(self, {}, self._vector_index)
            self._vector_index += 1

        return self._default_vector.measure(
            name=name,
            value=value,
            units=units,
            low=low,
            high=high,
            nominal=nominal,
            comparator=comparator,
            spec_ref=spec_ref,
        )

    def fail(self, message: str | None = None) -> None:
        """Mark this step as failed."""
        self._test_step.outcome = Outcome.FAILED
        if message:
            self._test_step.error_message = message

    def skip(self, message: str | None = None) -> None:
        """Mark this step as skipped."""
        self._test_step.outcome = Outcome.SKIPPED
        if message:
            self._test_step.error_message = message

    def _finish(self) -> TestStep:
        """Finalize the step (called by context manager)."""
        # Finalize default vector if it exists
        if self._default_vector is not None:
            self._test_step.vectors.append(self._default_vector._finish())
            if self._default_vector._vector.outcome == Outcome.FAILED:
                self._test_step.outcome = Outcome.FAILED

        self._test_step.ended_at = datetime.now(UTC)
        return self._test_step


class RunBuilder:
    """Builder for a test run.

    Use via `LitmusClient.start_run()`.
    """

    def __init__(
        self,
        client: "LitmusClient",
        dut_serial: str,
        station_id: str,
        *,
        dut_part_number: str | None = None,
        dut_revision: str | None = None,
        dut_lot_number: str | None = None,
        station_type: str | None = None,
        operator: str | None = None,
        test_phase: str | None = None,
    ):
        self._client = client
        self._test_run = TestRun(
            dut=DUT(
                serial=dut_serial,
                part_number=dut_part_number,
                revision=dut_revision,
                lot_number=dut_lot_number,
            ),
            station_id=station_id,
            station_type=station_type,
            operator_id=operator,
            test_phase=test_phase,
        )

    @property
    def id(self) -> UUID:
        """The test run ID."""
        return self._test_run.id

    @contextmanager
    def step(self, name: str, description: str | None = None) -> Generator[StepBuilder, None, None]:
        """Create a test step.

        Example:
            with run.step("measure_voltages", "Signal all power rails") as step:
                step.measure("5v_rail", 5.02, units="V", low=4.75, high=5.25)
        """
        builder = StepBuilder(self, name, description)
        try:
            yield builder
        finally:
            self._test_run.steps.append(builder._finish())
            # Update run outcome based on step
            if builder._test_step.outcome == Outcome.FAILED:
                self._test_run.outcome = Outcome.FAILED
            elif (
                builder._test_step.outcome == Outcome.ERRORED
                and self._test_run.outcome != Outcome.FAILED
            ):
                self._test_run.outcome = Outcome.ERRORED

    def finish(self) -> TestRun:
        """Finalize and save the test run.

        Returns:
            The completed TestRun object.
        """
        self._test_run.ended_at = datetime.now(UTC)
        self._client._backend.save_test_run(self._test_run)
        return self._test_run

    def abort(self, message: str | None = None) -> TestRun:
        """Abort the test run without saving.

        Args:
            message: Optional abort reason.

        Returns:
            The aborted TestRun object (not saved).
        """
        self._test_run.ended_at = datetime.now(UTC)
        self._test_run.outcome = Outcome.ABORTED
        return self._test_run


class LitmusClient:
    """Client for submitting test results to Litmus.

    Example:
        client = LitmusClient()

        run = client.start_run(
            dut_serial="SN12345",
            station_id="bench_1",
        )

        with run.step("voltage_check") as step:
            step.measure("vcc", 3.31, units="V", low=3.0, high=3.6)

        run.finish()
    """

    def __init__(self, data_dir: str | Path = "results"):
        """Initialize the client.

        Args:
            data_dir: Directory for Parquet result files.
        """
        self._backend = ParquetBackend(data_dir=Path(data_dir))

    def start_run(
        self,
        dut_serial: str,
        station_id: str,
        *,
        dut_part_number: str | None = None,
        dut_revision: str | None = None,
        dut_lot_number: str | None = None,
        station_type: str | None = None,
        operator: str | None = None,
        test_phase: str | None = None,
    ) -> RunBuilder:
        """Start a new test run.

        Args:
            dut_serial: Device under test serial number.
            station_id: Test station identifier.
            dut_part_number: Optional DUT part number.
            dut_revision: Optional DUT revision.
            dut_lot_number: Optional DUT lot/batch number.
            station_type: Optional station type.
            operator: Optional operator name/ID.
            test_phase: Test phase (e.g. "production", "characterization").

        Returns:
            A RunBuilder for adding steps and measurements.
        """
        return RunBuilder(
            self,
            dut_serial=dut_serial,
            station_id=station_id,
            dut_part_number=dut_part_number,
            dut_revision=dut_revision,
            dut_lot_number=dut_lot_number,
            station_type=station_type,
            operator=operator,
            test_phase=test_phase,
        )

    def list_runs(self, limit: int = 50) -> list[RunSummary]:
        """List recent test runs.

        Args:
            limit: Maximum number of runs to return.

        Returns:
            List of test run records.
        """
        return self._backend.list_runs(limit=limit)

    def get_run(self, run_id: str) -> RunSummary | None:
        """Get a test run by ID.

        Args:
            run_id: Test run ID (can be partial, at least 8 chars).

        Returns:
            Test run record or None if not found.
        """
        return self._backend.get_run(run_id)

    def get_measurements(self, run_id: str) -> list[dict]:
        """Get measurements for a test run.

        Args:
            run_id: Test run ID (can be partial).

        Returns:
            List of measurement records.
        """
        return self._backend.get_measurements(run_id)
