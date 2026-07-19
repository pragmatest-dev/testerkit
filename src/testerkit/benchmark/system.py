"""Hardware / version capture and the per-case process-tree sampler.

A benchmark file is only useful if it says what machine produced it and
how hard each case leaned on it. :func:`collect_hardware` /
:func:`collect_versions` record the machine; :class:`ResourceSampler`
samples the WHOLE process tree (main + the store daemons + concurrency
workers) and buckets each sample under the case currently running
(``mark()``), so every row gets its own peak RSS and CPU %.

``psutil`` powers it and is an optional extra (``testerkit[benchmark]``).
Without it, load columns are blank and a note explains why; throughput is
unaffected.
"""

from __future__ import annotations

import os
import platform
import subprocess
import threading
from dataclasses import dataclass

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


def have_psutil() -> bool:
    return psutil is not None


def _cpu_model() -> str:
    """Full CPU brand + generation, e.g. 'Intel(R) Core(TM) i9-13900K' or
    'Apple M3 Pro'. ``platform.processor()`` returns the bare arch on Linux,
    so read the real model from the OS; never fall back to the arch (that's
    captured separately as ``machine``)."""
    system = platform.system()
    if system == "Linux":
        try:
            for line in open("/proc/cpuinfo"):
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    elif system == "Darwin":
        try:
            out = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    name = platform.processor()  # Windows gives a real brand string here
    if name and name != platform.machine():
        return name
    return "unknown"


def collect_hardware() -> dict[str, object]:
    import shutil

    ram_gb: float | None = None
    if psutil is not None:
        ram_gb = round(psutil.virtual_memory().total / (1024**3), 2)
    free_bytes = shutil.disk_usage(os.getcwd()).free
    return {
        "cpu_model": _cpu_model(),
        "cpu_count": os.cpu_count(),
        "ram_gb": ram_gb,
        "free_disk_bytes": free_bytes,
        "free_disk_gb": round(free_bytes / (1024**3), 1),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def collect_versions() -> dict[str, object]:
    from testerkit import __version__ as testerkit_version

    versions: dict[str, object] = {
        "python": platform.python_version(),
        "testerkit": testerkit_version,
    }
    for mod in ("pyarrow", "duckdb"):
        try:
            versions[mod] = __import__(mod).__version__
        except (ImportError, AttributeError):
            versions[mod] = None
    return versions


@dataclass(frozen=True)
class ResourceFootprint:
    """The store daemons' footprint as a share of the WHOLE machine — the
    Task-Manager view ("how much is the data layer pulling from my other
    processes?"). A local scaling constraint: each machine has different totals."""

    cpu_pct_of_machine: float  # peak total daemon CPU, % of all cores
    ram_gb: float  # peak total daemon RSS
    ram_pct_of_machine: float  # peak total daemon RSS, % of total RAM


class ResourceSampler:
    """Background sampler of the store DAEMONS' footprint as a share of the
    whole machine: peak CPU as **% of all cores** and peak RAM as **GB + % of
    total** — like glancing at Task Manager while it's under load. The harness,
    pytest, and concurrency workers are excluded. No-op without ``psutil``.
    """

    def __init__(self, data_dir: object, interval: float = 0.1) -> None:
        self._data_dir = str(data_dir)
        self._interval = interval
        self._cores = os.cpu_count() or 1
        self._ram = psutil.virtual_memory().total if psutil is not None else 1
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak_cpu_pct = 0.0  # % of machine
        self._peak_rss = 0.0  # bytes

    def start(self) -> ResourceSampler:
        if psutil is not None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="bench-footprint")
            self._thread.start()
        return self

    def stop(self) -> ResourceFootprint | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return self.footprint()

    def _daemons(self) -> list:
        assert psutil is not None
        needles = tuple(_DAEMON_MODULES)
        out = []
        try:
            me = psutil.Process(os.getpid())
            for child in me.children(recursive=True):
                try:
                    cmd = " ".join(child.cmdline())
                except psutil.Error:
                    continue
                if self._data_dir in cmd and any(n in cmd for n in needles):
                    out.append(child)
        except psutil.Error:
            pass
        return out

    def _run(self) -> None:
        assert psutil is not None
        primed: dict[int, object] = {}
        while not self._stop.wait(self._interval):
            cpu_pct = 0.0
            rss = 0.0
            for p in self._daemons():
                if p.pid not in primed:
                    try:
                        p.cpu_percent(None)  # prime; first call is meaningless
                    except psutil.Error:
                        continue
                    primed[p.pid] = p
                try:
                    cpu_pct += p.cpu_percent(None)  # % where 100 = one core
                    rss += p.memory_info().rss
                except psutil.Error:
                    primed.pop(p.pid, None)
            self._peak_cpu_pct = max(self._peak_cpu_pct, cpu_pct / self._cores)
            self._peak_rss = max(self._peak_rss, rss)

    def footprint(self) -> ResourceFootprint | None:
        if psutil is None or self._peak_rss == 0:
            return None
        return ResourceFootprint(
            cpu_pct_of_machine=round(self._peak_cpu_pct, 1),
            ram_gb=round(self._peak_rss / (1024**3), 2),
            ram_pct_of_machine=round(self._peak_rss / self._ram * 100, 1),
        )


# Daemon module markers (cmdline) used to find the store daemon processes.
_DAEMON_MODULES: tuple[str, ...] = (
    "testerkit.data._runs_duckdb_daemon",
    "testerkit.data.channels._flight_daemon",
    "testerkit.data.files._catalog_daemon",
    "testerkit.data._duckdb_daemon",
)
