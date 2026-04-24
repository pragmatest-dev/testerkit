"""Station-driven smoke test (Tier 2).

Demonstrates the fixture split:

* ``context`` — vector params (``context.get_param("vin")``).
* ``verify``  — the primary verb: log + evaluate + raise on FAIL.
* ``logger``  — pure recorder for characterization rows (no assert).

Sidecar file ``test_power_board_smoke.yaml`` carries vectors + limits.
The product spec at ``products/power_board.yaml`` supplies the ``ref:``
limits — ``verify`` auto-fills pin/instrument/spec_ref from the active
:class:`SpecContext`.
"""

from __future__ import annotations

import pytest
from drivers import DMM, PSU, ELoad

from litmus.execution.harness import Context
from litmus.execution.logger import TestRunLogger


class TestPowerBoardSmoke:
    """Power-up verification for production screening."""

    def test_basic_power(
        self,
        context: Context,
        psu: PSU,
        dmm: DMM,
        verify,
    ) -> None:
        """Verify 3.3V output at no load — spec-driven."""
        vin = context.get_param("vin")
        psu.set_voltage(vin)
        psu.set_current_limit(0.1)
        psu.enable_output()

        verify("output_voltage", dmm.measure_dc_voltage())

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
        vin = context.get_param("vin")
        load = context.get_param("load")

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
        verify,
    ) -> None:
        """Verify low standby current — spec-driven."""
        vin = context.get_param("vin")
        psu.set_voltage(vin)
        psu.set_current_limit(0.05)
        psu.enable_output()

        current_ma = float(psu.measure_current()) * 1000
        verify("quiescent_current", current_ma)

    def test_regulation_sweep(
        self,
        context: Context,
        psu: PSU,
        dmm: DMM,
        eload: ELoad,
        logger: TestRunLogger,
    ) -> None:
        """Sweep VIN × load (5×5 = 25 combinations, sidecar-driven limit)."""
        vin = context.get_param("vin")
        load = context.get_param("load")

        if context.changed("vin"):
            psu.set_voltage(vin)
            psu.set_current_limit(1.0)
            psu.enable_output()

        eload.set_current(load)
        eload.enable()

        logger.measure("output_voltage_sweep", dmm.measure_dc_voltage())

        eload.disable()
