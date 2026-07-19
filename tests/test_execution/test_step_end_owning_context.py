"""Regression (#24-4): the step-end ``configure()`` merge uses the step's OWNING
Context (captured at ``start_step``), not ``get_current_context()`` — which at a
container / auto-close ``StepEnded`` may have moved to another step's context.
"""

from __future__ import annotations

from uuid import uuid4

from testerkit.data.events import StepEnded
from testerkit.execution._state import push_current_context, reset_current_context
from testerkit.execution.harness import Context, TestHarness
from testerkit.execution.run_scope import RunScope


class _FakeLog:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


def test_step_end_merge_uses_owning_context_not_ambient():
    run_scope = RunScope(uut_serial="SN1", station_id="st1")
    log = _FakeLog()
    run_scope.event_log = log  # type: ignore[assignment]

    owning = Context(harness=TestHarness(session_id=uuid4()))
    ambient = Context(harness=TestHarness(session_id=uuid4()))

    tok_a = push_current_context(owning)
    try:
        run_scope.start_step("calibrate")
        owning.configure("vin", 7.0)
        # Ambient context moves on (the container / auto-close hazard) before end.
        tok_b = push_current_context(ambient)
        try:
            ambient.configure("vin", 999.0)
            run_scope.end_step()
        finally:
            reset_current_context(tok_b)
    finally:
        reset_current_context(tok_a)

    # The non-swept step carries its own data: StepEnded.inputs reflects the
    # owning context's configured value, not the ambient 999.0.
    ended = next(e for e in log.events if isinstance(e, StepEnded))
    assert ended.inputs.get("vin") == 7.0
