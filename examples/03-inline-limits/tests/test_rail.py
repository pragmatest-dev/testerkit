"""Limits as pytest markers, resolved by name at ``verify`` time.

``@pytest.mark.litmus_limits(...)`` attaches a limit to the test.
``verify("v_rail", value)`` looks up the limit named ``v_rail`` and
uses it. The test body doesn't import ``Limit`` anymore — the limit
is metadata, not a Python object the body needs to hold.

``@pytest.mark.litmus_sweeps`` takes a list of sweep dicts. Each
dict is one nesting level (top = outer, slowest loop).

* **Single axis** — ``litmus_sweeps([{"vin": [...]}])``.
  ``linspace(...)`` etc. from ``litmus`` are IDE-friendly numeric
  helpers.
* **Cross-product / nested loops** — multiple dicts in the list
  (or stacked decorators). Each dict becomes one
  ``metafunc.parametrize`` call.
* **Zip / paired axes** — multi-key dict, keys pair together with
  one value-list each.
"""

from __future__ import annotations

import pytest

from litmus import linspace


@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4, "unit": "V"})
def test_rail_within_spec(verify, psu, dmm) -> None:
    """Marker supplies ``v_rail``; ``verify`` resolves it by name."""
    psu.set_voltage(5.0)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage())


@pytest.mark.litmus_sweeps([{"vin": linspace(3.3, 5.5, 5)}])
@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4, "unit": "V"})
def test_rail_holds_across_input(verify, psu, dmm, vin: float) -> None:
    """Single-axis sweep over five vin points; ``linspace`` returns a list."""
    psu.set_voltage(vin)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage())
