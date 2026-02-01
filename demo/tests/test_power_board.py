"""
Demo Test Suite: Power Board Validation
========================================

This is the GOLDEN EXAMPLE demonstrating Litmus best practices:

1. CONDITIONS (stimulus) come from vectors in config.yaml
2. LIMITS (pass/fail) come from config.yaml (derived from spec)
3. Tests SET UP conditions, MEASURE results, RETURN values
4. Framework handles limit checking, retry, logging, traceability

NO HARDCODED VALUES - everything is configurable.

PATTERNS DEMONSTRATED:
- Pattern 1: Simple single measurement
- Pattern 2: Multiple vectors with retry
- Pattern 3: Explicit vector list
- Pattern 4: Product expansion (Cartesian product)
- Pattern 5: Nested loops with change detection
- Pattern 6: Range expansion
- Pattern 7: Multiple measurements (dict return)
- Pattern 8: One-sided limits (max only)
- Pattern 9: Streaming measurements (yield)
- Pattern 10: Skipped test (thermal)

Run with:
    cd demo
    pytest tests/test_power_board.py --station=demo_station_001 --mock-instruments -v
"""

import time

import pytest

from litmus.execution import litmus_test


# =============================================================================
# Pattern 1: Simple Single Measurement
# =============================================================================
@litmus_test
def test_output_voltage_no_load(vector, psu, dmm):
    """Verify output voltage at no load.

    Vector: vin=5.0 (from config.yaml)
    Limit: 3.234V to 3.366V (from config.yaml, derived from spec ±2% + guardband)

    This is the simplest pattern:
    - Get conditions from vector
    - Set up stimulus
    - Measure and return
    """
    vin = vector.get("vin", 5.0)

    psu.set_voltage(vin)
    psu.set_current_limit(0.1)
    psu.enable_output()

    return dmm.measure_dc_voltage()


# =============================================================================
# Pattern 2: Single Vector with Retry (configured in config.yaml)
# =============================================================================
@litmus_test
def test_output_voltage_full_load(vector, psu, dmm, eload):
    """Verify output voltage at full load.

    Vector: vin=5.0, load_current=0.8 (from config.yaml)
    Limit: 3.201V to 3.399V (spec ±3% for full load)
    Retry: max_attempts=3, delay=0.5s (from config.yaml)

    If measurement fails, framework automatically retries.
    """
    vin = vector.get("vin", 5.0)
    load = vector.get("load_current", 0.8)

    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    vout = dmm.measure_dc_voltage()

    eload.disable()
    return vout


# =============================================================================
# Pattern 3: Explicit Vector List
# =============================================================================
@litmus_test
def test_load_regulation(vector, psu, dmm, eload):
    """Measure output at multiple load points.

    Vectors defined explicitly in config.yaml:
    - {vin: 5.0, load_current: 0.1}
    - {vin: 5.0, load_current: 0.4}
    - {vin: 5.0, load_current: 0.8}

    Test runs 3 times, once per vector.
    """
    vin = vector.get("vin", 5.0)
    load = vector["load_current"]

    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    vout = dmm.measure_dc_voltage()

    eload.disable()
    return vout


# =============================================================================
# Pattern 4: Product Expansion (Cartesian Product)
# =============================================================================
@litmus_test
def test_load_sweep(vector, psu, dmm, eload):
    """Sweep VIN and load using product expansion.

    Config specifies: expand=product, vin=[4.75, 5.0, 5.5], load_current=[0.1, 0.4, 0.8]
    Results in 3×3 = 9 vectors.

    Uses vector.changed() to detect when parameters change.
    """
    vin = vector["vin"]
    load = vector["load_current"]

    # Only reconfigure PSU when VIN changes (optimization)
    if vector.changed("vin"):
        psu.set_voltage(vin)
        psu.set_current_limit(1.0)
        psu.enable_output()

    # Load changes more frequently
    eload.set_current(load)
    eload.enable()

    vout = dmm.measure_dc_voltage()

    eload.disable()
    return vout


