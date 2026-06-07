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

    Each sampled process is attributed to the store whose **daemon** it is
    (by cmdline); the benchmark harness, pytest, and concurrency workers
    are ignored — we report each store's own server-side footprint, not
    ours. CPU is reported in **cores** (one fully-used core = 1.0), the
    cgroups/k8s/`time` convention, so the number is interpretable without
    knowing the machine's core count. No-op when ``psutil`` is absent.
    """

    def __init__(self, interval: float = 0.05) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._label: str | None = None
        self._lock = threading.Lock()
        # (case_label | "*", store) -> {"rss_peak": bytes, "cores": [float,...]}
        self._by: dict[tuple[str, str], dict] = {}
        self._store_by_pid: dict[int, str | None] = {}

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

    def _store_of(self, proc) -> str | None:
        """Which store's daemon is this process? Cached by pid; None = harness."""
        assert psutil is not None
        pid = proc.pid
        if pid in self._store_by_pid:
            return self._store_by_pid[pid]
        store: str | None = None
        try:
            cmd = " ".join(proc.cmdline())
            for needle, name in _DAEMON_MODULES:
                if needle in cmd:
                    store = name
                    break
        except psutil.Error:
            store = None
        self._store_by_pid[pid] = store
        return store

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
            for p in procs:
                store = self._store_of(p)
                if store is None:  # harness / pytest / workers — not a store daemon
                    continue
                try:
                    rss = p.memory_info().rss
                    cores = p.cpu_percent(None) / 100.0
                except psutil.Error:
                    continue
                with self._lock:
                    label = self._label
                    for key in (("*", store), *(((label, store),) if label else ())):
                        b = self._by.setdefault(key, {"rss_peak": 0.0, "cores": []})
                        b["rss_peak"] = max(b["rss_peak"], rss)
                        b["cores"].append(cores)

    @staticmethod
    def _summarize(b: dict | None) -> dict[str, object | None]:
        if not b or not b["cores"]:
            return {"rss_mb": None, "cores_mean": None, "cores_peak": None}
        c = b["cores"]
        return {
            "rss_mb": round(b["rss_peak"] / (1024**2), 1),
            "cores_mean": round(sum(c) / len(c), 2),
            "cores_peak": round(max(c), 2),
        }

    def case(self, label: str, store: str) -> dict[str, object | None]:
        """The ``store`` daemon's RSS (MB) + CPU (cores) during one case."""
        with self._lock:
            b = self._by.get((label, store))
        return self._summarize(b)

    def per_store(self) -> dict[str, dict[str, object | None]] | None:
        """Whole-run footprint per store daemon, or None without psutil."""
        if psutil is None:
            return None
        with self._lock:
            stores = sorted({s for (lbl, s) in self._by if lbl == "*"})
            return {s: self._summarize(self._by.get(("*", s))) for s in stores}


# Daemon module → store. Order matters: check the runs daemon before the
# events one (their module names share the ``duckdb_daemon`` suffix).
_DAEMON_MODULES: list[tuple[str, str]] = [
    ("litmus.data._runs_duckdb_daemon", "runs"),
    ("litmus.data.channels._flight_daemon", "channels"),
    ("litmus.data.files._catalog_daemon", "files"),
    ("litmus.data._duckdb_daemon", "events"),
]
