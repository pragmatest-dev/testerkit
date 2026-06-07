"""Benchmark orchestration — case execution, teardown, and report output.

Runs every case from :func:`build_cases` (one per operation x units x
writers), records latency + throughput + per-case load, and renders two
things: a RAW table (every case is a row, with Units and Writers as
columns) and a SUMMARY (methodology + a fixed-overhead / per-unit cost
model fitted from the scale sweep).

The run is hermetic: a fresh OS temp dir, store daemons spawned there,
killed and the dir wiped on teardown — even on error.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from litmus.benchmark.core import (
    BenchContext,
    BenchmarkReport,
    WorkloadResult,
    cost_anchors,
    time_workload,
)
from litmus.benchmark.system import (
    ResourceSampler,
    collect_hardware,
    collect_versions,
    have_psutil,
)
from litmus.benchmark.workloads import build_cases


@dataclass
class BenchmarkOptions:
    """How a run was parameterized — echoed into the result file."""

    tier: str = "fast"  # "fast" | "full"
    rounds: int = 3
    warmup: int = 1

    def as_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["psutil"] = have_psutil()
        return d


def _result_size(result: object) -> int | None:
    """Row count of a query result; None for writes (None / str / bytes)."""
    if result is None or isinstance(result, (str, bytes, bytearray)):
        return None
    try:
        return len(result)  # type: ignore[arg-type]
    except TypeError:
        return None


def _kill_daemons(data_dir: Path) -> None:
    from litmus.data.channels.flight_manager import FlightDaemonManager
    from litmus.data.duckdb_manager import DuckDBDaemonManager
    from litmus.data.files.catalog_manager import FilesCatalogManager
    from litmus.data.runs_duckdb_manager import RunsDuckDBManager

    for mgr in (
        DuckDBDaemonManager(data_dir / "events"),
        RunsDuckDBManager(data_dir / "runs"),
        FlightDaemonManager(data_dir / "channels"),
        FilesCatalogManager(data_dir / "files"),
    ):
        try:
            mgr.force_restart()
        except Exception:  # noqa: BLE001 — teardown is best-effort
            pass


def run_benchmark(
    options: BenchmarkOptions,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> BenchmarkReport:
    """Run all cases against a throwaway temp dir and return the report."""

    def progress(msg: str) -> None:
        if on_progress is not None:
            on_progress(msg)

    cases = build_cases(options.tier)
    conc_rounds = 3 if options.tier == "full" else 2
    data_dir = Path(tempfile.mkdtemp(prefix="litmus-bench-"))
    started = time.perf_counter()

    report = BenchmarkReport(
        hardware=collect_hardware(),
        versions=collect_versions(),
        options=options.as_dict(),
    )

    try:
        with ResourceSampler() as sampler:
            for case in cases:
                label = case.key
                progress(f"{label} ...")
                sampler.mark(label)
                records: int | None = None
                if case.writers == 1 and case.setup is not None:
                    ctx = BenchContext(data_dir)
                    try:
                        fn = case.setup(ctx)
                        mn, mean, median, mx = time_workload(
                            fn, rounds=options.rounds, warmup=options.warmup
                        )
                        records = _result_size(fn())  # one extra call to size the result
                    finally:
                        ctx.close()
                else:
                    from litmus.benchmark.concurrency import run_concurrency

                    walls = run_concurrency(
                        data_dir, case.op, case.scale, case.writers, rounds=conc_rounds
                    )
                    import statistics

                    mn, mean, median, mx = (
                        min(walls),
                        statistics.fmean(walls),
                        statistics.median(walls),
                        max(walls),
                    )
                sampler.mark(None)
                load = sampler.case(label)
                report.results.append(
                    WorkloadResult(
                        op=case.op,
                        store=case.store,
                        unit=case.unit,
                        scale=case.scale,
                        writers=case.writers,
                        rounds=options.rounds if case.writers == 1 else conc_rounds,
                        min_s=mn,
                        mean_s=mean,
                        median_s=median,
                        max_s=mx,
                        records=records,
                        bytes_per_unit=case.bytes_per_unit,
                        peak_rss_mb=load["peak_rss_mb"],  # type: ignore[arg-type]
                        cpu_pct_mean=load["cpu_pct_mean"],  # type: ignore[arg-type]
                        cpu_pct_max=load["cpu_pct_max"],  # type: ignore[arg-type]
                    )
                )
            report.resources = sampler.overall()
    finally:
        _kill_daemons(data_dir)
        shutil.rmtree(data_dir, ignore_errors=True)

    report.duration_s = time.perf_counter() - started
    return report


def write_report(report: BenchmarkReport, output_dir: Path | str = ".benchmarks") -> Path:
    """Write a dated run folder ``<output_dir>/<date>/`` (report.md + .json)."""
    import json

    out = Path(output_dir)
    stamp = datetime.now().strftime("%Y-%m-%d")
    run_dir = out / stamp
    if run_dir.exists():
        run_dir = out / f"{stamp}T{datetime.now().strftime('%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text(format_markdown(report))
    (run_dir / "report.json").write_text(json.dumps(report.as_dict(), indent=2))
    return run_dir


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _ordered(results: list[WorkloadResult]) -> list[WorkloadResult]:
    """Group by operation, then 1-writer scale sweep, then concurrency rows."""
    ops: list[str] = []
    for r in results:
        if r.op not in ops:
            ops.append(r.op)
    return sorted(results, key=lambda r: (ops.index(r.op), r.writers, r.scale))


class _Cost(NamedTuple):
    op: str
    unit: str
    floor_units: int
    floor_ms: float  # measured best time at the smallest scale
    marginal_us: float  # per-unit time (slope), µs
    marginal_per_s: float  # per-unit rate (1/slope), records/s
    marginal_bytes_per_s: float | None  # per-unit byte rate, if byte-sized


def _cost_models(results: list[WorkloadResult]) -> list[_Cost]:
    """Per-op cost from 1-writer rows: a measured floor + a marginal slope.

    Both come from real anchors (smallest-scale best time, and the
    smallest→largest slope) — see ``cost_anchors``. No least-squares
    intercept (it produced untrustworthy ~0/negative overheads).
    """
    by_op: dict[str, list[WorkloadResult]] = {}
    order: list[str] = []
    for r in results:
        if r.writers != 1:
            continue
        by_op.setdefault(r.op, []).append(r)
        if r.op not in order:
            order.append(r.op)
    models: list[_Cost] = []
    for op in order:
        rows = by_op[op]
        floor_units, floor_s, marginal_s = cost_anchors([(r.scale, r.min_s) for r in rows])
        rate = (1.0 / marginal_s) if marginal_s > 0 else 0.0
        bpu = rows[0].bytes_per_unit
        byte_rate = (bpu / marginal_s) if (bpu is not None and marginal_s > 0) else None
        models.append(
            _Cost(op, rows[0].unit, floor_units, floor_s * 1000, marginal_s * 1e6, rate, byte_rate)
        )
    return models


def _scaling(results: list[WorkloadResult]) -> list[tuple[str, dict[int, float]]]:
    """Per concurrency op: {writers -> throughput} including the 1-writer baseline."""
    conc_ops = sorted({r.op for r in results if r.writers > 1})
    out: list[tuple[str, dict[int, float]]] = []
    for op in conc_ops:
        rep = min(r.scale for r in results if r.op == op and r.writers > 1)
        series = {
            r.writers: r.throughput
            for r in results
            if r.op == op and (r.writers > 1 or r.scale == rep)
        }
        out.append((op, series))
    return out


def _methodology(report: BenchmarkReport) -> str:
    o = report.options
    return (
        f"Methodology: each operation is run at several **unit counts** "
        f"(one row each) and, for writes, several **writer counts** (separate "
        f"processes). Each row is best-of-{o.get('rounds')} timed rounds "
        f"(GC paused, {o.get('warmup')} warmup). Throughput = records moved / "
        f"best time. The cost model fits best-time vs units over the 1-writer "
        f"rows: **fixed overhead** is the per-call floor (RPC + plan + commit), "
        f"**per-unit** is the marginal cost of one more record."
    )


def _fmt_bytes_s(bps: float | None) -> str:
    """Bytes/second as B/s, KB/s, MB/s, or GB/s; '—' when not byte-sized."""
    if bps is None:
        return "—"
    for unit, scale in (("GB/s", 1024**3), ("MB/s", 1024**2), ("KB/s", 1024)):
        if bps >= scale:
            return f"{bps / scale:,.1f} {unit}"
    return f"{bps:,.0f} B/s"


def format_markdown(report: BenchmarkReport) -> str:
    hw, ver, opts = report.hardware, report.versions, report.options
    out: list[str] = ["# Litmus benchmark", ""]
    out.append(
        f"- **Machine:** {hw.get('cpu_model')} x{hw.get('cpu_count')}, "
        f"{hw.get('ram_gb')} GB RAM, {hw.get('platform')}"
    )
    out.append(
        f"- **Versions:** litmus {ver.get('litmus')}, Python {ver.get('python')}, "
        f"pyarrow {ver.get('pyarrow')}, duckdb {ver.get('duckdb')}"
    )
    out.append(
        f"- **Run:** {opts.get('tier')} tier, {len(report.results)} cases, "
        f"{report.duration_s:.1f} s"
    )
    out.append("")

    # Raw results — one row per (operation x units x writers).
    out.append("## Results (one row per case)")
    out.append("")
    out.append(
        "| Operation | Units | Writers | Best (ms) | Mean (ms) | Throughput | "
        "Bytes/s | Peak RSS | CPU% |"
    )
    out.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for r in _ordered(report.results):
        rss = f"{r.peak_rss_mb:,.0f} MB" if r.peak_rss_mb is not None else "—"
        cpu = f"{r.cpu_pct_max:.0f}%" if r.cpu_pct_max is not None else "—"
        bps = _fmt_bytes_s(r.bytes_per_s)
        out.append(
            f"| `{r.op}` | {r.scale:,} | {r.writers} | {r.min_s * 1000:.3f} | "
            f"{r.mean_s * 1000:.3f} | {r.throughput:,.0f} {r.unit}/s | {bps} | {rss} | {cpu} |"
        )
    out.append("")

    # Summary — cost model.
    out.append("## Summary")
    out.append("")
    out.append(_methodology(report))
    out.append("")
    out.append("### Per-operation cost (1 writer)")
    out.append("")
    out.append(
        "| Operation | Per-call floor | Marginal (records/s) "
        "| Marginal (time) | Marginal (bytes/s) |"
    )
    out.append("|---|--:|--:|--:|--:|")
    for c in _cost_models(report.results):
        u = c.unit[:-1] if c.unit.endswith("s") else c.unit
        rate = f"{c.marginal_per_s:,.0f} {c.unit}/s" if c.marginal_per_s else "—"
        per = f"{c.marginal_us:.2f} µs/{u}" if c.marginal_us else "—"
        byr = _fmt_bytes_s(c.marginal_bytes_per_s)
        out.append(
            f"| `{c.op}` | {c.floor_ms:.3f} ms @ {c.floor_units:,} {c.unit} "
            f"| {rate} | {per} | {byr} |"
        )
    out.append("")

    scaling = _scaling(report.results)
    if scaling:
        out.append("### Parallel scaling (writes)")
        out.append("")
        wset = sorted({w for _, s in scaling for w in s})
        out.append("| Operation | " + " | ".join(f"{w}w" for w in wset) + " | Speedup |")
        out.append("|---|" + "--:|" * (len(wset) + 1))
        for op, series in scaling:
            cells = [f"{series[w]:,.0f}" if w in series else "—" for w in wset]
            base = series.get(1)
            best = max(series.values()) if series else 0.0
            sp = f"{best / base:.2f}×" if base else "—"
            out.append(f"| `{op}` | " + " | ".join(cells) + f" | {sp} |")
        out.append("")

    if report.resources is not None:
        res = report.resources
        out.append("### Footprint (whole run)")
        out.append("")
        out.append(f"- Peak RSS {res.get('peak_rss_mb')} MB")
        out.append(
            f"- CPU {res.get('cpu_pct_mean')}% mean / {res.get('cpu_pct_max')}% max "
            "(process tree: main + daemons + workers)"
        )
        out.append("")
    return "\n".join(out)


def format_summary(report: BenchmarkReport) -> str:
    """Terminal summary — the same raw rows + cost model, ASCII-aligned."""
    hw, opts = report.hardware, report.options
    lines: list[str] = []
    lines.append(
        f"Litmus benchmark — {opts.get('tier')} tier, {len(report.results)} cases, "
        f"{report.duration_s:.1f} s"
    )
    lines.append(
        f"  {hw.get('cpu_model')} x{hw.get('cpu_count')}  |  "
        f"{hw.get('ram_gb')} GB RAM  |  {hw.get('platform')}"
    )
    lines.append("")
    lines.append("  Results — one row per case (operation x units x writers):")
    hdr = (
        f"  {'operation':<18} {'units':>8} {'wrtrs':>6} {'best ms':>10} "
        f"{'throughput':>18} {'bytes/s':>11} {'RSS':>8} {'CPU':>6}"
    )
    lines.append(hdr)
    lines.append("  " + "-" * (len(hdr) - 2))
    for r in _ordered(report.results):
        rss = f"{r.peak_rss_mb:,.0f}MB" if r.peak_rss_mb is not None else "—"
        cpu = f"{r.cpu_pct_max:.0f}%" if r.cpu_pct_max is not None else "—"
        thr = f"{r.throughput:,.0f} {r.unit}/s"
        lines.append(
            f"  {r.op:<18} {r.scale:>8,} {r.writers:>6} {r.min_s * 1000:>10.3f} "
            f"{thr:>18} {_fmt_bytes_s(r.bytes_per_s):>11} {rss:>8} {cpu:>6}"
        )

    lines.append("")
    lines.append("  Cost model (1 writer): measured per-call floor + marginal per unit")
    lines.append(
        f"  {'operation':<18} {'floor':>16} {'marginal (rec/s)':>18} "
        f"{'(time)':>13} {'(bytes/s)':>11}"
    )
    lines.append("  " + "-" * 78)
    for c in _cost_models(report.results):
        u = c.unit[:-1] if c.unit.endswith("s") else c.unit
        floor = f"{c.floor_ms:.2f}ms@{c.floor_units:,}"
        rate = f"{c.marginal_per_s:,.0f}/s" if c.marginal_per_s else "—"
        per = f"{c.marginal_us:.2f}µs/{u}" if c.marginal_us else "—"
        byr = _fmt_bytes_s(c.marginal_bytes_per_s)
        lines.append(f"  {c.op:<18} {floor:>16} {rate:>18} {per:>13} {byr:>11}")

    scaling = _scaling(report.results)
    if scaling:
        lines.append("")
        lines.append("  Parallel scaling (writes), throughput by writers:")
        for op, series in scaling:
            parts = "  ".join(f"{w}w={series[w]:,.0f}" for w in sorted(series))
            base = series.get(1)
            best = max(series.values()) if series else 0.0
            sp = f"  ({best / base:.2f}× at {max(series)}w)" if base else ""
            lines.append(f"    {op:<18} {parts}{sp}")

    lines.append("")
    if report.resources is not None:
        res = report.resources
        lines.append(
            f"  Footprint (whole run): peak RSS {res.get('peak_rss_mb')} MB  |  "
            f"CPU {res.get('cpu_pct_mean')}% mean / {res.get('cpu_pct_max')}% max"
        )
    else:
        lines.append("  Footprint: unavailable (install litmus-test[benchmark] for psutil)")
    lines.append("")
    lines.append("  Each row is a real measurement. Throughput = records moved / best time.")
    return "\n".join(lines)
