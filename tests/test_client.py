"""``LitmusClient`` results-API outcome propagation.

The catch-all results API (``LitmusClient`` / ``RunBuilder``) must stamp
step and run outcomes for the common ``step.measure()`` path the same way
the explicit ``step.vector()`` path does. A passing default-vector step
used to end ``outcome=None`` (``StepBuilder._finish`` only propagated
``FAILED``), which excluded it from step counts and broke yield/RTY/DPMO
for any non-pytest writer (LabVIEW/TestStand/custom).
"""

from __future__ import annotations

from litmus.client import LitmusClient
from litmus.data.models import Outcome


def test_passing_measure_step_propagates_passed(tmp_path):
    client = LitmusClient(data_dir=tmp_path)
    run = client.start_run(uut_serial="SN-PASS", station_id="bench")
    with run.step("rail_check") as step:
        step.measure("vcc", 3.30, unit="V", low=3.0, high=3.6)
    tr = run.finish()

    assert tr.steps[0].outcome == Outcome.PASSED
    assert tr.outcome == Outcome.PASSED


def test_failing_measure_step_propagates_failed(tmp_path):
    client = LitmusClient(data_dir=tmp_path)
    run = client.start_run(uut_serial="SN-FAIL", station_id="bench")
    with run.step("rail_check") as step:
        step.measure("vcc", 9.99, unit="V", low=3.0, high=3.6)
    tr = run.finish()

    assert tr.steps[0].outcome == Outcome.FAILED
    assert tr.outcome == Outcome.FAILED
