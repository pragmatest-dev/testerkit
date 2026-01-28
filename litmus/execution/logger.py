"""Test run logging for accumulating measurements."""

from datetime import UTC, datetime

from litmus.data.models import DUT, Measurement, PassFail, TestRun, TestStep


def _utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(UTC)


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
    ):
        self.test_run = TestRun(
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
        if measurement.pass_fail == PassFail.FAIL:
            self._current_step.pass_fail = PassFail.FAIL
            self.test_run.pass_fail = PassFail.FAIL
        elif measurement.pass_fail == PassFail.ERROR:
            if self._current_step.pass_fail != PassFail.FAIL:
                self._current_step.pass_fail = PassFail.ERROR
            if self.test_run.pass_fail != PassFail.FAIL:
                self.test_run.pass_fail = PassFail.ERROR

    def end_step(self):
        """Finalize current step."""
        if self._current_step:
            self._current_step.ended_at = _utcnow()
        self._current_step = None

    def finalize(self) -> TestRun:
        """Complete test run and return result."""
        self.test_run.ended_at = _utcnow()
        return self.test_run
