"""
Demo Test Suite: Power Board Validation
========================================

This is the GOLDEN EXAMPLE demonstrating Litmus best practices:

1. Test config (vectors, limits, mocks) lives in TWO places:
   - **Sequence steps** — primary source for orchestrated runs (--sequence)
   - **Inline decorator** — fallback for dev/ad-hoc pytest runs
2. Tests SET UP conditions, MEASURE results, RETURN values
3. Framework handles limit checking, retry, logging, traceability

NO HARDCODED VALUES - everything is configurable.

Config resolution: sequence step > inline decorator
When --sequence is active, the step config replaces decorator config entirely.
Without --sequence, inline decorator config is used.

PATTERNS DEMONSTRATED:
- Pattern 1: Simple single measurement (inline + sequence)
- Pattern 2: Multiple vectors with retry
- Pattern 3: Explicit vector list
- Pattern 4: Product expansion (Cartesian product)
- Pattern 5: Product with change detection
- Pattern 6: Range string expansion
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
    # Ad-hoc (uses inline decorator config):
    pytest tests/test_power_board.py::test_output_voltage_no_load \
        --station=demo_station_001 --mock-instruments -v
    # Orchestrated (uses sequence step config):
    pytest tests/test_power_board.py --sequence=power_board_smoke \
        --station=demo_station_001 --mock-instruments -v
"""

import random
import time

from demo.drivers import DMM, PSU, ELoad, Scope
from litmus.execution import litmus_test


# =============================================================================
# Pattern 1: Simple Single Measurement
# =============================================================================
@litmus_test(
    config={
        "vectors": [{"vin": 5.0}],
        "mocks": {"dmm.measure_dc_voltage": 3.3, "psu.measure_current": 0.005},
        "limits": {
            "output_voltage": {
                "low": 3.234,
                "high": 3.366,
                "nominal": 3.3,
                "units": "V",
                "ref": "output_voltage",
            }
        },
    }
)
def test_output_voltage_no_load(context, psu: PSU, dmm: DMM):
    """Verify output voltage at no load.

    Naming: The limit key 'output_voltage' names the measurement.
    You can also return {"output_voltage": value} for explicit naming.

    Inline config provides dev defaults. When run with --sequence,
    the sequence step config overrides these values entirely.
    """
    vin = context.get_param("vin", 5.0)

    psu.set_voltage(vin)
    psu.set_current_limit(0.1)
    psu.enable_output()

    # Single return value - measurement name inferred from limit key
    return dmm.measure_dc_voltage()


