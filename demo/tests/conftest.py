"""pytest configuration for demo tests.

This conftest demonstrates TWO approaches to instrument access:

1. INSTRUMENTS DICT (simple): Direct access by station config name
   - psu = instruments["psu"]
   - Good for: Quick tests, prototyping

2. PINS FIXTURE (recommended): Access by DUT pin name with full traceability
   - dmm = pins["TP_VOUT"]  # Returns instrument configured for that pin
   - Good for: Production tests, traceability, spec-driven testing

Run tests with:
    cd demo
    pytest tests/ --station=demo_station_001 --mock-instruments -v
"""

import pytest


# =============================================================================
# Simple Instrument Fixtures (from station config)
# =============================================================================


@pytest.fixture(scope="session")
def psu(instruments):
    """Power supply fixture.

    Resolves 'psu' from the instruments dictionary loaded from station config.
    This is the SIMPLE approach - direct access by config name.
    """
    return instruments.get("psu")


@pytest.fixture(scope="session")
def dmm(instruments):
    """Digital multimeter fixture.

    Resolves 'dmm' from the instruments dictionary loaded from station config.
    """
    return instruments.get("dmm")


@pytest.fixture(scope="session")
def eload(instruments):
    """Electronic load fixture.

    Resolves 'eload' from the instruments dictionary loaded from station config.
    """
    return instruments.get("eload")


@pytest.fixture(scope="session")
def scope(instruments):
    """Oscilloscope fixture.

    Resolves 'scope' from the instruments dictionary loaded from station config.
    Used for waveform capture patterns (Pattern 11: Ripple measurement).
    """
    return instruments.get("scope")


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
