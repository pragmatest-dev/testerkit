"""Class-as-sequence: a swept class whose methods run as an ordered sequence.

When ``@pytest.mark.litmus_sweeps`` decorates the CLASS, every method
in the class runs once per outer iteration in source order. The class
becomes the outer loop — pytest still produces N pytest items, but
they're ordered condition-first (full sequence per voltage, not per
method).

Two patterns shown here:

* :class:`TestPowerSequence` — class-level outer sweep, methods consume
  ``voltage`` as a fixture argument. Plain method-level sweeps stack on
  top for additional inner dimensions.
* :class:`TestPowerSequenceWithInnerLoop` — same outer sweep, but the
  method uses the ``vectors`` fixture to own the inner loop. Outer
  ``voltage`` still expands at the pytest layer; the inner ``current``
  matrix gets consumed by the ``vectors`` fixture.

Run with ``-v`` to see the condition-first execution order.
"""

from __future__ import annotations

import pytest


@pytest.mark.litmus_sweeps([{"voltage": [3.3, 5.0]}])
class TestPowerSequence:
    """Sequence: warmup → load test → cooldown, run once per voltage."""

    @pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 5.5, "unit": "V"})
    def test_warmup(self, voltage: float, verify, psu, dmm) -> None:
        psu.set_voltage(voltage)
        psu.enable_output()
        verify("v_rail", dmm.measure_dc_voltage())

    @pytest.mark.litmus_sweeps([{"current": [0.1, 0.5]}])  # method-level inner
    @pytest.mark.litmus_limits(v_rail={"low": 3.0, "high": 5.5, "unit": "V"})
    def test_load_regulation(self, voltage: float, current: float, verify, psu, dmm) -> None:
        # Outer voltage × inner current = 4 executions of this method,
        # interleaved with the surrounding methods condition-first.
        psu.set_voltage(voltage)
        # `current` would drive an electronic load in a real station; here we
        # just record it so the analytics layer can chart v_rail vs current.
        _ = current
        verify("v_rail", dmm.measure_dc_voltage())

    def test_cooldown(self, voltage: float, psu) -> None:
        _ = voltage  # used by the outer sweep; the cooldown itself ignores it.
        psu.disable_output()


@pytest.mark.litmus_sweeps([{"voltage": [3.3, 5.0]}])
class TestPowerSequenceWithInnerLoop:
    """Same outer sweep — but the method body owns the inner loop.

    Compare with the ``test_load_regulation`` above: that method is
    a separate pytest item per (voltage, current) combination — 4
    items total. The method below is ONE pytest item per voltage
    (2 items), each iterating 3 currents internally.

    When to prefer this shape: amortize expensive setup, stream rows
    into one row of analytics output, or just keep the loop in the
    test body for readability.
    """

    @pytest.mark.litmus_sweeps([{"current": [0.1, 0.3, 0.5]}])  # consumed by vectors fixture
    @pytest.mark.litmus_limits(v_rail={"low": 3.0, "high": 5.5, "unit": "V"})
    def test_load_sweep(self, voltage: float, vectors, verify, psu, dmm) -> None:
        # ``voltage`` arrives via pytest parametrize (outer).
        # ``vectors`` iterates the inner ``current`` matrix.
        psu.set_voltage(voltage)
        psu.enable_output()
        for v in vectors:
            # Each iteration sees the active inner row; measurements stamped
            # with the full effective inputs (outer voltage + inner current).
            _ = v["current"]  # would drive an eload in a real station
            verify("v_rail", dmm.measure_dc_voltage())
