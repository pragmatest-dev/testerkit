"""Limits derive from the part datasheet — no fixture YAML yet.

The part spec (``parts/buck_3v3.yaml``) declares each
characteristic's nominal value and unit once. Sidecar limits point
at a characteristic and add a ``tolerance_pct``; the resolver reads
the band from the part at measurement time. Each row carries
``characteristic_id`` for spec traceability without the test code
typing the nominal anywhere.

The bench is still the conftest from stages 2-4: ``psu`` and ``dmm``
fixtures defined in ``conftest.py`` (mocked under
``--mock-instruments``). Stage 6 swaps that hand-rolled conftest for a
station YAML AND introduces fixture connections that drive
``ctx.connections`` iteration. This stage shows the spec layer in
isolation so each concept has its own chapter.
"""

from __future__ import annotations

import pytest


def test_rail_within_spec(verify, psu, dmm) -> None:
    """5 V in → 3.3 V out; limit = rail_3v3 ± 2% from part spec."""
    psu.set_voltage(5.0)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage())


def test_rail_holds_across_input(verify, psu, dmm, vin: float) -> None:
    """Sweep vin; limit still derived from rail_3v3."""
    psu.set_voltage(vin)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage())


@pytest.mark.litmus_limits(v_rail={"characteristic": "rail_3v3", "tolerance_pct": 2})
def test_rail_inline_marker(verify, psu, dmm) -> None:
    """Same spec-driven limit as the sidecar tests, just declared inline."""
    psu.set_voltage(5.0)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage())


class TestIdle:
    """Two checks under one class. Both bind their own characteristic."""

    def test_idle_current(self, verify, psu) -> None:
        verify("i_idle", psu.measure_current())

    def test_no_load_voltage(self, verify, dmm) -> None:
        verify("v_rail", dmm.measure_dc_voltage())
