"""Litmus data-store benchmark — measure your machine's per-store ceiling.

``litmus benchmark`` runs the same store workloads the perf test suite
gates on, against a throwaway temp directory, and writes a self-
describing result file (hardware + versions + options + numbers) you can
send to the maintainer when diagnosing a performance problem. It is
local-only — nothing is sent anywhere automatically.

Public API:

- :func:`run_benchmark` — run the workloads and return a report.
- :func:`write_report` — write the report to ``.benchmarks/<date>.json``.
- :class:`BenchmarkOptions` — what was run (tier, concurrency, …).
"""

from __future__ import annotations

from litmus.benchmark.core import (
    BenchContext,
    BenchmarkReport,
    Workload,
    WorkloadResult,
)
from litmus.benchmark.runner import BenchmarkOptions, run_benchmark, write_report

__all__ = [
    "BenchContext",
    "BenchmarkOptions",
    "BenchmarkReport",
    "Workload",
    "WorkloadResult",
    "run_benchmark",
    "write_report",
]
