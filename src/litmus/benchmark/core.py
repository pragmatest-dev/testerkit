"""Core benchmark primitives — the case contract and the timing loop.

A :class:`Workload` is ONE measurable case: an operation at a specific
number of ``unit`` of work and a specific number of concurrent
``writers``. The benchmark sweeps unit (100 / 1k / 10k …) and writers
(1 / 2 / 4) by emitting a *separate case per combination*, so every row
in the report is a distinct, real measurement — not a collapsed average.

The same cases drive both ``litmus benchmark`` and the perf test suite
(``tests/test_data/test_perf.py``): one definition, two callers, so a
user's numbers and CI's numbers can't drift.

The timing loop is deliberately small (no pytest-benchmark at runtime):
warm up, then take ``rounds`` samples with the garbage collector paused,
and report best / mean / median. ``min`` (best-of-N) is the stable
statistic; throughput divides the work done by the best time.
"""

from __future__ import annotations

import gc
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litmus.benchmark.scenario import Coefficients, ConcurrencySweep, StorageFootprint
    from litmus.benchmark.system import ResourceFootprint


class BenchContext:
    """Carries the data directory and tracks store objects to close.

    Workload ``setup`` functions open stores against ``data_dir`` and
    register them with :meth:`track`; both the CLI runner and the perf
    tests call :meth:`close` when the case is done. The CLI points
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
    """One measurable case: ``op`` at ``scale`` unit with ``writers`` writers.

    ``writers == 1`` runs in-process via ``setup`` (which returns the
    callable to time, performing ``scale`` unit of work per call).
    ``writers > 1`` runs ``scale`` unit in each of N subprocesses via the
    concurrency runner (``setup`` is unused there).
    """

    op: str  # e.g. "events.emit"
    store: str  # "events" | "runs" | "channels" | "files"
    unit: str  # "events" | "samples" | "runs" | "files" | "queries" | ...
    scale: int  # unit of work attempted (per writer)
    writers: int = 1
    setup: Callable[[BenchContext], Callable[[], object]] | None = None
    tier: str = "fast"
    bytes_per_unit: int | None = None  # set for byte-heavy ops → enables bytes/s
    rounds: int | None = None  # per-case round override (None → use the tier default)

    @property
    def key(self) -> str:
        w = f" x{self.writers}" if self.writers > 1 else ""
        return f"{self.op} @ {_human(self.scale)}{w}"


@dataclass
class WorkloadResult:
    """Timing + load for one case (one row in the report)."""

    op: str
    store: str
    unit: str
    scale: int  # unit attempted per writer
    writers: int
    rounds: int
    min_s: float
    mean_s: float
    median_s: float
    max_s: float
    # Records actually moved per call by ONE writer: ``scale`` for writes,
    # the result-set size for queries (``None`` until measured -> falls
    # back to ``scale``).
    records: int | None = None
    bytes_per_unit: int | None = None

    @property
    def records_per_call(self) -> int:
        return self.records if self.records is not None else self.scale

    @property
    def throughput(self) -> float:
        """Aggregate records per second across all writers."""
        total = self.records_per_call * self.writers
        return (total / self.min_s) if self.min_s > 0 else 0.0

    @property
    def bytes_per_s(self) -> float | None:
        """Aggregate bytes per second, or None when the op isn't byte-sized."""
        if self.bytes_per_unit is None:
            return None
        total_bytes = self.records_per_call * self.writers * self.bytes_per_unit
        return (total_bytes / self.min_s) if self.min_s > 0 else 0.0

    @property
    def per_unit_us(self) -> float:
        """Microseconds per single record (one writer's view)."""
        rpc = self.records_per_call
        return (self.min_s * 1e6 / rpc) if rpc else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "op": self.op,
            "store": self.store,
            "unit": self.unit,
            "units_attempted": self.scale,
            "writers": self.writers,
            "records_per_call": self.records_per_call,
            "rounds": self.rounds,
            "best_ms": round(self.min_s * 1000, 4),
            "mean_ms": round(self.mean_s * 1000, 4),
            "median_ms": round(self.median_s * 1000, 4),
            "max_ms": round(self.max_s * 1000, 4),
            "per_unit_us": round(self.per_unit_us, 3),
            "throughput_per_s": round(self.throughput, 1),
            "bytes_per_s": round(self.bytes_per_s, 1) if self.bytes_per_s is not None else None,
        }


@dataclass
class BenchmarkReport:
    """The full result of a benchmark run — serialized to JSON + Markdown."""

    hardware: dict[str, object]
    versions: dict[str, object]
    options: dict[str, object]
    results: list[WorkloadResult] = field(default_factory=list)
    # Data layer's footprint under load — % of the machine's CPU and RAM
    # (Task-Manager view: "how much is it pulling from my other processes?").
    footprint: ResourceFootprint | None = None
    # Measured on-disk bytes/record per store (the calculator's storage input).
    storage: StorageFootprint | None = None
    # One write-throughput sweep per store (events / channels / files / runs).
    concurrency: list[ConcurrencySweep] = field(default_factory=list)
    # The capacity-calculator coefficient block.
    coefficients: Coefficients | None = None
    duration_s: float = 0.0

    @property
    def runs_concurrency(self) -> ConcurrencySweep | None:
        """The production-run sweep — the input to the runs-ingest capacity model."""
        return next((s for s in self.concurrency if s.store == "runs"), None)

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "hardware": self.hardware,
            "versions": self.versions,
            "options": self.options,
            "duration_s": round(self.duration_s, 2),
            "results": [r.as_dict() for r in self.results],
        }
        if self.footprint is not None:
            out["footprint_under_load"] = asdict(self.footprint)
        if self.storage is not None:
            out["storage_bytes_per_record"] = asdict(self.storage)
        if self.concurrency:
            out["concurrency_by_store"] = [asdict(s) for s in self.concurrency]
        if self.coefficients is not None:
            out["coefficients"] = asdict(self.coefficients)
        return out


def _human(n: int) -> str:
    """Compact unit count: 100, 1k, 10k, 1.5M."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:g}M"
    if n >= 1_000:
        return f"{n / 1_000:g}k"
    return str(n)


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
