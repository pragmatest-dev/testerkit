"""pytest configuration for demo tests.

Instrument role fixtures (psu, dmm, eload, scope) are AUTO-REGISTERED by the
Litmus plugin from your station config — no boilerplate needed.  Tests can
use them directly:

    def test_voltage(dmm):
        assert dmm.measure_dc_voltage() > 3.0

To override an auto-registered fixture (e.g. custom setup/teardown), define
a fixture with the same name here — standard pytest override behavior:

    @pytest.fixture(scope="session")
    def psu(instruments):
        inst = instruments.get("psu")
        inst.set_voltage(5.0)       # custom default
        return inst

Pin-based fixtures below add SEMANTIC VALUE beyond role access: they resolve
DUT pin names via the fixture configuration for full traceability.

Run tests with:
    cd demo
    pytest tests/ --station=demo_station_001 --mock-instruments -v
"""

import pytest


# =============================================================================
# Pin-Based Fixtures (for traceability)
#
# These fixtures demonstrate the RECOMMENDED approach for production tests:
# - Access instruments by DUT pin name
# - Full traceability (measurement → pin → fixture point → instrument)
# - Automatic signal routing via fixture configuration
# =============================================================================


@pytest.fixture(scope="session")
def output_dmm(pins):
    """DMM configured for output voltage measurement.

    Uses the 'pins' fixture to get instrument by DUT pin name.
    This provides full traceability in the measurement record.

    Usage:
        def test_output(output_dmm):
            vout = output_dmm.measure_dc_voltage()
            # Measurement includes: dut_pin="TP_VOUT", fixture_point="vout_measure"
    """
    if pins is None:
        return None
    return pins.get("TP_VOUT")


@pytest.fixture(scope="session")
def input_psu(pins):
    """PSU configured for input power.

    Uses the 'pins' fixture to get instrument by DUT pin name.
    """
    if pins is None:
        return None
    return pins.get("J1_VIN")
