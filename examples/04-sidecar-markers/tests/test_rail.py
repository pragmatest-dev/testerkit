"""Rail tests with no decorators — markers live next door in YAML.

Compare ``tests/test_rail.py`` (this file) to ``tests/test_rail.yaml``.
The YAML declares:

* a file-wide ``litmus_limits`` for ``v_rail``
* a per-test ``litmus_sweeps`` on ``vin`` for the sweep
* a class-level ``litmus_limits`` for ``i_idle`` that every method in ``TestIdle`` inherits
* a per-test ``litmus_retry`` on ``test_intermittent_glitch`` (sidecar
  form of the marker introduced inline in stage 2)

Nothing in this file imports Litmus. The test function signatures
alone drive execution; config lives in YAML.
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


# Module-level counter — pytest-rerunfailures re-runs the function but
# does not re-import the module, so the counter persists across attempts.
_attempts = [0]


def test_intermittent_glitch(verify, psu, dmm) -> None:
    """First attempt raises; sidecar's litmus_retry catches it."""
    _attempts[0] += 1
    if _attempts[0] == 1:
        raise OSError("simulated VISA timeout")
    psu.set_voltage(5.0)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage())


class TestIdle:
    """Group related checks with a class; share class-level markers in YAML."""

    def test_idle_current(self, verify, psu) -> None:
        verify("i_idle", psu.measure_current())

    def test_no_load_voltage(self, verify, dmm) -> None:
        verify("v_rail", dmm.measure_dc_voltage())