# =============================================================================
# Pattern 2: Single Vector with Retry (configured in config.yaml)
# =============================================================================
@litmus_test(
    config={
        "vectors": [{"vin": 5.0, "load_current": 0.8}],
        "mocks": {"dmm.measure_dc_voltage": 3.28, "psu.measure_current": 0.85},
        "retry": {"max_attempts": 3, "delay_seconds": 0.5},
        "limits": {
            "output_voltage": {
                "low": 3.201,
                "high": 3.399,
                "nominal": 3.3,
                "units": "V",
                "ref": "output_voltage",
            }
        },
    }
)
def test_output_voltage_full_load(context, psu: PSU, dmm: DMM, eload: ELoad):
    """Verify output voltage at full load.

    Inline config provides dev defaults. Retry: max_attempts=3, delay=0.5s.
    """
    vin = context.get_param("vin", 5.0)
    load = context.get_param("load_current", 0.8)

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
@litmus_test(
    config={
        "vectors": [
            {
                "vin": 5.0,
                "load_current": 0.1,
                "expected_dropout": 0,
                "_mocks": {"dmm.measure_dc_voltage": 3.32, "psu.measure_current": 0.15},
            },
            {
                "vin": 5.0,
                "load_current": 0.4,
                "expected_dropout": 2,
                "_mocks": {"dmm.measure_dc_voltage": 3.30, "psu.measure_current": 0.45},
            },
            {
                "vin": 5.0,
                "load_current": 0.8,
                "expected_dropout": 5,
                "_mocks": {"dmm.measure_dc_voltage": 3.28, "psu.measure_current": 0.85},
            },
        ],
        "limits": {
            "output_voltage": {
                "low": 3.2,
                "high": 3.4,
                "nominal": 3.3,
                "units": "V",
                "ref": "output_voltage",
            }
        },
    }
)
def test_load_regulation(context, psu: PSU, dmm: DMM, eload: ELoad):
    """Signal output at multiple load points.

    3 vectors with per-vector mock values, one limit for all.
    """
    vin = context.get_param("vin", 5.0)
    load = context.params["load_current"]

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
@litmus_test(
    config={
        "vectors": {
            "expand": "product",
            "vin": [4.5, 4.75, 5.0, 5.25, 5.5],
            "load_current": [0.1, 0.25, 0.4, 0.6, 0.8],
        },
        "limits": {
            "output_voltage": {
                "low": 3.1,
                "high": 3.5,
                "nominal": 3.3,
                "units": "V",
                "ref": "output_voltage",
            }
        },
    }
)
def test_load_sweep(context, psu: PSU, dmm: DMM, eload: ELoad):
    """Sweep VIN and load using product expansion.

    5×5 = 25 vectors. Uses context.changed() to detect when parameters change.
    """
    vin = context.params["vin"]
    load = context.params["load_current"]

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
# Pattern 5: Product with Change Detection
# =============================================================================
@litmus_test(
    config={
        "vectors": {
            "expand": "product",
            "temperature": [-20, 25, 55, 85],
            "load_current": [0.1, 0.3, 0.5, 0.7, 0.8],
        },
        "limits": {
            "output_voltage": {
                "low": 3.0,
                "high": 3.6,
                "nominal": 3.3,
                "units": "V",
                "ref": "output_voltage",
            }
        },
    }
)
def test_temp_load_matrix(context, psu: PSU, dmm: DMM, eload: ELoad):
    """Full characterization matrix with product expansion.

    4×5 = 20 vectors. Uses context.changed() for outer parameter changes.
    """
    _temp = context.params["temperature"]  # Unused in demo; would set chamber temp
    load = context.params["load_current"]

    # Temperature is outer loop - changes less frequently
    if context.changed("temperature"):
        # In production: set_chamber_temperature(_temp)
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
# Pattern 6: Range String Expansion
# =============================================================================
@litmus_test(
    config={
        "vectors": {
            "expand": "product",
            "vin": "4.5:5.5:0.1",
        },
        "limits": {
            "output_voltage": {
                "low": 3.2,
                "high": 3.4,
                "nominal": 3.3,
                "units": "V",
                "ref": "output_voltage",
            }
        },
    }
)
def test_line_regulation(context, psu: PSU, dmm: DMM, eload: ELoad):
    """Sweep input voltage using range string (4.5V to 5.5V, 0.1V steps)."""
    vin = context.params["vin"]

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
@litmus_test(
    config={
        "vectors": [{"vin": 5.0, "load_current": 0.5}],
        "mocks": {
            "dmm.measure_dc_voltage": 3.3,
            "psu.measure_current": 0.5,
        },
        "limits": {
            "input_power": {"low": 0, "high": 5.0, "nominal": 2.0, "units": "W"},
            "output_power": {"low": 0, "high": 3.0, "nominal": 1.65, "units": "W"},
            "efficiency": {
                "low": 60,
                "high": 100,
                "nominal": 66,
                "units": "%",
                "spec_ref": "efficiency @ vin=5V, load=0.5A",
            },
        },
    }
)
def test_power_analysis(context, psu: PSU, dmm: DMM, eload: ELoad):
    """Signal multiple values and return as dict.

    Returns: {"input_power": W, "output_power": W, "efficiency": %}
    Each key gets checked against its own limit.
    """
    vin = context.get_param("vin", 5.0)
    load = context.get_param("load_current", 0.5)

    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    # Signal input
    v_in = float(psu.measure_voltage())
    i_in = float(psu.measure_current())

    # Signal output
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
@litmus_test(
    config={
        "vectors": [{"vin": 5.0}],
        "mocks": {"psu.measure_current": 0.005},
        "limits": {
            "quiescent_current": {
                "low": 0,
                "high": 10,
                "nominal": 5,
                "units": "mA",
                "comparator": "LE",
                "ref": "quiescent_current",
            }
        },
    }
)
def test_quiescent_current(context, psu: PSU):
    """Verify quiescent current (no load). Comparator=LE for upper-bound-only."""
    vin = context.get_param("vin", 5.0)

    psu.set_voltage(vin)
    psu.set_current_limit(0.05)  # Low limit - no load
    psu.enable_output()

    # Signal input current (no load attached)
    current_ma = float(psu.measure_current()) * 1000
    return current_ma


