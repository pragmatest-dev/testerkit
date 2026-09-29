"""Live channel wire format (docs/41 §8 T1-T3, M1/M6 hooks).

* T1  live == finalized: an executing run projected in-flight equals the P3
      ``run_detail`` of the parquet the materializer writes for the same events.
* T2  ``rejoin(strip(detail)) == detail`` on real projections (swept, retried,
      class-outer, step-scoped-measurement steps).
* T3  bucketing / ``row_id`` determinism, size guard, per-doc hash diff, resync
      manifest, parts, and the cost guard (one new measurement in a 5,000-vector step
      re-sends the tail chunk + the step doc, never the header).
* M1/M6  ``test_measure_live_doc_sizes_and_cpu`` PRINTS real doc sizes and the
      projection + diff time (run with ``-s``); it asserts nothing about the numbers.
"""

from __future__ import annotations

import statistics
import time
from typing import Any

import pytest
from pydantic import ValidationError

from testerkit.data import live_rows
from testerkit.data._accumulator_pool import AccumulatorPool
from testerkit.data.backends.parquet import materialize_run_to_parquet
from testerkit.data.events import MeasurementRecorded
from testerkit.data.live_projection import LiveRunProjection, project_run
from testerkit.data.live_rows import (
    LiveDoc,
    LiveKey,
    LivePush,
    LiveSyncState,
    build_header,
    estimate_doc_size,
    rejoin,
    row_id_for,
    sort_detail,
    to_live_docs,
)
from testerkit.data.read_models import RunDetail, run_detail
from tests.test_data.live_streams import (
    RUN_ID,
    as_dicts,
    representative_events,
    run_started,
    sweep_point_events,
    swept_step_events,
    ts,
)

_started = run_started

RID = str(RUN_ID)


def _fold(events: list[Any]) -> tuple[AccumulatorPool, RunDetail]:
    pool = AccumulatorPool()
    for evt in as_dicts(events):
        pool.dispatch(evt)
    acc = pool.get(RID)
    assert acc is not None
    detail = project_run(acc)
    assert detail is not None
    return pool, detail


@pytest.fixture(scope="module")
def rep_detail() -> RunDetail:
    return _fold(representative_events())[1]


# ---------------------------------------------------------------------------
# take_dirty
# ---------------------------------------------------------------------------


def test_take_dirty_drains_without_building_rows() -> None:
    pool = AccumulatorPool()
    assert pool.take_dirty() == (set(), set())
    for evt in as_dicts(representative_events()[:3]):
        pool.dispatch(evt)
    dirty, evicted = pool.take_dirty()
    assert dirty == {RID} and evicted == set()
    assert pool.take_dirty() == (set(), set())  # drained
    pool.dispatch(as_dicts(representative_events()[:1])[0])
    assert pool.take_dirty()[0] == {RID}  # re-dirtied after the drain
    pool.dispatch(as_dicts(representative_events()[:1])[0])
    pool.evict(RID)
    assert pool.take_dirty() == (set(), {RID})


# ---------------------------------------------------------------------------
# In-flight projection + T1 parity
# ---------------------------------------------------------------------------


def test_in_flight_projection_has_no_end_or_outcome() -> None:
    _, detail = _fold(representative_events(ended=False))
    assert detail.run.ended_at is None and detail.run.outcome is None
    assert build_header(detail).state == "running"
    assert len(detail.steps) == 6 and len(detail.measurements) == 12


@pytest.mark.parametrize("ended_outcome", ["failed", None])
def test_t1_live_equals_finalized_at_commit(tmp_path: Any, ended_outcome: str | None) -> None:
    """The live projection after RunEnded == run_detail(materialized parquet) row for
    row (after the total order), ``ended_at`` included. ``file_path`` is the only
    exception: the parquet has a path, the live rows do not. The materializer is called
    as the runs daemon calls it — no explicit ``run_ended_at``."""
    events = representative_events()
    if ended_outcome is None:
        events[-1] = events[-1].model_copy(update={"outcome": None})
    pool, live = _fold(events)
    acc = pool.get(RID)
    assert acc is not None and acc.run_ended_at is not None
    path = materialize_run_to_parquet(acc, tmp_path, outcome=acc.run_outcome)
    assert path is not None
    finalized = run_detail(path, file_path="")
    assert finalized.run.ended_at == acc.run_ended_at == live.run.ended_at
    assert sort_detail(live) == sort_detail(finalized)
    # ...and through the wire docs, which is what the server serves.
    assert rejoin(live.run, to_live_docs(live).docs.values()) == sort_detail(finalized)


