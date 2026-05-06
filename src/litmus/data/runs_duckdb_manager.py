"""DuckDB run index daemon manager.

Subclasses ``DaemonManager`` for the runs DuckDB daemon.
Clients call ``acquire()`` / ``release()``.

Parquet files are the source of truth. The in-memory DuckDB index
is rebuilt from parquet on every daemon start.
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path

import pyarrow.flight as flight

from litmus.data._daemon_lifecycle import DaemonManager


class RunsDuckDBManager(DaemonManager):
    """Manages the DuckDB runs index daemon."""

    _state_name = "_runs_duckdb.json"
    _lock_name = "_runs_duckdb.lock"
    _ready_name = "_runs_duckdb_ready"
    _pid_name = "_runs_duckdb_pid"
    _daemon_module = "litmus.data._runs_duckdb_daemon"
    _port_file = "_runs_duckdb_flight_port"


# Module-level convenience — RunStore uses these directly.


def acquire(runs_dir: Path) -> str:
    """Acquire a reference to the runs DuckDB daemon, starting it if needed.

    Returns the gRPC location string for Flight queries.

    The location is written to state file by either
    :meth:`DaemonManager.acquire` (fresh spawn — reads the port file via
    ``_post_spawn_state``) or by the daemon itself
    (:meth:`DaemonManager.update_state` after ``write_ready``). Under
    load on slow CI runners we've seen reuse hit a state file the
    daemon hasn't finished filling in yet — poll briefly so transient
    "missing location" cases don't fail the test.

    After getting the location, a lightweight Flight connectivity probe
    is attempted. If the Flight server isn't responding (e.g. the daemon
    process is alive but its Flight thread crashed), the daemon is killed
    and respawned so callers always get a working connection.
    """
    mgr = RunsDuckDBManager(runs_dir)
    mgr.acquire()
    location = _wait_for_location(mgr, runs_dir)

    # Verify the Flight server is actually responding. The PID may be alive
    # but the Flight thread may have crashed (e.g. port conflict, OOM).
    # One cheap probe avoids leaving callers with a silent dead connection.
    if not _flight_probe(location):
        warnings.warn(
            f"Runs daemon at {location} is not responding — killing and respawning.",
            stacklevel=2,
        )
        from litmus.data._flight_query import _drop_pooled_client  # avoid circular at module level

        _drop_pooled_client(location)
        mgr.force_restart()
        mgr.acquire()
        location = _wait_for_location(mgr, runs_dir)

    return location


def _wait_for_location(mgr: RunsDuckDBManager, runs_dir: Path) -> str:
    """Poll the state file until the daemon writes its Flight location (up to 5s)."""
    deadline = time.monotonic() + 5.0
    while True:
        location = mgr.read_state().get("location")
        if location:
            return location
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"runs DuckDB daemon started but no location in state after 5s: {runs_dir}"
            )
        time.sleep(0.05)


def _flight_probe(location: str) -> bool:
    """Return True if the Flight server at ``location`` responds to a trivial query."""
    try:
        client = flight.connect(location)
        try:
            client.do_get(flight.Ticket(b"runs\x00SELECT 1")).read_all()
            return True
        finally:
            client.close()
    except Exception:  # noqa: BLE001
        return False


def release(runs_dir: Path) -> None:
    """Release our reference to the runs DuckDB daemon."""
    RunsDuckDBManager(runs_dir).release()
