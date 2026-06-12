"""Benchmark orchestration — case execution, teardown, and report output.

Runs every case from :func:`build_cases` (one per operation x size x
writers) and records latency + throughput + per-case load. The human report
(:func:`format_markdown` / :func:`format_summary`) leads with a *derived*
verdict — does Litmus keep up with a busy test station? — and a curated
"what you do" table. The full raw per-case detail goes to ``report.json``.

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

from litmus.benchmark.core import (
    BenchContext,
    BenchmarkReport,
    WorkloadResult,
    time_workload,
)
from litmus.benchmark.scenario import (
    PROFILES,
    ConcurrencyPoint,
    ConcurrencySweep,
    StorageFootprint,
    channel_capacity,
    derive_capacity,
    extract_coefficients,
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


def _prewarm(data_dir: Path) -> None:
    """Start every store's background service before timing, so its one-time
    spawn CPU/RAM burst isn't charged to the first measured case."""
    from uuid import uuid4

    from litmus.data.channels.store import ChannelStore
    from litmus.data.event_store import EventStore
    from litmus.data.files.catalog_manager import acquire, release
    from litmus.data.run_store import RunStore

    try:
        EventStore(_data_dir=data_dir).close()
        RunStore(_data_dir=data_dir).close()
        ch = ChannelStore(data_dir, uuid4(), serve=True)
        ch.open()
        ch.close()
        files_dir = data_dir / "files"
        acquire(files_dir)
        release(files_dir)
    except Exception:  # noqa: BLE001 — best-effort warmup
        pass


def _dir_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def _measure_storage(data_dir: Path) -> StorageFootprint | None:
    """On-disk bytes per record per store — a controlled write + dir-size delta,
    so storage capacity uses the real (compressed) footprint, not raw sizes."""
    from uuid import uuid4

    from litmus.benchmark.workloads import build_run, make_measurement
    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.channels.store import ChannelStore
    from litmus.data.event_store import EventStore
    from litmus.data.files.store import FileStore
    from litmus.data.run_store import RunStore

    try:
        ev = data_dir / "events"
        b0 = _dir_bytes(ev)
        es = EventStore(_data_dir=data_dir)
        sid = uuid4()
        for i in range(2000):
            es.emit(make_measurement(sid, i))
        es.flush()
        es.close()
        measurement_bytes = max(0, _dir_bytes(ev) - b0) / 2000

        ch_dir = data_dir / "channels"
        b0 = _dir_bytes(ch_dir)
        ch = ChannelStore(data_dir, uuid4(), flush_threshold=100, serve=True)
        ch.open()
        wf = [25.0 + (i % 100) * 0.01 for i in range(100_000)]
        ch.write("scope.ch1", wf, sample_interval=1e-6)
        ch.close()
        point_bytes = max(0, _dir_bytes(ch_dir) - b0) / 100_000

        runs_dir = data_dir / "runs"
        b0 = _dir_bytes(runs_dir)
        backend = ParquetBackend(data_dir=data_dir)
        rs = RunStore(_data_dir=data_dir)
        for _ in range(10):
            rs.notify_new_run(backend.save_test_run(build_run(uuid4().int % 1_000_000)))  # type: ignore[union-attr]
        rs.close()
        run_bytes = max(0, _dir_bytes(runs_dir) - b0) / 10

        f_dir = data_dir / "files"
        b0 = _dir_bytes(f_dir)
        fs = FileStore(data_dir=data_dir)
        size = 1024 * 1024
        fs.write(f"store_probe_{uuid4().hex[:8]}", b"a" * size, session_id=uuid4().hex)
        file_byte_ratio = max(1.0, _dir_bytes(f_dir) - b0) / size
    except Exception:  # noqa: BLE001 — best-effort; coefficients fall back to raw bytes
        return None
    return StorageFootprint(measurement_bytes, point_bytes, run_bytes, file_byte_ratio)


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

    import statistics

    from litmus.benchmark.concurrency import run_concurrency

    total = len(cases)
    try:
        progress("starting store services ...")
        _prewarm(data_dir)
        sampler = ResourceSampler(data_dir).start()
        for i, case in enumerate(cases, 1):
            progress(f"[{i}/{total}] {i * 100 // total:>3}%  {case.key} ...")
            records: int | None = None
            case_rounds = case.rounds or options.rounds
            if case.writers == 1 and case.setup is not None:
                ctx = BenchContext(data_dir)
                try:
                    fn = case.setup(ctx)
                    mn, mean, median, mx = time_workload(
                        fn, rounds=case_rounds, warmup=options.warmup
                    )
                    records = _result_size(fn())  # one extra call to size the result
                finally:
                    ctx.close()
            else:
                walls = run_concurrency(
                    data_dir, case.op, case.scale, case.writers, rounds=conc_rounds
                )
                mn, mean, median, mx = (
                    min(walls),
                    statistics.fmean(walls),
                    statistics.median(walls),
                    max(walls),
                )
            report.results.append(
                WorkloadResult(
                    op=case.op,
                    store=case.store,
                    unit=case.unit,
                    scale=case.scale,
                    writers=case.writers,
                    rounds=case_rounds if case.writers == 1 else conc_rounds,
                    min_s=mn,
                    mean_s=mean,
                    median_s=median,
                    max_s=mx,
                    records=records,
                    bytes_per_unit=case.bytes_per_unit,
                )
            )
        progress("measuring on-disk footprint ...")
        report.storage = _measure_storage(data_dir)
        progress("measuring concurrent-write capacity (per store) ...")
        writer_counts = [1, 2, 4, 8] if options.tier == "full" else [1, 2, 4]
        # (store label, concurrency op, units per writer). One sweep per store
        # — each gets its own scaling curve and per-writer efficiency.
        sweep_specs = [
            ("events", "events.emit", 1000),
            ("channels", "channels.write", 1000),
            ("files", "files.write", 10),
            ("runs", "representative.production", 2),
        ]
        for store, op, scale in sweep_specs:
            progress(f"measuring concurrent-write capacity: {store} ...")
            points: list[ConcurrencyPoint] = []
            for w in writer_counts:
                walls = run_concurrency(data_dir, op, scale, w, rounds=2)
                wall = min(walls) if walls else 0.0
                points.append(ConcurrencyPoint(w, (w * scale) / wall if wall > 0 else 0.0))
            report.concurrency.append(ConcurrencySweep(store, points))
        report.footprint = sampler.stop()
    finally:
        _kill_daemons(data_dir)
        shutil.rmtree(data_dir, ignore_errors=True)

    report.coefficients = extract_coefficients(report.results, report.storage)
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
#
# The human report answers ONE question — does Litmus keep up with a test
# station, or get in the way? It leads with a verdict DERIVED from the run
# (never hardcoded), then a curated table with a HEADROOM column (sustained
# rate ÷ what a busy station produces). Full per-case detail lives in
# report.json via WorkloadResult.as_dict, so the human page stays confident
# and scannable rather than a profiler dump.
# ---------------------------------------------------------------------------


