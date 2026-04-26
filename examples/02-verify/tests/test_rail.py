"""Same tests, but measurements now flow into a Parquet log.

The Litmus pytest plugin provides the ``verify`` fixture. Every call
to ``verify(name, value, limit=...)`` writes a row with the name,
value, units, limit, and pass/fail outcome — so you get the ``value``
persisted, not just a pass/fail.

Test vectors are introduced here too: ``@pytest.mark.litmus_vectors``
is the runner-neutral name for declaring sweep axes. Each kwarg is
one axis; multiple kwargs cross-product — same family shape as
``litmus_limits``. Limits are still inline in Python here
(``Limit(low=..., high=...)``). Later stages move them to YAML.
Start with what's familiar: Python.
"""

from __future__ import annotations

import pytest

from litmus.config.test_config import Limit

V_RAIL = Limit(low=3.2, high=3.4, units="V")


def test_rail_within_spec(verify, psu, dmm) -> None:
    """Source 5 V into the rail input and log the output voltage."""
    psu.set_voltage(5.0)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage(), limit=V_RAIL)


@pytest.mark.litmus_vectors(vin=[3.3, 5.0, 5.5])
def test_rail_holds_across_input(verify, psu, dmm, vin: float) -> None:
    """Sweep input voltage; every reading becomes its own row in the log."""
    psu.set_voltage(vin)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage(), limit=V_RAIL)
