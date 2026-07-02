"""Tests for class step containers — per-iteration emission, parent-path
correctness, outcome rollup via the severity-max ladder, and condition-first
ordering for composed sweeps.

Design contract (see ``project_followup_parametrized_step_nesting``):

* A test class always emits a ``StepStarted`` / ``StepEnded`` event pair as
  the parent of its methods, even when un-swept.
* When the class is sweep-vectorized via ``litmus_sweeps``, one container
  event pair is emitted **per outer iteration** — distinct events sharing
  one ``step_path`` but with distinct ``vector_index`` values and the
  outer-dim parameter values populated in ``inputs``.
* Methods' ``step_path`` is ``"{container}/{method}"`` — parent derivable via ``rsplit``.
* Outcome rollup uses ``escalate_outcome`` — worst child outcome wins.
* Iteration outcomes are isolated: iteration N's container only rolls up
  iteration N's children, not prior iterations sharing the same step_path.

Each test spawns a pytest subprocess with ``_LITMUS_SESSION_ID`` set so
queries scope precisely to this run's events. Event ordering assertions
read the events table (emit order via ``(writer_key, event_offset)``); structural
assertions read the materialized steps table via :class:`StepsQuery`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from litmus.analysis.runs_query import RunsQuery
from litmus.analysis.steps_query import StepRow, StepsQuery
from litmus.data.event_store import EventStore


def _write_test(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body))


def _run_pytest(test_file: Path, *, session_id: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "_LITMUS_SESSION_ID": session_id}
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-v"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _wait_for_run(session_id: str, *, timeout: float = 15.0) -> str:
    """Block until the run is FULLY materialized (ended_at set).

    Polling with ``include_incomplete=True`` returns as soon as
    ``RunStarted`` lands, but the steps materialization continues
    asynchronously — a subsequent ``StepsQuery.list_for_run`` would
    race the daemon and could read a partial step set. Polling for
    a completed run (``ended_at IS NOT NULL``, the default) ensures
    every step has been written to parquet before we read it.
    """
    deadline = time.monotonic() + timeout
    runs_q = RunsQuery()
    try:
        while time.monotonic() < deadline:
            runs = runs_q.list_for_session(session_id)
            if runs:
                assert runs[0].run_id is not None
                return runs[0].run_id
            time.sleep(0.2)
        raise AssertionError(f"timed out waiting for session {session_id} run to materialize")
    finally:
        runs_q.close()


def _read_steps(session_id: str) -> list[StepRow]:
    """Return all step + vector rows for the session (both grains).

    Sorted by (step_index, vector_index, vector_outer_index). ``steps`` and
    ``step_vectors`` are now grain-explicit surfaces, so this merges the
    logical-step rows with their condition-point rows to reproduce the flat
    step+vector view these assertions expect. ``include_incomplete=True`` so
    finalized rows aren't filtered by the default ``ended_at IS NOT NULL``.
    """
    run_id = _wait_for_run(session_id)
    steps_q = StepsQuery()
    try:
        rows = list(steps_q.list_for_run(run_id, include_incomplete=True))
        rows += list(steps_q.list_vectors_for_run(run_id, include_incomplete=True))
    finally:
        steps_q.close()
    rows.sort(key=lambda s: (s.step_index or 0, s.vector_index or 0, s.vector_outer_index or 0))
    return rows


def _read_step_events(session_id: str) -> list[dict[str, Any]]:
    """Return all StepStarted + StepEnded events for the session, in chronological order."""
    store = EventStore.get_shared()
    sess_uuid = UUID(session_id)
    started = store.events(session_id=sess_uuid, event_type="test.step_started")
    ended = store.events(session_id=sess_uuid, event_type="test.step_ended")
    merged = sorted(
        started + ended,
        key=lambda e: (e.get("writer_key", ""), e.get("event_offset", 0)),
    )
    return merged


# ---------------------------------------------------------------------------
# 1. Un-swept class still emits a single container.
# ---------------------------------------------------------------------------


def test_unswept_class_emits_single_container(tmp_path: Path) -> None:
    """A class with no sweep marker emits exactly one container StepStarted /
    StepEnded pair around its method events.
    """
    session_id = str(uuid4())
    test_file = tmp_path / "test_unswept.py"
    _write_test(
        test_file,
        """\
        class TestSeq:
            def test_alpha(self):
                assert True

            def test_beta(self):
                assert True
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stderr

    events = _read_step_events(session_id)
    container_starts = [
        e
        for e in events
        if e.get("event_type") == "test.step_started" and e.get("step_name") == "TestSeq"
    ]
    container_ends = [
        e
        for e in events
        if e.get("event_type") == "test.step_ended" and e.get("step_name") == "TestSeq"
    ]
    assert len(container_starts) == 1, container_starts
    assert len(container_ends) == 1, container_ends
    assert container_starts[0].get("vector_index", 0) == 0
    assert container_starts[0].get("inputs") in (None, {}), container_starts[0].get("inputs")
    assert "/" not in (container_starts[0].get("step_path") or "")


