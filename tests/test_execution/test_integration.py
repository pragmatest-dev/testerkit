"""Integration tests for the full measurement flow."""

from litmus.data.models import Outcome
from litmus.execution.decorators import measure
from litmus.execution.plugin import LitmusSequence
from litmus.instruments import Mock
from litmus.models.config import Limit


class FakeDMM:
    """Fake DMM class for testing."""

    def __init__(self, resource: str = ""):
        self.resource = resource
        self._connected = False

    def connect(self):
        self._connected = True

    def disconnect(self):
        self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    def measure_voltage(self):
        pass

    def measure_current(self):
        pass


class TestFullFlow(LitmusSequence):
    """Integration tests using mock instruments.

    Inheriting ``LitmusSequence`` triggers the plugin's per-method step
    lifecycle — each ``test_*`` runs inside its own logger step.
    """

    def test_measure_with_mocked_dmm(self, logger):
        """Integration test: measure with limit check, log to parquet."""
        limit = Limit(low=4.5, high=5.5, units="V")

        with Mock(FakeDMM, measure_voltage=5.0) as dmm:

            @measure(name="rail_5v", limit=limit)
            def measure_voltage():
                return dmm.measure_voltage()

            result = measure_voltage()
            assert result.outcome == Outcome.PASS
            assert result.value == 5.0

    def test_multiple_measurements(self, logger):
        """Test multiple measurements in a single step."""
        voltage_limit = Limit(low=4.5, high=5.5, units="V")
        current_limit = Limit(low=0.05, high=0.15, units="A")

        with Mock(FakeDMM, measure_voltage=5.0, measure_current=0.1) as dmm:

            @measure(name="rail_5v", limit=voltage_limit)
            def measure_voltage():
                return dmm.measure_voltage()

            @measure(name="input_current", limit=current_limit)
            def measure_current():
                return dmm.measure_current()

            v_result = measure_voltage()
            i_result = measure_current()

            assert v_result.outcome == Outcome.PASS
            assert i_result.outcome == Outcome.PASS

    def test_measurement_failure(self, logger):
        """Test that measurement failure is properly logged."""
        limit = Limit(low=4.5, high=5.5, units="V")

        with Mock(FakeDMM, measure_voltage=6.0) as dmm:

            @measure(name="rail_5v", limit=limit, raise_on_fail=False)
            def measure_voltage():
                return dmm.measure_voltage()

            result = measure_voltage()
            assert result.outcome == Outcome.FAIL
            # Check the current step's outcome (last step in shared session logger)
            current_step = logger.test_run.steps[-1]
            assert current_step.outcome == Outcome.FAIL
