"""Limits as pytest markers, resolved by name at ``verify`` time.

``@pytest.mark.litmus_limits(...)`` attaches a limit to the test.
``verify("v_rail", value)`` looks up the limit named ``v_rail`` and
uses it. The test body doesn't import ``Limit`` anymore — the limit
is metadata, not a Python object the body needs to hold.

Same pattern as ``pytest.mark.parametrize``: configuration lives on
the marker, the body just does work.
"""

from __future__ import annotations

import pytest


@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4, "units": "V"})
def test_rail_within_spec(verify, dut) -> None:
    """Marker supplies ``v_rail``; ``verify`` resolves it by name."""
    verify("v_rail", dut.read_voltage())


@pytest.mark.parametrize("vin", [3.3, 5.0, 5.5])
@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4, "units": "V"})
def test_rail_holds_across_input(verify, dut, vin: float) -> None:
    """Limits marker stacks cleanly with parametrize."""
    dut.set_input(vin)
    verify("v_rail", dut.read_voltage())
