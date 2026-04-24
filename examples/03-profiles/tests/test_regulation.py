"""Regulation sweeps for the PMIC-A23.

Exercises Litmus's sweep primitives:

* Cartesian vector expansion via sidecar ``vectors.product`` — one test
  method collects into N parametrized cases.
* ``context.changed(key)`` — only reprogram the supply when the sweep
  key advances, avoiding redundant bench operations.
* ``@pytest.mark.flaky(reruns=2)`` — transient-measurement retry via
  ``pytest-rerunfailures``. No Litmus-specific retry decorator; the
  stock pytest-ecosystem plugin slots in.

Profile overrides (``examples/03-profiles/litmus.yaml``) collapse the
sweeps to a single nominal point under ``--litmus-profile production``
for screening runs; ``characterization`` uses the full sweeps.
"""

from __future__ import annotations

import pytest
from drivers import DMM, PSU, ELoad

from litmus.execution.harness import Context


class TestRegulation:
    """Line and load regulation across VIN × load."""

    def test_line_regulation(
        self,
        context: Context,
        psu: PSU,
        dmm: DMM,
        verify,
    ) -> None:
        """Sweep VIN; 3V3 rail must stay within the line-regulation band."""
        vin = context.get_param("vin")
        if context.changed("vin"):
            psu.set_voltage(vin)
            psu.set_current_limit(1.0)
            psu.enable_output()

        verify("rail_3v3_under_line", float(dmm.measure_dc_voltage()))

    @pytest.mark.flaky(reruns=2, reruns_delay=0.2)
    def test_load_regulation(
        self,
        context: Context,
        psu: PSU,
        dmm: DMM,
        eload: ELoad,
        verify,
    ) -> None:
        """Sweep load; 3V3 rail must stay within the load-regulation band.

        Marked flaky to demonstrate pytest-rerunfailures retry. The
        sidecar keeps retry scoped to this single method.
        """
        load = context.get_param("load_current")
        psu.set_voltage(5.0)
        psu.set_current_limit(1.0)
        psu.enable_output()

        eload.set_current(load)
        eload.enable()

        verify("rail_3v3_under_load", float(dmm.measure_dc_voltage()))

        eload.disable()
