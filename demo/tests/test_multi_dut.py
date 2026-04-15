"""
Demo: Multi-DUT Parallel Testing
=================================

Demonstrates parallel testing of two power boards using multi-slot fixtures.
Each slot runs in its own subprocess — tests are identical to single-DUT,
but sync points coordinate steps across slots.

Key concepts:
- Tests are SLOT-UNAWARE: same test code, same fixtures
- ``sync`` fixture provides cross-slot coordination (None in single-slot mode)
- Each subprocess gets its own instrument connections via env vars
- File locks handle shared physical resources

Run with:
    cd demo
    pytest tests/test_multi_dut.py \
        --station=demo_station_001 \
        --fixture-config=fixtures/dual_power_board.yaml \
        --dut-serials=SN001,SN002 \
        --mock-instruments -v

    # Or with a sequence (includes sync steps):
    pytest tests/test_multi_dut.py \
        --sequence=dual_power_board_smoke \
        --station=demo_station_001 \
        --dut-serials=SN001,SN002 \
        --mock-instruments -v
"""

from demo.drivers import DMM, PSU, ELoad
from litmus.execution import litmus_test


# =============================================================================
# Test 1: Power-up — both slots must complete before measurement
# =============================================================================
@litmus_test(
    config={
        "vectors": [{"vin": 5.0}],
        "mocks": {"psu.measure_current": 0.005},
        "limits": {
            "startup_current": {
                "low": 0, "high": 50, "nominal": 5, "units": "mA",
                "comparator": "LE",
            }
        },
    }
)
def test_power_up(context, psu: PSU):
    """Power up the DUT and verify startup current.

    In a multi-slot run, both boards power up independently (no sync needed).
    Each slot's PSU channel is routed via the fixture config.
    """
    vin = context.get_in("vin", 5.0)

    psu.set_voltage(vin)
    psu.set_current_limit(0.1)
    psu.enable_output()

    current_ma = float(psu.measure_current()) * 1000
    return current_ma


# =============================================================================
# Test 2: Synchronized measurement — all slots must be powered before measuring
# =============================================================================
@litmus_test(
    config={
        "vectors": [{"vin": 5.0}],
        "mocks": {"dmm.measure_dc_voltage": 3.3},
        "limits": {
            "output_voltage": {
                "low": 3.234, "high": 3.366, "nominal": 3.3,
                "units": "V", "ref": "output_voltage",
            }
        },
    }
)
def test_output_voltage_synced(context, psu: PSU, dmm: DMM, sync):
    """Measure output voltage after all slots are powered.

    The ``sync`` fixture coordinates across slots:
    - In multi-slot mode: blocks until all slots reach this point
    - In single-slot mode: ``sync`` is None, no-op

    This ensures all boards are powered before any measurement starts,
    which matters when boards share a power bus or thermal environment.
    """
    vin = context.get_in("vin", 5.0)

    psu.set_voltage(vin)
    psu.set_current_limit(0.5)
    psu.enable_output()

    # Wait for all slots to finish power-up
    if sync is not None:
        sync.wait("all_powered", timeout=30)

    return dmm.measure_dc_voltage()


# =============================================================================
# Test 3: Independent measurement — no sync needed
# =============================================================================
@litmus_test(
    config={
        "vectors": [{"vin": 5.0, "load_current": 0.5}],
        "mocks": {
            "dmm.measure_dc_voltage": 3.28,
            "psu.measure_current": 0.55,
        },
        "limits": {
            "efficiency": {
                "low": 55, "high": 100, "nominal": 60, "units": "%",
            }
        },
    }
)
def test_efficiency(context, psu: PSU, dmm: DMM, eload: ELoad):
    """Measure efficiency — runs independently per slot.

    No sync needed here. Each slot measures its own board at its own pace.
    File locks ensure shared instruments are accessed safely.
    """
    vin = context.get_in("vin", 5.0)
    load = context.get_in("load_current", 0.5)

    psu.set_voltage(vin)
    psu.set_current_limit(1.0)
    psu.enable_output()

    eload.set_current(load)
    eload.enable()

    v_in = float(psu.measure_voltage()) if hasattr(psu, "measure_voltage") else vin
    i_in = float(psu.measure_current())
    v_out = float(dmm.measure_dc_voltage())

    eload.disable()

    p_in = v_in * i_in
    p_out = v_out * load
    efficiency = (p_out / p_in * 100) if p_in > 0 else 0

    return efficiency
