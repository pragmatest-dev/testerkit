"""Ref-counted daemon lifecycle base class.

``DaemonManager`` handles the common pattern shared by ``duckdb_manager``
and ``flight_manager``:

- Acquire / release with PID-list state files and file locks
- Detached process spawning with readiness polling
- atexit / signal cleanup
- Daemon-side ref monitoring with idle timeout

Subclasses set class-level file names and override ``_spawn_cmd()``.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import warnings
import zlib
from pathlib import Path
from typing import Any

from filelock import FileLock

logger = logging.getLogger(__name__)

# 300s (5 min) so daemons survive routine UI page switches. The
# previous 10s was timed to die exactly between operator
# navigations, causing every page click to wait for a fresh spawn
# (~1s python startup + parquet rediscovery, observed as ~10s
# loads with multi-daemon pages). Override via env var if a
# constrained dev environment needs more aggressive shutdown.
_IDLE_TIMEOUT = int(os.environ.get("LITMUS_DAEMON_IDLE_TIMEOUT", "300"))
_POLL_INTERVAL = 2  # seconds between daemon ref-count checks

# Spawn timeout — how long a client waits for a freshly-spawned
# daemon to write its ready file. The python interpreter startup
# alone is ~500ms; loading the litmus package + DuckDB + Flight
# adds another second; on slow CI runners or under contention
# (multiple tests spawning daemons concurrently), this can stretch
# past the previous 10s ceiling. 30s is a safer ceiling that still
# surfaces real spawn failures (corrupt index, unbindable port).
_SPAWN_TIMEOUT = float(os.environ.get("LITMUS_DAEMON_SPAWN_TIMEOUT", "30"))

# Default number of CPUs a daemon process pins itself to. gRPC's pollers +
# EventEngine workers, Arrow's thread pool, and DuckDB's executor all size
# themselves to the visible core count, so an unpinned Flight daemon on a
# 24-core box spawns ~96 threads. Since every store × directory is its own
# daemon, the aggregate trips WSL's pids cgroup at ~30 daemons. Query daemons
# are I/O-bound, so a small core budget costs no meaningful throughput.
# Override (or disable with <= 0) via ``LITMUS_DAEMON_CPU_CAP``.
_CPU_CAP = int(os.environ.get("LITMUS_DAEMON_CPU_CAP", "4"))

# Track acquired managers so atexit/signal can release them all
_acquired: dict[str, DaemonManager] = {}


def _taskset_prefix(dir_key: str) -> list[str]:
    """Return a ``taskset -c <cpus>`` command prefix that pins a spawned daemon
    to a small core set, or ``[]`` when capping is off/unsupported/unneeded.

    Prepended to the spawn command rather than set from inside the daemon:
    gRPC, Arrow, and DuckDB all read the core count when they are *imported*,
    and importing ``litmus`` already pulls in pyarrow — so affinity has to be
    set before the Python process starts. ``taskset`` does that and, unlike a
    fork-time ``preexec_fn``, is safe to use from a multithreaded parent. It
    ``exec``s into the command, so the ``-m <module> <dir>`` tail (and thus
    daemon process-matching) is preserved.

    Each Flight daemon otherwise sizes its gRPC pollers + EventEngine workers,
    Arrow pool, and DuckDB executor to the full core count (~96 threads on a
    24-core box); since every store × directory is its own daemon, the
    aggregate trips WSL's pids cgroup at ~30 daemons. Query daemons are
    I/O-bound, so a small core budget costs no meaningful throughput.

    ``dir_key`` (the daemon's directory) offsets the chosen core window by a
    stable hash so different projects' daemons spread across cores instead of
    all piling onto cpus 0..cap-1. ``LITMUS_DAEMON_CPU_CAP`` overrides the cap;
    <= 0 disables it.
    """
    if _CPU_CAP <= 0 or not hasattr(os, "sched_getaffinity"):
        return []
    taskset = shutil.which("taskset")
    if taskset is None:
        return []
    try:
        available = sorted(os.sched_getaffinity(0))
    except OSError:
        return []
    n = len(available)
    if _CPU_CAP >= n:
        return []  # already at/under the cap — don't restrict
    offset = zlib.crc32(dir_key.encode()) % n
    chosen = sorted({available[(offset + i) % n] for i in range(_CPU_CAP)})
    return [taskset, "-c", ",".join(str(c) for c in chosen)]


def daemon_duckdb_config() -> dict[str, Any]:
    """DuckDB ``connect(config=...)`` sizing the executor pool to the process's
    *allowed* cores — for a daemon's long-lived index connection.

    DuckDB defaults its thread pool to ``hardware_concurrency()`` (the whole
    machine), ignoring CPU affinity — so a daemon pinned by :func:`_taskset_prefix`
    to a few cores otherwise spawns ~24 oversubscribed executor threads on those
    cores. Sizing to ``sched_getaffinity`` drops that to the pin with no
    throughput loss (you can't outrun the cores you're pinned to). It also
    auto-scopes: a client that opens the same index unpinned reads its full core
    count, so only daemons shrink. No-op on platforms without ``sched_getaffinity``.
    """
    if not hasattr(os, "sched_getaffinity"):
        return {}
    try:
        return {"threads": max(1, len(os.sched_getaffinity(0)))}
    except OSError:
        return {}


def _daemon_log_tail(log_path: Path, n: int = 25) -> str:
    """Return the last ``n`` significant lines of a daemon log for error context.

    Drops the high-volume Arrow Acero alignment warnings so a real traceback
    (e.g. a DuckDB BinderException from a schema-drifted index) isn't buried.
    """
    try:
        lines = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return "(no daemon log)"
    significant = [ln for ln in lines if "poorly aligned" not in ln]
    tail = (significant or lines)[-n:]
    return "--- daemon log tail ---\n" + "\n".join(tail)


def _installed_version() -> str:
    """Return the installed litmus version string."""
    from litmus import __version__

    return __version__


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple of ints."""
    parts: list[int] = []
    for segment in v.split("."):
        digits = ""
        for ch in segment:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive (cross-platform)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


class DaemonManager:
    """Base class for ref-counted daemon lifecycle management.

    Subclasses set the four ``_*_name`` class variables. The simple
    case (DuckDB events / runs daemons) sets ``_daemon_module`` and
    ``_port_file`` and inherits the default ``_spawn_cmd`` /
    ``_post_spawn_state`` shown below. Daemons that need extra
    arguments (e.g. ``FlightDaemonManager`` passes host/port) override
    those methods directly.
    """

    _state_name: str
    _lock_name: str
    _ready_name: str
    _pid_name: str
    # Optional class-level shortcuts for the common DuckDB-style daemons.
    # Set by subclasses; ``None`` means the subclass overrides
    # ``_spawn_cmd`` / ``_post_spawn_state`` directly.
    _daemon_module: str | None = None
    _port_file: str | None = None

    def __init__(self, daemon_dir: Path) -> None:
        self._dir = daemon_dir

    # -- Client-side API -----------------------------------------------------

    def acquire(self) -> None:
        """Acquire a reference to the daemon, starting it if needed."""
        self._dir.mkdir(parents=True, exist_ok=True)
        lock = FileLock(self._dir / self._lock_name, timeout=10)
        state = self._dir / self._state_name

        with lock:
            if state.exists():
                try:
                    data = json.loads(state.read_text())
                    if _pid_alive(data["pid"]):
                        if self._can_reuse(data):
                            refs: list[int] = data.get("refs", [])
                            if os.getpid() not in refs:
                                refs.append(os.getpid())
                            data["refs"] = refs
                            state.write_text(json.dumps(data))
                            self._register_cleanup()
                            return
                        else:
                            warnings.warn(
                                f"Restarting {type(self).__name__} daemon "
                                "(running instance is incompatible with this client)",
                                stacklevel=2,
                            )
                            self._kill_daemon(data["pid"])
                            state.unlink(missing_ok=True)
                            (self._dir / self._ready_name).unlink(missing_ok=True)
                            (self._dir / self._pid_name).unlink(missing_ok=True)
                except (json.JSONDecodeError, OSError, KeyError, TypeError) as exc:
                    warnings.warn(
                        f"Stale daemon state, respawning: {exc}",
                        stacklevel=2,
                    )
                state.unlink(missing_ok=True)

            self._spawn()
            pid = self._read_pid()

            data = {"pid": pid, "refs": [os.getpid()], **self._daemon_identity()}
            data.update(self._post_spawn_state())
            state.write_text(json.dumps(data))

        self._register_cleanup()

    def release(self) -> None:
        """No-op. The daemon prunes dead client PIDs itself via
        monitor_refs() every poll cycle — no blocking lock needed on
        the caller's exit path. Ctrl+C on ``litmus serve`` is instant."""
        return

    def read_state(self) -> dict:
        """Read the current state file. Returns empty dict if missing."""
        state = self._dir / self._state_name
        if not state.exists():
            return {}
        try:
            return json.loads(state.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def _kill_daemon(pid: int) -> None:
        """Kill a daemon process and wait for it to exit."""
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                time.sleep(0.1)
                if not _pid_alive(pid):
                    return
            os.kill(pid, signal.SIGKILL)
        except (OSError, PermissionError):
            pass

    def force_restart(self) -> None:
        """Kill a running daemon unconditionally so it rebuilds on next access."""
        lock = FileLock(self._dir / self._lock_name, timeout=10)
        state = self._dir / self._state_name

        with lock:
            if state.exists():
                try:
                    data = json.loads(state.read_text())
                    pid = data.get("pid")
                    if pid and _pid_alive(pid):
                        self._kill_daemon(pid)
                except (json.JSONDecodeError, OSError, KeyError, TypeError):
                    pass
                state.unlink(missing_ok=True)
            (self._dir / self._ready_name).unlink(missing_ok=True)
            (self._dir / self._pid_name).unlink(missing_ok=True)

    # -- Subclass hooks ------------------------------------------------------

    def _daemon_identity(self) -> dict[str, Any]:
        """The compatibility identity stamped into the state file on spawn —
        what ``_can_reuse`` later compares against.

        Default: the litmus version (the version-ratchet). A subclass with a
        projection fingerprint overrides this to add e.g. ``{"fingerprint":
        ...}`` so reuse can key on it.
        """
        return {"litmus_version": _installed_version()}

    def _can_reuse(self, running_state: dict[str, Any]) -> bool:
        """Whether THIS client may reuse the running daemon described by
        ``running_state`` (its state-file dict).

        Default reproduces the version ratchet exactly: reuse iff the
        running daemon's version is >= ours (an older-or-equal client rides
        a newer daemon; a newer client does not reuse an older one — it
        respawns/upgrades). A subclass keying on a fingerprint overrides
        this to require an exact match instead.
        """
        daemon_version = running_state.get("litmus_version", "0.0.0")
        return _version_tuple(daemon_version) >= _version_tuple(_installed_version())

    def _spawn_cmd(self) -> list[str]:
        """Command to spawn the daemon process.

        Default uses ``_daemon_module`` if set: ``python -m <module>
        <dir>``. Subclasses with extra args override this method.
        """
        if self._daemon_module is None:
            raise NotImplementedError(
                f"{type(self).__name__} must set _daemon_module or override _spawn_cmd"
            )
        return [sys.executable, "-m", self._daemon_module, str(self._dir)]

    def _post_spawn_state(self) -> dict:
        """Extra fields to store in state file after spawn.

        Default uses ``_port_file`` if set: read the port file the
        daemon writes before signalling ready, return ``{"location":
        ...}``. Subclasses without a port file override or fall back
        to the empty default.
        """
        if self._port_file is None:
            return {}
        return {"location": (self._dir / self._port_file).read_text().strip()}

    # -- Daemon-side API -----------------------------------------------------

    def write_ready(self) -> None:
        """Write PID and ready files, update state. Called from daemon.

        If the ready file was already written (e.g. Flight writes its port
        file with actual content), this will not overwrite it — subclasses
        that pre-write the ready file should call this after.
        """
        (self._dir / self._pid_name).write_text(str(os.getpid()))
        ready = self._dir / self._ready_name
        if not ready.exists():
            ready.write_text("ok")

        lock = FileLock(self._dir / self._lock_name, timeout=10)
        state = self._dir / self._state_name
        with lock:
            if state.exists():
                try:
                    data = json.loads(state.read_text())
                    data["pid"] = os.getpid()
                    state.write_text(json.dumps(data))
                except (json.JSONDecodeError, OSError, TypeError) as exc:
                    warnings.warn(
                        f"Failed to update daemon state: {exc}",
                        stacklevel=2,
                    )

    def update_state(self, **extra: object) -> None:
        """Merge extra fields into the state file. Called from daemon."""
        lock = FileLock(self._dir / self._lock_name, timeout=10)
        state = self._dir / self._state_name
        with lock:
            if state.exists():
                try:
                    data = json.loads(state.read_text())
                    data.update(extra)
                    state.write_text(json.dumps(data))
                except (json.JSONDecodeError, OSError, TypeError) as exc:
                    warnings.warn(
                        f"Failed to update daemon state: {exc}",
                        stacklevel=2,
                    )

    def monitor_refs(
        self,
        *,
        idle_timeout: float = _IDLE_TIMEOUT,
        poll_interval: float = _POLL_INTERVAL,
    ) -> None:
        """Block until refs drop to zero and idle timeout expires.

        Called from daemon processes to know when to shut down.
        """
        state = self._dir / self._state_name
        lock = FileLock(self._dir / self._lock_name, timeout=10)
        idle_since: float | None = None

        while True:
            time.sleep(poll_interval)

            with lock:
                if not state.exists():
                    break
                try:
                    data = json.loads(state.read_text())
                    refs: list[int] = data.get("refs", [])
                    live = [p for p in refs if _pid_alive(p)]
                    if live != refs:
                        data["refs"] = live
                        state.write_text(json.dumps(data))
                        refs = live
                except (json.JSONDecodeError, OSError):
                    break

            if not refs:
                if idle_since is None:
                    idle_since = time.monotonic()
                elif time.monotonic() - idle_since >= idle_timeout:
                    break
            else:
                idle_since = None

    def cleanup_state_files(self) -> None:
        """Remove state, ready, and PID files. Called from daemon on shutdown."""
        with FileLock(self._dir / self._lock_name, timeout=5):
            (self._dir / self._state_name).unlink(missing_ok=True)
            (self._dir / self._ready_name).unlink(missing_ok=True)
            (self._dir / self._pid_name).unlink(missing_ok=True)

    # -- Internal ------------------------------------------------------------

    def _spawn(self) -> None:
        """Spawn daemon as a detached process, wait for ready file.

        stdout/stderr are appended to ``_daemon.log`` in the daemon's
        directory. Without a log sink the daemon's warnings (ingest
        failures, schema-drift rebuilds, exceptions) vanish into
        ``/dev/null`` and a misbehaving daemon looks identical to a
        healthy one. With it, ``tail -f`` on the file shows the
        actual reason a query is empty / slow / wrong.
        """
        ready_file = self._dir / self._ready_name
        ready_file.unlink(missing_ok=True)

        # Prefix with ``taskset`` (when available) so gRPC/Arrow/DuckDB size
        # their thread pools to a small core budget — they read the core count
        # at import, before any daemon code (or even ``import litmus``) runs.
        cmd = _taskset_prefix(str(self._dir)) + self._spawn_cmd()
        log_path = self._dir / "_daemon.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, "ab", buffering=0)
        kwargs: dict = {
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            )
        else:
            kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cmd, **kwargs)
        finally:
            # Daemon inherits the file descriptor; we close ours so
            # the parent process doesn't keep the log file open.
            log_handle.close()

        deadline = time.monotonic() + _SPAWN_TIMEOUT
        while time.monotonic() < deadline:
            if ready_file.exists():
                return
            if proc.poll() is not None:
                raise RuntimeError(
                    f"Daemon exited (code {proc.returncode}) before signaling ready. "
                    f"dir={self._dir}, cmd={cmd}\n{_daemon_log_tail(log_path)}"
                )
            time.sleep(0.05)

        proc.kill()
        proc.wait(timeout=2)
        raise RuntimeError(
            f"Daemon failed to start within {_SPAWN_TIMEOUT}s. dir={self._dir}, "
            f"cmd={cmd}\n{_daemon_log_tail(log_path)}"
        )

    def _read_pid(self) -> int:
        """Read the daemon PID from its PID file."""
        pid_file = self._dir / self._pid_name
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if pid_file.exists():
                try:
                    return int(pid_file.read_text().strip())
                except ValueError:
                    pass
            time.sleep(0.05)
        raise RuntimeError("Daemon did not write PID file")

    def _register_cleanup(self) -> None:
        key = str(self._dir) + ":" + self._state_name
        if key in _acquired:
            return
        _acquired[key] = self

        def _cleanup() -> None:
            mgr = _acquired.pop(key, None)
            if mgr is not None:
                try:
                    mgr.release()
                except Exception:  # noqa: BLE001 — deliberately broad for atexit cleanup
                    pass

        atexit.register(_cleanup)

        # No signal handlers — release() is a no-op so there is nothing
        # to do on SIGINT/SIGTERM. pytest and uvicorn both manage their
        # own signal handling; installing a handler here only interferes.


def wait_for_location(mgr: DaemonManager, store_dir: Path, label: str) -> str:
    """Poll the daemon's state file until it writes its Flight location (up to 5s).

    Shared by every store's manager (events / runs / files). ``label`` names
    the store in the timeout error.
    """
    deadline = time.monotonic() + 5.0
    while True:
        location = mgr.read_state().get("location")
        if location:
            return location
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"{label} daemon started but no location in state after 5s: {store_dir}"
            )
        time.sleep(0.05)
