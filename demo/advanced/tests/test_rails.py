"""Multi-pin rail characterization for the PMIC-A23.

Exercises ``ctx.points`` — the framework's iteration primitive. The
sidecar declares ``characteristic: rail_voltage_trio`` (a multi-pin
``ProductCharacteristic``) and the plugin expands it to an ordered
list of :class:`FixturePoint`. Iterating ``ctx.points`` pushes each
point into a ContextVar; ``dmm`` fixtures route through the active
point's channel, and ``logger.measure`` stamps the per-rail DUT pin,
channel, net, and terminal on every row.

Test code sees opaque handles only — no pin IDs, no channel numbers,
no spec IDs.
"""

from __future__ import annotations

from demo.drivers import DMM
from litmus.execution.harness import Context
from litmus.execution.logger import TestRunLogger


class TestRails:
    """Per-rail voltage capture using ``ctx.points`` iteration."""

    def test_rail_voltages(
        self,
        context: Context,
        dmm: DMM,
        logger: TestRunLogger,
    ) -> None:
        """Record each rail's voltage; one row per rail, distinct pins.

        ``logger.measure`` (not ``verify``) — this is characterization
        data, not a screen. The sidecar does not declare a numeric limit
        for ``rail_voltage``, so rows land unchecked; the value, pin,
        channel, and spec_ref columns are still populated.
        """
        for _ in context.points:
            logger.measure("rail_voltage", float(dmm.measure_dc_voltage()))
