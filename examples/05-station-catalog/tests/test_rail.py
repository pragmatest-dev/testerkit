"""Test bodies take instruments as fixtures — same as stages 2-4.

Two new markers land here, each shown inline and in the sidecar:

* ``litmus_mock`` — patch one method on one fixture for one test.
  Use case: the station's ``mock_config`` returns a nominal value;
  to exercise a fault path (OVP, undervoltage) you need a
  *different* return for one test.
* ``litmus_prompt`` — gate the test on operator interaction (a
  confirmation, a choice, an input). ``LITMUS_PROMPT_MODE=auto-confirm``
  drives the demo without a tty; production runs route through a
  UI handler or terminal.
"""

from __future__ import annotations

import pytest


def test_rail_within_spec(verify, psu, dmm) -> None:
    """Source 5 V into the rail input and log the output voltage."""
    psu.set_voltage(5.0)
    psu.set_current(0.5)
    verify("v_rail", dmm.measure_dc_voltage())


def test_rail_holds_across_input(verify, psu, dmm, vin: float) -> None:
    psu.set_voltage(vin)
    verify("v_rail", dmm.measure_dc_voltage())


# --- litmus_mock ---


@pytest.mark.litmus_mock(target="dmm.measure_dc_voltage", return_value=4.5)
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


# --- litmus_prompt ---


@pytest.mark.litmus_prompt(
    pick_fixture={
        "message": "Pick a fixture variant",
        "prompt_type": "choice",
        "choices": ["bench_01", "bench_02"],
    }
)
def test_operator_choice_inline(verify, prompt, psu, dmm) -> None:
    """Auto-confirm returns the first choice; the assert proves the prompt fired."""
    chosen = prompt("pick_fixture")
    assert chosen == "bench_01"
    psu.set_voltage(5.0)
    verify("v_rail", dmm.measure_dc_voltage())


def test_operator_choice_sidecar(verify, prompt, psu, dmm) -> None:
    """Same gate; prompt config in the sidecar."""
    chosen = prompt("pick_fixture")
    assert chosen == "bench_01"
    psu.set_voltage(5.0)
    verify("v_rail", dmm.measure_dc_voltage())


class TestIdle:
    def test_idle_current(self, verify, psu) -> None:
        verify("i_idle", psu.measure_current())

    def test_no_load_voltage(self, verify, dmm) -> None:
        verify("v_rail", dmm.measure_dc_voltage())
