"""DuckDB run index daemon manager.

Subclasses ``DaemonManager`` for the runs DuckDB daemon.
Clients call ``acquire()`` / ``release()``.

Parquet files are the source of truth. The in-memory DuckDB index
is rebuilt from parquet on every daemon start.
"""

from __future__ import annotations

import time
from pathlib import Path

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
    """
    mgr = RunsDuckDBManager(runs_dir)
    mgr.acquire()
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


def release(runs_dir: Path) -> None:
    """Release our reference to the runs DuckDB daemon."""
    RunsDuckDBManager(runs_dir).release()