# ---------------------------------------------------------------------------
# T2 strip / rejoin identity
# ---------------------------------------------------------------------------


def test_t2_strip_rejoin_identity(rep_detail: RunDetail) -> None:
    docset = to_live_docs(rep_detail)
    assert not docset.truncated
    back = rejoin(rep_detail.run, docset.docs.values())
    expected = sort_detail(rep_detail)
    for name in ("run", "steps", "vectors", "measurements", "inputs", "outputs"):
        assert getattr(back, name) == getattr(expected, name), name


def test_t2_identity_survives_the_json_wire(rep_detail: RunDetail) -> None:
    push = LiveSyncState().build_pushes(RID, rep_detail, now=0.0, now_ns=1)[0]
    wire = LivePush.model_validate_json(push.model_dump_json())
    assert wire.header is not None
    assert rejoin(wire.header.run, wire.upserts) == sort_detail(rep_detail)


def test_t2_identity_in_flight() -> None:
    _, detail = _fold(representative_events(ended=False))
    assert rejoin(detail.run, to_live_docs(detail).docs.values()) == sort_detail(detail)


def test_stripped_columns_are_absent_from_docs(rep_detail: RunDetail) -> None:
    docs = to_live_docs(rep_detail).docs.values()
    for doc in docs:
        if doc.kind == "chunk" and doc.measurements:
            assert not {"uut_serial_number", "run_outcome", "step_outcome", "step_path"} & set(
                doc.measurements
            )
        if doc.kind == "step":
            assert doc.step is not None and "station_name" not in doc.step


# ---------------------------------------------------------------------------
# T3 bucketing, row_id, size guard
# ---------------------------------------------------------------------------


def test_row_id_is_deterministic_and_bucket_sensitive() -> None:
    key = LiveKey(step_path="a/b", step_retry=0, vector_outer_index=None)
    assert row_id_for("chunk", key, "v0") == row_id_for("chunk", key, "v0")
    assert row_id_for("chunk", key, "v0") != row_id_for("chunk", key, "v1")
    assert row_id_for("step", key) != row_id_for("chunk", key, "v0")
    assert len(row_id_for("step", key)) == 24
    other = LiveKey(step_path="a/b", step_retry=0, vector_outer_index=0)
    assert row_id_for("step", key) != row_id_for("step", other)


def test_sweep_buckets_into_chunks_of_16_and_ids_are_stable_across_restart() -> None:
    events = swept_step_events("sweep", 0, vectors=40, t=1)
    first = to_live_docs(_fold([_started(), *events])[1])
    restarted = to_live_docs(_fold([_started(), *events])[1])  # a fresh pool: a restart
    assert set(first.docs) == set(restarted.docs)
    chunks = {d.bucket: d for d in first.docs.values() if d.kind == "chunk" and d.bucket}
    assert sorted(chunks) == ["v0", "v1", "v2"]
    assert [len(chunks[b].vectors["vector_index"]) for b in ("v0", "v1", "v2")] == [16, 16, 8]  # type: ignore[index]
    assert sum(1 for d in first.docs.values() if d.kind == "step") == 1


def test_vector_retries_share_the_bucket_of_their_sweep_point(rep_detail: RunDetail) -> None:
    docs = to_live_docs(rep_detail).docs.values()
    flaky = [d for d in docs if d.kind == "chunk" and d.key.step_path == "flaky"]
    # step_retry 0 and 1 are distinct attempts -> distinct chunks, each bucket v0
    assert sorted((d.key.step_retry, d.bucket) for d in flaky) == [(0, "v0"), (1, "v0")]


def test_step_scoped_rows_bucket_by_ordinal(rep_detail: RunDetail) -> None:
    docs = to_live_docs(rep_detail).docs.values()
    plain = next(d for d in docs if d.kind == "chunk" and d.key.step_path == "plain")
    assert plain.bucket == "s0" and plain.measurements is not None and plain.vectors is None
    assert live_rows.bucket_for(None, 127) == "s0" and live_rows.bucket_for(None, 128) == "s1"
    assert live_rows.bucket_for(15, None) == "v0" and live_rows.bucket_for(16, None) == "v1"


