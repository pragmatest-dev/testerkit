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


def test_rail_within_spec(verify, dut) -> None:
    """Nominal read of the 3.3 V rail is logged + checked against ``V_RAIL``."""
    verify("v_rail", dut.read_voltage(), limit=V_RAIL)


@pytest.mark.litmus_vectors(vin=[3.3, 5.0, 5.5])
def test_rail_holds_across_input(verify, dut, vin: float) -> None:
    """Sweep input voltage; every reading becomes its own row in the log."""
    dut.set_input(vin)
    verify("v_rail", dut.read_voltage(), limit=V_RAIL)
