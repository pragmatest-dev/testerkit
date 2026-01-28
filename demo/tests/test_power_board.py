"""
Demo Test Suite: Power Board Validation

This test suite demonstrates the Litmus test framework by validating
a simulated DC-DC converter board. All instruments run in simulation
mode, so no real hardware is required.

Run with:
    cd demo
    pytest tests/ --dut-serial=DPB001-0001 -v

Or use the demo runner:
    python run_demo.py
"""

from decimal import Decimal

import pytest

from litmus.config.models import Limit
from litmus.execution.decorators import measure
from litmus.instruments import DMM

# =============================================================================
# Test Limits (derived from specifications)
# =============================================================================

LIMITS = {
    "input_voltage": Limit(
        low=Decimal("4.5"),
        high=Decimal("5.5"),
        nominal=Decimal("5.0"),
        units="V",
        spec_ref="PWR-IN-001",
    ),
    "input_current": Limit(
        low=Decimal("0.005"),
        high=Decimal("0.015"),
        nominal=Decimal("0.010"),
        units="A",
        spec_ref="PWR-IN-002",
    ),
    "output_voltage": Limit(
        low=Decimal("3.135"),  # 3.3V - 5%
        high=Decimal("3.465"),  # 3.3V + 5%
        nominal=Decimal("3.3"),
        units="V",
        spec_ref="PWR-OUT-001",
    ),
    "output_ripple": Limit(
        low=Decimal("0.0"),
        high=Decimal("0.080"),  # 50mV + 30mV tolerance
        nominal=Decimal("0.050"),
        units="V",
        spec_ref="PWR-OUT-002",
    ),
}


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def input_dmm():
    """DMM for input-side measurements."""
    with DMM(
        "SIM::DMM1",
        simulated=True,
        sim_values={
            "voltage": 5.02,  # Slightly above nominal
            "current": 0.0095,  # Within spec
        },
    ) as dmm:
        yield dmm


@pytest.fixture
def output_dmm():
    """DMM for output-side measurements."""
    with DMM(
        "SIM::DMM2",
        simulated=True,
        sim_values={
            "voltage": 3.31,  # Within spec
            "current": 0.098,
        },
    ) as dmm:
        yield dmm


# =============================================================================
# Test Cases
# =============================================================================


class TestInputPower:
    """Tests for input power characteristics."""

    def test_input_voltage(self, litmus_step, input_dmm):
        """Verify input voltage is within specification."""

        @measure(name="input_voltage", limit=LIMITS["input_voltage"])
        def read_input_voltage():
            return input_dmm.measure_dc_voltage()

        result = read_input_voltage()
        assert result.pass_fail.value == "pass"

    def test_input_current(self, litmus_step, input_dmm):
        """Verify quiescent input current is within specification."""

        @measure(name="input_current", limit=LIMITS["input_current"])
        def read_input_current():
            return input_dmm.measure_dc_current()

        result = read_input_current()
        assert result.pass_fail.value == "pass"


class TestOutputPower:
    """Tests for output power characteristics."""

    def test_output_voltage(self, litmus_step, output_dmm):
        """Verify regulated output voltage is within specification."""

        @measure(name="output_voltage", limit=LIMITS["output_voltage"])
        def read_output_voltage():
            return output_dmm.measure_dc_voltage()

        result = read_output_voltage()
        assert result.pass_fail.value == "pass"

    def test_output_voltage_stability(self, litmus_step, output_dmm):
        """Verify output voltage stability over multiple readings."""
        readings = []

        for i in range(3):

            @measure(
                name=f"output_voltage_sample_{i + 1}",
                limit=LIMITS["output_voltage"],
            )
            def read_voltage():
                return output_dmm.measure_dc_voltage()

            result = read_voltage()
            readings.append(result.value)

        # All readings should be within spec
        assert all(
            LIMITS["output_voltage"].low <= r <= LIMITS["output_voltage"].high for r in readings
        )


class TestEfficiency:
    """Tests for power conversion efficiency."""

    def test_efficiency_calculation(self, litmus_step, input_dmm, output_dmm):
        """Calculate and verify power conversion efficiency."""
        # Read input power
        v_in = input_dmm.measure_dc_voltage()  # 5.02V
        i_in = Decimal("0.080")  # Input current at load (calculated for ~85% eff)

        # Read output power
        v_out = output_dmm.measure_dc_voltage()  # 3.31V
        i_out = Decimal("0.100")  # 100mA load

        # Calculate efficiency: P_out / P_in * 100
        # Expected: (3.31 * 0.1) / (5.02 * 0.08) * 100 = 82.4%
        p_in = v_in * i_in
        p_out = v_out * i_out
        efficiency = (p_out / p_in) * 100

        efficiency_limit = Limit(
            low=Decimal("75"),
            high=Decimal("100"),
            nominal=Decimal("85"),
            units="%",
            spec_ref="PWR-EFF-001",
        )

        @measure(name="efficiency", limit=efficiency_limit)
        def get_efficiency():
            return efficiency

        result = get_efficiency()
        assert result.pass_fail.value == "pass"
