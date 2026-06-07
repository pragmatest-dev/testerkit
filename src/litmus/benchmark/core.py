"""Core benchmark primitives — the workload contract and the timing loop.

A :class:`Workload` is a single measurable unit of store work. Its
``setup`` opens whatever store it needs against a :class:`BenchContext`
(which carries the data directory) and returns the callable to time.
The SAME workloads drive both ``litmus benchmark`` and the perf test
suite (``tests/test_data/test_perf.py``) — one definition, two callers,
so a user's numbers and CI's numbers can never drift.

The timing loop here is deliberately small (no pytest-benchmark at
runtime): warm up, then take ``rounds`` samples with the garbage
collector paused, and report best/mean. ``min`` is the stable statistic
(best-of-N sheds scheduler jitter); ``units_per_s`` divides the work
done per call by the best time so throughput is comparable across
machines.
"""

from __future__ import annotations

import gc
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


class BenchContext:
    """Carries the data directory and tracks store objects to close.

    Workload ``setup`` functions open stores against ``data_dir`` and
    register them with :meth:`track`; both the CLI runner and the perf
    tests call :meth:`close` when the workload is done. The CLI points
    ``data_dir`` at a throwaway OS temp dir; the perf tests point it at
    the canonical store.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._closeables: list[object] = []

    def track(self, obj: object) -> object:
        """Register an object with a ``close()`` / ``release()`` to tear down later."""
        self._closeables.append(obj)
        return obj

    def close(self) -> None:
        """Close tracked objects in reverse order. Best-effort — never raises."""
        for obj in reversed(self._closeables):
            for method in ("close", "release"):
                fn = getattr(obj, method, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:  # noqa: BLE001 — teardown is best-effort
                        pass
                    break
        self._closeables.clear()


@dataclass(frozen=True)
class Workload:
    """One measurable store operation.

    ``setup`` receives a :class:`BenchContext` and returns the callable
    to time. The returned callable performs ``n`` units of work per
    invocation (``n`` events emitted, samples written, bytes streamed,
    or ``1`` for a single query), so throughput is ``n / best_time``.
    """

    key: str
    store: str
    label: str
    unit: str
    n: int
    setup: Callable[[BenchContext], Callable[[], object]]
    tier: str = "fast"


@dataclass
class WorkloadResult:
    """Timing outcome for one workload."""

    key: str
    store: str
    label: str
    unit: str
    n: int
    rounds: int
    min_s: float
    mean_s: float
    median_s: float
    max_s: float

    @property
    def units_per_s(self) -> float:
        return (self.n / self.min_s) if self.min_s > 0 else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "store": self.store,
            "label": self.label,
            "unit": self.unit,
            "n_per_call": self.n,
            "rounds": self.rounds,
            "min_ms": round(self.min_s * 1000, 4),
            "mean_ms": round(self.mean_s * 1000, 4),
            "median_ms": round(self.median_s * 1000, 4),
            "max_ms": round(self.max_s * 1000, 4),
            "units_per_s": round(self.units_per_s, 1),
        }


@dataclass
class ConcurrencyResult:
    """Aggregate throughput of a parallel-writer probe."""

    store: str
    n_writers: int
    n_per_writer: int
    total_ops: int
    wall_s: float

    @property
    def ops_per_s(self) -> float:
        return (self.total_ops / self.wall_s) if self.wall_s > 0 else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "store": self.store,
            "n_writers": self.n_writers,
            "n_per_writer": self.n_per_writer,
            "total_ops": self.total_ops,
            "wall_s": round(self.wall_s, 4),
            "ops_per_s": round(self.ops_per_s, 1),
        }


@dataclass
class BenchmarkReport:
    """The full result of a benchmark run — serialized to JSON."""

    hardware: dict[str, object]
    versions: dict[str, object]
    options: dict[str, object]
    results: list[WorkloadResult] = field(default_factory=list)
    concurrency: list[ConcurrencyResult] = field(default_factory=list)
    resources: dict[str, object] | None = None
    duration_s: float = 0.0

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "hardware": self.hardware,
            "versions": self.versions,
            "options": self.options,
            "duration_s": round(self.duration_s, 2),
            "results": [r.as_dict() for r in self.results],
            "concurrency": [c.as_dict() for c in self.concurrency],
        }
        if self.resources is not None:
            out["resources"] = self.resources
        return out


def time_workload(fn: Callable[[], object], *, rounds: int, warmup: int) -> tuple[float, ...]:
    """Run ``fn`` ``warmup`` times to settle caches, then ``rounds`` timed
    samples with GC paused. Returns ``(min, mean, median, max)`` seconds.
    """
    for _ in range(max(0, warmup)):
        fn()

    samples: list[float] = []
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(rounds):
            t0 = time.perf_counter()
            fn()
            samples.append(time.perf_counter() - t0)
    finally:
        if gc_was_enabled:
            gc.enable()

    return (
        min(samples),
        statistics.fmean(samples),
        statistics.median(samples),
        max(samples),
    )
