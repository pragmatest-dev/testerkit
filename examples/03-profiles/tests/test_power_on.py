"""Power-on smoke tests for the PMIC-A23.

Exercises Litmus's simplest patterns:

* A class-based test with three ordered methods. Tests are independent
  by default — use ``pytest-dependency`` if you need to skip downstream
  tests on an upstream failure.
* Measurement labels only — no pin IDs, no characteristic IDs, no limits
  in the test source. Sidecar ``test_power_on.yaml`` binds labels to
  product characteristics via ``limits:``; the framework fills in pin,
  channel, net, and spec-ref columns automatically.

Run::

    cd examples/03-profiles
    uv run pytest tests/test_power_on.py --mock-instruments -v
"""

from __future__ import annotations

from examples.drivers import DMM, PSU
from litmus.execution.harness import Context


class TestPowerOn:
    """Sequential power-on gate for the PMIC."""

    def test_power_up(self, context: Context, psu: PSU, verify) -> None:
        """Apply VIN and confirm the supply reads back."""
        vin = context.get_param("vin")
        psu.set_voltage(vin)
        psu.set_current_limit(0.1)
        psu.enable_output()

        verify("vin_applied", float(psu.measure_voltage()))

    def test_quiescent_current(self, psu: PSU, verify) -> None:
        """Quiescent (no-load) ground current against the product spec."""
        current_ma = float(psu.measure_current()) * 1000.0
        verify("quiescent_current", current_ma)

    def test_rail_3v3_nominal(self, dmm: DMM, verify) -> None:
        """3V3 rail at light load against the product spec."""
        verify("rail_3v3", float(dmm.measure_dc_voltage()))