# =============================================================================
# Pattern 5: Nested Loops with Change Detection
# =============================================================================
@litmus_test
def test_temp_load_matrix(vector, psu, dmm, eload):
    """Full characterization matrix with nested loops.

    Config specifies nested expansion:
    - Outer loop: temperature=[25, 85]
    - Inner loop: load_current=[0.1, 0.5, 0.8]

    Uses vector.changed() to detect when outer parameter changes.
    In real test, you'd adjust thermal chamber when temperature changes.
    """
    temp = vector["temperature"]
    load = vector["load_current"]

    # Temperature is outer loop - changes less frequently
    if vector.changed("temperature"):
        # In production: set_chamber_temperature(temp)
        # Here we just log it
        pass

    psu.set_voltage(5.0)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    vout = dmm.measure_dc_voltage()

    eload.disable()
    return vout


# =============================================================================
# Pattern 6: Range Expansion
# =============================================================================
@litmus_test
def test_line_regulation(vector, psu, dmm, eload):
    """Sweep input voltage using range expansion.

    Config specifies: expand=range, start=4.5, stop=6.0, step=0.5
    Results in vectors: vin=[4.5, 5.0, 5.5, 6.0]
    """
    vin = vector["vin"]

    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    # Fixed load for line regulation test
    eload.set_current(0.5)
    eload.enable()

    vout = dmm.measure_dc_voltage()

    eload.disable()
    return vout


# =============================================================================
# Pattern 7: Multiple Measurements (Dict Return)
# =============================================================================
@litmus_test
def test_power_analysis(vector, psu, dmm, eload):
    """Measure multiple values and return as dict.

    Returns: {"input_power": W, "output_power": W, "efficiency": %}

    Each key gets checked against its own limit in config.yaml.
    This is the pattern for calculated values and multi-point measurements.
    """
    vin = vector.get("vin", 5.0)
    load = vector.get("load_current", 0.5)

    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    # Measure input
    v_in = float(psu.measure_voltage())
    i_in = float(psu.measure_current())

    # Measure output
    v_out = float(dmm.measure_dc_voltage())
    i_out = load  # Load current we commanded

    eload.disable()

    # Calculate power and efficiency
    p_in = v_in * i_in
    p_out = v_out * i_out
    efficiency = (p_out / p_in * 100) if p_in > 0 else 0

    # Return multiple measurements - framework checks each against its limit
    return {
        "input_power": p_in,
        "output_power": p_out,
        "efficiency": efficiency,
    }


# =============================================================================
# Pattern 8: One-Sided Limit (Max Only)
# =============================================================================
@litmus_test
def test_quiescent_current(vector, psu):
    """Verify quiescent current (no load).

    Limit uses comparator=LE (less than or equal) for upper-bound-only check.
    Spec: max 10mA quiescent current.
    """
    vin = vector.get("vin", 5.0)

    psu.set_voltage(vin)
    psu.set_current_limit(0.05)  # Low limit - no load
    psu.enable_output()

    # Measure input current (no load attached)
    current_ma = float(psu.measure_current()) * 1000
    return current_ma


# =============================================================================
# Pattern 9: Streaming Measurements (Yield)
# =============================================================================
@litmus_test
def test_stability_over_time(vector, psu, dmm, eload):
    """Monitor output stability over time using yield.

    Yield pattern allows streaming multiple measurements from a single test.
    Each yielded value is logged and checked against limits.

    This is the pattern for:
    - Time-series data (burn-in, soak tests)
    - Progress reporting during long tests
    - Data arrays (waveform capture)
    """
    vin = vector.get("vin", 5.0)
    load = vector.get("load_current", 0.5)
    sample_count = vector.get("sample_count", 5)

    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    # Stream measurements over time
    for i in range(sample_count):
        voltage = dmm.measure_dc_voltage()
        yield {"voltage": float(voltage)}
        time.sleep(0.1)  # Sample interval

    eload.disable()


# =============================================================================
# Pattern 10: Manual Test (Requires Operator Action)
# =============================================================================
@litmus_test
def test_thermal_shutdown(vector, psu, dmm, eload):
    """Verify thermal protection (manual test).

    Config includes prompt_before to instruct operator.
    This test is typically skipped in automated runs.

    In production, this would verify the LDO shuts down
    when overheated, protecting the circuit.
    """
    vin = vector.get("vin", 5.0)
    load = vector.get("load_current", 0.5)

    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    # After thermal shutdown, output should collapse
    vout = dmm.measure_dc_voltage()

    eload.disable()
    return float(vout)