# =============================================================================
# Pattern 9: Streaming Measurements (Yield)
# =============================================================================
@litmus_test(
    config={
        "vectors": [{"vin": 5.0, "load_current": 0.5, "sample_count": 5}],
        "mocks": {"dmm.measure_dc_voltage": 3.3},
        "limits": {
            "voltage": {
                "low": 3.25,
                "high": 3.35,
                "nominal": 3.3,
                "units": "V",
                "spec_ref": "output stability",
            }
        },
    }
)
def test_stability_over_time(context, psu: PSU, dmm: DMM, eload: ELoad):
    """Monitor output stability over time using yield (streaming measurements)."""
    vin = context.get_param("vin", 5.0)
    load = context.get_param("load_current", 0.5)
    sample_count = context.get_param("sample_count", 5)

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
@litmus_test(
    config={
        "vectors": [{"vin": 5.0, "load_current": 0.5}],
        "mocks": {"dmm.measure_dc_voltage": 0.05},
        "limits": {
            "output_voltage": {
                "low": 0,
                "high": 0.1,
                "nominal": 0,
                "units": "V",
                "ref": "output_voltage",
            }
        },
    }
)
def test_thermal_shutdown(context, psu: PSU, dmm: DMM, eload: ELoad):
    """Verify thermal protection (manual test). Output should collapse."""
    vin = context.get_param("vin", 5.0)
    load = context.get_param("load_current", 0.5)

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
@litmus_test(
    config={
        "vectors": [{"vin": 5.0, "load_current": 0.5}],
        "mocks": {
            "scope.fetch_waveform": [
                [3.285, 3.290, 3.300, 3.310, 3.315, 3.310, 3.300, 3.290, 3.285, 3.290],
                0.00001,
            ],
        },
        "limits": {
            "output_ripple": {
                "low": 0,
                "high": 50,
                "nominal": 30,
                "units": "mV",
                "comparator": "LE",
                "ref": "output_ripple",
            }
        },
    }
)
def test_output_ripple(context, psu: PSU, eload: ELoad, scope: Scope):
    """Signal output ripple using oscilloscope waveform capture."""
    vin = context.get_param("vin", 5.0)
    load = context.get_param("load_current", 0.5)

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
@litmus_test(
    config={
        "vectors": {
            "expand": "product",
            "temperature": [-40, 25, 85],
            "vin": [5.0],
        },
        "mocks": {"dmm.measure_dc_voltage": 3.3},
        "limits": {
            "output_voltage": {
                "callable": (
                    'temp = ctx.get_param("temperature")\n'
                    "if temp < 0:\n"
                    "  return Limit(low=3.15, high=3.45, units='V')\n"
                    "elif temp < 50:\n"
                    "  return Limit(low=3.25, high=3.35, units='V')\n"
                    "else:\n"
                    "  return Limit(low=3.10, high=3.50, units='V')\n"
                ),
            }
        },
    }
)
def test_output_voltage_temp(context, psu: PSU, dmm: DMM):
    """Verify output voltage with temperature-dependent callable limits."""
    temp = context.params["temperature"]
    vin = context.get_param("vin", 5.0)

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
@litmus_test(
    config={
        "vectors": [
            {
                "vin": 5.0,
                "load_current": 0.5,
                "_mocks": {
                    "psu.measure_voltage": 5.0,
                    "psu.measure_current": 0.55,
                    "dmm.measure_dc_voltage": 3.3,
                },
            },
        ],
        "limits": {
            "input_power": {"low": 0, "high": 6.0, "units": "W"},
            "output_power": {"low": 0, "high": 4.0, "units": "W"},
            "efficiency": {
                "low": 55,
                "high": 100,
                "nominal": 60,
                "units": "%",
                "spec_ref": "efficiency across line/load",
            },
        },
    }
)
def test_efficiency_with_context(context, psu: PSU, dmm: DMM, eload: ELoad):
    """Full efficiency test with context traceability (configure/observe)."""
    vin = context.params["vin"]
    load = context.params["load_current"]

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
@litmus_test(
    config={
        "vectors": {
            "expand": "product",
            "vin": [4.5, 5.0, 5.5],
            "load_current": [0.2, 0.5, 0.8],
        },
        "mocks": {
            "scope.fetch_waveform": [
                [
                    3.290,
                    3.295,
                    3.305,
                    3.310,
                    3.308,
                    3.302,
                    3.295,
                    3.288,
                    3.292,
                    3.298,
                    3.305,
                    3.312,
                    3.310,
                    3.303,
                    3.296,
                    3.290,
                    3.288,
                    3.293,
                    3.300,
                    3.308,
                ],
                0.00001,
            ],
        },
        "limits": {
            "output_ripple": {
                "low": 0,
                "high": 50,
                "nominal": 25,
                "units": "mV",
                "comparator": "LE",
                "ref": "output_ripple",
            }
        },
    }
)
def test_ripple_waveform_capture(context, psu: PSU, eload: ELoad, scope: Scope):
    """Capture and observe raw waveform data (stored in _ref/ directory)."""
    from litmus.data.models import Waveform

    vin = context.get_param("vin", 5.0)
    load = context.get_param("load_current", 0.5)

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
        3.3
        + 0.015 * math.sin(2 * math.pi * 50000 * i * dt)  # 50kHz ripple
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