def test_soft_budget_splits_a_chunk_per_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = _fold([_started(), *swept_step_events("sweep", 0, vectors=20, t=1)])[1]
    whole = to_live_docs(detail)
    big = max(
        estimate_doc_size(RID, d)
        for d in whole.docs.values()
        if d.kind == "chunk" and d.bucket == "v0"
    )
    monkeypatch.setattr(live_rows, "SOFT_DOC_BYTES", big - 1)
    split = to_live_docs(detail)
    assert not split.truncated
    buckets = sorted(d.bucket or "" for d in split.docs.values() if d.kind == "chunk")
    # chunk v0 (16 points) became per-vector docs; chunk v1 (4 points) stayed whole
    assert buckets.count("v1") == 1 and sum(b.startswith("p") for b in buckets) == 16
    assert len(set(split.docs)) == len(split.docs)  # no row_id collision
    assert rejoin(detail.run, split.docs.values()) == sort_detail(detail)


def test_hard_budget_omits_the_doc_and_marks_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = _fold([_started(), *swept_step_events("sweep", 0, vectors=20, t=1)])[1]
    monkeypatch.setattr(live_rows, "SOFT_DOC_BYTES", 1)
    monkeypatch.setattr(live_rows, "HARD_DOC_BYTES", 1200)
    docset = to_live_docs(detail)
    assert docset.truncated
    assert all(estimate_doc_size(RID, d) <= 1200 for d in docset.docs.values())
    header = build_header(detail, truncated=docset.truncated)
    assert header.truncated


def test_estimate_doc_size_grows_with_content(rep_detail: RunDetail) -> None:
    docs = list(to_live_docs(rep_detail).docs.values())
    step = next(d for d in docs if d.kind == "step")
    chunk = next(d for d in docs if d.kind == "chunk" and d.key.step_path == "sweep")
    assert 100 < estimate_doc_size(RID, step) < estimate_doc_size(RID, chunk) < 64 * 1024


# ---------------------------------------------------------------------------
# T3 hash diff, manifest, parts
# ---------------------------------------------------------------------------


def test_first_push_sends_everything_then_nothing(rep_detail: RunDetail) -> None:
    state = LiveSyncState()
    pushes = state.build_pushes(RID, rep_detail, now=0.0, now_ns=10)
    assert len(pushes) == 1
    push = pushes[0]
    n_docs = len(to_live_docs(rep_detail).docs)
    assert len(push.upserts) == n_docs and push.header is not None
    assert push.manifest is not None and sorted(push.manifest) == sorted(
        d.row_id for d in push.upserts
    )
    state.commit(push, now=0.0)
    assert state.build_pushes(RID, rep_detail, now=1.0, now_ns=20) == []


def test_failed_push_leaves_docs_dirty_and_seq_increases(rep_detail: RunDetail) -> None:
    state = LiveSyncState()
    first = state.build_pushes(RID, rep_detail, now=0.0, now_ns=10)[0]
    # no commit (the POST failed): the next push resends the same docs
    again = state.build_pushes(RID, rep_detail, now=1.0, now_ns=10)[0]
    assert {d.row_id for d in again.upserts} == {d.row_id for d in first.upserts}
    assert again.seq > first.seq  # strictly increasing even on a stalled clock
    assert again.manifest is not None  # the manifest is still owed


def test_only_changed_docs_are_sent(rep_detail: RunDetail) -> None:
    state = LiveSyncState()
    state.commit(state.build_pushes(RID, rep_detail, now=0.0, now_ns=1)[0], now=0.0)
    docs = to_live_docs(rep_detail).docs
    victim = next(r for r, d in docs.items() if d.kind == "chunk" and d.key.step_path == "plain")
    state.sent[victim] = "stale-hash"
    pushes = state.build_pushes(RID, rep_detail, now=1.0, now_ns=2)
    assert len(pushes) == 1 and [d.row_id for d in pushes[0].upserts] == [victim]
    assert pushes[0].header is None and pushes[0].manifest is None


def test_vanished_doc_becomes_a_delete(rep_detail: RunDetail) -> None:
    state = LiveSyncState()
    state.commit(state.build_pushes(RID, rep_detail, now=0.0, now_ns=1)[0], now=0.0)
    state.sent["f" * 24] = "x"
    push = state.build_pushes(RID, rep_detail, now=1.0, now_ns=2)[0]
    assert push.deletes == ["f" * 24] and not push.upserts
    state.commit(push, now=1.0)
    assert "f" * 24 not in state.sent


def test_resync_request_owes_a_complete_manifest(rep_detail: RunDetail) -> None:
    state = LiveSyncState()
    state.commit(state.build_pushes(RID, rep_detail, now=0.0, now_ns=1)[0], now=0.0)
    state.request_resync()
    push = state.build_pushes(RID, rep_detail, now=1.0, now_ns=2)[0]
    assert push.manifest is not None
    assert sorted(push.manifest) == sorted(to_live_docs(rep_detail).docs)
    assert not push.upserts


