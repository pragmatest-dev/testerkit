"""Hardware / version capture and the per-case process-tree sampler.

A benchmark file is only useful if it says what machine produced it and
how hard each case leaned on it. :func:`collect_hardware` /
:func:`collect_versions` record the machine; :class:`ResourceSampler`
samples the WHOLE process tree (main + the store daemons + concurrency
workers) and buckets each sample under the case currently running
(``mark()``), so every row gets its own peak RSS and CPU %.

``psutil`` powers it and is an optional extra (``litmus-test[benchmark]``).
Without it, load columns are blank and a note explains why; throughput is
unaffected.
"""

from __future__ import annotations

import os
import platform
import threading
import time

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


def have_psutil() -> bool:
    return psutil is not None


def _cpu_model() -> str:
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
    """Background sampler over the process tree, bucketed per case.

    ``mark(label)`` switches the bucket; the thread tags each sample with
    the active label. :meth:`case` returns one case's peak RSS + CPU%.
    No-op when ``psutil`` is absent.
    """

    def __init__(self, interval: float = 0.05) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._label: str | None = None
        self._lock = threading.Lock()
        # label -> {"rss_peak": float, "cpu": [float, ...]}
        self._by_label: dict[str, dict] = {}
        self._rss_peak_all = 0.0
        self._cpu_all: list[float] = []

    def mark(self, label: str | None) -> None:
        with self._lock:
            self._label = label

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
            if time.monotonic() - last_refresh > 0.5:
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
            with self._lock:
                label = self._label
                self._rss_peak_all = max(self._rss_peak_all, rss)
                self._cpu_all.append(cpu)
                if label is not None:
                    b = self._by_label.setdefault(label, {"rss_peak": 0.0, "cpu": []})
                    b["rss_peak"] = max(b["rss_peak"], rss)
                    b["cpu"].append(cpu)

    def case(self, label: str) -> dict[str, object | None]:
        """Peak RSS (MB) + CPU% mean/max for one case, or Nones if unsampled."""
        with self._lock:
            b = self._by_label.get(label)
        if not b or not b["cpu"]:
            return {"peak_rss_mb": None, "cpu_pct_mean": None, "cpu_pct_max": None}
        cpu = b["cpu"]
        return {
            "peak_rss_mb": round(b["rss_peak"] / (1024**2), 1),
            "cpu_pct_mean": round(sum(cpu) / len(cpu), 1),
            "cpu_pct_max": round(max(cpu), 1),
        }

    def overall(self) -> dict[str, object] | None:
        if psutil is None:
            return None
        cpu = self._cpu_all
        return {
            "peak_rss_mb": round(self._rss_peak_all / (1024**2), 1),
            "cpu_pct_max": round(max(cpu), 1) if cpu else None,
            "cpu_pct_mean": round(sum(cpu) / len(cpu), 1) if cpu else None,
            "samples": len(cpu),
        }