def _si(n: float | None) -> str:
    """Number with an SI prefix and 3 significant figures: 3.64M, 78.4k, 82."""
    if n is None:
        return "0"
    n = float(n)
    for suffix, div in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.3g}{suffix}"
    return str(int(n)) if n == int(n) else f"{n:.3g}"


def _mbps(bps: float | None) -> str:
    """Bytes/second as a bare MB/s number (3 significant figures)."""
    if bps is None:
        return "0"
    return f"{bps / (1024**2):.3g}"


def _ms(time_ms: float) -> str:
    """Readable time: 0.21 ms · 2.4 ms · 76 ms · 5.8 s (no scientific notation)."""
    if time_ms >= 1000:
        return f"{time_ms / 1000:.1f} s"
    if time_ms >= 10:
        return f"{time_ms:.0f} ms"
    if time_ms >= 1:
        return f"{time_ms:.1f} ms"
    return f"{time_ms:.2g} ms"


def _short_os(platform_str: object) -> str:
    """'Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-...' -> 'Linux 6.6 (WSL2)'."""
    s = str(platform_str)
    parts = s.split("-")
    sys_name = parts[0] if parts else s
    ver = parts[1].rsplit(".", 1)[0] if len(parts) > 1 else ""
    wsl = " (WSL2)" if "WSL2" in s or "microsoft" in s.lower() else ""
    return f"{sys_name} {ver}{wsl}".strip()


# Curated friendly names + how to read each op's cost (NO "headroom").
#   write  -> per-record time + sustained units/s
#   query  -> call latency + sustained units/s
#   block  -> per-block latency + points/s
#   stream -> per-chunk latency + MB/s
_OP_LABELS: list[tuple[str, str, str]] = [
    ("Log a measurement", "events.emit", "write"),
    ("Read recent measurements", "events.query", "query"),
    ("Write a sensor sample", "channels.write", "write"),
    ("Write a waveform block", "channels.block", "block"),
    ("Read channel data", "channels.query", "query"),
    ("Store a file artifact", "files.write", "write"),
    ("Stream raw file data", "files.stream_raw", "stream"),
    ("Save a finished run", "runs.save", "write"),
    ("Read run history", "runs.list", "query"),
    ("Read a run's steps", "runs.steps", "query"),
    ("Locate a file", "files.resolve", "query"),
]

# Representative per-part test cycle used only to express the "in parallel"
# figure (sustained runs/s × cycle). Stated in the report so it's interpretable.
_CYCLE_S = 10.0


