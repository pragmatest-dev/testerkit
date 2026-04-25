"""Rail tests with no decorators — markers live next door in YAML.

Compare ``tests/test_rail.py`` (this file) to ``tests/test_rail.yaml``.
The YAML declares:

* a file-wide ``litmus_limits`` for ``v_rail``
* a per-test ``parametrize`` on ``vin`` for the sweep
* a class-level ``litmus_limits`` for ``i_idle`` that every method in ``TestIdle`` inherits

Nothing in this file imports Litmus. The test function signatures
alone drive execution; limits and vectors are configuration.
"""

from __future__ import annotations


def test_rail_within_spec(verify, dut) -> None:
    verify("v_rail", dut.read_voltage())


def test_rail_holds_across_input(verify, dut, vin: float) -> None:
    dut.set_input(vin)
    verify("v_rail", dut.read_voltage())


class TestIdle:
    """Group related checks with a class; share class-level markers in YAML."""

    def test_idle_current(self, verify, dut) -> None:
        verify("i_idle", dut.read_current())

    def test_no_load_voltage(self, verify, dut) -> None:
        verify("v_rail", dut.read_voltage())