def test_header_only_on_non_counter_change_or_lease() -> None:
    _, running = _fold(representative_events(ended=False))
    state = LiveSyncState()
    state.commit(state.build_pushes(RID, running, now=0.0, now_ns=1)[0], now=0.0)
    assert state.build_pushes(RID, running, now=1.0, now_ns=2) == []
    counters = running.model_copy(
        update={"run": running.run.model_copy(update={"num_steps": 99, "num_measurements": 99})}
    )
    assert state.build_pushes(RID, counters, now=2.0, now_ns=3) == []  # counters alone: no write
    lease = state.build_pushes(RID, running, now=3.0, now_ns=4, force_header=True)
    assert len(lease) == 1 and lease[0].header is not None and not lease[0].upserts
    ended = _fold(representative_events(ended=True))[1]
    push = state.build_pushes(RID, ended, now=4.0, now_ns=5)[0]
    assert push.header is not None and push.header.state == "ended"
    assert push.header.run.outcome == "failed"


def test_unchanged_doc_is_refreshed_after_24h(rep_detail: RunDetail) -> None:
    state = LiveSyncState()
    state.commit(state.build_pushes(RID, rep_detail, now=0.0, now_ns=1)[0], now=0.0)
    assert state.build_pushes(RID, rep_detail, now=86_399.0, now_ns=2) == []
    late = state.build_pushes(RID, rep_detail, now=86_400.0, now_ns=3)
    assert len(late[0].upserts) == len(to_live_docs(rep_detail).docs)


def test_writes_beyond_the_cap_go_out_as_parts(
    rep_detail: RunDetail, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_rows, "MAX_WRITES_PER_PUSH", 5)
    state = LiveSyncState()
    pushes = state.build_pushes(RID, rep_detail, now=0.0, now_ns=1)
    assert len(pushes) > 1
    assert pushes[0].header is not None and pushes[0].manifest is not None
    assert all(p.header is None and p.manifest is None for p in pushes[1:])
    assert all(len(p.upserts) + len(p.deletes) + (p.header is not None) <= 5 for p in pushes)
    assert [p.seq for p in pushes] == sorted({p.seq for p in pushes})
    sent = [d.row_id for p in pushes for d in p.upserts]
    assert sorted(sent) == sorted(to_live_docs(rep_detail).docs)


def test_cost_guard_one_new_measurement_touches_tail_chunk_and_step_doc() -> None:
    """docs/41 T3: one new measurement in a long sweep re-sends <= 2 row docs (the
    tail chunk + the step doc) and no header."""
    events = [_started(), *swept_step_events("sweep", 0, vectors=200, t=1, finish=False)]
    pool, detail = _fold(events)
    state = LiveSyncState()
    state.commit(state.build_pushes(RID, detail, now=0.0, now_ns=1)[0], now=0.0)
    extra = MeasurementRecorded(
        session_id=events[0].session_id,
        run_id=RUN_ID,
        step_name="sweep",
        step_index=0,
        step_path="sweep",
        vector_index=199,
        measurement_name="late",
        value=1.0,
        unit="V",
        outcome="passed",
        occurred_at=ts(50),
    )
    pool.dispatch(as_dicts([extra])[0])
    acc = pool.get(RID)
    assert acc is not None
    after = project_run(acc)
    assert after is not None
    pushes = state.build_pushes(RID, after, now=1.0, now_ns=2)
    assert len(pushes) == 1
    assert 1 <= len(pushes[0].upserts) <= 2 and pushes[0].header is None
    assert {d.kind for d in pushes[0].upserts} <= {"step", "chunk"}
    assert [d.bucket for d in pushes[0].upserts if d.kind == "chunk"] == ["v12"]  # 199 // 16


# ---------------------------------------------------------------------------
# Wire validation (the server validates against these same models)
# ---------------------------------------------------------------------------


def test_wire_models_reject_bad_input(rep_detail: RunDetail) -> None:
    push = LiveSyncState().build_pushes(RID, rep_detail, now=0.0, now_ns=1)[0]
    body = push.model_dump(mode="json")
    LivePush.model_validate(body)
    with pytest.raises(ValidationError):  # no org field on the wire
        LivePush.model_validate({**body, "org": "someone-else"})
    forged = {**body, "upserts": [{**body["upserts"][0], "row_id": "0" * 24}, *body["upserts"][1:]]}
    with pytest.raises(ValidationError, match="row_id"):
        LivePush.model_validate(forged)
    chunk = next(u for u in body["upserts"] if u["kind"] == "chunk")
    bad_col = {**chunk, "measurements": {"not_a_p3_column": [1]}}
    with pytest.raises(ValidationError, match="unknown"):
        LiveDoc.model_validate(bad_col)
    with pytest.raises(ValidationError, match="writes"):
        LivePush(run_id=RID, seq=1, deletes=["x"] * (live_rows.MAX_WRITES_PER_PUSH + 1))


