"""Coefficients → scenario → capacity — the calculator core.

The benchmark measures per-component COST COEFFICIENTS for a machine (cost per
measurement, per waveform point, per file byte, per run save, per-store
overhead, the concurrency factor). A SCENARIO is a composition
(measurements · waveform captures × points · files × bytes). Everything the
report leads with is then arithmetic on the coefficients:

    per-run cost  = overhead + Σ(component × coefficient)
    storage/run   = Σ(component × on-disk bytes)
    concurrency   = how many runs at once the data layer sustains
    storage total = free disk ÷ storage/run (capped by retention)

This is what powers the phase presets, a user's custom scenario, and the
future web calculator — all from one published coefficient block. Validated
by prior art that sizes the same way (Prometheus: series·rate·retention →
RAM+disk; FlexLogger: channels×rate; DAQ file-size = channels×rate×bytes×time).
"""

from __future__ import annotations

from dataclasses import dataclass

from testerkit.benchmark.core import WorkloadResult


@dataclass(frozen=True)
class Scenario:
    """One test run's data composition. A waveform is POINTS (→ bytes), not a
    count; its capture rate is a separate (ingest) concern."""

    name: str
    steps: int
    measurements: int  # scalar measurements logged as events
    waveform_captures: int
    waveform_points: int  # points per capture
    files: int
    file_bytes: int  # bytes per file

    @property
    def total_points(self) -> int:
        return self.waveform_captures * self.waveform_points

    @property
    def total_file_bytes(self) -> int:
        return self.files * self.file_bytes


# The three phase presets (research-grounded; tunable). Extrapolated from
# coefficients — never run end-to-end for the report.
PROFILES: list[Scenario] = [
    Scenario(
        "characterization",
        steps=50,
        measurements=2000,
        waveform_captures=20,
        waveform_points=1_000_000,
        files=3,
        file_bytes=100 * 1024,
    ),
    Scenario(
        "validation",
        steps=30,
        measurements=500,
        waveform_captures=5,
        waveform_points=100_000,
        files=2,
        file_bytes=100 * 1024,
    ),
    Scenario(
        "production",
        steps=15,
        measurements=100,
        waveform_captures=0,
        waveform_points=0,
        files=0,
        file_bytes=0,
    ),
]


@dataclass(frozen=True)
class Coefficients:
    """Per-component costs + sizes measured for this machine."""

    measurement_s: float  # seconds per measurement (event emit)
    point_s: float  # seconds per waveform point (channel ingest)
    run_save_s: float  # seconds per run save
    file_byte_s: float  # seconds per file byte (write/stream)
    run_overhead_s: float  # fixed per-run cost (events flush/commit)
    # On-disk bytes per component — the actual (possibly compressed) footprint
    # when measured; ``on_disk_measured`` flags measured vs. raw fallback.
    measurement_bytes: int
    point_bytes: int
    run_bytes: int
    file_byte_ratio: float = 1.0  # on-disk bytes per raw file byte (~1 for blobs)
    on_disk_measured: bool = False


@dataclass(frozen=True)
class StorageFootprint:
    """Measured on-disk bytes per record per store (the real, possibly
    compressed, footprint — feeds the storage-capacity coefficients)."""

    measurement_bytes: float
    point_bytes: float
    run_bytes: float
    file_byte_ratio: float  # on-disk bytes per raw file byte (~1 for blobs)


@dataclass(frozen=True)
class ConcurrencyPoint:
    writers: int
    throughput_per_s: float  # aggregate unit/s across all writers


@dataclass(frozen=True)
class ConcurrencySweep:
    """One store's write throughput across writer counts → per-writer efficiency."""

    store: str
    points: list[ConcurrencyPoint]

    def factor(self) -> float:
        """Per-writer efficiency at the max writer count:
        (rate at max writers ÷ writers) ÷ rate at 1 writer. ~1 = scales."""
        if not self.points:
            return 1.0
        base = next((p.throughput_per_s for p in self.points if p.writers == 1), 0.0)
        top = max(self.points, key=lambda p: p.writers)
        if not base or not top.writers:
            return 1.0
        return (top.throughput_per_s / top.writers) / base


def _one_writer(results: list[WorkloadResult], op: str) -> list[WorkloadResult]:
    return sorted((r for r in results if r.op == op and r.writers == 1), key=lambda r: r.scale)


def _per_unit_s(results: list[WorkloadResult], op: str, default: float) -> float:
    """Seconds per record at the largest measured scale (the marginal cost)."""
    rows = _one_writer(results, op)
    if not rows:
        return default
    big = rows[-1]
    return big.min_s / max(1, big.records_per_call)


def _byte_s(results: list[WorkloadResult], op: str, default: float) -> float:
    """Seconds per byte from the largest byte-rate measurement of ``op``."""
    rows = _one_writer(results, op)
    rows = [r for r in rows if r.bytes_per_s]
    if not rows:
        return default
    return 1.0 / max(rows, key=lambda r: r.bytes_per_s or 0.0).bytes_per_s  # type: ignore[operator]