# ---------------------------------------------------------------------------
# 2. Class sweep emits one container per outer iteration.
# ---------------------------------------------------------------------------


def test_class_sweep_emits_container_per_iteration(tmp_path: Path) -> None:
    """A class swept over voltage=[1,2,3] emits three container StepStarted
    events (vi=0 each — top-level, no enclosing loop) plus three VectorStarted
    events (vi=0/1/2) carrying the voltage values.
    """
    session_id = str(uuid4())
    test_file = tmp_path / "test_class_sweep.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])
        class TestSeq:
            def test_one(self, voltage):
                assert voltage in (1, 2, 3)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stderr

    events = _read_step_events(session_id)
    container_starts = [
        e
        for e in events
        if e.get("event_type") == "test.step_started" and e.get("step_name") == "TestSeq"
    ]
    container_ends = [
        e
        for e in events
        if e.get("event_type") == "test.step_ended" and e.get("step_name") == "TestSeq"
    ]
    assert len(container_starts) == 3, [e.get("vector_index") for e in container_starts]
    assert len(container_ends) == 3

    # Phase 2 shape: container StepStarted is top-level (encl=None → vi=0).
    # Voltage values land on VectorStarted events emitted by begin_outer_vector.
    vector_indices = [e.get("vector_index") for e in container_starts]
    assert vector_indices == [0, 0, 0], vector_indices

    # Containers carry empty inputs on StepStarted (enclosing=None).
    inputs = [e.get("inputs") or {} for e in container_starts]
    assert inputs == [{}, {}, {}], inputs

    # VectorStarted events carry the actual sweep params.
    store = EventStore.get_shared()
    vec_events = store.events(session_id=UUID(session_id), event_type="test.vector_started")
    container_vecs = sorted(
        (e for e in vec_events if e.get("step_name") == "TestSeq"),
        key=lambda e: e.get("vector_index", 0),
    )
    assert len(container_vecs) == 3, container_vecs
    vec_vis = [e.get("vector_index") for e in container_vecs]
    assert vec_vis == [0, 1, 2], vec_vis
    vec_inputs = [e.get("inputs") or {} for e in container_vecs]
    assert vec_inputs == [{"voltage": 1}, {"voltage": 2}, {"voltage": 3}], vec_inputs


# ---------------------------------------------------------------------------
# 3. Canonical composed sweep — class voltage + method B current.
# ---------------------------------------------------------------------------


