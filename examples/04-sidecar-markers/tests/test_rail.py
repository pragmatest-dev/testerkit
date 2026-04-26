"""Rail tests with no decorators — markers live next door in YAML.

Compare ``tests/test_rail.py`` (this file) to ``tests/test_rail.yaml``.
The YAML declares:

* a file-wide ``litmus_limits`` for ``v_rail``
* a per-test ``litmus_vectors`` on ``vin`` for the sweep
* a class-level ``litmus_limits`` for ``i_idle`` that every method in ``TestIdle`` inherits

Nothing in this file imports Litmus. The test function signatures
alone drive execution; limits and vectors are configuration.
"""

from __future__ import annotations


def test_rail_within_spec(verify, psu, dmm) -> None:
    psu.set_voltage(5.0)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage())


def test_rail_holds_across_input(verify, psu, dmm, vin: float) -> None:
    psu.set_voltage(vin)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage())


class TestIdle:
    """Group related checks with a class; share class-level markers in YAML."""

    def test_idle_current(self, verify, psu) -> None:
        verify("i_idle", psu.measure_current())

    def test_no_load_voltage(self, verify, dmm) -> None:
        verify("v_rail", dmm.measure_dc_voltage())
