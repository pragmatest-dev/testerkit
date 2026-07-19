"""Phase 1 grain-reshape verification: event-stream shape.

Verifies at the RunScope API level (no daemon, no parquet) that Phase 1
produces the correct event sequence for the four canonical cases in the
permutation table.

Row A  -- non-swept step: StepStarted + StepEnded, zero VectorStarted.
Row B  -- Mode-1 (@parametrize): two VectorStarted (vi=0,1); both
          StepStarted carry vector_index=None (a step has no own index) and
          vector_outer_index=None (no enclosing loop at top level).
Row H  -- class-outer (testerkit_sweeps): C emits VectorStarted per outer point;
          m.StepStarted.vector_index=None, vector_outer_index = enclosing
          outer index (0 then 1).
Pre-merge -- inner vector's inputs already contain enclosing condition.
"""

from __future__ import annotations

from typing import Any

from testerkit.data.events import StepEnded, StepStarted, VectorEnded, VectorStarted
from testerkit.data.models import TestVector
from testerkit.execution.run_scope import RunScope


class _FakeLog:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)

    def of_type(self, cls: type) -> list[Any]:
        return [e for e in self.events if isinstance(e, cls)]


def _scope() -> tuple[RunScope, _FakeLog]:
    rs = RunScope(uut_serial="SN1", station_id="st1")
    log = _FakeLog()
    rs.event_log = log  # type: ignore[assignment]
    return rs, log


# ---------------------------------------------------------------------------
# Row A: non-swept step
# ---------------------------------------------------------------------------


def test_non_swept_step_zero_vector_started() -> None:
    rs, log = _scope()
    rs.start_step("t")
    rs.end_step()

    assert len(log.of_type(StepStarted)) == 1
    assert len(log.of_type(VectorStarted)) == 0
    assert len(log.of_type(VectorEnded)) == 0
    assert len(log.of_type(StepEnded)) == 1
    # A step has no own vector_index (canonically NULL); no enclosing at top.
    assert log.of_type(StepStarted)[0].vector_index is None
    assert log.of_type(StepStarted)[0].vector_outer_index is None


# ---------------------------------------------------------------------------
# Row B: Mode-1 (@parametrize(v=[0,1]))
# ---------------------------------------------------------------------------


def test_mode1_two_variants_emit_two_vector_started() -> None:
    rs, log = _scope()

    vec0 = TestVector(index=0, params={"v": 0})
    vec1 = TestVector(index=1, params={"v": 1})

    rs.start_step("t_sweep", step_index=0)
    rs.begin_outer_vector(vec0)
    rs.end_outer_vector(vec0)
    rs.end_step()

    rs.start_step("t_sweep", step_index=0)
    rs.begin_outer_vector(vec1)
    rs.end_outer_vector(vec1)
    rs.end_step()

    step_starts = log.of_type(StepStarted)
    vec_starts = log.of_type(VectorStarted)
    vec_ends = log.of_type(VectorEnded)

    assert len(step_starts) == 2
    assert len(vec_starts) == 2
    assert len(vec_ends) == 2

    # A step's own vector_index is canonically NULL; no enclosing at top level
    # so vector_outer_index is None too.
    assert step_starts[0].vector_index is None
    assert step_starts[1].vector_index is None
    assert step_starts[0].vector_outer_index is None
    assert step_starts[1].vector_outer_index is None

    # VectorStarted carry the variant's own index
    assert vec_starts[0].vector_index == 0
    assert vec_starts[1].vector_index == 1


# ---------------------------------------------------------------------------
# Row H: class-outer (testerkit_sweeps(temp=[25,85])) + def m(ctx)
# ---------------------------------------------------------------------------


def test_class_outer_method_gets_enclosing_vector_index() -> None:
    rs, log = _scope()

    # Iteration 0: temp=25
    rs.start_step("C", class_name="C")
    c_vec_0 = TestVector(index=0, params={"temp": 25})
    rs.begin_outer_vector(c_vec_0)
    rs.start_step("m", class_name="C")
    rs.end_step()
    rs.end_outer_vector(c_vec_0)
    rs.end_step()

    # Iteration 1: temp=85
    rs.start_step("C", class_name="C")
    c_vec_1 = TestVector(index=1, params={"temp": 85})
    rs.begin_outer_vector(c_vec_1)
    rs.start_step("m", class_name="C")
    rs.end_step()
    rs.end_outer_vector(c_vec_1)
    rs.end_step()

    step_starts = log.of_type(StepStarted)
    vec_starts = log.of_type(VectorStarted)

    # Two C containers + two m invocations = 4 StepStarted
    assert len(step_starts) == 4
    # Two C-vectors emitted
    assert len(vec_starts) == 2
    assert vec_starts[0].vector_index == 0
    assert vec_starts[1].vector_index == 1

    m_starts = [e for e in step_starts if e.step_name == "m"]
    assert len(m_starts) == 2
    # m's own vector_index is NULL; the enclosing class-outer condition rides
    # vector_outer_index (0 under temp=25, 1 under temp=85).
    assert m_starts[0].vector_index is None
    assert m_starts[1].vector_index is None
    assert m_starts[0].vector_outer_index == 0
    assert m_starts[1].vector_outer_index == 1


# ---------------------------------------------------------------------------
# Pre-merge: inner vector inputs already contain enclosing condition
# ---------------------------------------------------------------------------


def test_inner_vector_inputs_contain_enclosing_condition() -> None:
    """VectorStarted for a nested step carries merged inputs (outer + inner)."""
    rs, log = _scope()

    rs.start_step("C", class_name="C")
    c_vec = TestVector(index=0, params={"temp": 25})
    rs.begin_outer_vector(c_vec)

    # Method m has its own inner parametrize variant; inputs = merged {temp, v}
    rs.start_step("m", class_name="C")
    m_vec = TestVector(index=0, params={"temp": 25, "v": 0})
    rs.begin_outer_vector(m_vec)
    rs.end_outer_vector(m_vec)
    rs.end_step()

    rs.end_outer_vector(c_vec)
    rs.end_step()

    vec_starts = log.of_type(VectorStarted)
    m_vec_start = next(e for e in vec_starts if e.inputs.get("v") is not None)
    assert m_vec_start.inputs["temp"] == 25, "enclosing temp must be present"
    assert m_vec_start.inputs["v"] == 0, "inner v must be present"


# ---------------------------------------------------------------------------
# Pre-merge on the STEP row: a non-swept method under a swept class carries the
# enclosing condition on its own step record (Row H), so a step-scope
# measurement is filterable by the enclosing sweep without a chain-walk.
# ---------------------------------------------------------------------------


def test_non_swept_method_step_carries_enclosing_condition() -> None:
    rs, log = _scope()

    rs.start_step("C", class_name="C")
    c_vec = TestVector(index=0, params={"temp": 25})
    rs.begin_outer_vector(c_vec)
    rs.start_step("m", class_name="C")  # non-swept method, no own vector
    rs.end_step()
    rs.end_outer_vector(c_vec)
    rs.end_step()

    m_started = next(e for e in log.of_type(StepStarted) if e.step_name == "m")
    m_ended = next(e for e in log.of_type(StepEnded) if e.step_name == "m")
    assert m_started.inputs["temp"] == 25
    assert m_ended.inputs["temp"] == 25
