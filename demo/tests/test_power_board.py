"""
Demo Test Suite: Power Board Validation

This test suite demonstrates the Litmus test framework by validating
a simulated DC-DC converter board. All instruments run in simulation
mode, so no real hardware is required.

Test configuration is loaded from config.yaml in the same directory.

Run with:
    cd demo
    pytest tests/ --dut-serial=DPB001-0001 -v

Or use the demo runner:
    python run_demo.py
"""

from decimal import Decimal

import pytest

from litmus.execution import litmus_test
from litmus.instruments import DMM


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
# Tests - Using @litmus_test decorator with file-based config
#
# Configuration (vectors, limits, retry) is loaded from config.yaml
# in the same directory. The decorator auto-discovers the config file.
# =============================================================================


@litmus_test
def test_input_voltage(vector, input_dmm):
    """Verify input voltage is within specification."""
    return input_dmm.measure_dc_voltage()


@litmus_test
def test_input_current(vector, input_dmm):
    """Verify quiescent input current is within specification."""
    return input_dmm.measure_dc_current()


@litmus_test
def test_output_voltage(vector, output_dmm):
    """Verify regulated output voltage is within specification."""
    return output_dmm.measure_dc_voltage()


@litmus_test
def test_output_stability(vector, output_dmm):
    """Verify output voltage stability over multiple readings.

    Vectors are defined in config.yaml: [{sample: 1}, {sample: 2}, {sample: 3}]
    """
    return output_dmm.measure_dc_voltage()


@litmus_test
def test_efficiency(vector, input_dmm, output_dmm):
    """Calculate and verify power conversion efficiency."""
    # Read input power
    v_in = input_dmm.measure_dc_voltage()  # 5.02V
    i_in = Decimal("0.080")  # Input current at load

    # Read output power
    v_out = output_dmm.measure_dc_voltage()  # 3.31V
    i_out = Decimal("0.100")  # 100mA load

    # Calculate efficiency: P_out / P_in * 100
    p_in = v_in * i_in
    p_out = v_out * i_out
    efficiency = (p_out / p_in) * 100

    return efficiency


# =============================================================================
# Vector Expansion Examples (config-driven)
# =============================================================================


@litmus_test(raise_on_fail=False)
def test_load_sweep(vector, output_dmm):
    """Sweep through multiple load conditions.

    Vectors from config.yaml: expand=product, load_percent=[0, 50, 100]
    Creates 3 test vectors.
    No limits = characterization mode (all measurements recorded as PASS).
    """
    # In real test, would set load to vector["load_percent"]
    return output_dmm.measure_dc_voltage()


@litmus_test(raise_on_fail=False)
def test_temp_load_matrix(vector, output_dmm):
    """Test across temperature and load matrix.

    Vectors from config.yaml: nested loops
    - temperature: [25, 85] (outer loop)
    - load: [0, 50, 100] (inner loop)
    Creates 2 x 3 = 6 vectors.

    Use vector.changed() to detect outer loop transitions.
    """
    if vector.changed("temperature"):
        # Would set chamber temperature here
        pass

    return output_dmm.measure_dc_voltage()


# =============================================================================
# Sequence-Referenced Tests (for power_board_smoke.yaml)
# =============================================================================


@litmus_test
def test_measure_5v_rail(vector, input_dmm):
    """Verify 5V rail is present and within spec.

    Referenced by: sequences/power_board_smoke.yaml
    """
    return input_dmm.measure_dc_voltage()


@litmus_test
def test_measure_3v3_rail(vector, output_dmm):
    """Verify 3.3V rail is present and within spec.

    Referenced by: sequences/power_board_smoke.yaml
    """
    return output_dmm.measure_dc_voltage()


@litmus_test
def test_load_5v(vector, input_dmm):
    """Test 5V rail under load.

    Referenced by: sequences/power_board_full.yaml
    """
    # In real test, electronic load would be enabled here
    return input_dmm.measure_dc_voltage()
