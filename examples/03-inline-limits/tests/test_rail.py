"""Limits as pytest markers, resolved by name at ``verify`` time.

``@pytest.mark.litmus_limits(...)`` attaches a limit to the test.
``verify("v_rail", value)`` looks up the limit named ``v_rail`` and
uses it. The test body doesn't import ``Limit`` anymore — the limit
is metadata, not a Python object the body needs to hold.

``@pytest.mark.litmus_vectors`` uses the same kwargs/dict-of-named-things
shape: each kwarg is one sweep axis. Multiple kwargs cross-product;
stacked decorators do the same.

* **Single axis** — ``litmus_vectors(vin=[...])``. ``linspace(...)``
  etc. from ``litmus`` are IDE-friendly numeric helpers.
* **Cross-product** — multiple kwargs in one decorator OR stack
  decorators. Both translate to stacked ``metafunc.parametrize`` calls.
* **Zip / paired axis** — comma-joined argname key; YAML reads cleanly
  with ``"a,b": [[v1, v2], ...]`` (inline uses ``**{"a,b": [...]}``).
"""

from __future__ import annotations

import pytest

from litmus import linspace


@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4, "units": "V"})
def test_rail_within_spec(verify, psu, dmm) -> None:
    """Marker supplies ``v_rail``; ``verify`` resolves it by name."""
    psu.set_voltage(5.0)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage())


@pytest.mark.litmus_vectors(vin=linspace(3.3, 5.5, 5))
@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4, "units": "V"})
def test_rail_holds_across_input(verify, psu, dmm, vin: float) -> None:
    """Single-axis sweep over five vin points; ``linspace`` returns a list."""
    psu.set_voltage(vin)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage())
