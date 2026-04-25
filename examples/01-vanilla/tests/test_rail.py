"""Vanilla pytest tests for a 3.3 V rail.

This file uses only native pytest primitives:

* ``assert`` for pass/fail
* a fixture from ``conftest.py`` for the DUT
* ``@pytest.mark.parametrize`` for a sweep

No Litmus features are in use. Running ``pytest -v`` reports pass/fail
and nothing else — the measured values are not captured anywhere the
next engineer can see. Stage 2 fixes that.
"""

from __future__ import annotations

import pytest


def test_rail_within_spec(dut) -> None:
    """Nominal read of the 3.3 V rail is within spec."""
    v = dut.read_voltage()
    assert 3.2 <= v <= 3.4


@pytest.mark.parametrize("vin", [3.3, 5.0, 5.5])
def test_rail_holds_across_input(dut, vin: float) -> None:
    """Rail stays in spec as input voltage sweeps."""
    dut.set_input(vin)
    v = dut.read_voltage()
    assert 3.2 <= v <= 3.4
