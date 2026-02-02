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
- Pattern 11: Waveform capture (oscilloscope)
- Pattern 12: Callable limits (temperature-dependent)
- Pattern 13: Context traceability (configure/observe)
- Pattern 14: Large data observation (waveform stored in _ref/)

Run with:
    cd demo
    pytest tests/test_power_board.py --station=demo_station_001 --mock-instruments -v
"""

import random
import time

import pytest

from litmus.execution import litmus_test


# =============================================================================
# Pattern 1: Simple Single Measurement
# =============================================================================
@litmus_test
def test_output_voltage_no_load(context, psu, dmm):
    """Verify output voltage at no load.

    Vector params: vin=5.0 (from config.yaml)
    Limit: 3.234V to 3.366V (from config.yaml, derived from spec ±2% + guardband)

    This is the simplest pattern:
    - Get conditions from context (vector params)
    - Set up stimulus
    - Measure and return
    """
    vin = context.get_in("vin", 5.0)

    psu.set_voltage(vin)
    psu.set_current_limit(0.1)
    psu.enable_output()

    return dmm.measure_dc_voltage()


# =============================================================================
# Pattern 2: Single Vector with Retry (configured in config.yaml)
# =============================================================================
@litmus_test
def test_output_voltage_full_load(context, psu, dmm, eload):
    """Verify output voltage at full load.

    Vector params: vin=5.0, load_current=0.8 (from config.yaml)
    Limit: 3.201V to 3.399V (spec ±3% for full load)
    Retry: max_attempts=3, delay=0.5s (from config.yaml)

    If measurement fails, framework automatically retries.
    """
    vin = context.get_in("vin", 5.0)
    load = context.get_in("load_current", 0.8)

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
def test_load_regulation(context, psu, dmm, eload):
    """Measure output at multiple load points.

    Vectors defined explicitly in config.yaml:
    - {vin: 5.0, load_current: 0.1}
    - {vin: 5.0, load_current: 0.4}
    - {vin: 5.0, load_current: 0.8}

    Test runs 3 times, once per vector.
    """
    vin = context.get_in("vin", 5.0)
    load = context.inputs["load_current"]

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
def test_load_sweep(context, psu, dmm, eload):
    """Sweep VIN and load using product expansion.

    Config specifies: expand=product, vin=[4.75, 5.0, 5.5], load_current=[0.1, 0.4, 0.8]
    Results in 3×3 = 9 vectors.

    Uses context.changed() to detect when parameters change.
    """
    vin = context.inputs["vin"]
    load = context.inputs["load_current"]

    # Only reconfigure PSU when VIN changes (optimization)
    if context.changed("vin"):
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
def test_temp_load_matrix(context, psu, dmm, eload):
    """Full characterization matrix with nested loops.

    Config specifies nested expansion:
    - Outer loop: temperature=[25, 85]
    - Inner loop: load_current=[0.1, 0.5, 0.8]

    Uses context.changed() to detect when outer parameter changes.
    In real test, you'd adjust thermal chamber when temperature changes.
    """
    temp = context.inputs["temperature"]
    load = context.inputs["load_current"]

    # Temperature is outer loop - changes less frequently
    if context.changed("temperature"):
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
def test_line_regulation(context, psu, dmm, eload):
    """Sweep input voltage using range expansion.

    Config specifies: expand=range, start=4.5, stop=6.0, step=0.5
    Results in vectors: vin=[4.5, 5.0, 5.5, 6.0]
    """
    vin = context.inputs["vin"]

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
def test_power_analysis(context, psu, dmm, eload):
    """Measure multiple values and return as dict.

    Returns: {"input_power": W, "output_power": W, "efficiency": %}

    Each key gets checked against its own limit in config.yaml.
    This is the pattern for calculated values and multi-point measurements.
    """
    vin = context.get_in("vin", 5.0)
    load = context.get_in("load_current", 0.5)

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
def test_quiescent_current(context, psu):
    """Verify quiescent current (no load).

    Limit uses comparator=LE (less than or equal) for upper-bound-only check.
    Spec: max 10mA quiescent current.
    """
    vin = context.get_in("vin", 5.0)

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
def test_stability_over_time(context, psu, dmm, eload):
    """Monitor output stability over time using yield.

    Yield pattern allows streaming multiple measurements from a single test.
    Each yielded value is logged and checked against limits.

    This is the pattern for:
    - Time-series data (burn-in, soak tests)
    - Progress reporting during long tests
    - Data arrays (waveform capture)
    """
    vin = context.get_in("vin", 5.0)
    load = context.get_in("load_current", 0.5)
    sample_count = context.get_in("sample_count", 5)

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
def test_thermal_shutdown(context, psu, dmm, eload):
    """Verify thermal protection (manual test).

    Config includes prompt_before to instruct operator.
    This test is typically skipped in automated runs.

    In production, this would verify the LDO shuts down
    when overheated, protecting the circuit.
    """
    vin = context.get_in("vin", 5.0)
    load = context.get_in("load_current", 0.5)

    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    # After thermal shutdown, output should collapse
    vout = dmm.measure_dc_voltage()

    eload.disable()
    return float(vout)


# =============================================================================
# Pattern 11: Waveform Capture (Oscilloscope)
# =============================================================================
@litmus_test
def test_output_ripple(context, psu, eload, scope):
    """Measure output ripple using oscilloscope waveform capture.

    Captures a waveform from the scope, calculates peak-to-peak ripple.

    This pattern demonstrates:
    - Waveform capture from oscilloscope
    - Analysis of time-series data
    - Scope mock configuration with sample data

    The scope.fetch_waveform() returns (samples, dt):
    - samples: list of voltage values
    - dt: time between samples (seconds)

    For the Waveform model (with t0, dt, Y, attrs), see:
    litmus.data.models.Waveform
    """
    vin = context.get_in("vin", 5.0)
    load = context.get_in("load_current", 0.5)

    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    # Capture waveform from scope (returns samples, dt)
    samples, dt = scope.fetch_waveform("CH1")

    # Calculate ripple (peak-to-peak voltage) in mV
    ripple_vpp = (max(samples) - min(samples)) * 1000

    eload.disable()
    return ripple_vpp  # mV ripple


# =============================================================================
# Pattern 12: Callable Limits (Temperature-Dependent)
# =============================================================================
@litmus_test
def test_output_voltage_temp(context, psu, dmm):
    """Verify output voltage with temperature-dependent limits.

    Uses a callable limit (defined in config.yaml) that adjusts
    based on the temperature condition from the test vector.

    This pattern demonstrates:
    - Dynamic limits via inline Python in config.yaml
    - context.configure() to record test conditions
    - Limits that access ctx.get_in() for condition-dependent checks

    Callable limits have access to:
    - ctx.get_in(key) - Input parameters from vectors
    - ctx.get_out(key) - Observations from context.observe()
    - Limit class - For constructing return limits
    """
    temp = context.inputs["temperature"]
    vin = context.get_in("vin", 5.0)

    # Record conditions for traceability (and callable limit access)
    context.configure("temperature", temp)
    context.configure("vin", vin)

    psu.set_voltage(vin)
    psu.set_current_limit(0.5)
    psu.enable_output()

    voltage = float(dmm.measure_dc_voltage())
    return voltage


# =============================================================================
# Pattern 13: Context Traceability (Configure/Observe)
# =============================================================================
@litmus_test
def test_efficiency_with_context(context, psu, dmm, eload):
    """Full efficiency test with context traceability.

    Uses context.configure() for commanded stimulus and context.observe()
    for environmental observations that aren't formal measurements.

    This pattern demonstrates:
    - context.configure() - Records inputs (→ in_* columns in Parquet)
    - context.observe() - Records observations (→ out_* columns in Parquet)
    - Separation of stimulus, environment, and measurements

    Use context for:
    - Commanded setpoints (PSU voltage, load current)
    - Environmental data (temperature probes, humidity)
    - Debug data (timestamps, instrument status)

    Use return values for:
    - Measurements with pass/fail limits
    """
    vin = context.inputs["vin"]
    load = context.inputs["load_current"]

    # Record commanded stimulus (→ in_vin, in_load columns)
    context.configure("vin", vin)
    context.configure("load", load)

    # Observe environmental conditions (→ out_* columns)
    # In production, these would come from sensors
    context.observe("ambient_temp", 24.5)
    context.observe("dut_temp", 42.3)

    # Set up stimulus
    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    # Take measurements
    vin_actual = float(psu.measure_voltage())
    iin = float(psu.measure_current())
    vout = float(dmm.measure_dc_voltage())
    iout = load  # Commanded load current

    # Also observe actuals (not limit-checked, but recorded)
    context.observe("vin_actual", vin_actual)
    context.observe("iin_actual", iin)

    eload.disable()

    # Calculate derived values
    pin = vin_actual * iin
    pout = vout * iout
    efficiency = (pout / pin * 100) if pin > 0 else 0

    # Return measurements for limit checking
    return {
        "input_power": pin,
        "output_power": pout,
        "efficiency": efficiency,
    }


# =============================================================================
# Pattern 14: Large Data Observation (Waveform → _ref/)
# =============================================================================
@litmus_test
def test_ripple_waveform_capture(context, psu, eload, scope):
    """Capture and observe raw waveform data.

    This pattern demonstrates storing large data structures via context.observe().
    Waveform objects (and numpy arrays, large byte strings) are automatically
    stored in the _ref/ directory alongside the Parquet file, with a path
    reference in the Parquet column.

    The waveform has semi-random noise added to demonstrate realistic data.

    Use this pattern for:
    - Oscilloscope waveforms for post-test analysis
    - FFT data for frequency domain analysis
    - Image captures (e.g., thermal camera)
    - Large sensor arrays
    """
    from litmus.data.models import Waveform

    vin = context.get_in("vin", 5.0)
    load = context.get_in("load_current", 0.5)

    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    # Capture waveform from scope
    samples, dt = scope.fetch_waveform("CH1")

    # Generate realistic waveform: 100 samples of 3.3V with ripple + noise
    # (Real scope would return actual captured data)
    import math
    t0 = time.time()  # Capture timestamp
    dt = 1e-5  # 10µs sample interval (100kHz)
    num_samples = 100
    noisy_samples = [
        3.3 + 0.015 * math.sin(2 * math.pi * 50000 * i * dt)  # 50kHz ripple
        + random.gauss(0, 0.005)  # noise
        for i in range(num_samples)
    ]

    # Create Waveform model - stored in _ref/ due to size
    waveform = Waveform(
        t0=t0,
        dt=dt,
        Y=noisy_samples,
        attrs={
            "channel": "CH1",
            "units": "V",
            "coupling": "DC",
            "vin": vin,
            "load": load,
        },
    )

    # Observe the waveform - stored in _ref/ directory
    context.observe("ripple_waveform", waveform)

    # Calculate and return ripple for limit checking
    ripple_mV = (max(noisy_samples) - min(noisy_samples)) * 1000

    eload.disable()
    return ripple_mV
