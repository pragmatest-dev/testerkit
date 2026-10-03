"""Incremental live projection == whole-run projection (docs/41 M6 design change).

``LiveRunProjection`` re-projects only the partitions that changed. The reference is
``project_run`` + ``to_live_docs`` over the whole run: after EVERY event (and after
random batches of events) the incremental docs, hashes and header must equal it, and
must rejoin to the whole-run ``run_detail``.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from testerkit.data._accumulator_pool import AccumulatorPool
from testerkit.data.backends._event_accumulator import EventAccumulator
from testerkit.data.events import (
    MeasurementRecorded,
    RunEnded,
    StepEnded,
    StepsDiscovered,
    StepStarted,
    VectorEnded,
    VectorStarted,
)
from testerkit.data.live_projection import LiveRunProjection, project_run
from testerkit.data.live_rows import (
    build_header,
    content_hash,
    rejoin,
    sort_detail,
    to_live_docs,
)
from tests.test_data.live_streams import (
    RUN_ID,
    SESSION_ID,
    as_dicts,
    plain_step_events,
    representative_events,
    run_started,
    sweep_point_events,
    swept_step_events,
    ts,
)

RID = str(RUN_ID)


def _assert_equivalent(
    acc: EventAccumulator, proj: LiveRunProjection, where: str, *, rejoined: bool = True
) -> None:
    full = project_run(acc)
    assert full is not None
    docset = to_live_docs(full)
    assert {r: content_hash(d) for r, d in docset.docs.items()} == proj.hashes, where
    assert proj.header == build_header(full, truncated=docset.truncated), where
    assert proj.header is not None
    if rejoined and full.run.ended_at is not None:
        # Only at the end: while a step is on a retry, the at-rest builders give its
        # measurement rows the PREVIOUS attempt's `step_ended_at` (a min-retry lookup),
        # which the step doc (the value the docs carry) rightly does not.
        assert rejoin(proj.header.run, proj.docs.values()) == sort_detail(full), where


def _replay(events: list[Any], *, batches: list[int] | None = None, rejoined: bool = True) -> None:
    """Feed ``events``; after every batch (default: every event) the incremental
    projection must equal the whole-run one."""
    pool = AccumulatorPool()
    proj = LiveRunProjection()
    dicts = as_dicts(events)
    sizes = batches or [1] * len(dicts)
    i = 0
    for n, size in enumerate(sizes):
        for evt in dicts[i : i + size]:
            pool.dispatch(evt)
        i += size
        acc = pool.get(RID)
        assert acc is not None
        if acc._run_started is None:
            continue
        proj.refresh(acc)
        where = f"after batch {n} (event {i}: {dicts[i - 1]['event_type']})"
        _assert_equivalent(acc, proj, where, rejoined=rejoined)
    assert i == len(dicts) or batches is not None


def _with_ghosts(events: list[Any]) -> list[Any]:
    """Representative stream whose discovered items include steps that never run."""
    out = list(events)
    idx = next(i for i, e in enumerate(out) if isinstance(e, StepsDiscovered))
    discovered = out[idx]
    extra = [
        {
            "node_id": "tests/test_hw.py::never",
            "step_index": 9,
            "step_path": "never",
            "markers": None,
            "vector_count_planned": 1,
        },
        {
            "node_id": "tests/test_hw.py::sweep[5]",
            "step_index": 0,
            "step_path": "sweep",
            "vector_index": 5,
            "markers": None,
            "vector_count_planned": 1,
        },
    ]
    out[idx] = discovered.model_copy(update={"items": [*discovered.items, *extra]})
    return out


def test_every_prefix_of_the_representative_run() -> None:
    _replay(representative_events())
    _replay(representative_events(ended=False))


def test_never_ran_steps_match_the_whole_run_build() -> None:
    # A never-ran row of a step path that also ran shares its step key with the real
    # step, so the two collapse to one doc (same as the whole-run build): no rejoin check.
    _replay(_with_ghosts(representative_events()), rejoined=False)


def test_multi_bucket_sweep_in_random_batches() -> None:
    events = [run_started(), *swept_step_events("sweep", 0, vectors=40, t=1)]
    events += plain_step_events("plain", 1, t=3)
    rng = random.Random(7)
    sizes: list[int] = []
    left = len(events)
    while left:
        n = min(left, rng.randint(1, 25))
        sizes.append(n)
        left -= n
    _replay(events, batches=sizes)


def test_measurement_before_its_vector_reclassifies() -> None:
    """A measurement can land before its VectorStarted: step-scope until the vector
    appears, then it moves to the sweep bucket."""
    kw = {"session_id": SESSION_ID, "run_id": RUN_ID, "step_name": "s", "step_index": 0}
    events: list[Any] = [
        run_started(),
        StepStarted(**kw, step_path="s", occurred_at=ts(1)),
        MeasurementRecorded(
            **kw, step_path="s", vector_index=2, measurement_name="early", value=1.0, unit="V"
        ),
        VectorStarted(**kw, step_path="s", vector_index=2, inputs={"x": 1}, occurred_at=ts(2)),
        VectorEnded(**kw, step_path="s", vector_index=2, outcome="passed", occurred_at=ts(2)),
        StepEnded(**kw, step_path="s", outcome="passed", occurred_at=ts(3)),
    ]
    _replay(events)


def test_out_of_order_positions_reindex_earlier_docs() -> None:
    """A step that sorts BEFORE an already-projected one arrives late: the run-wide
    per-name ``index`` of the earlier-arrived rows shifts, so their docs are re-sent."""
    events: list[Any] = [run_started()]
    events += swept_step_events("late", 5, vectors=2, t=1)
    events += swept_step_events("early", 1, vectors=2, t=5)  # same names, lower step_index
    events.append(
        RunEnded(session_id=SESSION_ID, run_id=RUN_ID, outcome="passed", occurred_at=ts(60))
    )
    _replay(events)


def test_a_new_sweep_point_projects_only_its_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cost guard behind M6: after the first projection, one more sweep point
    re-projects exactly one partition, whatever the sweep length."""
    pool = AccumulatorPool()
    for evt in as_dicts(
        [run_started(), *swept_step_events("s", 0, vectors=200, t=1, finish=False)]
    ):
        pool.dispatch(evt)
    acc = pool.get(RID)
    assert acc is not None
    proj = LiveRunProjection()
    proj.refresh(acc)
    calls: list[tuple[str, int | None]] = []
    real = EventAccumulator.partition

    def spy(self: EventAccumulator, path: str, bucket: int | None) -> EventAccumulator:
        calls.append((path, bucket))
        return real(self, path, bucket)

    monkeypatch.setattr(EventAccumulator, "partition", spy)
    for evt in as_dicts(sweep_point_events("s", 0, 200, t=50)):
        pool.dispatch(evt)
    assert proj.refresh(acc) is True
    assert calls == [("s", 200 // 16)]
    _assert_equivalent(acc, proj, "after one more point")
    assert proj.refresh(acc) is False  # nothing changed


def test_header_tracks_run_end_without_reprojecting_rows() -> None:
    events = representative_events(ended=False)
    pool = AccumulatorPool()
    proj = LiveRunProjection()
    for evt in as_dicts(events):
        pool.dispatch(evt)
    acc = pool.get(RID)
    assert acc is not None
    proj.refresh(acc)
    assert proj.header is not None and proj.header.state == "running"
    pool.dispatch(as_dicts(representative_events()[-1:])[0])
    proj.refresh(acc)
    assert proj.header.state == "ended" and proj.header.run.outcome == "failed"
    _assert_equivalent(acc, proj, "after RunEnded")