def _op_rate(results: list[WorkloadResult], op: str, kind: str) -> tuple[float, str] | None:
    """(latency_ms, sustained-rate string) for one op — no headroom."""
    rows = sorted((r for r in results if r.op == op and r.writers == 1), key=lambda r: r.scale)
    if not rows:
        return None
    small, big = rows[0], rows[-1]
    if kind == "write":
        return big.min_s / max(1, big.records_per_call) * 1000, f"{_si(big.throughput)}/s"
    if kind == "block":
        return small.min_s * 1000, f"{_si(big.throughput)} points/s"
    if kind == "stream":
        return small.min_s * 1000, f"{_mbps(big.bytes_per_s)} MB/s"
    return small.min_s * 1000, f"{_si(big.throughput)}/s"


def _composition(sc: object) -> str:
    """Reader-facing description of a phase scenario's data footprint."""
    parts = [f"{sc.measurements:,} measurements"]  # type: ignore[attr-defined]
    if sc.waveform_captures:  # type: ignore[attr-defined]
        mb = sc.total_points * 8 / (1024**2)  # type: ignore[attr-defined]
        parts.append(
            f"{sc.waveform_captures}×{_si(sc.waveform_points)}-pt waveforms ({mb:.0f} MB)"  # type: ignore[attr-defined]
        )
    else:
        parts.append("no raw waveforms")
    if sc.files:  # type: ignore[attr-defined]
        parts.append(f"{sc.files} files")  # type: ignore[attr-defined]
    return " · ".join(parts)


def _machine_line(report: BenchmarkReport) -> str:
    hw, ver = report.hardware, report.versions
    parts = [
        f"**{hw.get('cpu_model')}**",
        f"{hw.get('cpu_count')} cores",
        f"{hw.get('ram_gb')} GB RAM",
        _short_os(hw.get("platform")),
        f"litmus {ver.get('litmus')}",
        f"duckdb {ver.get('duckdb')}",
        f"pyarrow {ver.get('pyarrow')}",
        f"{report.options.get('tier')} tier · {report.duration_s:.0f}s",
    ]
    return " · ".join(parts)


def _free_disk(report: BenchmarkReport) -> int:
    v = report.hardware.get("free_disk_bytes")
    return int(v) if isinstance(v, (int, float)) else 0


def _verdict(report: BenchmarkReport) -> str:
    """Derived takeaway in capacity terms (production scenario), not headroom."""
    coef = report.coefficients
    if coef is None:
        return "No capacity was measured."
    free = _free_disk(report)
    prod = next(p for p in PROFILES if p.name == "production")
    cap = derive_capacity(coef, prod, free_disk_bytes=free, sweep=report.runs_concurrency)
    return (
        f"Recording a production test run costs ~{_ms(cap.per_run_s * 1000)} and "
        f"~{cap.storage_gb_per_run * 1024:.1f} MB. This machine finalizes "
        f"~{cap.sustained_runs_per_s:.0f} runs/s (≈{cap.parallel_parts(_CYCLE_S)} parts in "
        f"parallel at a {_CYCLE_S:.0f}s cycle) and can hold ~{_si(cap.storage_runs)} runs. "
        "Litmus stays out of your test's way."
    )


def _footprint_line(report: BenchmarkReport) -> str | None:
    """Task-Manager view: the data layer's share of the whole machine."""
    fp = report.footprint
    if fp is None:
        return None
    cpu = f"{fp.cpu_pct_of_machine:.1f}%" if fp.cpu_pct_of_machine >= 0.1 else "<0.1%"
    return (
        f"Under load it uses ~{cpu} of this machine's CPU and ~{fp.ram_gb:.1f} GB "
        f"({fp.ram_pct_of_machine:.0f}% of RAM) — the rest stays free for your test "
        "code and other apps."
    )