# ---------------------------------------------------------------------------
# M1 / M6: measurement hook (prints, does not assert numbers)
# ---------------------------------------------------------------------------


def _quantiles(values: list[int]) -> str:
    values = sorted(values)
    p95 = values[min(len(values) - 1, int(len(values) * 0.95))]
    return f"n={len(values)} p50={int(statistics.median(values))} p95={p95} max={values[-1]}"


def _report(label: str, detail: RunDetail) -> None:
    docs = to_live_docs(detail).docs.values()
    steps = [estimate_doc_size(RID, d) for d in docs if d.kind == "step"]
    chunks = [estimate_doc_size(RID, d) for d in docs if d.kind == "chunk"]
    header = len(build_header(detail).model_dump_json())
    full = [
        estimate_doc_size(RID, d)
        for d in docs
        if d.kind == "chunk" and d.vectors and len(d.vectors["vector_index"]) == 16
    ]
    print(f"[M1] {label}: header~{header} B (json)")  # noqa: T201
    print(f"[M1] {label}: step docs   {_quantiles(steps)} (bytes, Firestore formula)")  # noqa: T201
    print(f"[M1] {label}: chunk docs  {_quantiles(chunks)}")  # noqa: T201
    if full:
        print(f"[M1] {label}: FULL 16-vector chunks {_quantiles(full)}")  # noqa: T201


def test_measure_live_doc_sizes_and_cpu() -> None:
    """M1 (doc sizes) and M6 (per-push CPU, before/after the incremental projection)."""
    typical: list[Any] = [_started()]
    for i in range(20):
        typical += swept_step_events(f"step_{i:02d}", i, vectors=15, t=1 + i, finish=False)
    long_sweep = [_started(), *swept_step_events("long_sweep", 0, vectors=5000, t=1, finish=False)]
    for label, events, last_step, last_idx, next_vi in (
        ("typical 20x15 vectors", typical, "step_19", 19, 15),
        ("5000-vector sweep", long_sweep, "long_sweep", 0, 5000),
    ):
        pool = AccumulatorPool()
        for evt in as_dicts(events):
            pool.dispatch(evt)
        acc = pool.get(RID)
        assert acc is not None

        # before: re-project the whole run on every push
        t0 = time.perf_counter()
        detail = project_run(acc)
        t1 = time.perf_counter()
        assert detail is not None
        full_state = LiveSyncState()
        pushes = full_state.build_pushes(RID, detail, now=0.0, now_ns=1)
        t2 = time.perf_counter()
        _report(label, detail)
        body = sum(len(p.model_dump_json()) for p in pushes)
        print(  # noqa: T201
            f"[M6 before] {label}: project_run {1000 * (t1 - t0):.0f} ms + to_live_docs/diff "
            f"{1000 * (t2 - t1):.0f} ms = {1000 * (t2 - t0):.0f} ms per push; "
            f"first-push body {body} B in {len(pushes)} push(es)"
        )

        # after: project only the dirty partitions (steady state = one new sweep point)
        proj, state = LiveRunProjection(), LiveSyncState()
        ti = time.perf_counter()
        proj.refresh(acc)
        initial = time.perf_counter() - ti
        assert proj.header is not None
        for push in state.build_pushes_from(
            RID, proj.header, proj.docs, proj.hashes, now=0.0, now_ns=1
        ):
            state.commit(push, now=0.0)
        steady = []
        new: list = []
        for k in range(5):
            for evt in as_dicts(sweep_point_events(last_step, last_idx, next_vi + k, t=200 + k)):
                pool.dispatch(evt)
            ts0 = time.perf_counter()
            proj.refresh(acc)
            assert proj.header is not None
            new = state.build_pushes_from(
                RID, proj.header, proj.docs, proj.hashes, now=float(k + 1), now_ns=k + 2
            )
            steady.append(time.perf_counter() - ts0)
            for push in new:
                state.commit(push, now=float(k + 1))
        print(  # noqa: T201
            f"[M6 after]  {label}: initial catch-up {1000 * initial:.0f} ms (once); steady per "
            f"push median {1000 * statistics.median(steady):.0f} ms, "
            f"max {1000 * max(steady):.0f} ms; docs re-sent {sum(len(p.upserts) for p in new)}"
        )
