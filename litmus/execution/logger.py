"""Test run logging for accumulating measurements."""

import os
from datetime import UTC, datetime
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


class TestRunLogger:
    """Accumulates measurements during test run, produces TestRun."""

    def __init__(
        self,
        dut_serial: str,
        station_id: str,
        test_sequence_id: str,
        station_type: str | None = None,
        operator: str | None = None,
        test_phase: str = "production",
        run_id: UUID | str | None = None,
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
            dut=DUT(serial=dut_serial),
            station_id=station_id,
            station_type=station_type,
            operator=operator,
            test_sequence_id=test_sequence_id,
            test_phase=test_phase,
        )
        self._current_step: TestStep | None = None

    def start_step(self, name: str, description: str | None = None):
        """Begin a new test step."""
        self._current_step = TestStep(name=name, description=description)
        self.test_run.steps.append(self._current_step)

    def log_measurement(self, measurement: Measurement):
        """Add measurement to current step."""
        if self._current_step is None:
            # Auto-create step if none exists
            self.start_step(measurement.name)
        self._current_step.measurements.append(measurement)

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
        self._current_step = None

    def finalize(self) -> TestRun:
        """Complete test run and return result."""
        self.test_run.ended_at = _utcnow()
        return self.test_run
