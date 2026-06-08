"""Parallel-writer cases — run one write op in N subprocesses.

Each round spawns ``writers`` subprocesses (spawn start method, so they
hit the real daemon RPC path the way separate stations would), each
writing ``scale`` units. The round's wall is the slowest worker; we run
``rounds`` rounds and return the per-round walls so the runner builds a
:class:`WorkloadResult` exactly like a single-writer case.

Workers take ``data_dir`` explicitly: ``resolve_data_dir`` checks a
project ``litmus.yaml`` before any env var, so the only reliable way to
pin a spawned worker to the benchmark's temp dir is to pass the path.
"""

from __future__ import annotations

import time
from multiprocessing import get_context
from pathlib import Path
from uuid import uuid4


def _event_worker(data_dir: str, scale: int, seed: int) -> float:
    from litmus.data.event_store import EventStore
    from litmus.data.events import MeasurementRecorded

    store = EventStore(_data_dir=Path(data_dir))
    sid = uuid4()
    t0 = time.perf_counter()
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
    return time.perf_counter() - t0


def _channel_worker(data_dir: str, scale: int, seed: int) -> float:
    from litmus.data.channels.store import ChannelStore

    store = ChannelStore(Path(data_dir), uuid4(), flush_threshold=50, serve=True)
    store.open()
    t0 = time.perf_counter()
    try:
        for i in range(scale):
            store.write(f"dmm.voltage_w{seed}", 3.3 + (i % 100) * 0.01)
    finally:
        store.close()
    return time.perf_counter() - t0


def _file_worker(data_dir: str, scale: int, seed: int) -> float:
    from litmus.data.files.store import FileStore

    store = FileStore(data_dir=Path(data_dir))
    sid = uuid4().hex
    payload = b"z" * (100 * 1024)
    t0 = time.perf_counter()
    for i in range(scale):
        store.write(f"art_{seed}_{i:04d}", payload, session_id=sid)
    return time.perf_counter() - t0


def _runs_worker(data_dir: str, scale: int, seed: int) -> float:
    from litmus.benchmark.workloads import build_run
    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.run_store import RunStore

    backend = ParquetBackend(data_dir=Path(data_dir))
    store = RunStore(_data_dir=Path(data_dir))
    t0 = time.perf_counter()
    try:
        for i in range(scale):
            store.notify_new_run(backend.save_test_run(build_run(seed * 100_000 + i)))
    finally:
        store.close()
    return time.perf_counter() - t0


def _representative_worker(data_dir: str, scale: int, seed: int) -> float:
    """Record ``scale`` PRODUCTION-profile runs (lean: measurements + run save,
    no waveforms/files) — the unit for the 'how many runs at once' sweep."""
    from litmus.benchmark.scenario import PROFILES
    from litmus.benchmark.workloads import build_run, make_measurement
    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.event_store import EventStore
    from litmus.data.run_store import RunStore

    prof = next(p for p in PROFILES if p.name == "production")
    es = EventStore(_data_dir=Path(data_dir))
    backend = ParquetBackend(data_dir=Path(data_dir))
    store = RunStore(_data_dir=Path(data_dir))
    t0 = time.perf_counter()
    try:
        for r in range(scale):
            sid = uuid4()
            for i in range(prof.measurements):
                es.emit(make_measurement(sid, i))
            es.flush()
            run = build_run(seed * 100_000 + r, n_steps=prof.steps)
            store.notify_new_run(backend.save_test_run(run))
    finally:
        es.close()
        store.close()
    return time.perf_counter() - t0


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
    times. Returns per-round wall times (slowest worker each round)."""
    worker = _WORKERS[op]
    walls: list[float] = []
    ctx = get_context("spawn")
    for _ in range(rounds):
        with ctx.Pool(writers) as pool:
            worker_walls = pool.starmap(worker, [(str(data_dir), scale, w) for w in range(writers)])
        walls.append(max(worker_walls) if worker_walls else 0.0)
    return walls
