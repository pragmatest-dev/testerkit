"""Demo tests for shared DMM through relay matrix.

Two slots share one DMM via a switch matrix. Each slot measures its
own DUT's voltage/current through different matrix channels.

Run with:
    cd demo && uv run pytest tests/test_shared_dmm.py \
        --station=demo_station_001 \
        --fixture-config=fixtures/shared_dmm_board.yaml \
        --dut-serials slot_1=SN001,slot_2=SN002 \
        --mock-instruments -v
"""


def test_output_voltage(pins):
    """Measure output voltage through shared DMM + matrix."""
    v = pins["VOUT"].measure_voltage()
    assert v is not None


def test_output_current(pins):
    """Measure output current through shared DMM + matrix."""
    i = pins["IOUT"].measure_current()
    assert i is not None


def test_supply_and_measure(pins):
    """Set input voltage, then measure output through shared DMM."""
    pins["J1_VIN"].set_voltage(5.0)
    pins["J1_VIN"].enable_output()
    v = pins["VOUT"].measure_voltage()
    assert v is not None