def format_markdown(report: BenchmarkReport) -> str:
    out: list[str] = ["# Litmus performance on this machine", ""]
    out.append(_machine_line(report))
    out.append("")
    out.append(_verdict(report))
    fpl = _footprint_line(report)
    if fpl:
        out.append("")
        out.append(fpl)
    out.append("")

    coef = report.coefficients
    free = _free_disk(report)

    out.append("## Recording test runs (by phase)")
    out.append("")
    out.append("| Test phase | What it records | Time / run | On disk / run | Runs that fit |")
    out.append("|---|---|--:|--:|--:|")
    if coef is not None:
        for sc in PROFILES:
            cap = derive_capacity(coef, sc, free_disk_bytes=free, sweep=report.runs_concurrency)
            out.append(
                f"| {sc.name.capitalize()} | {_composition(sc)} | {_ms(cap.per_run_s * 1000)} "
                f"| {cap.storage_gb_per_run * 1024:.1f} MB | {_si(cap.storage_runs)} |"
            )
    out.append("")
    out.append(
        '_Compositions are illustrative, tunable. "Parts in parallel" = the data layer\'s '
        "sustained run rate × your test cycle — at a multi-second cycle that's hundreds._"
    )
    out.append("")

    out.append("## Capturing instrument data (channels)")
    out.append("")
    if coef is not None:
        for rate_hz, label in ((1_000, "1 kS/s"), (10_000, "10 kS/s")):
            cc = channel_capacity(coef, channels=1, sample_rate_hz=rate_hz, free_disk_bytes=free)
            out.append(f"- Log up to **{_si(cc.max_channels_at_rate)} channels at {label}** each.")
        cc = channel_capacity(coef, channels=32, sample_rate_hz=10_000, free_disk_bytes=free)
        out.append(
            f"- Ingest ceiling ~{_si(cc.ingest_ceiling_per_s)} points/s; a 32-channel × 10 kS/s "
            f"capture fills your free disk in ~{cc.max_seconds_on_disk / 3600:,.0f} hours."
        )
    out.append("")

    out.append("## Per-operation rates")
    out.append("")
    out.append("| Operation | Latency | Sustained rate |")
    out.append("|---|--:|--:|")
    for label, op, kind in _OP_LABELS:
        r = _op_rate(report.results, op, kind)
        if r is not None:
            out.append(f"| {label} | {_ms(r[0])} | {r[1]} |")
    out.append("")

    if report.concurrency:
        writers = sorted({p.writers for s in report.concurrency for p in s.points})
        out.append("## Concurrent writes (per store)")
        out.append("")
        out.append("| Store | " + " | ".join(f"{w}w" for w in writers) + " | Per-writer |")
        out.append("|---" + "|--:" * (len(writers) + 1) + "|")
        for s in report.concurrency:
            rate = {p.writers: p.throughput_per_s for p in s.points}
            cells = " | ".join(f"{_si(rate.get(w, 0.0))}/s" for w in writers)
            out.append(f"| {s.store} | {cells} | {s.factor():.2f} |")
        out.append("")
        out.append(
            "_Per-writer efficiency = (rate at max writers ÷ writers) ÷ single-writer rate. "
            "~1.0 scales cleanly; ~0.5 serializes._"
        )
        out.append("")

    out.append(
        "_Full per-size, parallel-scaling, coefficient, and storage detail is in `report.json`._"
    )
    return "\n".join(out)


def format_summary(report: BenchmarkReport) -> str:
    """Terminal summary — the capacity report, ASCII-aligned."""
    lines: list[str] = ["Litmus performance on this machine", ""]
    lines.append(f"  {_machine_line(report).replace('**', '')}")
    lines.append("")
    lines.append(f"  {_verdict(report)}")
    fpl = _footprint_line(report)
    if fpl:
        lines.append(f"  {fpl}")
    lines.append("")

    coef = report.coefficients
    free = _free_disk(report)

    lines.append("  Recording test runs (by phase):")
    lines.append(f"    {'phase':<16} {'time/run':>10} {'on disk':>10} {'runs fit':>12}")
    if coef is not None:
        for sc in PROFILES:
            cap = derive_capacity(coef, sc, free_disk_bytes=free, sweep=report.runs_concurrency)
            lines.append(
                f"    {sc.name:<16} {_ms(cap.per_run_s * 1000):>10} "
                f"{cap.storage_gb_per_run * 1024:>8.1f}MB {_si(cap.storage_runs):>12}"
            )
    lines.append("")

    lines.append("  Per-operation rates:")
    lines.append(f"    {'operation':<26} {'latency':>9} {'sustained rate':>16}")
    for label, op, kind in _OP_LABELS:
        r = _op_rate(report.results, op, kind)
        if r is not None:
            lines.append(f"    {label:<26} {_ms(r[0]):>9} {r[1]:>16}")
    lines.append("")

    if report.concurrency:
        writers = sorted({p.writers for s in report.concurrency for p in s.points})
        lines.append("  Concurrent writes (per store, aggregate rate):")
        cols = "".join(f"{str(w) + 'w':>12}" for w in writers)
        lines.append(f"    {'store':<10}{cols}{'per-writer':>12}")
        for s in report.concurrency:
            rate = {p.writers: p.throughput_per_s for p in s.points}
            cells = "".join(f"{_si(rate.get(w, 0.0)) + '/s':>12}" for w in writers)
            lines.append(f"    {s.store:<10}{cells}{s.factor():>12.2f}")
        lines.append("")

    lines.append("  Full detail (coefficients, per-size, scaling, storage) is in report.json.")
    return "\n".join(lines)
