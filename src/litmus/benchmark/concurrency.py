"""Parallel-writer probes — does the singleton daemon scale with writers?

Each probe spawns ``n_writers`` subprocesses (spawn start method, so
they hit the real daemon RPC path the way separate test stations would)
and reports aggregate writes/second. Flat throughput as writers grow
means the daemon writes in parallel; a drop means a serialization point.

The fast tier runs a single ``--concurrency N`` probe per store
(default N=2 — most setups never run four stations at once). The full
tier runs the 1/2/4 sweep. The ≥1.5× sublinear *gate* stays in the perf
test suite (CI-only) — the CLI reports, it does not gate.

Workers take ``data_dir`` explicitly: ``resolve_data_dir`` checks a
project ``litmus.yaml`` before any env var, so the only reliable way to
pin a spawned worker to the benchmark's temp dir is to pass the path.
"""

from __future__ import annotations

import time
from multiprocessing import get_context
from pathlib import Path
from uuid import uuid4

from litmus.benchmark.core import ConcurrencyResult


def _event_worker(data_dir: str, n: int, seed: int) -> tuple[float, int]:
    from litmus.data.event_store import EventStore
    from litmus.data.events import MeasurementRecorded

    store = EventStore(_data_dir=Path(data_dir))
    sid = uuid4()
    ok = 0
    t0 = time.perf_counter()
    try:
        for i in range(n):
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
            ok += 1
    finally:
        store.close()
    return time.perf_counter() - t0, ok


def _channel_worker(data_dir: str, n: int, seed: int) -> tuple[float, int]:
    from litmus.data.channels.store import ChannelStore

    store = ChannelStore(Path(data_dir), uuid4(), flush_threshold=50, serve=True)
    store.open()
    ok = 0
    t0 = time.perf_counter()
    try:
        for i in range(n):
            store.write(f"dmm.voltage_w{seed}", 3.3 + (i % 100) * 0.01)
            ok += 1
    finally:
        store.close()
    return time.perf_counter() - t0, ok


def _file_worker(data_dir: str, n: int, seed: int) -> tuple[float, int]:
    from litmus.data.files.store import FileStore

    store = FileStore(data_dir=Path(data_dir))
    sid = uuid4().hex
    payload = b"z" * (10 * 1024)
    ok = 0
    t0 = time.perf_counter()
    for i in range(n):
        store.write(f"art_{seed}_{i:04d}", payload, session_id=sid)
        ok += 1
    return time.perf_counter() - t0, ok


def _runs_worker(data_dir: str, n: int, seed: int) -> tuple[float, int]:
    from litmus.benchmark.workloads import build_run
    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.run_store import RunStore

    backend = ParquetBackend(data_dir=Path(data_dir))
    store = RunStore(_data_dir=Path(data_dir))
    ok = 0
    t0 = time.perf_counter()
    try:
        for i in range(n):
            store.notify_new_run(backend.save_test_run(build_run(seed * 100_000 + i)))
            ok += 1
    finally:
        store.close()
    return time.perf_counter() - t0, ok


_WORKERS = {
    "events": _event_worker,
    "channels": _channel_worker,
    "files": _file_worker,
    "runs": _runs_worker,
}

# Per-store writes-per-worker for a concurrency probe. Runs are far
# heavier per op (parquet write + index) so they get a smaller count.
_N_PER = {"events": 300, "channels": 500, "files": 100, "runs": 15}


def run_concurrency_probe(data_dir: Path, store: str, n_writers: int) -> ConcurrencyResult:
    """Spawn ``n_writers`` writers for ``store``; return aggregate throughput."""
    worker = _WORKERS[store]
    n_per = _N_PER[store]
    with get_context("spawn").Pool(n_writers) as pool:
        results = pool.starmap(worker, [(str(data_dir), n_per, w) for w in range(n_writers)])
    total_ok = sum(ok for _, ok in results)
    wall = max(w for w, _ in results) if results else 0.0  # concurrent → slowest = wall
    return ConcurrencyResult(
        store=store,
        n_writers=n_writers,
        n_per_writer=n_per,
        total_ops=total_ok,
        wall_s=wall,
    )
