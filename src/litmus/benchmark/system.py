"""Hardware / version capture and the process-tree resource sampler.

A benchmark file is only useful to a maintainer if it says what machine
produced it. :func:`collect_hardware` and :func:`collect_versions`
record that; :class:`ResourceSampler` records how hard the run leaned on
the machine (peak RAM, CPU %) across the WHOLE process tree — the store
daemons do the indexing work in separate processes, so a main-process-
only reading would undercount.

``psutil`` powers RAM + CPU capture and is an optional extra
(``litmus-test[benchmark]``). Without it, the resource block is omitted
and a note explains why; the throughput numbers are unaffected.
"""

from __future__ import annotations

import os
import platform
import threading
import time

try:
    import psutil
except ImportError:  # pragma: no cover - exercised by the no-extra path
    psutil = None  # type: ignore[assignment]


def have_psutil() -> bool:
    return psutil is not None


def _cpu_model() -> str:
    """Best-effort CPU model name across platforms."""
    name = platform.processor()
    if name:
        return name
    if platform.system() == "Linux":
        try:
            for line in open("/proc/cpuinfo"):
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.machine() or "unknown"


def collect_hardware() -> dict[str, object]:
    """CPU model + count, total RAM, OS — the machine fingerprint."""
    ram_gb: float | None = None
    if psutil is not None:
        ram_gb = round(psutil.virtual_memory().total / (1024**3), 2)
    return {
        "cpu_model": _cpu_model(),
        "cpu_count": os.cpu_count(),
        "ram_gb": ram_gb,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def collect_versions() -> dict[str, object]:
    """Python + the libraries whose performance the numbers depend on."""
    from litmus import __version__ as litmus_version

    versions: dict[str, object] = {
        "python": platform.python_version(),
        "litmus": litmus_version,
    }
    for mod in ("pyarrow", "duckdb"):
        try:
            versions[mod] = __import__(mod).__version__
        except (ImportError, AttributeError):
            versions[mod] = None
    return versions


class ResourceSampler:
    """Background sampler over the process tree (main + spawned daemons).

    Samples summed RSS and summed CPU % every ``interval`` seconds while
    the benchmark runs, then reports peak RSS (MB) and CPU % min/max/mean
    so a received file answers "did this saturate a core / fit in RAM?".
    No-op when ``psutil`` is absent.
    """

    def __init__(self, interval: float = 0.1) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._rss_peak = 0.0
        self._cpu_samples: list[float] = []

    def __enter__(self) -> ResourceSampler:
        if psutil is not None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="bench-sampler")
            self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _tree(self) -> list:
        assert psutil is not None
        try:
            me = psutil.Process(os.getpid())
            procs = [me, *me.children(recursive=True)]
        except psutil.Error:
            return []
        # Prime cpu_percent so the next read reports % since this moment.
        for p in procs:
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass
        return procs

    def _run(self) -> None:
        assert psutil is not None
        procs = self._tree()
        last_refresh = time.monotonic()
        while not self._stop.wait(self._interval):
            # Refresh the child list periodically — daemons spawn lazily.
            if time.monotonic() - last_refresh > 1.0:
                procs = self._tree()
                last_refresh = time.monotonic()
                continue
            rss = 0.0
            cpu = 0.0
            for p in procs:
                try:
                    rss += p.memory_info().rss
                    cpu += p.cpu_percent(None)
                except psutil.Error:
                    continue
            self._rss_peak = max(self._rss_peak, rss)
            self._cpu_samples.append(cpu)

    def report(self) -> dict[str, object] | None:
        """Resource block, or ``None`` if psutil was unavailable."""
        if psutil is None:
            return None
        cpu = self._cpu_samples
        return {
            "peak_rss_mb": round(self._rss_peak / (1024**2), 1),
            "cpu_pct_min": round(min(cpu), 1) if cpu else None,
            "cpu_pct_max": round(max(cpu), 1) if cpu else None,
            "cpu_pct_mean": round(sum(cpu) / len(cpu), 1) if cpu else None,
            "samples": len(cpu),
        }