def test_canonical_composed_sweep_order(tmp_path: Path) -> None:
    """Class voltage=[1,2,3] + method B current=[4,5,6] produces condition-first
    event order. Container vector_index increments per outer iteration; method
    vector_index counts per-method executions in arrival order.
    """
    session_id = str(uuid4())
    test_file = tmp_path / "test_composed.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])
        class TestSeq:
            def test_a(self, voltage):
                assert voltage in (1, 2, 3)

            @pytest.mark.litmus_sweeps([{"current": [4, 5, 6]}])
            def test_b(self, voltage, current):
                assert voltage in (1, 2, 3)
                assert current in (4, 5, 6)

            def test_c(self, voltage):
                assert voltage in (1, 2, 3)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stderr

    events = _read_step_events(session_id)
    started = [e for e in events if e.get("event_type") == "test.step_started"]

    # Condition-first sequence — TestSeq container opens at each voltage,
    # methods A/B/C run in source order with B's inner sweep unrolled inline.
    expected_step_names = [
        "TestSeq",  # iteration 0 container
        "test_a",
        "test_b",
        "test_b",
        "test_b",
        "test_c",
        "TestSeq",  # iteration 1 container
        "test_a",
        "test_b",
        "test_b",
        "test_b",
        "test_c",
        "TestSeq",  # iteration 2 container
        "test_a",
        "test_b",
        "test_b",
        "test_b",
        "test_c",
    ]
    actual = [e.get("step_name") for e in started]
    assert actual == expected_step_names, actual

    # Phase 2 shape:
    # - Container StepStarted (TestSeq) vi = encl=None → 0 (all three)
    # - Method StepStarted vi = enclosing class-outer vector index (0/1/2)
    # - test_b variants are Mode-1 (separate pytest items); each has encl=c_vec_N
    #   → vi = N (NOT their own b_vec index).
    expected_vector_indices = [
        0,  # TestSeq iter 0 (encl=None)
        0,  # test_a (encl=c_vec_0, index=0)
        0,  # test_b[c=4] (encl=c_vec_0)
        0,  # test_b[c=5] (encl=c_vec_0)
        0,  # test_b[c=6] (encl=c_vec_0)
        0,  # test_c (encl=c_vec_0)
        0,  # TestSeq iter 1 (encl=None)
        1,  # test_a (encl=c_vec_1, index=1)
        1,  # test_b[c=4] (encl=c_vec_1)
        1,  # test_b[c=5] (encl=c_vec_1)
        1,  # test_b[c=6] (encl=c_vec_1)
        1,  # test_c (encl=c_vec_1)
        0,  # TestSeq iter 2 (encl=None)
        2,  # test_a (encl=c_vec_2, index=2)
        2,  # test_b[c=4] (encl=c_vec_2)
        2,  # test_b[c=5] (encl=c_vec_2)
        2,  # test_b[c=6] (encl=c_vec_2)
        2,  # test_c (encl=c_vec_2)
    ]
    actual_vi = [e.get("vector_index", 0) for e in started]
    assert actual_vi == expected_vector_indices, actual_vi

    # inputs sequence:
    # - Container (TestSeq) StepStarted: encl=None → {} (params land on VectorStarted)
    # - Method StepStarted: encl=c_vec_N → {voltage: N} (latched at start_step)
    # - test_b: own Mode-1 outer begin_outer_vector fires AFTER StepStarted →
    #   StepStarted still only carries the enclosing voltage, NOT current.
    expected_inputs: list[dict[str, Any]] = [
        {},  # TestSeq iter 0
        {"voltage": 1},  # test_a
        {"voltage": 1},  # test_b[c=4]
        {"voltage": 1},  # test_b[c=5]
        {"voltage": 1},  # test_b[c=6]
        {"voltage": 1},  # test_c
        {},  # TestSeq iter 1
        {"voltage": 2},
        {"voltage": 2},
        {"voltage": 2},
        {"voltage": 2},
        {"voltage": 2},
        {},  # TestSeq iter 2
        {"voltage": 3},
        {"voltage": 3},
        {"voltage": 3},
        {"voltage": 3},
        {"voltage": 3},
    ]
    actual_inputs = [e.get("inputs") or {} for e in started]
    assert actual_inputs == expected_inputs, actual_inputs

    # Union-at-rest on the Mode-1 (parametrize) VECTOR rows: each test_b variant
    # is a separate pytest item whose class-outer begin_outer_vector emits a
    # VectorStarted carrying the merged callspec {voltage, current}. This guards
    # the parametrize build path — the peer of the vectors-fixture inner path
    # (test_swept_class_with_vectors_fixture_inner_sweep). Both must carry the
    # full enclosing+own condition on the vector row, not the inner layer alone.
    store = EventStore.get_shared()
    vec_events = store.events(session_id=UUID(session_id), event_type="test.vector_started")
    b_vecs = [e for e in vec_events if e.get("step_name") == "test_b"]
    assert len(b_vecs) == 9, b_vecs
    for e in b_vecs:
        inputs = e.get("inputs") or {}
        assert "voltage" in inputs and "current" in inputs, inputs
    b_pairs = {
        ((e.get("inputs") or {})["voltage"], (e.get("inputs") or {})["current"]) for e in b_vecs
    }
    assert b_pairs == {(v, c) for v in (1, 2, 3) for c in (4, 5, 6)}, b_pairs