def extract_coefficients(
    results: list[WorkloadResult],
    storage: StorageFootprint | None = None,
) -> Coefficients:
    """Derive the machine's per-component coefficients from a benchmark run.

    ``storage`` overrides raw byte sizes with the measured on-disk footprint.
    Concurrency is reported per store (see ``BenchmarkReport.concurrency``),
    not folded into a single headline factor.
    """
    # Per-component time. Defaults are conservative fallbacks if an op is absent.
    measurement_s = _per_unit_s(results, "events.emit", 2e-4)
    point_s = _per_unit_s(results, "channels.block", 3e-7)
    run_save_s = _per_unit_s(results, "runs.save", 4e-2)
    file_byte_s = _byte_s(results, "files.stream_raw", 1e-8)

    # Events per-run overhead = single-emit latency (flush/commit), ≈ the
    # smallest-scale events.emit time minus its one-record marginal.
    emit_rows = _one_writer(results, "events.emit")
    if emit_rows:
        small = emit_rows[0]
        run_overhead_s = max(0.0, small.min_s - small.records_per_call * measurement_s)
    else:
        run_overhead_s = 0.012

    # On-disk bytes — raw until Stage 2 measures the compressed footprint.
    meas_bytes = next(
        (r.bytes_per_unit for r in results if r.op == "events.emit" and r.bytes_per_unit),
        256,
    )
    run_b = next(
        (r.bytes_per_unit for r in results if r.op == "runs.save" and r.bytes_per_unit),
        4096,
    )
    point_bytes = 8
    file_ratio = 1.0
    on_disk = False
    if storage is not None:
        meas_bytes = int(storage.measurement_bytes)
        point_bytes = max(1, int(round(storage.point_bytes)))
        run_b = int(storage.run_bytes)
        file_ratio = storage.file_byte_ratio or 1.0
        on_disk = True
    return Coefficients(
        measurement_s=measurement_s,
        point_s=point_s,
        run_save_s=run_save_s,
        file_byte_s=file_byte_s,
        run_overhead_s=run_overhead_s,
        measurement_bytes=int(meas_bytes),
        point_bytes=point_bytes,
        run_bytes=int(run_b),
        file_byte_ratio=file_ratio,
        on_disk_measured=on_disk,
    )


def predict_run_cost_s(coef: Coefficients, sc: Scenario) -> float:
    """Predicted data-layer time to record one run of ``sc`` (seconds)."""
    return (
        coef.run_overhead_s
        + sc.measurements * coef.measurement_s
        + sc.total_points * coef.point_s
        + sc.total_file_bytes * coef.file_byte_s
        + coef.run_save_s
    )


def run_bytes(coef: Coefficients, sc: Scenario) -> int:
    """On-disk bytes one run of ``sc`` occupies (raw until Stage 2)."""
    return int(
        sc.measurements * coef.measurement_bytes
        + sc.total_points * coef.point_bytes
        + sc.total_file_bytes * coef.file_byte_ratio
        + coef.run_bytes
    )


@dataclass(frozen=True)
class Capacity:
    """Derived machine capacity for a scenario, the report's headline.

    "How many parts in parallel" is a THROUGHPUT question, not an OS-process
    one: the data layer finalizes ``sustained_runs_per_s`` runs/s, so it keeps
    up with that many parts as long as their combined completion rate stays
    under it. At a typical multi-second test cycle that's hundreds — far past a
    physical line — which is the honest, confidence-building framing.
    """

    per_run_s: float
    sustained_runs_per_s: float  # measured data-layer ceiling (max aggregate)
    storage_runs: int  # runs that fit on free disk
    storage_gb_per_run: float

    def parallel_parts(self, cycle_s: float) -> int:
        """Parts you can run in parallel at a per-part cycle of
        ``cycle_s`` seconds: sustained-rate × cycle (combined completion rate
        must stay under the data layer's ceiling)."""
        return int(self.sustained_runs_per_s * cycle_s)


@dataclass(frozen=True)
class ChannelCapacity:
    """Telemetry/DAQ capacity (the FlexLogger framing): channels × rate × time."""

    sample_rate_hz: float
    channels: int
    keeps_up: bool  # aggregate ingest ≤ the data layer's ceiling
    ingest_ceiling_per_s: float  # max total samples/s the channel store ingests
    max_channels_at_rate: int  # at sample_rate_hz, how many channels keep up
    max_seconds_on_disk: float  # how long this {channels × rate} fits on free disk


def channel_capacity(
    coef: Coefficients, *, channels: int, sample_rate_hz: float, free_disk_bytes: int
) -> ChannelCapacity:
    """Can this machine log ``channels`` at ``sample_rate_hz`` each, and for how
    long before the disk fills? Ingest ceiling = 1/point cost; storage = total
    samples/s × on-disk bytes/point."""
    ceiling = 1.0 / coef.point_s if coef.point_s else float("inf")
    aggregate = channels * sample_rate_hz
    bytes_per_s = aggregate * coef.point_bytes
    return ChannelCapacity(
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        keeps_up=aggregate <= ceiling,
        ingest_ceiling_per_s=ceiling,
        max_channels_at_rate=int(ceiling / sample_rate_hz) if sample_rate_hz else 0,
        max_seconds_on_disk=(free_disk_bytes / bytes_per_s) if bytes_per_s else float("inf"),
    )


def derive_capacity(
    coef: Coefficients,
    sc: Scenario,
    *,
    free_disk_bytes: int,
    sweep: ConcurrencySweep | None = None,
) -> Capacity:
    """Sustained-rate and storage capacity for ``sc`` on this machine.

    Sustained rate: the max MEASURED aggregate run-completion rate (the data
    layer's ceiling). Storage: free disk ÷ per-run on-disk bytes.
    """
    per_run = predict_run_cost_s(coef, sc)
    rb = max(1, run_bytes(coef, sc))
    if sweep and sweep.points:
        sustained = max(p.throughput_per_s for p in sweep.points)
    else:
        sustained = 1.0 / per_run
    return Capacity(
        per_run_s=per_run,
        sustained_runs_per_s=sustained,
        storage_runs=int(free_disk_bytes // rb),
        storage_gb_per_run=rb / (1024**3),
    )
