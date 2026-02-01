"""Tests for TestRunLogger."""

from decimal import Decimal

from litmus.data.models import Measurement, Outcome
from litmus.execution.logger import TestRunLogger


class TestTestRunLogger:
    """Tests for TestRunLogger."""

    def test_init(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            test_sequence_id="test_suite",
        )
        assert logger.test_run.dut.serial == "SN001"
        assert logger.test_run.station_id == "station_001"
        assert logger.test_run.test_sequence_id == "test_suite"
        assert logger.test_run.outcome == Outcome.PASS

    def test_init_with_all_options(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            test_sequence_id="test_suite",
            station_type="production",
            operator_id="John Doe",
            test_phase="debug",
        )
        assert logger.test_run.station_type == "production"
        assert logger.test_run.operator_id == "John Doe"
        assert logger.test_run.test_phase == "debug"

    def test_start_step(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            test_sequence_id="test",
        )
        logger.start_step("measure_voltage", description="Measure 5V rail")

        assert len(logger.test_run.steps) == 1
        assert logger.test_run.steps[0].name == "measure_voltage"
        assert logger.test_run.steps[0].description == "Measure 5V rail"
        assert logger._current_step is not None

    def test_log_measurement(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            test_sequence_id="test",
        )
        logger.start_step("test_step")

        m = Measurement(name="voltage", value=Decimal("5.0"), outcome=Outcome.PASS)
        logger.log_measurement(m)

        # Measurements are stored in vectors within the step
        assert len(logger._current_step.vectors) == 1
        assert len(logger._current_step.vectors[0].measurements) == 1
        assert logger._current_step.vectors[0].measurements[0].name == "voltage"

    def test_log_measurement_auto_creates_step(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            test_sequence_id="test",
        )

        m = Measurement(name="voltage", value=Decimal("5.0"), outcome=Outcome.PASS)
        logger.log_measurement(m)

        assert len(logger.test_run.steps) == 1
        assert logger.test_run.steps[0].name == "voltage"

    def test_log_measurement_fail_propagates(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            test_sequence_id="test",
        )
        logger.start_step("test_step")

        m = Measurement(name="voltage", value=Decimal("6.0"), outcome=Outcome.FAIL)
        logger.log_measurement(m)

        assert logger._current_step.outcome == Outcome.FAIL
        assert logger.test_run.outcome == Outcome.FAIL

    def test_log_measurement_error_propagates(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            test_sequence_id="test",
        )
        logger.start_step("test_step")

        m = Measurement(name="voltage", value=None, outcome=Outcome.ERROR)
        logger.log_measurement(m)

        assert logger._current_step.outcome == Outcome.ERROR
        assert logger.test_run.outcome == Outcome.ERROR

    def test_fail_overrides_error(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            test_sequence_id="test",
        )
        logger.start_step("test_step")

        m1 = Measurement(name="voltage", value=None, outcome=Outcome.ERROR)
        m2 = Measurement(name="current", value=Decimal("6.0"), outcome=Outcome.FAIL)
        logger.log_measurement(m1)
        logger.log_measurement(m2)

        # FAIL should override ERROR
        assert logger._current_step.outcome == Outcome.FAIL
        assert logger.test_run.outcome == Outcome.FAIL

    def test_end_step(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            test_sequence_id="test",
        )
        logger.start_step("test_step")
        logger.end_step()

        assert logger._current_step is None
        assert logger.test_run.steps[0].ended_at is not None

    def test_finalize(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            test_sequence_id="test",
        )
        logger.start_step("test_step")
        logger.end_step()

        test_run = logger.finalize()

        assert test_run.ended_at is not None
        assert test_run is logger.test_run

    def test_multiple_steps(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            test_sequence_id="test",
        )

        logger.start_step("step1")
        m1 = Measurement(name="voltage", value=Decimal("5.0"), outcome=Outcome.PASS)
        logger.log_measurement(m1)
        logger.end_step()

        logger.start_step("step2")
        m2 = Measurement(name="current", value=Decimal("0.1"), outcome=Outcome.PASS)
        logger.log_measurement(m2)
        logger.end_step()

        assert len(logger.test_run.steps) == 2
        assert logger.test_run.steps[0].name == "step1"
        assert logger.test_run.steps[1].name == "step2"
