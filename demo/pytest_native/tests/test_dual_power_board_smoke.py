"""Pytest-native port of ``demo/sequences/dual_power_board_smoke.yaml``.

Multi-DUT variant. Each DUT runs in its own subprocess (LITMUS_SLOT_ID
set) via the main Litmus plugin's slot runner; the ``sync`` fixture
blocks until all slots reach a shared barrier. Tests themselves are
slot-unaware — same class, same methods, same fixtures.

Run with::

    cd demo
    pytest pytest_native/tests/test_dual_power_board_smoke.py \\
        --station=demo_station_001 \\
        --fixture-config=fixtures/dual_power_board.yaml \\
        --dut-serials=SN001,SN002 \\
        --mock-instruments -v
"""

from __future__ import annotations

from typing import Any

from demo.drivers import DMM, PSU, ELoad
from litmus.execution.harness import Context
from litmus.execution.logger import TestRunLogger


class TestDualPowerBoardSmoke:
    """Parallel smoke test for two power boards; sync before output measure."""

    def test_power_up(
        self,
        context: Context,
        psu: PSU,
        logger: TestRunLogger,
    ) -> None:
        """Power up each board independently (no sync needed)."""
        vin = context.get_param("vin")
        psu.set_voltage(vin)
        psu.set_current_limit(0.1)
        psu.enable_output()

        # Sidecar resolves `startup_current` to an LE-comparator limit.
        current_ma = float(psu.measure_current()) * 1000
        logger.measure("startup_current", current_ma)

    def test_output_voltage_synced(
        self,
        context: Context,
        psu: PSU,
        dmm: DMM,
        sync: Any,
        verify,
    ) -> None:
        """Verify 3.3V output after ALL boards are powered.

        The ``sync`` fixture (session-scoped, from the main plugin) is
        ``None`` in single-slot mode and a ``SyncPoint`` in worker mode.
        Waiting before the measurement guarantees every DUT is powered
        — relevant when boards share a bus or thermal environment.
        """
        vin = context.get_param("vin")
        psu.set_voltage(vin)
        psu.set_current_limit(0.5)
        psu.enable_output()

        if sync is not None:
            sync.wait("all_powered", timeout=30)

        verify("output_voltage", dmm.measure_dc_voltage())

    def test_efficiency(
        self,
        context: Context,
        psu: PSU,
        dmm: DMM,
        eload: ELoad,
        verify,
    ) -> None:
        """Measure efficiency — spec-driven per slot, no sync."""
        vin = context.get_param("vin")
        load = context.get_param("load_current")

        psu.set_voltage(vin)
        psu.set_current_limit(1.0)
        psu.enable_output()

        eload.set_current(load)
        eload.enable()

        v_in = float(psu.measure_voltage())
        i_in = float(psu.measure_current())
        v_out = float(dmm.measure_dc_voltage())

        eload.disable()

        p_in = v_in * i_in
        p_out = v_out * load
        efficiency = (p_out / p_in * 100) if p_in > 0 else 0
        verify("efficiency", efficiency)
