"""Test bodies shrink further — limits derive from the product spec.

The sidecar binds each test to a characteristic (``rail_3v3``,
``idle_current``). The body iterates ``context.points`` so each
measurement row stamps ``spec_id`` / ``spec_ref`` / ``dut_pin``.
Single-pin characteristics iterate once; multi-pin ones iterate
per-pin — same loop shape either way.
"""

from __future__ import annotations


def test_rail_within_spec(verify, psu, dmm, context) -> None:
    psu.set_voltage(5.0)
    for _ in context.points:
        verify("v_rail", dmm.measure_dc_voltage())


def test_rail_holds_across_input(verify, psu, dmm, context, vin: float) -> None:
    psu.set_voltage(vin)
    for _ in context.points:
        verify("v_rail", dmm.measure_dc_voltage())


class TestIdle:
    def test_idle_current(self, verify, psu, context) -> None:
        for _ in context.points:
            verify("i_idle", psu.measure_current())

    def test_no_load_voltage(self, verify, dmm, context) -> None:
        for _ in context.points:
            verify("v_rail", dmm.measure_dc_voltage())
