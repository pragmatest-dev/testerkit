"""Parallel-writer cases — run one write op in N subprocesses.

Each round spawns ``writers`` subprocesses (spawn start method, so they
hit the real daemon RPC path the way separate stations would), each
writing ``scale`` units. Workers return their ``(start, end)`` on
``CLOCK_MONOTONIC`` (system-wide on Linux, so comparable across
processes); the round's wall is the TRUE overlapped span,
``max(end) - min(start)`` — not the slowest worker's self-timed loop,
which would miss cross-worker stagger and overstate throughput. Worker
clocks start AFTER store construction so spawn/connect cost stays out of
the timed window.

Workers take ``data_dir`` explicitly: ``resolve_data_dir`` checks a
project ``litmus.yaml`` before any env var, so the only reliable way to
pin a spawned worker to the benchmark's temp dir is to pass the path.
"""

from __future__ import annotations

import time
from multiprocessing import get_context
from pathlib import Path
from uuid import uuid4

# Cross-process-comparable clock (same reference for every process on Linux).
_clock = time.CLOCK_MONOTONIC


def _now() -> float:
    return time.clock_gettime(_clock)


def _event_worker(data_dir: str, scale: int, seed: int) -> tuple[float, float]:
    from litmus.data.event_store import EventStore
    from litmus.data.events import MeasurementRecorded

    store = EventStore(_data_dir=Path(data_dir))
    sid = uuid4()
    t0 = _now()
    try:
        for i in range(scale):
            store.emit(
                MeasurementRecorded(
                    session_id=sid,
                    step_name=f"step_{i % 10}",
                    step_index=i % 10,
                    measurement_name=f"voltage_{seed}_{i}",
                    value=3.3 + ((seed * 1000 + i) % 100) * 0.01,
                    units="V",
                    outcome="passed",
                    limit_low=3.0,
                    limit_high=3.6,
                )
            )
    finally:
        store.close()
    return (t0, _now())


def _channel_worker(data_dir: str, scale: int, seed: int) -> tuple[float, float]:
    from litmus.data.channels.store import ChannelStore

    store = ChannelStore(Path(data_dir), uuid4(), flush_threshold=50, serve=True)
    store.open()
    t0 = _now()
    try:
        for i in range(scale):
            store.write(f"dmm.voltage_w{seed}", 3.3 + (i % 100) * 0.01)
    finally:
        store.close()
    return (t0, _now())


def _file_worker(data_dir: str, scale: int, seed: int) -> tuple[float, float]:
    from litmus.data.files.store import FileStore

    store = FileStore(data_dir=Path(data_dir))
    sid = uuid4().hex
    payload = b"z" * (100 * 1024)
    t0 = _now()
    for i in range(scale):
        store.write(f"art_{seed}_{i:04d}", payload, session_id=sid)
    return (t0, _now())


def _runs_worker(data_dir: str, scale: int, seed: int) -> tuple[float, float]:
    # The real finalize path (``BuildRun.finish`` → ``save_test_run``) writes
    # the parquet atomically and returns — it does NOT notify the daemon
    # (that's a query-time / background-ingest concern). So the write
    # benchmark mirrors that: file write only, no synchronous notify.
    from litmus.benchmark.workloads import build_run
    from litmus.data.backends.parquet import ParquetBackend

    backend = ParquetBackend(data_dir=Path(data_dir))
    t0 = _now()
    for i in range(scale):
        backend.save_test_run(build_run(seed * 100_000 + i))
    return (t0, _now())


def _representative_worker(data_dir: str, scale: int, seed: int) -> tuple[float, float]:
    """Record ``scale`` PRODUCTION-profile runs (lean: measurements + run save,
    no waveforms/files) — the unit for the 'how many runs at once' sweep.

    Mirrors the real path: measurements go through the events store (which
    does push to its daemon), the run is a plain ``save_test_run`` file write
    with no synchronous daemon notify.
    """
    from litmus.benchmark.scenario import PROFILES
    from litmus.benchmark.workloads import build_run, make_measurement
    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.event_store import EventStore

    prof = next(p for p in PROFILES if p.name == "production")
    es = EventStore(_data_dir=Path(data_dir))
    backend = ParquetBackend(data_dir=Path(data_dir))
    t0 = _now()
    try:
        for r in range(scale):
            sid = uuid4()
            for i in range(prof.measurements):
                es.emit(make_measurement(sid, i))
            es.flush()
            run = build_run(seed * 100_000 + r, n_steps=prof.steps, n_meas=prof.measurements)
            backend.save_test_run(run)
    finally:
        es.close()
    return (t0, _now())


_WORKERS = {
    "events.emit": _event_worker,
    "channels.write": _channel_worker,
    "files.write": _file_worker,
    "runs.save": _runs_worker,
    "representative.production": _representative_worker,
}


def run_concurrency(
    data_dir: Path, op: str, scale: int, writers: int, *, rounds: int
) -> list[float]:
    """Run ``op`` at ``scale`` units in ``writers`` subprocesses, ``rounds``
    times. Returns per-round wall times — the TRUE overlapped span
    (``max(end) - min(start)`` across workers), so throughput reflects real
    concurrency rather than the slowest worker's self-timed loop.
    """
    worker = _WORKERS[op]
    walls: list[float] = []
    ctx = get_context("spawn")
    for _ in range(rounds):
        with ctx.Pool(writers) as pool:
            spans = pool.starmap(worker, [(str(data_dir), scale, w) for w in range(writers)])
        if spans:
            walls.append(max(e for _, e in spans) - min(s for s, _ in spans))
        else:
            walls.append(0.0)
    return walls
