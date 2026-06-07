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

    total = len(cases)
    try:
        with ResourceSampler() as sampler:
            for i, case in enumerate(cases, 1):
                label = case.key
                progress(f"[{i}/{total}] {i * 100 // total:>3}%  {label} ...")
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
                load = sampler.case(label, case.store)
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
                        daemon_rss_mb=load["rss_mb"],  # type: ignore[arg-type]
                        daemon_cores_mean=load["cores_mean"],  # type: ignore[arg-type]
                        daemon_cores_peak=load["cores_peak"],  # type: ignore[arg-type]
                    )
                )
            report.resources = sampler.per_store()
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


def _short_os(platform_str: object) -> str:
    """'Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-...' -> 'Linux 6.6 (WSL2)'."""
    s = str(platform_str)
    parts = s.split("-")
    sys_name = parts[0] if parts else s
    ver = parts[1].rsplit(".", 1)[0] if len(parts) > 1 else ""
    wsl = " (WSL2)" if "WSL2" in s or "microsoft" in s.lower() else ""
    return f"{sys_name} {ver}{wsl}".strip()


def _meta_groups(report: BenchmarkReport) -> list[tuple[str, list[tuple[str, str]]]]:
    """Header facts grouped as ``[(group, [(field, value), ...]), ...]``."""
    hw, ver, opts = report.hardware, report.versions, report.options
    return [
        (
            "Run",
            [
                ("Tier", str(opts.get("tier"))),
                ("Cases", str(len(report.results))),
                ("Duration", f"{report.duration_s:.0f} s"),
            ],
        ),
        (
            "Machine",
            [
                ("CPU", f"{hw.get('cpu_count')} × {hw.get('machine')}"),
                ("RAM", f"{hw.get('ram_gb')} GB"),
                ("OS", _short_os(hw.get("platform"))),
            ],
        ),
        (
            "Versions",
            [
                ("litmus", str(ver.get("litmus"))),
                ("Python", str(ver.get("python"))),
                ("pyarrow", str(ver.get("pyarrow"))),
                ("duckdb", str(ver.get("duckdb"))),
            ],
        ),
    ]


def format_markdown(report: BenchmarkReport) -> str:
    out: list[str] = ["# Litmus benchmark", ""]
    out.append("| Group | Field | Value |")
    out.append("|---|---|---|")
    for group, fields in _meta_groups(report):
        for idx, (field, value) in enumerate(fields):
            label = f"**{group}**" if idx == 0 else ""
            out.append(f"| {label} | {field} | {value} |")
    out.append("")

    # Raw results — one row per (operation x units x writers).
    out.append("## Results (one row per case)")
    out.append("")
    out.append(
        "| Operation | Units | Writers | Best (ms) | Throughput | Bytes/s "
        "| Daemon RSS | Daemon CPU |"
    )
    out.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for r in _ordered(report.results):
        rss = f"{r.daemon_rss_mb:,.0f} MB" if r.daemon_rss_mb is not None else "—"
        cores = f"{r.daemon_cores_mean:.2f} cores" if r.daemon_cores_mean is not None else "—"
        bps = _fmt_bytes_s(r.bytes_per_s)
        out.append(
            f"| `{r.op}` | {r.scale:,} | {r.writers} | {r.min_s * 1000:.3f} | "
            f"{r.throughput:,.0f} {r.unit}/s | {bps} | {rss} | {cores} |"
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
        flat = c.marginal_us < 0.05  # below measurement resolution → no per-unit cost
        rate = (
            "flat" if flat else (f"{c.marginal_per_s:,.0f} {c.unit}/s" if c.marginal_per_s else "—")
        )
        per = "≈0 (flat)" if flat else f"{c.marginal_us:.2f} µs/{u}"
        byr = "—" if flat else _fmt_bytes_s(c.marginal_bytes_per_s)
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

    if report.resources:
        out.append("### Per-store daemon footprint (whole run)")
        out.append("")
        out.append("CPU in cores (1.0 = one core fully used); the harness/pytest is excluded.")
        out.append("")
        out.append("| Store daemon | Peak RSS | Peak CPU | Mean CPU |")
        out.append("|---|--:|--:|--:|")
        for store, d in report.resources.items():
            rss = f"{d.get('rss_mb'):,.0f} MB" if d.get("rss_mb") is not None else "—"
            peak = f"{d.get('cores_peak')} cores" if d.get("cores_peak") is not None else "—"
            mean = f"{d.get('cores_mean')} cores" if d.get("cores_mean") is not None else "—"
            out.append(f"| {store} | {rss} | {peak} | {mean} |")
        out.append("")
    return "\n".join(out)


def format_summary(report: BenchmarkReport) -> str:
    """Terminal summary — the same raw rows + cost model, ASCII-aligned."""
    lines: list[str] = ["Litmus benchmark", ""]
    for group, fields in _meta_groups(report):
        for idx, (field, value) in enumerate(fields):
            grp = group if idx == 0 else ""
            lines.append(f"  {grp:<10} {field:<10} {value}")
    lines.append("")
    lines.append("  Results — one row per case (operation x units x writers):")
    hdr = (
        f"  {'operation':<18} {'units':>8} {'wrtrs':>6} {'best ms':>10} "
        f"{'throughput':>18} {'bytes/s':>11} {'dmn RSS':>9} {'dmn CPU':>9}"
    )
    lines.append(hdr)
    lines.append("  " + "-" * (len(hdr) - 2))
    for r in _ordered(report.results):
        rss = f"{r.daemon_rss_mb:,.0f}MB" if r.daemon_rss_mb is not None else "—"
        cores = f"{r.daemon_cores_mean:.2f}c" if r.daemon_cores_mean is not None else "—"
        thr = f"{r.throughput:,.0f} {r.unit}/s"
        lines.append(
            f"  {r.op:<18} {r.scale:>8,} {r.writers:>6} {r.min_s * 1000:>10.3f} "
            f"{thr:>18} {_fmt_bytes_s(r.bytes_per_s):>11} {rss:>9} {cores:>9}"
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
        flat = c.marginal_us < 0.05
        rate = "flat" if flat else (f"{c.marginal_per_s:,.0f}/s" if c.marginal_per_s else "—")
        per = "≈0" if flat else f"{c.marginal_us:.2f}µs/{u}"
        byr = "—" if flat else _fmt_bytes_s(c.marginal_bytes_per_s)
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
    if report.resources:
        lines.append("  Per-store daemon footprint (CPU in cores; harness excluded):")
        lines.append(f"    {'store':<10} {'peak RSS':>10} {'peak CPU':>10} {'mean CPU':>10}")
        for store, d in report.resources.items():
            rss = f"{d.get('rss_mb'):,.0f} MB" if d.get("rss_mb") is not None else "—"
            peak = f"{d.get('cores_peak')}c" if d.get("cores_peak") is not None else "—"
            mean = f"{d.get('cores_mean')}c" if d.get("cores_mean") is not None else "—"
            lines.append(f"    {store:<10} {rss:>10} {peak:>10} {mean:>10}")
    else:
        lines.append("  Footprint: unavailable (install litmus-test[benchmark] for psutil)")
    lines.append("")
    lines.append("  Each row is a real measurement. Throughput = records moved / best time.")
    return "\n".join(lines)
