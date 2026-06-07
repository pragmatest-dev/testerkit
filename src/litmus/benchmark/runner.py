"""Benchmark orchestration — temp-dir lifecycle, timing, teardown, output.

The run is hermetic: it works against a fresh OS temp directory (never
the user's canonical store), spawns the store daemons there, runs the
workloads, then kills those daemons and deletes the temp dir — even on
error. Daemons are killed BEFORE the wipe so the delete never races a
daemon write.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from litmus.benchmark.core import (
    BenchContext,
    BenchmarkReport,
    WorkloadResult,
    time_workload,
)
from litmus.benchmark.system import (
    ResourceSampler,
    collect_hardware,
    collect_versions,
    have_psutil,
)
from litmus.benchmark.workloads import all_workloads, fast_workloads


@dataclass
class BenchmarkOptions:
    """Exactly how a run was parameterized — echoed into the result file
    so a received benchmark is interpretable and comparable."""

    tier: str = "fast"  # "fast" | "full"
    concurrency: int = 2
    rounds: int = 5
    warmup: int = 1
    full_concurrency_sweep: list[int] = field(default_factory=lambda: [1, 2, 4])

    def as_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["psutil"] = have_psutil()
        return d


def _kill_daemons(data_dir: Path) -> None:
    """Kill all four store daemons rooted at ``data_dir`` (by PID, then clean state)."""
    from litmus.data.channels.flight_manager import FlightDaemonManager
    from litmus.data.duckdb_manager import DuckDBDaemonManager
    from litmus.data.files.catalog_manager import FilesCatalogManager
    from litmus.data.runs_duckdb_manager import RunsDuckDBManager

    managers = [
        DuckDBDaemonManager(data_dir / "events"),
        RunsDuckDBManager(data_dir / "runs"),
        FlightDaemonManager(data_dir / "channels"),
        FilesCatalogManager(data_dir / "files"),
    ]
    for mgr in managers:
        try:
            mgr.force_restart()
        except Exception:  # noqa: BLE001 — teardown is best-effort
            pass


def run_benchmark(
    options: BenchmarkOptions,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> BenchmarkReport:
    """Run the workloads against a throwaway temp dir and return the report."""

    def progress(msg: str) -> None:
        if on_progress is not None:
            on_progress(msg)

    workloads = all_workloads() if options.tier == "full" else fast_workloads()
    data_dir = Path(tempfile.mkdtemp(prefix="litmus-bench-"))
    started = time.perf_counter()

    report = BenchmarkReport(
        hardware=collect_hardware(),
        versions=collect_versions(),
        options=options.as_dict(),
    )

    try:
        with ResourceSampler() as sampler:
            for wl in workloads:
                progress(f"{wl.key} …")
                ctx = BenchContext(data_dir)
                try:
                    fn = wl.setup(ctx)
                    mn, mean, median, mx = time_workload(
                        fn, rounds=options.rounds, warmup=options.warmup
                    )
                    report.results.append(
                        WorkloadResult(
                            key=wl.key,
                            store=wl.store,
                            label=wl.label,
                            unit=wl.unit,
                            n=wl.n,
                            rounds=options.rounds,
                            min_s=mn,
                            mean_s=mean,
                            median_s=median,
                            max_s=mx,
                        )
                    )
                finally:
                    ctx.close()

            # Concurrency probes — fast: one probe per store at N; full: sweep.
            from litmus.benchmark.concurrency import run_concurrency_probe

            # Always include a 1-writer baseline so the report can show
            # parallel speedup (the number that answers "does it scale").
            sweep = (
                options.full_concurrency_sweep
                if options.tier == "full"
                else sorted({1, options.concurrency})
            )
            for store in ("events", "channels", "files", "runs"):
                for n_writers in sweep:
                    progress(f"concurrency:{store} ×{n_writers} …")
                    report.concurrency.append(run_concurrency_probe(data_dir, store, n_writers))

        report.resources = sampler.report()
    finally:
        _kill_daemons(data_dir)
        shutil.rmtree(data_dir, ignore_errors=True)

    report.duration_s = time.perf_counter() - started
    return report


def write_report(report: BenchmarkReport, output_dir: Path | str = ".benchmarks") -> Path:
    """Write a dated run folder ``<output_dir>/<date>/`` and return it.

    The folder holds both ``report.md`` (the human deliverable — renders
    as tables when pasted into a GitHub issue) and ``report.json`` (the
    machine-readable record for trend comparison). A second run on the
    same day gets a time suffix so nothing is clobbered.
    """
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


def format_summary(report: BenchmarkReport) -> str:
    """Human-readable terminal summary, with the options echoed."""
    lines: list[str] = []
    hw = report.hardware
    opts = report.options
    rounds = opts.get("rounds")

    lines.append(
        f"Litmus benchmark — {opts.get('tier')} tier, "
        f"concurrency {opts.get('concurrency')}, "
        f"{rounds} rounds"
    )
    lines.append(
        f"  {hw.get('cpu_model')} ×{hw.get('cpu_count')}  |  "
        f"{hw.get('ram_gb')} GB RAM  |  {hw.get('platform')}"
    )
    lines.append(
        f"  {len(report.results)} workloads · {rounds} timed rounds each · "
        f"finished in {report.duration_s:.1f} s"
    )

    # Per-workload table. "n/call" = units done per timed round; "best"
    # and "mean" are per-round wall time (best-of-N is the stable figure,
    # mean shows the spread); "per unit" = best ÷ n (the cost of a single
    # event / sample / query); throughput is the headline.
    lines.append("")
    lines.append(
        f"  {'workload':<22} {'n/call':>7} {'best (ms)':>10} {'mean (ms)':>10} "
        f"{'per unit':>10} {'throughput':>16}"
    )
    lines.append(f"  {'-' * 22} {'-' * 7} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 16}")
    for r in report.results:
        thr = f"{r.units_per_s:,.0f} {r.unit}/s"
        per_unit = _fmt_per_unit((r.min_s * 1000) / r.n) if r.n else "—"
        lines.append(
            f"  {r.key:<22} {r.n:>7} {r.min_s * 1000:>10.3f} {r.mean_s * 1000:>10.3f} "
            f"{per_unit:>10} {thr:>16}"
        )

    if report.concurrency:
        # Pivot: one row per store, one column per writer count, so scaling
        # reads left-to-right. Final column = max speedup over the 1-writer
        # baseline — the number that answers "does this store parallelize?".
        writer_counts = sorted({c.n_writers for c in report.concurrency})
        ops: dict[tuple[str, int], float] = {
            (c.store, c.n_writers): c.ops_per_s for c in report.concurrency
        }
        stores: list[str] = []
        for c in report.concurrency:
            if c.store not in stores:
                stores.append(c.store)

        lines.append("")
        lines.append("  Parallel writers — aggregate ops/s by writer count:")
        header = f"  {'store':<10}" + "".join(f"{f'{n}w':>12}" for n in writer_counts)
        lines.append(header + f"{'speedup':>10}")
        lines.append(
            f"  {'-' * 10}" + "".join(f"{'-' * 12}" for _ in writer_counts) + f"{'-' * 10}"
        )
        for store in stores:
            row = f"  {store:<10}"
            for n in writer_counts:
                val = ops.get((store, n))
                row += f"{val:>12,.0f}" if val is not None else f"{'—':>12}"
            base = ops.get((store, 1))
            best = max((ops[(store, n)] for n in writer_counts if (store, n) in ops), default=0.0)
            row += f"{best / base:>9.2f}×" if base else f"{'—':>10}"
            lines.append(row)
        per = ", ".join(
            f"{c.n_per_writer} {c.store}" for c in report.concurrency if c.n_writers == 1
        )
        lines.append(f"    each writer writes: {per}")

    lines.append("")
    if report.resources is not None:
        res = report.resources
        lines.append(
            f"  Footprint: peak RSS {res.get('peak_rss_mb')} MB  |  "
            f"CPU {res.get('cpu_pct_min')}–{res.get('cpu_pct_max')}% "
            f"(mean {res.get('cpu_pct_mean')}%)"
        )
    else:
        lines.append("  Footprint: unavailable (install litmus-test[benchmark] for psutil)")

    return "\n".join(lines)


def _fmt_per_unit(ms: float) -> str:
    """Per-unit time, in µs below 1 ms, else ms — keeps the column readable."""
    if ms < 1.0:
        return f"{ms * 1000:.1f} µs"
    return f"{ms:.3f} ms"


def format_markdown(report: BenchmarkReport) -> str:
    """Render the report as GitHub-flavored Markdown (real tables).

    This is what a user pastes into an issue — the tables render natively.
    """
    hw = report.hardware
    ver = report.versions
    opts = report.options
    out: list[str] = []

    out.append("# Litmus benchmark")
    out.append("")
    out.append(
        f"- **Machine:** {hw.get('cpu_model')} ×{hw.get('cpu_count')}, "
        f"{hw.get('ram_gb')} GB RAM, {hw.get('platform')}"
    )
    out.append(
        f"- **Versions:** litmus {ver.get('litmus')}, Python {ver.get('python')}, "
        f"pyarrow {ver.get('pyarrow')}, duckdb {ver.get('duckdb')}"
    )
    out.append(
        f"- **Run:** {opts.get('tier')} tier · concurrency {opts.get('concurrency')} · "
        f"{opts.get('rounds')} rounds · {len(report.results)} workloads · "
        f"{report.duration_s:.1f} s"
    )
    out.append("")

    out.append("## Throughput")
    out.append("")
    out.append("| Workload | n/call | Best (ms) | Mean (ms) | Per unit | Throughput |")
    out.append("|---|--:|--:|--:|--:|--:|")
    for r in report.results:
        per_unit = _fmt_per_unit((r.min_s * 1000) / r.n) if r.n else "—"
        out.append(
            f"| `{r.key}` | {r.n} | {r.min_s * 1000:.3f} | {r.mean_s * 1000:.3f} | "
            f"{per_unit} | {r.units_per_s:,.0f} {r.unit}/s |"
        )
    out.append("")

    if report.concurrency:
        writer_counts = sorted({c.n_writers for c in report.concurrency})
        ops = {(c.store, c.n_writers): c.ops_per_s for c in report.concurrency}
        stores: list[str] = []
        for c in report.concurrency:
            if c.store not in stores:
                stores.append(c.store)
        out.append("## Parallel writers (aggregate ops/s)")
        out.append("")
        out.append("| Store | " + " | ".join(f"{n}w" for n in writer_counts) + " | Speedup |")
        out.append("|---|" + "--:|" * (len(writer_counts) + 1))
        for store in stores:
            cells = []
            for n in writer_counts:
                val = ops.get((store, n))
                cells.append(f"{val:,.0f}" if val is not None else "—")
            base = ops.get((store, 1))
            best = max((ops[(store, n)] for n in writer_counts if (store, n) in ops), default=0.0)
            speedup = f"{best / base:.2f}×" if base else "—"
            out.append(f"| {store} | " + " | ".join(cells) + f" | {speedup} |")
        per = ", ".join(
            f"{c.n_per_writer} {c.store}" for c in report.concurrency if c.n_writers == 1
        )
        out.append("")
        out.append(f"_Each writer writes: {per}._")
        out.append("")

    if report.resources is not None:
        res = report.resources
        out.append("## Footprint")
        out.append("")
        out.append(f"- **Peak RSS:** {res.get('peak_rss_mb')} MB")
        out.append(
            f"- **CPU:** {res.get('cpu_pct_min')}–{res.get('cpu_pct_max')}% "
            f"(mean {res.get('cpu_pct_mean')}%)"
        )
        out.append("")

    return "\n".join(out)
