"""DuckDB event index daemon manager.

Subclasses ``DaemonManager`` for the DuckDB-specific daemon.
Clients call ``acquire()`` / ``release()``.

Arrow IPC files are the crash-safe source of truth. The in-memory
DuckDB index is rebuilt from IPC files on every daemon start.
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path

from litmus.data._daemon_lifecycle import DaemonManager
from litmus.data._flight_query import probe_sql


class DuckDBDaemonManager(DaemonManager):
    """Manages the DuckDB event index daemon."""

    _state_name = "_duckdb.json"
    _lock_name = "_duckdb.lock"
    _ready_name = "_duckdb_ready"
    _pid_name = "_duckdb_pid"
    _daemon_module = "litmus.data._duckdb_daemon"
    _port_file = "_duckdb_flight_port"


# Module-level convenience — EventStore uses these directly.


def _wait_for_location(mgr: DuckDBDaemonManager, events_dir: Path) -> str:
    """Poll the state file until the daemon writes its Flight location (up to 5s)."""
    deadline = time.monotonic() + 5.0
    while True:
        location = mgr.read_state().get("location")
        if location:
            return location
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"DuckDB daemon started but no location in state after 5s: {events_dir}"
            )
        time.sleep(0.05)


def acquire(events_dir: Path) -> str:
    """Acquire a reference to the DuckDB daemon, starting it if needed.

    Returns the gRPC location string for Flight queries. Probes the daemon
    after acquiring: if its Flight thread is wedged or dead (PID alive but not
    responding), it's killed and respawned so callers get a working connection.
    """
    mgr = DuckDBDaemonManager(events_dir)
    mgr.acquire()
    location = _wait_for_location(mgr, events_dir)
    if not probe_sql(location, "events"):
        warnings.warn(
            f"Events daemon at {location} is not responding — killing and respawning.",
            stacklevel=2,
        )
        mgr.force_restart()
        mgr.acquire()
        location = _wait_for_location(mgr, events_dir)
    return location


def release(events_dir: Path) -> None:
    """Release our reference to the DuckDB daemon."""
    DuckDBDaemonManager(events_dir).release()
