"""Engineering units set via ``configure(unit=)`` / ``observe(unit=)`` reach the
materialized vector's ``input_units`` / ``output_units`` — the parquet-bound
lane unit columns.

Root cause (fixed here): units live on the LIVE ``Context`` in
``_param_units`` / ``_observation_units``, but at vector-end the materialization
seams (``run_scope.end_outer_vector`` for Mode-1 sweeps, ``Context._emit_vector_ended``
for Mode-2 in-body loops) only read units off the VECTOR — never merging in the
live context's units the way they already merge in the live context's VALUES
(``configured_params`` / ``ctx.params``). ``configure(key, val, unit="V")`` set
``ctx._param_units`` but it never landed in ``input_units``, so the parquet unit
column stayed null.

These tests cover both materialization seams plus the new
``Context.configured_units`` / ``Context.observed_units`` public read API that
both fixes the sync source and gives programmatic unit reads.

Follow-up (same bug class, two more grains found not to be covered by the
first pass):

- **Class-swept-only method** (class-level ``@testerkit_sweeps``, no inner
  sweep): this method's data rides a plain STEP with no vector of its own
  (``vector_index`` NULL, ``vector_outer_index`` set) — it never runs through
  ``end_outer_vector`` / ``Context._emit_vector_ended`` at all.
  ``run_scope._emit_step_event``'s ``StepEnded`` branch merged
  ``own_configured`` (values) into ``inputs`` but only read the enclosing
  vector's (empty) units for ``input_units``, and hardcoded
  ``output_units={}``. Fixed by threading an ``own_configured_units``
  twin of ``own_configured`` (ambient snapshot in ``_step_ambient_config_units``,
  set by ``begin_outer_vector`` the same way as ``_step_ambient_config``) and
  reading ``ctx.observed_units`` for outputs.
- **Test-controlled self-loop** (the ``vectors`` fixture / ``_VectorIterator``,
  no marker at all): goes through ``Context._emit_vector_ended`` — the same
  seam already fixed for ``TestHarness.run_vector`` — so it's covered by the
  same fix; verified end-to-end here rather than assumed.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from testerkit.data.events import StepEnded, VectorEnded
from testerkit.data.models import TestVector
from testerkit.execution._state import (
    get_current_context,
    get_current_run_scope,
    push_current_context,
    reset_current_context,
    set_current_run_scope,
)
from testerkit.execution.harness import Context, TestHarness
from testerkit.execution.run_scope import RunScope
from testerkit.execution.vectors import Vector
from testerkit.pytest_plugin import _VectorIterator


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
# Context.configured_units / Context.observed_units — the public read API
# ---------------------------------------------------------------------------


def test_configured_units_only_keys_with_a_unit() -> None:
    ctx = Context()
    ctx.configure("vin_5v0", 5.01, unit="V")
    ctx.configure("temp", 24.8)  # no unit — must NOT appear

    assert ctx.configured_units == {"vin_5v0": "V"}
    assert "temp" not in ctx.configured_units
    # configured_params still carries both values regardless of unit presence.
    assert ctx.configured_params == {"vin_5v0": 5.01, "temp": 24.8}


def test_configured_units_parent_merge_child_wins() -> None:
    parent = Context()
    parent.configure("vin_5v0", 5.0, unit="V")
    child = parent.child()
    child.configure("vin_5v0", 5.05, unit="mV")
    child.configure("iout", 0.1, unit="A")

    assert child.configured_units == {"vin_5v0": "mV", "iout": "A"}


def test_observed_units_only_keys_with_a_unit() -> None:
    ctx = Context()
    ctx.observe("iout", 0.5, unit="A")
    ctx.observe("note", "ok")  # no unit — must NOT appear

    assert ctx.observed_units == {"iout": "A"}
    assert "note" not in ctx.observed_units


def test_observed_units_parent_merge_child_wins() -> None:
    parent = Context()
    parent.observe("iout", 0.5, unit="A")
    child = parent.child()
    child.observe("iout", 0.51, unit="mA")

    assert child.observed_units == {"iout": "mA"}


# ---------------------------------------------------------------------------
# Mode-1 (Sweep vector spanning the whole body): run_scope.end_outer_vector
# ---------------------------------------------------------------------------


def test_end_outer_vector_syncs_live_context_units_onto_vectorended() -> None:
    rs, log = _scope()
    vec = TestVector(index=0, params={"vin_5v0": 5.0})
    ctx = Context()

    rs.start_step("t_sweep", step_index=0)
    rs.begin_outer_vector(vec)
    tok = push_current_context(ctx)
    try:
        # In-body configure()/observe() with units — the bug: these never
        # reached ``vector.param_units`` / ``vector.observation_units``.
        ctx.configure("vin_5v0", 5.01, unit="V")
        ctx.observe("iout", 0.5, unit="A")
        rs.end_outer_vector(vec)
    finally:
        reset_current_context(tok)
    rs.end_step()

    ended = log.of_type(VectorEnded)[0]
    assert ended.inputs["vin_5v0"] == 5.01
    assert ended.input_units == {"vin_5v0": "V"}
    assert ended.outputs["iout"] == 0.5
    assert ended.output_units == {"iout": "A"}


def test_end_outer_vector_preserves_vector_seeded_units_when_no_live_context() -> None:
    """No live context at vector-end (e.g. manual harness driving) — the
    vector's own (seeded) units still ride through untouched."""
    rs, log = _scope()
    vec = TestVector(
        index=0,
        params={"vin_5v0": 5.0},
        param_units={"vin_5v0": "V"},
        observations={"iout": 0.5},
        observation_units={"iout": "A"},
    )

    rs.start_step("t_sweep", step_index=0)
    rs.begin_outer_vector(vec)
    rs.end_outer_vector(vec)
    rs.end_step()

    ended = log.of_type(VectorEnded)[0]
    assert ended.input_units == {"vin_5v0": "V"}
    assert ended.output_units == {"iout": "A"}


