"""Tests for measurement decorators."""

import pytest

from litmus.data.models import Measurement, Outcome
from litmus.execution.decorators import get_current_logger, measure, set_current_logger
from litmus.execution.logger import TestRunLogger
from litmus.models.test_config import Limit


class TestMeasureDecorator:
    """Tests for @measure decorator."""

    def test_basic_measure(self):
        @measure(name="test_voltage")
        def get_voltage():
            return 5.0

        result = get_voltage()
        assert isinstance(result, Measurement)
        assert result.name == "test_voltage"
        assert result.value == 5.0
        assert result.outcome == Outcome.PASS

    def test_measure_uses_function_name(self):
        @measure()
        def my_measurement():
            return 3.3

        result = my_measurement()
        assert result.name == "my_measurement"

    def test_measure_with_limit_pass(self):
        limit = Limit(low=4.5, high=5.5, units="V")

        @measure(name="voltage", limit=limit)
        def get_voltage():
            return 5.0

        result = get_voltage()
        assert result.outcome == Outcome.PASS
        assert result.limit_low == 4.5
        assert result.limit_high == 5.5
        assert result.units == "V"

    def test_measure_with_limit_fail_raises(self):
        limit = Limit(low=4.5, high=5.5, units="V")

        @measure(name="voltage", limit=limit)
        def get_voltage():
            return 6.0  # Above high limit

        with pytest.raises(AssertionError, match="FAILED"):
            get_voltage()

    def test_measure_with_limit_fail_no_raise(self):
        limit = Limit(low=4.5, high=5.5, units="V")

        @measure(name="voltage", limit=limit, raise_on_fail=False)
        def get_voltage():
            return 6.0  # Above high limit

        result = get_voltage()
        assert result.outcome == Outcome.FAIL

    def test_measure_with_float_value(self):
        @measure(name="precise")
        def get_value():
            return 1.23456789

        result = get_value()
        assert result.value == 1.23456789

    def test_measure_with_none_value(self):
        @measure(name="missing", raise_on_fail=False)
        def get_value():
            return None

        result = get_value()
        assert result.value is None
        assert result.outcome == Outcome.ERROR

    def test_measure_units_override(self):
        limit = Limit(low=0.0, high=10.0, units="V")

        @measure(name="voltage", limit=limit, units="mV")
        def get_voltage():
            return 5.0

        result = get_voltage()
        assert result.units == "mV"  # Overridden from limit

    def test_measure_spec_ref_from_limit(self):
        limit = Limit(low=0.0, high=10.0, units="V", spec_ref="SPEC-001")

        @measure(name="voltage", limit=limit)
        def get_voltage():
            return 5.0

        result = get_voltage()
        assert result.spec_ref == "SPEC-001"


class TestMeasureWithLogger:
    """Tests for @measure decorator with logger integration."""

    def setup_method(self):
        """Save and reset logger before each test."""
        self._prev_logger = get_current_logger()
        set_current_logger(None)

    def teardown_method(self):
        """Restore previous logger after each test."""
        set_current_logger(self._prev_logger)

    def test_measure_logs_to_logger(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        set_current_logger(logger)
        logger.start_step("test_step")

        @measure(name="voltage")
        def get_voltage():
            return 5.0

        get_voltage()

        # Measurements are stored in vectors within the step
        from litmus.execution._state import get_current_step

        step = get_current_step()
        assert step is not None
        assert len(step.vectors) == 1
        assert len(step.vectors[0].measurements) == 1
        assert step.vectors[0].measurements[0].name == "voltage"

    def test_measure_without_logger_works(self):
        assert get_current_logger() is None

        @measure(name="voltage")
        def get_voltage():
            return 5.0

        result = get_voltage()
        assert result.name == "voltage"

    def test_measure_auto_creates_step(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        set_current_logger(logger)

        @measure(name="voltage")
        def get_voltage():
            return 5.0

        get_voltage()

        assert len(logger.test_run.steps) == 1
        assert logger.test_run.steps[0].name == "voltage"
