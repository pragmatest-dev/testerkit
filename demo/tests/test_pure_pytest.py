"""
Pure Pytest Example: Without @litmus_test Decorator
====================================================

This file demonstrates using Litmus with PURE PYTEST - no decorator.
Use this approach when you need:
- Full control over test flow
- Custom assertion logic
- Integration with existing test suites
- Gradual migration to Litmus

The litmus_logger fixture provides manual measurement logging.
Results are still saved to Parquet with full traceability.

Run with:
    cd demo
    pytest tests/test_pure_pytest.py --station=demo_station_001 --mock-instruments -v
"""

import pytest

from litmus.config.models import Limit


class TestPurePytest:
    """Pure pytest tests with manual Litmus logging.

    These tests use the litmus_logger fixture directly instead of
    the @litmus_test decorator. This gives you full control while
    still getting Litmus benefits:
    - Structured measurement logging
    - Parquet storage
    - Traceability fields
    """

    def test_basic_measurement(self, psu, dmm, litmus_logger):
        """Basic measurement with manual limit checking.

        Shows the explicit pattern:
        1. Set up stimulus
        2. Signal
        3. Log with litmus_logger.measure()
        4. Assert manually
        """
        # Define limit (could also load from config)
        limit = Limit(
            low=3.2,
            high=3.4,
            nominal=3.3,
            units="V",
            spec_ref="output_voltage @ no load",
        )

        # Set up stimulus
        psu.set_voltage(5.0)
        psu.set_current_limit(0.5)
        psu.enable_output()

        # Signal
        vout = float(dmm.measure_dc_voltage())

        # Log measurement (this is what @litmus_test does automatically)
        litmus_logger.measure(
            name="output_voltage",
            value=vout,
            limit=limit,
            dut_pin="TP_VOUT",  # Traceability
        )

        # Assert (you control the assertion)
        assert limit.low <= vout <= limit.high, f"Output {vout}V out of range"

    def test_multiple_measurements(self, psu, dmm, eload, litmus_logger):
        """Log multiple measurements in one test.

        Shows how to log several values with their own limits.
        """
        psu.set_voltage(5.0)
        psu.set_current_limit(1.0)
        psu.enable_output()

        eload.set_current(0.5)
        eload.enable()

        # Log input measurements
        v_in = float(psu.measure_voltage())
        i_in = float(psu.measure_current())

        litmus_logger.measure(
            name="input_voltage",
            value=v_in,
            limit=Limit(low=4.8, high=5.2, nominal=5.0, units="V"),
            dut_pin="TP_VIN",
        )

        litmus_logger.measure(
            name="input_current",
            value=i_in,
            limit=Limit(low=0, high=1.0, nominal=0.5, units="A"),
            dut_pin="J1_VIN",
        )

        # Log output measurement
        v_out = float(dmm.measure_dc_voltage())

        litmus_logger.measure(
            name="output_voltage",
            value=v_out,
            limit=Limit(low=3.2, high=3.4, nominal=3.3, units="V"),
            dut_pin="TP_VOUT",
        )

        eload.disable()

        # Calculate and log derived value
        efficiency = (v_out * 0.5) / (v_in * i_in) * 100 if (v_in * i_in) > 0 else 0

        litmus_logger.measure(
            name="efficiency",
            value=efficiency,
            limit=Limit(low=60, high=100, nominal=66, units="%"),
        )

        # Assertions
        assert v_out >= 3.2, f"Output voltage {v_out}V below minimum"
        assert efficiency >= 60, f"Efficiency {efficiency}% below spec"

    def test_parametrized_sweep(self, psu, dmm, eload, litmus_logger):
        """Manual sweep without decorator.

        Shows how to implement vector-like behavior manually.
        """
        loads = [0.1, 0.3, 0.5, 0.8]

        psu.set_voltage(5.0)
        psu.set_current_limit(1.0)
        psu.enable_output()

        results = []

        for load in loads:
            eload.set_current(load)
            eload.enable()

            vout = float(dmm.measure_dc_voltage())

            # Log each measurement with load context
            litmus_logger.measure(
                name=f"vout_at_{int(load*1000)}mA",
                value=vout,
                limit=Limit(low=3.1, high=3.5, nominal=3.3, units="V"),
                dut_pin="TP_VOUT",
            )

            results.append({"load": load, "vout": vout})

        eload.disable()

        # Calculate regulation
        voltages = [r["vout"] for r in results]
        regulation_mv = (max(voltages) - min(voltages)) * 1000

        litmus_logger.measure(
            name="load_regulation",
            value=regulation_mv,
            limit=Limit(low=0, high=50, nominal=10, units="mV"),
        )

        assert regulation_mv <= 50, f"Load regulation {regulation_mv}mV exceeds spec"


# =============================================================================
# Parametrized Tests (pytest native)
# =============================================================================


@pytest.mark.parametrize("vin,expected_vout", [
    (4.75, 3.3),
    (5.0, 3.3),
    (5.5, 3.3),
])
def test_line_regulation_parametrized(vin, expected_vout, psu, dmm, litmus_logger):
    """Pytest parametrize with Litmus logging.

    Shows how to use pytest's native parametrize with litmus_logger.
    Each parameter combination runs as a separate test.
    """
    psu.set_voltage(vin)
    psu.set_current_limit(0.5)
    psu.enable_output()

    vout = float(dmm.measure_dc_voltage())

    # Log with context from parametrize
    litmus_logger.measure(
        name="output_voltage",
        value=vout,
        limit=Limit(low=3.2, high=3.4, nominal=expected_vout, units="V"),
        dut_pin="TP_VOUT",
    )

    tolerance = 0.1
    assert abs(vout - expected_vout) <= tolerance, \
        f"Output {vout}V at Vin={vin}V outside tolerance"
