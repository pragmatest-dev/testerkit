"""Bench is config-driven now — instruments come from the station YAML
and pin↔channel routing comes from the fixture YAML. Tests iterate
``ctx.connections`` instead of naming an instrument explicitly per
measurement.

Two new markers also land here, each shown inline and in the sidecar:

* ``litmus_mocks`` — patch one or more methods on a fixture for one
  test. Use case: the station's ``mock_config`` returns a nominal
  value; to exercise a fault path (OVP, undervoltage) you need a
  *different* return for one test.
* ``litmus_prompts`` — gate the test on operator interaction (a
  confirmation, a choice, an input). ``LITMUS_AUTO_CONFIRM=1``
  drives the demo without a tty; production runs route through a
  UI handler or terminal.

The conftest from earlier stages is gone — instrument fixtures
(``psu``, ``dmm``) are auto-registered from
``stations/bench_01.yaml``. Limits flow from the product spec
introduced in stage 5; the fixture YAML wires DUT pins through the
station instruments.
"""

from __future__ import annotations

import pytest


def test_rail_within_spec(verify, psu, dmm, context) -> None:
    """5 V in → 3.3 V out; iterate the rail_3v3 fixture connection."""
    psu.set_voltage(5.0)
    psu.set_current(0.5)
    for _ in context.connections:
        verify("v_rail", dmm.measure_dc_voltage())


def test_rail_holds_across_input(verify, psu, dmm, context, vin: float) -> None:
    """Sweep vin; same connection iteration, same spec-driven limit."""
    psu.set_voltage(vin)
    for _ in context.connections:
        verify("v_rail", dmm.measure_dc_voltage())


@pytest.mark.litmus_characteristics("rail_3v3")
@pytest.mark.litmus_connections(["vout_measure"])
@pytest.mark.litmus_limits(v_rail={"characteristic": "rail_3v3", "tolerance_pct": 2})
def test_rail_inline_markers(verify, psu, dmm, connections) -> None:
    """All three markers inline. ``connections`` fixture (sibling to
    ``context.connections``) drives iteration."""
    psu.set_voltage(5.0)
    for _ in connections:
        verify("v_rail", dmm.measure_dc_voltage())


# --- litmus_mocks ---


@pytest.mark.litmus_mocks([{"target": "dmm.measure_dc_voltage", "return_value": 4.5}])
def test_ovp_path_inline(verify, psu, dmm) -> None:
    """Override the bench mock so the OVP band sees a real OVP value.

    Without the override, ``dmm.measure_dc_voltage()`` returns 3.31
    (from ``stations/bench_01.yaml: mock_config:``) which falls below
    the ``v_overvoltage`` band (4.0-5.0) and the test fails.
    """
    psu.set_voltage(5.0)
    verify("v_overvoltage", dmm.measure_dc_voltage())


def test_ovp_path_sidecar(verify, psu, dmm) -> None:
    """Same fault-injection check; mock declared in the sidecar."""
    psu.set_voltage(5.0)
    verify("v_overvoltage", dmm.measure_dc_voltage())


# --- litmus_prompts ---


@pytest.mark.litmus_prompts(
    pick_fixture={
        "message": "Pick a fixture variant",
        "prompt_type": "choice",
        "choices": ["bench_01", "bench_02"],
    }
)
def test_operator_choice_inline(logger, prompt, psu, dmm) -> None:
    """Auto-confirm returns the first choice; the assert proves the prompt fired.

    No limit is configured for this gate — the rail value is recorded as
    DONE via ``logger.measure``. ``verify`` is for judgment-bearing
    measurements; ``logger.measure`` is the recorder.
    """
    chosen = prompt("pick_fixture")
    assert chosen == "bench_01"
    psu.set_voltage(5.0)
    logger.measure("v_rail", dmm.measure_dc_voltage())


def test_operator_choice_sidecar(logger, prompt, psu, dmm) -> None:
    """Same gate; prompt config in the sidecar."""
    chosen = prompt("pick_fixture")
    assert chosen == "bench_01"
    psu.set_voltage(5.0)
    logger.measure("v_rail", dmm.measure_dc_voltage())


class TestIdle:
    def test_idle_current(self, verify, psu, context) -> None:
        for _ in context.connections:
            verify("i_idle", psu.measure_current())

    def test_no_load_voltage(self, verify, dmm, context) -> None:
        for _ in context.connections:
            verify("v_rail", dmm.measure_dc_voltage())
