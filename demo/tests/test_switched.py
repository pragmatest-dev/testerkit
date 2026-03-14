"""Demo tests using switch-routed fixtures.

Shows both access patterns:
- Pattern 1: Explicit routes + typed instruments (context manager)
- Pattern 2: Transparent via pins[] (auto-activation)

Run with:
    cd demo && uv run pytest tests/test_switched.py \
        --station=demo_station_001 \
        --fixture-config=fixtures/switched_board.yaml \
        --mock-instruments -v
"""


# ---------------------------------------------------------------------------
# Pattern 1: Explicit routes + direct instrument access
# ---------------------------------------------------------------------------


def test_vout_explicit(instrument, routes):
    """Measure output voltage using explicit route context manager."""
    dmm = instrument("dmm")
    with routes.for_pin("VOUT"):
        v = dmm.measure_voltage()
    assert v is not None


def test_multi_measurement_explicit(instrument, routes):
    """Multiple measurements with sequential route switching."""
    dmm = instrument("dmm")

    with routes.for_pin("VOUT"):
        vout = dmm.measure_voltage()

    with routes.for_pin("VREF"):
        vref = dmm.measure_voltage()

    assert vout is not None
    assert vref is not None


# ---------------------------------------------------------------------------
# Pattern 2: Transparent via pins[]
# ---------------------------------------------------------------------------


def test_vout_transparent(pins):
    """Measure output voltage — route activates automatically."""
    v = pins["VOUT"].measure_voltage()
    assert v is not None


def test_supply_direct(pins):
    """Direct-wired supply — no switching involved."""
    pins["J1_VIN"].set_voltage(5.0)
