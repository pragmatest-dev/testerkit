"""Pytest-native port of ``demo/sequences/power_board_smoke.yaml``.

Demonstrates the three-object split:

* ``context`` — vector inputs (``context.get_in("vin")``).
* ``spec``    — product-characteristic assertions (``spec.check("output_voltage", v)``).
* ``logger``  — ad-hoc measurements with inline or sidecar limits
  (``logger.measure("efficiency", e, low=55, high=100, units="%")``).

Sidecar file ``test_power_board_smoke.yaml`` carries vectors + limits.
The product spec at ``demo/products/power_board.yaml`` supplies the
``ref:`` limits.
"""

from __future__ import annotations

import pytest

from demo.drivers import DMM, PSU, ELoad
from litmus.execution.harness import Context
from litmus.execution.logger import TestRunLogger
from litmus.execution.plugin import LitmusSequence
from litmus.products.context import SpecContext


class TestPowerBoardSmoke(LitmusSequence):
    """Power-up verification for production screening."""

    def test_basic_power(
        self,
        context: Context,
        psu: PSU,
        dmm: DMM,
        spec: SpecContext,
    ) -> None:
        """Verify 3.3V output at no load — spec-driven."""
        vin = context.get_in("vin")
        psu.set_voltage(vin)
        psu.set_current_limit(0.1)
        psu.enable_output()

        spec.check("output_voltage", dmm.measure_dc_voltage(), load=0.1)

    @pytest.mark.flaky(reruns=2, reruns_delay=0.5)
    def test_load_test(
        self,
        context: Context,
        psu: PSU,
        dmm: DMM,
        eload: ELoad,
        logger: TestRunLogger,
    ) -> None:
        """Verify output under 800mA load (sidecar-driven limit, retryable)."""
        vin = context.get_in("vin")
        load = context.get_in("load_current")

        psu.set_voltage(vin)
        psu.set_current_limit(1.0)
        psu.enable_output()

        eload.set_current(load)
        eload.enable()

        # Resolves against sidecar `output_voltage_load:` (named uniquely
        # because spec.check("output_voltage", ...) would push the
        # light-load limit; this step uses a looser one for load test).
        logger.measure("output_voltage_load", dmm.measure_dc_voltage())

        eload.disable()

    def test_quiescent(
        self,
        context: Context,
        psu: PSU,
        spec: SpecContext,
    ) -> None:
        """Verify low standby current — spec-driven."""
        vin = context.get_in("vin")
        psu.set_voltage(vin)
        psu.set_current_limit(0.05)
        psu.enable_output()

        current_ma = float(psu.measure_current()) * 1000
        spec.check("quiescent_current", current_ma, load=0)

    def test_regulation_sweep(
        self,
        context: Context,
        psu: PSU,
        dmm: DMM,
        eload: ELoad,
        logger: TestRunLogger,
    ) -> None:
        """Sweep VIN × load (5×5 = 25 combinations, sidecar-driven limit)."""
        vin = context.get_in("vin")
        load = context.get_in("load_current")

        if context.changed("vin"):
            psu.set_voltage(vin)
            psu.set_current_limit(1.0)
            psu.enable_output()

        eload.set_current(load)
        eload.enable()

        logger.measure("output_voltage_sweep", dmm.measure_dc_voltage())

        eload.disable()
