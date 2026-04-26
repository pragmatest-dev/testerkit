"""Test bodies shrink further — limits derive from the product spec.

The sidecar binds each test to a characteristic (``rail_3v3``,
``idle_current``). The body iterates ``context.connections`` so each
measurement row stamps ``spec_id`` / ``spec_ref`` / ``dut_pin``.
Single-pin characteristics iterate once; multi-pin ones iterate
per-pin — same loop shape either way.

Most tests below pull config from the sibling ``test_rail.yaml``
sidecar. ``test_rail_inline_markers`` is the exception: it carries
``@pytest.mark.litmus_spec`` and ``@pytest.mark.litmus_connections``
inline so you can see both markers in their decorator form. Inline
or sidecar — same merge rules, same runtime behavior.
"""

from __future__ import annotations

import pytest


def test_rail_within_spec(verify, psu, dmm, context) -> None:
    psu.set_voltage(5.0)
    for _ in context.connections:
        verify("v_rail", dmm.measure_dc_voltage())


def test_rail_holds_across_input(verify, psu, dmm, context, vin: float) -> None:
    psu.set_voltage(vin)
    for _ in context.connections:
        verify("v_rail", dmm.measure_dc_voltage())


@pytest.mark.litmus_spec(characteristic="rail_3v3")
@pytest.mark.litmus_connections(connections=["vout_measure"])
@pytest.mark.litmus_limits(v_rail={"characteristic": "rail_3v3", "tolerance_pct": 2})
def test_rail_inline_markers(verify, psu, dmm, connections) -> None:
    """All three markers inline. ``connections`` fixture (sibling to
    ``context.connections``) drives iteration."""
    psu.set_voltage(5.0)
    for _ in connections:
        verify("v_rail", dmm.measure_dc_voltage())


class TestIdle:
    def test_idle_current(self, verify, psu, context) -> None:
        for _ in context.connections:
            verify("i_idle", psu.measure_current())

    def test_no_load_voltage(self, verify, dmm, context) -> None:
        for _ in context.connections:
            verify("v_rail", dmm.measure_dc_voltage())
