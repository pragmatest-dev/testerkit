"""The retry model — S1–S6 table as a hard guard.

Encodes ``runs-execution-model.md`` ("Retry definitions — count the right
grain"):

* ``step_retry``   = how many times the step (the pytest item) executed (−1).
                     Sourced from ``item.execution_count``; stamped on
                     ``StepStarted``.
* ``vector_retry`` = how many times this ``(step_path, vector_index)``
                     executed (−1). The occurrence ordinal, counted at emit by
                     the session-scoped ``RunScope`` counter (which survives
                     reruns), so a step rerun AND an in-body retry both count.

Each scenario drives the event stream the producer would emit (vector
ordinals stamped exactly as ``RunScope.next_vector_occurrence`` yields them),
materializes through the accumulator, and asserts the full row set + the
derived counts. A regression in either counter fails the matching scenario.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pyarrow.parquet as pq

from litmus.data.backends._event_accumulator import EventAccumulator
from litmus.data.backends.parquet import materialize_run_to_parquet
from litmus.data.events import (
    RunStarted,
    StepEnded,
    StepStarted,
    VectorEnded,
    VectorStarted,
)

_T0 = datetime(2026, 6, 27, 12, 0, 0, tzinfo=UTC)
_SP = "s"


def _occ():
    """A `(step_path, vector_index) -> 0-based occurrence` counter.

    Mirrors ``RunScope.next_vector_occurrence`` exactly: returns the current
    count then increments. Used to stamp each ``VectorStarted.retry`` as the
    producer would, so the asserted table is what the runtime emits.
    """
    counts: dict[tuple[str, int], int] = {}

    def nxt(step_path: str, vector_index: int) -> int:
        n = counts.get((step_path, vector_index), 0)
        counts[(step_path, vector_index)] = n + 1
        return n

    return nxt


def _tick(n: int) -> datetime:
    return datetime(2026, 6, 27, 12, 0, n, tzinfo=UTC)


def _new_acc():
    acc = EventAccumulator()
    rid, sid = uuid4(), uuid4()
    acc.on_event(
        RunStarted(
            session_id=sid, run_id=rid, station_id="st1", uut_serial_number="SN1", occurred_at=_T0
        )
    )
    return acc, rid, sid


def _step_start(acc, sid, rid, sr):
    acc.on_event(
        StepStarted(
            session_id=sid, run_id=rid, step_name=_SP, step_index=0, step_path=_SP, retry=sr
        )
    )


def _step_end(acc, sid, rid, sr, outcome="passed"):
    acc.on_event(
        StepEnded(
            session_id=sid,
            run_id=rid,
            step_name=_SP,
            step_index=0,
            step_path=_SP,
            retry=sr,
            outcome=outcome,
        )
    )


def _vector(acc, sid, rid, vi, r, tick, *, outcome="passed"):
    acc.on_event(
        VectorStarted(
            session_id=sid,
            run_id=rid,
            step_name=_SP,
            step_index=0,
            step_path=_SP,
            vector_index=vi,
            retry=r,
            occurred_at=_tick(tick),
        )
    )
    acc.on_event(
        VectorEnded(
            session_id=sid,
            run_id=rid,
            step_name=_SP,
            step_index=0,
            step_path=_SP,
            vector_index=vi,
            retry=r,
            outcome=outcome,
        )
    )


def _materialize(acc, tmp_path):
    path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")
    assert path is not None
    return pq.read_table(path).to_pylist()


def _vectors(rows):
    """Sorted ``(vector_index, vector_retry)`` for vector rows."""
    return sorted(
        (r["vector_index"], r["vector_retry"]) for r in rows if r["record_type"] == "vector"
    )


def _step_retries(rows):
    return sorted(r["step_retry"] for r in rows if r["record_type"] == "step")


def _vector_attempts(vectors):
    """retry count per vector_index = max vector_retry seen for it."""
    out: dict[int, int] = {}
    for vi, vr in vectors:
        out[vi] = max(out.get(vi, 0), vr)
    return out


def _run_inner_loop(plan):
    """Drive an inner-loop step: ``plan`` = list of (step_retry, [vector_index,...]).

    Returns the materialized rows' helper closure (caller passes tmp_path).
    """
    acc, rid, sid = _new_acc()
    occ = _occ()
    tick = 0
    for sr, vis in plan:
        _step_start(acc, sid, rid, sr)
        for vi in vis:
            _vector(acc, sid, rid, vi, occ(_SP, vi), tick)
            tick += 1
        _step_end(acc, sid, rid, sr)
    return acc


# ---------------------------------------------------------------------------
# S1 — 1:1 step, fails then reruns. Scope vector (no VectorStarted): its
#   (step_path, vector_index) occurrence ordinal IS step_retry, so the two
#   scope vectors carry vector_retry = step_retry = 0, 1.
# ---------------------------------------------------------------------------


def test_s1_one_to_one_step_rerun(tmp_path):
    acc, rid, sid = _new_acc()
    for sr, outcome in ((0, "failed"), (1, "passed")):
        _step_start(acc, sid, rid, sr)
        _step_end(acc, sid, rid, sr, outcome)

    rows = _materialize(acc, tmp_path)
    assert _step_retries(rows) == [0, 1]
    assert _vectors(rows) == [(0, 0), (0, 1)]
    # The decisive 1:1 fact: scope vector vector_retry == step_retry.
    for r in rows:
        if r["record_type"] == "vector":
            assert r["vector_retry"] == r["step_retry"]


# ---------------------------------------------------------------------------
# S2 — inner loop ×3, no retry. Three in-body vectors, each its first
#   occurrence → vector_retry 0.
# ---------------------------------------------------------------------------


def test_s2_inner_loop_no_retry(tmp_path):
    acc = _run_inner_loop([(0, [0, 1, 2])])
    vectors = _vectors(_materialize(acc, tmp_path))
    assert vectors == [(0, 0), (1, 0), (2, 0)]
    assert _vector_attempts(vectors) == {0: 0, 1: 0, 2: 0}


# ---------------------------------------------------------------------------
# S3 — inner loop ×3, whole step reruns once. Each (s, vi) runs twice → the
#   second occurrence is vector_retry 1.
# ---------------------------------------------------------------------------


def test_s3_inner_loop_step_rerun(tmp_path):
    acc = _run_inner_loop([(0, [0, 1, 2]), (1, [0, 1, 2])])
    vectors = _vectors(_materialize(acc, tmp_path))
    assert vectors == [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]
    assert _vector_attempts(vectors) == {0: 1, 1: 1, 2: 1}


# ---------------------------------------------------------------------------
# S4 — inner loop, step reruns AND vec 1 retries in-body in attempt 1. vec 1
#   runs 3× → vector_retry up to 2; vec 0/2 run 2× → up to 1.
# ---------------------------------------------------------------------------


def test_s4_inner_loop_step_rerun_plus_in_body(tmp_path):
    acc = _run_inner_loop([(0, [0, 1, 2]), (1, [0, 1, 1, 2])])
    vectors = _vectors(_materialize(acc, tmp_path))
    assert vectors == [(0, 0), (0, 1), (1, 0), (1, 1), (1, 2), (2, 0), (2, 1)]
    assert _vector_attempts(vectors) == {0: 1, 1: 2, 2: 1}


# ---------------------------------------------------------------------------
# S5 — conditional: vec 2 runs ONLY in attempt 0 (not the rerun). It ran once
#   → vector_retry 0, while the step ran twice. Proves vector_retry is NOT
#   floored by step_retry: vector < step.
# ---------------------------------------------------------------------------


def test_s5_conditional_vector_below_step(tmp_path):
    acc = _run_inner_loop([(0, [0, 1, 2]), (1, [0, 1])])
    rows = _materialize(acc, tmp_path)
    assert _step_retries(rows) == [0, 1]
    assert _vector_attempts(_vectors(rows)) == {0: 1, 1: 1, 2: 0}


# ---------------------------------------------------------------------------
# S6 — partial-then-full: attempt 0 runs vec 0,1 only (vec 2 never reached);
#   the rerun runs all three. vec 2's FIRST execution is in attempt 1, so its
#   ordinal starts at 0 — vector_retry 0 even though step_retry reached 1.
# ---------------------------------------------------------------------------


def test_s6_partial_then_full(tmp_path):
    acc = _run_inner_loop([(0, [0, 1]), (1, [0, 1, 2])])
    vectors = _vectors(_materialize(acc, tmp_path))
    assert (2, 0) in vectors  # vec 2 first occurrence is ordinal 0
    assert _vector_attempts(vectors) == {0: 1, 1: 1, 2: 0}


# ---------------------------------------------------------------------------
# The RunScope counter itself — the source of the vector ordinals above.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# #24-1 — a rerun step's in-body iteration vectors must read THEIR OWN
#   attempt's step span and step_retry, not the lowest attempt's. Before the
#   fix the accumulator resolved the enclosing step via the retry-invariant
#   lowest match, so attempt 1's vectors carried attempt 0's step_started_at
#   and step_retry=0. The vectors now carry step_retry on their own event, and
#   the accumulator resolves the step span at that attempt.
# ---------------------------------------------------------------------------


def test_rerun_iteration_vector_reads_own_attempt_step_span(tmp_path):
    acc, rid, sid = _new_acc()

    def step_start(sr, t):
        acc.on_event(
            StepStarted(
                session_id=sid,
                run_id=rid,
                step_name=_SP,
                step_index=0,
                step_path=_SP,
                retry=sr,
                occurred_at=_tick(t),
            )
        )

    def step_end(sr, t):
        acc.on_event(
            StepEnded(
                session_id=sid,
                run_id=rid,
                step_name=_SP,
                step_index=0,
                step_path=_SP,
                retry=sr,
                outcome="passed",
                occurred_at=_tick(t),
            )
        )

    def vector(vr, sr, t):
        acc.on_event(
            VectorStarted(
                session_id=sid,
                run_id=rid,
                step_name=_SP,
                step_index=0,
                step_path=_SP,
                vector_index=0,
                retry=vr,
                step_retry=sr,
                occurred_at=_tick(t),
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=sid,
                run_id=rid,
                step_name=_SP,
                step_index=0,
                step_path=_SP,
                vector_index=0,
                retry=vr,
                step_retry=sr,
                outcome="passed",
                occurred_at=_tick(t),
            )
        )

    # attempt 0 then a rerun (attempt 1), each with its own step span
    step_start(0, 0)
    vector(vr=0, sr=0, t=1)
    step_end(0, 2)
    step_start(1, 10)
    vector(vr=1, sr=1, t=11)
    step_end(1, 12)

    rows = _materialize(acc, tmp_path)
    vrows = {r["vector_retry"]: r for r in rows if r["record_type"] == "vector"}
    assert set(vrows) == {0, 1}

    assert vrows[0]["step_retry"] == 0
    assert vrows[0]["step_started_at"] == _tick(0)
    assert vrows[0]["step_ended_at"] == _tick(2)

    # The decisive #24-1 fact: attempt 1's iteration vector reads attempt 1's
    # step span, not attempt 0's.
    assert vrows[1]["step_retry"] == 1
    assert vrows[1]["step_started_at"] == _tick(10)
    assert vrows[1]["step_ended_at"] == _tick(12)


def test_runscope_next_vector_occurrence_counts_per_point():
    from litmus.execution.run_scope import RunScope

    rs = RunScope(uut_serial="SN1", station_id="st1")
    assert rs.next_vector_occurrence("s", 0) == 0
    assert rs.next_vector_occurrence("s", 1) == 0
    assert rs.next_vector_occurrence("s", 0) == 1  # step rerun re-runs point 0
    assert rs.next_vector_occurrence("s", 1) == 1  # step rerun re-runs point 1
    assert rs.next_vector_occurrence("s", 1) == 2  # in-body retry of point 1
    assert rs.next_vector_occurrence("s", 2) == 0  # a point first seen later
