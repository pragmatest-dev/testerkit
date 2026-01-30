"""Integration tests for the full measurement flow."""

from decimal import Decimal

from litmus.config.models import Limit
from litmus.data.models import Outcome
from litmus.execution.decorators import measure
from litmus.instruments import DMM


class TestFullFlow:
    """Integration tests using simulated instruments."""

    def test_measure_with_simulated_dmm(self, litmus_logger, litmus_step):
        """Integration test: measure with limit check, log to parquet."""
        limit = Limit(low=Decimal("4.5"), high=Decimal("5.5"), units="V")

        with DMM("TCPIP::192.168.1.100::INSTR", simulate=True, sim_config={"voltage": 5.0}) as dmm:

            @measure(name="rail_5v", limit=limit)
            def measure_voltage():
                return dmm.measure_voltage()

            result = measure_voltage()
            assert result.outcome == Outcome.PASS
            assert result.value == Decimal("5.0")

    def test_multiple_measurements(self, litmus_logger, litmus_step):
        """Test multiple measurements in a single step."""
        voltage_limit = Limit(low=Decimal("4.5"), high=Decimal("5.5"), units="V")
        current_limit = Limit(low=Decimal("0.05"), high=Decimal("0.15"), units="A")

        with DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 5.0, "current": 0.1},
        ) as dmm:

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

    def test_measurement_failure(self, litmus_logger, litmus_step):
        """Test that measurement failure is properly logged."""
        limit = Limit(low=Decimal("4.5"), high=Decimal("5.5"), units="V")

        with DMM("TCPIP::192.168.1.100::INSTR", simulate=True, sim_config={"voltage": 6.0}) as dmm:

            @measure(name="rail_5v", limit=limit, raise_on_fail=False)
            def measure_voltage():
                return dmm.measure_voltage()

            result = measure_voltage()
            assert result.outcome == Outcome.FAIL
            assert litmus_logger.test_run.outcome == Outcome.FAIL