# ---------------------------------------------------------------------------
# Mode-2 (in-body loop / harness.run_vector): Context._emit_vector_ended
# ---------------------------------------------------------------------------


def test_run_vector_syncs_live_context_units_onto_vectorended_mode2() -> None:
    run_scope, log = _scope()
    session_id = uuid4()

    prior = get_current_run_scope()
    set_current_run_scope(run_scope)
    try:
        harness = TestHarness(session_id=session_id, logger=run_scope)
        run_scope.start_step("t_loop")
        vector = Vector(vin_5v0=5.0, _index=0)
        with harness.run_vector(vector) as tv:
            harness.context.configure("vin_5v0", 5.01, unit="V")
            harness.context.observe("iout", 0.5, unit="A")
        run_scope.end_step()
    finally:
        set_current_run_scope(prior)

    ended = log.of_type(VectorEnded)[0]
    assert ended.inputs["vin_5v0"] == 5.01
    assert ended.input_units == {"vin_5v0": "V"}
    assert ended.outputs["iout"] == 0.5
    assert ended.output_units == {"iout": "A"}

    # The offline batch-write path (``save_test_run`` / ``ParquetBackend.
    # _append_step_rows``) reads units straight off the TestVector object,
    # bypassing the event log entirely — so the object itself must carry
    # the same synced units, not just the emitted event.
    assert tv.param_units == {"vin_5v0": "V"}
    assert tv.observation_units == {"iout": "A"}


# ---------------------------------------------------------------------------
# Class-swept-ONLY grain (class @testerkit_sweeps, no inner sweep): the
# method's data rides a plain STEP (vector_index None, vector_outer_index
# set) — it never opens its own vector, so it never runs through
# end_outer_vector / Context._emit_vector_ended at all. Real-parquet repro:
# a class-swept-only method calling configure(unit=) landed input value but
# unit=None.
# ---------------------------------------------------------------------------


def test_class_swept_only_method_syncs_configure_and_observe_units() -> None:
    """Row H (class-outer sweep, non-swept method) — the grain that slipped
    the first pass. configure()/observe() with units in the method body must
    reach StepEnded.input_units / output_units."""
    rs, log = _scope()
    ctx = Context()

    rs.start_step("C", class_name="C")
    c_vec = TestVector(index=0, params={"vin_5v0": 5.0})
    rs.begin_outer_vector(c_vec)

    tok = push_current_context(ctx)
    try:
        rs.start_step("m", class_name="C")  # non-swept method, no own vector
        ctx.configure("vin_5v0", 5.01, unit="V")
        ctx.observe("v_out_5v0", 5.0, unit="V")
        rs.end_step()
    finally:
        reset_current_context(tok)
    rs.end_outer_vector(c_vec)
    rs.end_step()

    m_ended = next(e for e in log.of_type(StepEnded) if e.step_name == "m")
    assert m_ended.inputs["vin_5v0"] == 5.01
    assert m_ended.input_units == {"vin_5v0": "V"}
    assert m_ended.outputs["v_out_5v0"] == 5.0
    assert m_ended.output_units == {"v_out_5v0": "V"}


def test_class_swept_only_method_no_configure_leaves_units_empty() -> None:
    """No live context / no configure() calls — StepEnded.input_units is
    empty, not populated with stray values (guards against an over-eager
    merge picking up unrelated context state)."""
    rs, log = _scope()

    rs.start_step("C", class_name="C")
    c_vec = TestVector(index=0, params={"vin_5v0": 5.0})
    rs.begin_outer_vector(c_vec)
    rs.start_step("m", class_name="C")
    rs.end_step()
    rs.end_outer_vector(c_vec)
    rs.end_step()

    m_ended = next(e for e in log.of_type(StepEnded) if e.step_name == "m")
    assert m_ended.input_units == {}
    assert m_ended.output_units == {}


# ---------------------------------------------------------------------------
# Test-controlled self-loop (no marker at all): the ``vectors`` fixture's
# ``_VectorIterator`` drives per-row Context children through
# Context._emit_vector_started / _emit_vector_ended — the SAME Mode-2 seam
# already fixed for TestHarness.run_vector. Verified end-to-end rather than
# assumed, per the outer-vector-grain slip above.
# ---------------------------------------------------------------------------


def test_self_loop_vector_iterator_syncs_configure_and_observe_units() -> None:
    run_scope, log = _scope()
    base_ctx = Context(session_id=uuid4())

    prior = get_current_run_scope()
    set_current_run_scope(run_scope)
    try:
        run_scope.start_step("t_selfloop")
        matrix = [Vector(vin_5v0=5.0, _index=0)]
        it = _VectorIterator(matrix, base_ctx)
        for _row in it:
            live = get_current_context()
            assert live is not None
            live.configure("vin_5v0", 5.01, unit="V")
            live.observe("iout", 0.5, unit="A")
        run_scope.end_step()
    finally:
        set_current_run_scope(prior)

    ended = log.of_type(VectorEnded)[0]
    assert ended.inputs["vin_5v0"] == 5.01
    assert ended.input_units == {"vin_5v0": "V"}
    assert ended.outputs["iout"] == 0.5
    assert ended.output_units == {"iout": "A"}