# ---------------------------------------------------------------------------
# 4. Method step_paths are "{container}/{method}" — parent derivable from step_path.
# ---------------------------------------------------------------------------


def test_method_step_path_contains_container(tmp_path: Path) -> None:
    """Every method ``StepStarted`` has ``step_path = "{container}/{method}"``.
    Container is a root step (no "/" in step_path). Parent is derived via
    ``step_path.rsplit("/", 1)[0]``.
    """
    session_id = str(uuid4())
    test_file = tmp_path / "test_step_paths.py"
    _write_test(
        test_file,
        """\
        class TestSeq:
            def test_a(self):
                assert True

            def test_b(self):
                assert True
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stderr

    events = _read_step_events(session_id)
    started = [e for e in events if e.get("event_type") == "test.step_started"]
    container = next(e for e in started if e.get("step_name") == "TestSeq")
    methods = [e for e in started if e.get("step_name") in {"test_a", "test_b"}]

    container_path = container.get("step_path") or "TestSeq"
    assert "/" not in container_path, f"Container should be root: {container_path}"
    for m in methods:
        sp = m.get("step_path") or ""
        assert sp.startswith(container_path + "/"), f"Expected {container_path}/*, got {sp!r}"
        assert sp.rsplit("/", 1)[0] == container_path, m


# ---------------------------------------------------------------------------
# 5. Outcome rollup follows the severity-max ladder.
# ---------------------------------------------------------------------------


def test_class_outcome_rollup_severity_max(tmp_path: Path) -> None:
    """A failing method drives the container outcome to FAILED. An erroring
    method (uncaught non-assertion exception) drives it to ERRORED. A mix of
    passing + skipped methods stays PASSED (passed beats skipped on severity).
    """
    # Variant 1: failing assert → FAILED
    session_id = str(uuid4())
    test_file = tmp_path / "test_failed.py"
    _write_test(
        test_file,
        """\
        class TestSeq:
            def test_pass(self):
                assert True

            def test_fail(self):
                assert 1 == 2
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode != 0
    events = _read_step_events(session_id)
    container_end = next(
        e
        for e in events
        if e.get("event_type") == "test.step_ended" and e.get("step_name") == "TestSeq"
    )
    assert container_end.get("outcome") == "failed", container_end.get("outcome")

    # Variant 2: raised exception → ERRORED (severity above FAILED)
    session_id = str(uuid4())
    test_file = tmp_path / "test_errored.py"
    _write_test(
        test_file,
        """\
        class TestSeq:
            def test_pass(self):
                assert True

            def test_error(self):
                raise RuntimeError("boom")
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode != 0
    events = _read_step_events(session_id)
    container_end = next(
        e
        for e in events
        if e.get("event_type") == "test.step_ended" and e.get("step_name") == "TestSeq"
    )
    assert container_end.get("outcome") == "errored", container_end.get("outcome")

    # Variant 3: one passed + one skipped → PASSED (passed > skipped in
    # the severity ladder). Validates that SKIPPED doesn't override real
    # outcomes when at least one sibling has a positive verdict.
    session_id = str(uuid4())
    test_file = tmp_path / "test_pass_skip.py"
    _write_test(
        test_file,
        """\
        import pytest

        class TestSeq:
            def test_pass(self):
                assert True

            @pytest.mark.skip(reason="y")
            def test_skipped(self):
                pass
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0
    events = _read_step_events(session_id)
    container_end = next(
        e
        for e in events
        if e.get("event_type") == "test.step_ended" and e.get("step_name") == "TestSeq"
    )
    assert container_end.get("outcome") == "passed", container_end.get("outcome")


# ---------------------------------------------------------------------------
# 6. Per-iteration outcome isolation — iteration N rollup ignores prior iterations.
# ---------------------------------------------------------------------------


def test_per_iteration_outcome_isolation(tmp_path: Path) -> None:
    """When a class is swept and ONLY iteration 1 (voltage=2) fails, container
    rows for iteration 0 (voltage=1) and iteration 2 (voltage=3) must be PASSED.
    Catches stale-children rollup bugs that would propagate the iteration-1
    failure to surrounding iterations.
    """
    session_id = str(uuid4())
    test_file = tmp_path / "test_iter_isolation.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])
        class TestSeq:
            def test_one(self, voltage):
                assert voltage != 2, "only voltage=2 fails"
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode != 0

    # Phase 2 shape: container StepEnded all carry vi=0 (top-level, encl=None).
    # Use VectorEnded events for per-iteration outcome — they carry vi=0/1/2
    # and the vector's rolled-up outcome.
    store = EventStore.get_shared()
    vec_ended = store.events(session_id=UUID(session_id), event_type="test.vector_ended")
    container_vec_ends = [e for e in vec_ended if e.get("step_name") == "TestSeq"]
    assert len(container_vec_ends) == 3, container_vec_ends

    by_vi = {e.get("vector_index"): e.get("outcome") for e in container_vec_ends}
    assert by_vi == {0: "passed", 1: "failed", 2: "passed"}, by_vi


# ---------------------------------------------------------------------------
# 7. inputs dict gets auto-projected to in_* parquet columns.
# ---------------------------------------------------------------------------


def test_swept_class_with_vectors_fixture_inner_sweep(tmp_path: Path) -> None:
    """Class swept over voltage + method with vectors-fixture inner sweep over current.

    Phase F contract: outer (class-level) sweeps fan out at the pytest
    parametrize layer so the class container can iterate per condition.
    Inner (method-level) sweeps get consumed into the ``vectors`` matrix.

    Expected shape:

    * 3 ``TestSeq`` container events (voltage 0/1/2)
    * 3 ``test_b`` ``StepStarted`` events with matching ``vector_index``
      and ``inputs={voltage: N}`` — the OUTER index, not the last inner.
    * 3 matching ``StepEnded`` events (vi same as Started; severity-max
      across inner iterations).
    * 9 measurements with full ``inputs={voltage, current}`` so each row
      carries the complete sweep context.
    """
    session_id = str(uuid4())
    test_file = tmp_path / "test_inner_outer.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])
        class TestSeq:
            @pytest.mark.litmus_sweeps([{"current": [4, 5, 6]}])
            def test_b(self, voltage, vectors, measure):
                for v in vectors:
                    measure("vout", voltage * v["current"])
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stderr

    events = _read_step_events(session_id)
    starts = [e for e in events if e.get("event_type") == "test.step_started"]
    ends = [e for e in events if e.get("event_type") == "test.step_ended"]

    container_starts = [e for e in starts if e.get("step_name") == "TestSeq"]
    container_ends = [e for e in ends if e.get("step_name") == "TestSeq"]
    method_starts = [e for e in starts if e.get("step_name") == "test_b"]
    method_ends = [e for e in ends if e.get("step_name") == "test_b"]

    assert len(container_starts) == 3, container_starts
    assert len(container_ends) == 3, container_ends
    assert len(method_starts) == 3, method_starts
    assert len(method_ends) == 3, method_ends

    # Phase 2 shape: container StepStarted vi=0 (top-level, encl=None).
    # Voltage values land on VectorStarted events.
    assert [e.get("vector_index") for e in container_starts] == [0, 0, 0]
    assert [e.get("inputs") or {} for e in container_starts] == [{}, {}, {}]

    # Method StepStarted vector_index = enclosing class-outer vi (0/1/2).
    # test_b is NOT a Mode-1 swept step here (it uses the vectors fixture,
    # i.e. Mode-2). So method_starts have vi from encl=c_vec_0/1/2.
    assert [e.get("vector_index") for e in method_starts] == [0, 1, 2]
    assert [e.get("vector_index") for e in method_ends] == [0, 1, 2]
    assert [e.get("inputs") or {} for e in method_starts] == [
        {"voltage": 1},
        {"voltage": 2},
        {"voltage": 3},
    ]

    # Measurements carry the FULL effective inputs (outer voltage + inner current)
    # so analytics queries can filter on either dimension without joining steps.
    store = EventStore.get_shared()
    meas_events = store.events(session_id=UUID(session_id), event_type="test.measurement")
    assert len(meas_events) == 9
    # Every measurement carries both keys.
    for m in meas_events:
        inputs = m.get("inputs") or {}
        assert "voltage" in inputs and "current" in inputs, inputs
    # All 9 (voltage, current) cross-product combinations present.
    pairs = {
        ((m.get("inputs") or {})["voltage"], (m.get("inputs") or {})["current"])
        for m in meas_events
    }
    assert pairs == {(v, c) for v in (1, 2, 3) for c in (4, 5, 6)}, pairs

    # Union-at-rest invariant on the INNER VECTOR rows themselves (not just the
    # measurements): each vectors-fixture iteration under the swept class carries
    # the merged {voltage, current} — the enclosing class condition overlaid with
    # the iteration's own — because the plugin builds TestVector.params as the
    # union at construction. The class-outer vectors carry only their own layer.
    vec_events = store.events(session_id=UUID(session_id), event_type="test.vector_started")
    inner_vecs = [e for e in vec_events if e.get("step_name") == "test_b"]
    assert len(inner_vecs) == 9, inner_vecs
    for e in inner_vecs:
        inputs = e.get("inputs") or {}
        assert "voltage" in inputs and "current" in inputs, inputs
    inner_pairs = {
        ((e.get("inputs") or {})["voltage"], (e.get("inputs") or {})["current"]) for e in inner_vecs
    }
    assert inner_pairs == {(v, c) for v in (1, 2, 3) for c in (4, 5, 6)}, inner_pairs
    outer_vecs = [e for e in vec_events if e.get("step_name") == "TestSeq"]
    assert {(e.get("inputs") or {}).get("voltage") for e in outer_vecs} == {1, 2, 3}
    for e in outer_vecs:
        assert "current" not in (e.get("inputs") or {}), e

    # Isolation (union flows outer→inner, NEVER inner→outer): the test_b STEP
    # rows carry only the enclosing {voltage} — never the per-iteration `current`,
    # which varies across inner vectors and lives on the vector rows alone. Guards
    # against the union fix leaking inner context up onto the step.
    b_steps = [
        r for r in _read_steps(session_id) if r.step_name == "test_b" and r.vector_index is None
    ]
    assert len(b_steps) == 3, [(r.step_path, r.vector_index, r.vector_outer_index) for r in b_steps]
    for r in b_steps:
        assert (r.inputs or {}).get("voltage") in (1, 2, 3), r.inputs
        assert "current" not in (r.inputs or {}), r.inputs


def test_inner_loop_configure_is_logged(tmp_path: Path) -> None:
    """An in-body ``configure()`` inside a Mode-2 ``vectors`` loop reaches the
    logged measurement AND the ``VectorEnded`` row — because inputs are resolved
    from the live context the test holds at that instant, not a snapshot copied
    off the vector. Guards the reconstruction path that silently dropped in-loop
    configure() (the values existed in the context but never made it to the log).
    """
    session_id = str(uuid4())
    test_file = tmp_path / "test_cfg_loop.py"
    _write_test(
        test_file,
        """\
        import pytest
        from litmus.execution._state import get_current_context

        @pytest.mark.litmus_sweeps([{"voltage": [1, 2]}])
        class TestSeq:
            @pytest.mark.litmus_sweeps([{"current": [4, 5]}])
            def test_b(self, voltage, vectors, measure):
                for v in vectors:
                    get_current_context().configure("trim", v["current"] * 10)
                    measure("vout", 1.0)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stderr

    store = EventStore.get_shared()
    meas = store.events(session_id=UUID(session_id), event_type="test.measurement")
    assert len(meas) == 4, meas
    for m in meas:
        inp = m.get("inputs") or {}
        assert {"voltage", "current", "trim"} <= set(inp), inp
        assert inp["trim"] == inp["current"] * 10, inp
    # VectorEnded (emitted after the iteration body) also carries the configured
    # trim — the vector row reflects the final context, not the seeded params.
    ve = [
        e
        for e in store.events(session_id=UUID(session_id), event_type="test.vector_ended")
        if e.get("step_name") == "test_b"
    ]
    assert len(ve) == 4, ve
    for e in ve:
        inp = e.get("inputs") or {}
        assert {"voltage", "current", "trim"} <= set(inp), inp


def test_vectors_fixture_outcome_rollup(tmp_path: Path) -> None:
    """Vectors-fixture step outcome is the severity-max rollup of its
    inner-iteration measurements.

    Three inner iterations: two measurements pass their limit, one fails.
    Severity ladder says FAILED beats PASSED — so:

    * ``StepEnded.outcome`` (the step's aggregate verdict, escalated across
      ``step.vectors[]`` via :func:`escalate_outcome`) = FAILED
    * Class-container ``StepEnded.outcome`` = FAILED (rolled up from the step)

    This locks in the rollup chain for mixed-outcome inner iterations and
    catches the prior bug where the step's outcome reflected only the last
    inner iteration's verdict (a passing iteration after a failing one
    would have masked the failure).
    """
    session_id = str(uuid4())
    test_file = tmp_path / "test_rollup.py"
    _write_test(
        test_file,
        """\
        import pytest
        from litmus.models.test_config import Limit

        class TestSeq:
            @pytest.mark.litmus_sweeps([{"target": [10, 99, 30]}])
            def test_check(self, vectors, verify):
                for v in vectors:
                    # verify(name, value, limit) records pass/fail against
                    # the limit. target=10 → passes [0..20], target=99 →
                    # FAILS [25..40], target=30 → passes [25..40].
                    if v["target"] < 25:
                        verify(
                            "reading", float(v["target"]),
                            limit=Limit(low=0, high=20, unit="V"),
                        )
                    else:
                        verify(
                            "reading", float(v["target"]),
                            limit=Limit(low=25, high=40, unit="V"),
                        )
        """,
    )
    _run_pytest(test_file, session_id=session_id)
    # ``verify`` records pass/fail outcomes against the limit but does NOT
    # raise inside the loop — the step's verdict comes from the rollup.
    # pytest itself may still pass (exit 0) because no AssertionError
    # propagated; we assert on the rolled-up outcome instead.

    events = _read_step_events(session_id)

    method_end = next(
        e
        for e in events
        if e.get("event_type") == "test.step_ended" and e.get("step_name") == "test_check"
    )
    # Step outcome reflects the rollup of inner measurements: FAILED wins
    # over the two PASSED siblings.
    assert method_end.get("outcome") == "failed", method_end.get("outcome")

    container_end = next(
        e
        for e in events
        if e.get("event_type") == "test.step_ended" and e.get("step_name") == "TestSeq"
    )
    # Container rolls up the step's outcome.
    assert container_end.get("outcome") == "failed", container_end.get("outcome")


def test_inputs_auto_projected_to_parquet(tmp_path: Path) -> None:
    """Container's ``inputs={"voltage": N}`` lands as ``in_voltage`` on the
    materialized steps rows for the container itself (confirming
    ``_row_helpers.INPUT_PREFIX`` auto-projection works for synthesized
    container events as well as method events).
    """
    session_id = str(uuid4())
    test_file = tmp_path / "test_projection.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])
        class TestSeq:
            def test_one(self, voltage):
                assert voltage in (1, 2, 3)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stderr

    rows = _read_steps(session_id)
    container_rows = [r for r in rows if r.step_name == "TestSeq"]
    # Phase 2 shape: ONE step row (vi=NULL) + THREE vector rows (vi=0/1/2).
    assert len(container_rows) == 4, [(r.step_path, r.vector_index) for r in container_rows]

    # The 3 vector rows carry the voltage values as inputs; the step row
    # (vi=NULL) carries no sweep params.
    vector_rows = [r for r in container_rows if r.vector_index is not None]
    assert len(vector_rows) == 3, [(r.step_path, r.vector_index) for r in vector_rows]
    by_vi = {r.vector_index: r.inputs for r in vector_rows}
    assert by_vi == {0: {"voltage": 1}, 1: {"voltage": 2}, 2: {"voltage": 3}}, by_vi


def test_configure_overrides_parametrize_in_stored_inputs(tmp_path: Path) -> None:
    """``configure()`` overrides a parametrize key (and adds new ones) in the
    stored step inputs, while keys it never touches keep their swept value."""
    session_id = str(uuid4())
    test_file = tmp_path / "test_configure_override.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])
        class TestSeq:
            def test_one(self, voltage, context):
                context.configure("voltage", voltage + 100)
                context.configure("trim", 7)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stderr

    rows = _read_steps(session_id)
    method_rows = [r for r in rows if r.step_name == "test_one"]
    assert len(method_rows) == 3, [(r.step_path, r.vector_outer_index) for r in method_rows]

    by_vi = {r.vector_outer_index: r.inputs for r in method_rows}
    assert by_vi == {
        0: {"voltage": 101, "trim": 7},
        1: {"voltage": 102, "trim": 7},
        2: {"voltage": 103, "trim": 7},
    }, by_vi
