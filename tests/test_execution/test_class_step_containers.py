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
* Methods get ``parent_path`` equal to the container's ``step_path``.
* Outcome rollup uses ``escalate_outcome`` — worst child outcome wins.
* Iteration outcomes are isolated: iteration N's container only rolls up
  iteration N's children, not prior iterations sharing the same step_path.

Each test spawns a pytest subprocess with ``_LITMUS_SESSION_ID`` set so
queries scope precisely to this run's events. Event ordering assertions
read the events table (chronological via ``event_number``); structural
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
            runs = runs_q.find_for_session(session_id)
            if runs:
                assert runs[0].run_id is not None
                return runs[0].run_id
            time.sleep(0.2)
        raise AssertionError(f"timed out waiting for session {session_id} run to materialize")
    finally:
        runs_q.close()


def _read_steps(session_id: str) -> list[StepRow]:
    """Return all step rows for the session, sorted by (step_index, vector_index)."""
    run_id = _wait_for_run(session_id)
    steps_q = StepsQuery()
    try:
        rows = list(steps_q.list_for_run(run_id))
    finally:
        steps_q.close()
    rows.sort(key=lambda s: (s.step_index or 0, s.vector_index or 0))
    return rows


def _read_step_events(session_id: str) -> list[dict[str, Any]]:
    """Return all StepStarted + StepEnded events for the session, in chronological order."""
    store = EventStore.get_shared()
    sess_uuid = UUID(session_id)
    started = store.events(session_id=sess_uuid, event_type="test.step_started")
    ended = store.events(session_id=sess_uuid, event_type="test.step_ended")
    merged = sorted(started + ended, key=lambda e: e.get("event_number", 0))
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
    assert container_starts[0].get("parent_path", "") == ""


# ---------------------------------------------------------------------------
# 2. Class sweep emits one container per outer iteration.
# ---------------------------------------------------------------------------


def test_class_sweep_emits_container_per_iteration(tmp_path: Path) -> None:
    """A class swept over voltage=[1,2,3] emits three container StepStarted
    events with vector_index 0/1/2 and ``inputs={"voltage": <value>}``.
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

    vector_indices = [e.get("vector_index") for e in container_starts]
    assert vector_indices == [0, 1, 2], vector_indices

    inputs = [e.get("inputs") or {} for e in container_starts]
    assert inputs == [{"voltage": 1}, {"voltage": 2}, {"voltage": 3}], inputs


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

    # vector_index sequence: container 0/1/2; A 0/1/2; B 0..8; C 0/1/2.
    expected_vector_indices = [
        0,
        0,
        0,
        1,
        2,
        0,
        1,
        1,
        3,
        4,
        5,
        1,
        2,
        2,
        6,
        7,
        8,
        2,
    ]
    actual_vi = [e.get("vector_index", 0) for e in started]
    assert actual_vi == expected_vector_indices, actual_vi

    # inputs sequence: container carries {voltage: N}, A/C carry {voltage: N},
    # B carries {voltage: N, current: M}.
    expected_inputs: list[dict[str, Any]] = [
        {"voltage": 1},
        {"voltage": 1},
        {"voltage": 1, "current": 4},
        {"voltage": 1, "current": 5},
        {"voltage": 1, "current": 6},
        {"voltage": 1},
        {"voltage": 2},
        {"voltage": 2},
        {"voltage": 2, "current": 4},
        {"voltage": 2, "current": 5},
        {"voltage": 2, "current": 6},
        {"voltage": 2},
        {"voltage": 3},
        {"voltage": 3},
        {"voltage": 3, "current": 4},
        {"voltage": 3, "current": 5},
        {"voltage": 3, "current": 6},
        {"voltage": 3},
    ]
    actual_inputs = [e.get("inputs") or {} for e in started]
    assert actual_inputs == expected_inputs, actual_inputs


# ---------------------------------------------------------------------------
# 4. Methods' parent_path matches the container's step_path.
# ---------------------------------------------------------------------------


def test_method_parent_path_matches_container(tmp_path: Path) -> None:
    """Every method ``StepStarted`` has parent_path equal to its enclosing
    class container's step_path. Container's parent_path is the empty string.
    """
    session_id = str(uuid4())
    test_file = tmp_path / "test_parent_path.py"
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

    assert container.get("parent_path", "") == ""
    container_path = container.get("step_path") or "TestSeq"
    for m in methods:
        assert m.get("parent_path") == container_path, m


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

    events = _read_step_events(session_id)
    container_ends = [
        e
        for e in events
        if e.get("event_type") == "test.step_ended" and e.get("step_name") == "TestSeq"
    ]
    assert len(container_ends) == 3, container_ends

    by_vi = {e.get("vector_index", 0): e.get("outcome") for e in container_ends}
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
            def test_b(self, voltage, vectors, logger):
                for v in vectors:
                    logger.measure("vout", voltage * v["current"])
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

    # Container vector_index matches outer iteration (0/1/2) with voltage in inputs.
    assert [e.get("vector_index") for e in container_starts] == [0, 1, 2]
    assert [e.get("inputs") or {} for e in container_starts] == [
        {"voltage": 1},
        {"voltage": 2},
        {"voltage": 3},
    ]

    # Method StepStarted and StepEnded share vector_index — the OUTER index —
    # NOT the inner iteration's last vector index.
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


def test_vectors_fixture_outcome_rollup(tmp_path: Path) -> None:
    """Vectors-fixture step outcome is the severity-max rollup of its
    inner-iteration measurements.

    Three inner iterations: two measurements pass their limit, one fails.
    Severity ladder says FAILED beats PASSED — so:

    * ``StepEnded.outcome`` (the step's aggregate verdict) = FAILED
    * ``StepEnded.vector_outcome`` (aggregated across step.vectors[]) = FAILED
    * Class-container ``StepEnded.outcome`` = FAILED (rolled up from the step)

    This locks in the rollup chain for mixed-outcome inner iterations and
    catches the prior bug where ``vector_outcome`` reflected only the last
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
                            limit=Limit(low=0, high=20, units="V"),
                        )
                    else:
                        verify(
                            "reading", float(v["target"]),
                            limit=Limit(low=25, high=40, units="V"),
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
    # Step outcome AND vector_outcome both reflect the rollup of inner
    # measurements: FAILED wins over the two PASSED siblings.
    assert method_end.get("outcome") == "failed", method_end.get("outcome")
    assert method_end.get("vector_outcome") == "failed", method_end.get("vector_outcome")

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
    assert len(container_rows) == 3, [(r.step_path, r.vector_index) for r in container_rows]

    by_vi = {r.vector_index: r.inputs for r in container_rows}
    assert by_vi == {0: {"voltage": 1}, 1: {"voltage": 2}, 2: {"voltage": 3}}, by_vi
