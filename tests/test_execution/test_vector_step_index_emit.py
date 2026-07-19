"""Regression: VectorStarted, VectorEnded, and Observation emit the correct
step_index for steps beyond index 0.

Prior to the fix, all three emit sites used ``getattr(step, "step_index", 0)``
on a TestStep — which has no ``step_index`` attribute — so the fallback 0 was
always stamped.  The fix reads ``run_scope._current_step_index`` instead, which
is the same counter StepStarted uses.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from testerkit.data.events import Observation, VectorEnded, VectorStarted
from testerkit.data.models import TestStep, TestVector
from testerkit.execution._state import (
    push_current_step,
    push_current_vector,
    reset_current_step,
    reset_current_vector,
)
from testerkit.execution.harness import Context, TestHarness


class FakeEventLog:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


class FakeTestRun:
    def __init__(self, run_id: UUID) -> None:
        self.id = run_id


class FakeRunScope:
    def __init__(self, event_log: FakeEventLog, run_id: UUID, step_index: int) -> None:
        self.event_log = event_log
        self.test_run = FakeTestRun(run_id)
        self._current_step_index = step_index
        self._step_enclosing: list[Any] = []
        self._occurrences: dict[tuple[str, int | None, int], int] = {}

    def next_vector_occurrence(
        self, step_path: str, vector_outer_index: int | None, vector_index: int
    ) -> int:
        key = (step_path, vector_outer_index, vector_index)
        n = self._occurrences.get(key, 0)
        self._occurrences[key] = n + 1
        return n


@pytest.fixture
def event_log() -> FakeEventLog:
    return FakeEventLog()


@pytest.fixture
def session_id() -> UUID:
    return uuid4()


@pytest.fixture
def run_scope(event_log: FakeEventLog) -> FakeRunScope:
    return FakeRunScope(event_log=event_log, run_id=uuid4(), step_index=3)


@pytest.fixture
def ctx(session_id: UUID) -> Context:
    harness = TestHarness(session_id=session_id)
    return Context(harness=harness)


def test_emit_vector_started_uses_run_scope_step_index(
    monkeypatch: pytest.MonkeyPatch,
    ctx: Context,
    event_log: FakeEventLog,
    run_scope: FakeRunScope,
) -> None:
    import testerkit.execution.harness as harness_mod

    monkeypatch.setattr(harness_mod, "get_current_run_scope", lambda: run_scope)

    step = TestStep(name="calibrate", step_path="power/calibrate")
    vector = TestVector(index=0)

    step_token = push_current_step(step)
    vec_token = push_current_vector(vector)
    try:
        ctx._emit_vector_started()
    finally:
        reset_current_vector(vec_token)
        reset_current_step(step_token)

    assert len(event_log.events) == 1
    ev = event_log.events[0]
    assert isinstance(ev, VectorStarted)
    assert ev.step_index == 3


def test_emit_vector_ended_uses_run_scope_step_index(
    monkeypatch: pytest.MonkeyPatch,
    ctx: Context,
    event_log: FakeEventLog,
    run_scope: FakeRunScope,
) -> None:
    import testerkit.execution.harness as harness_mod

    monkeypatch.setattr(harness_mod, "get_current_run_scope", lambda: run_scope)

    step = TestStep(name="calibrate", step_path="power/calibrate")
    vector = TestVector(index=0)

    step_token = push_current_step(step)
    vec_token = push_current_vector(vector)
    try:
        ctx._emit_vector_ended()
    finally:
        reset_current_vector(vec_token)
        reset_current_step(step_token)

    assert len(event_log.events) == 1
    ev = event_log.events[0]
    assert isinstance(ev, VectorEnded)
    assert ev.step_index == 3


def test_emit_observation_uses_run_scope_step_index(
    monkeypatch: pytest.MonkeyPatch,
    ctx: Context,
    event_log: FakeEventLog,
    run_scope: FakeRunScope,
) -> None:
    import testerkit.execution.harness as harness_mod

    monkeypatch.setattr(harness_mod, "get_current_run_scope", lambda: run_scope)

    step = TestStep(name="calibrate", step_path="power/calibrate")
    vector = TestVector(index=0)

    step_token = push_current_step(step)
    vec_token = push_current_vector(vector)
    try:
        ctx._emit_observation("voltage", 3.3)
    finally:
        reset_current_vector(vec_token)
        reset_current_step(step_token)

    assert len(event_log.events) == 1
    ev = event_log.events[0]
    assert isinstance(ev, Observation)
    assert ev.step_index == 3
