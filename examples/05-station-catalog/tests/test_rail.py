"""Test bodies take instruments as fixtures — same as stages 2-4.

Only difference: no ``conftest.py``. The station YAML registers
``psu`` and ``dmm`` as pytest fixtures automatically, so you ask for
them by name and the Litmus plugin constructs them from the catalog
+ station config. With ``mock_instruments: true`` in ``litmus.yaml``
each method returns the value declared in ``stations/bench_01.yaml``.
"""

from __future__ import annotations


def test_rail_within_spec(verify, psu, dmm) -> None:
    """Source 5 V into the rail input and log the output voltage."""
    psu.set_voltage(5.0)
    psu.set_current(0.5)
    verify("v_rail", dmm.measure_dc_voltage())


def test_rail_holds_across_input(verify, psu, dmm, vin: float) -> None:
    psu.set_voltage(vin)
    verify("v_rail", dmm.measure_dc_voltage())


class TestIdle:
    def test_idle_current(self, verify, psu) -> None:
        verify("i_idle", psu.measure_current())

    def test_no_load_voltage(self, verify, dmm) -> None:
        verify("v_rail", dmm.measure_dc_voltage())
