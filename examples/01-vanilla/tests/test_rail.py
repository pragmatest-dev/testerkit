"""Vanilla pytest tests for a 3.3 V rail.

This file uses only native pytest primitives:

* ``assert`` for pass/fail
* ``psu`` and ``dmm`` fixtures from ``conftest.py``
* ``@pytest.mark.parametrize`` for a sweep

No TesterKit features are in use. Running ``pytest -v`` reports pass/fail
and nothing else — the measured values are not captured anywhere the
next engineer can see. Stage 2 fixes that.
"""

from __future__ import annotations

import pytest


def test_rail_within_spec(psu, dmm) -> None:
    """Source 5 V into the rail input and check the output is in spec."""
    psu.set_voltage(5.0)
    psu.enable_output()
    v = dmm.measure_dc_voltage()
    assert 3.2 <= v <= 3.4


@pytest.mark.parametrize("vin", [3.3, 5.0, 5.5])
def test_rail_holds_across_input(psu, dmm, vin: float) -> None:
    """Rail stays in spec as input voltage sweeps."""
    psu.set_voltage(vin)
    psu.enable_output()
    v = dmm.measure_dc_voltage()
    assert 3.2 <= v <= 3.4
