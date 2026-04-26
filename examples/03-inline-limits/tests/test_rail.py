"""Limits as pytest markers, resolved by name at ``verify`` time.

``@pytest.mark.litmus_limits(...)`` attaches a limit to the test.
``verify("v_rail", value)`` looks up the limit named ``v_rail`` and
uses it. The test body doesn't import ``Limit`` anymore — the limit
is metadata, not a Python object the body needs to hold.

Same pattern as ``@pytest.mark.litmus_vectors``: configuration lives
on the marker, the body just does work.

Inline list-builders (``litmus.linspace``, ``litmus.paired``, …) are
the Python counterparts to YAML's range expanders — call them
directly so the IDE shows signatures and autocompletes argnames.
"""

from __future__ import annotations

import pytest

from litmus import linspace, paired


@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4, "units": "V"})
def test_rail_within_spec(verify, dut) -> None:
    """Marker supplies ``v_rail``; ``verify`` resolves it by name."""
    verify("v_rail", dut.read_voltage())


@pytest.mark.litmus_vectors(vin=linspace(3.3, 5.5, 5))
@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4, "units": "V"})
def test_rail_holds_across_input(verify, dut, vin: float) -> None:
    """Sweep input voltage; ``linspace`` is the inline counterpart to YAML's
    ``{linspace: [...]}`` expander — IDE-friendly and reads naturally."""
    dut.set_input(vin)
    verify("v_rail", dut.read_voltage())


@paired(vin=[3.3, 5.0, 5.5], expected=[3.30, 3.30, 3.30])
@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4, "units": "V"})
def test_rail_paired_with_expected(verify, dut, vin: float, expected: float) -> None:
    """``paired`` zips multiple kwargs into a single paired axis — clean
    inline alternative to ``**{"vin,expected": [(3.3, 3.30), ...]}``."""
    dut.set_input(vin)
    reading = dut.read_voltage()
    verify("v_rail", reading)
    # Expected is just along for the ride here — illustrates the pairing.
    assert abs(reading - expected) < 1.0
