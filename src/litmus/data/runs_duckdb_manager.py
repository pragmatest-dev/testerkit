"""DuckDB run index daemon manager.

Subclasses ``DaemonManager`` for the runs DuckDB daemon.
Clients call ``acquire()`` / ``release()``.

Parquet files are the source of truth. The in-memory DuckDB index
is rebuilt from parquet on every daemon start.
"""

from __future__ import annotations

import sys
from pathlib import Path

from litmus.data._daemon_lifecycle import DaemonManager


class RunsDuckDBManager(DaemonManager):
    """Manages the DuckDB runs index daemon."""

    _state_name = "_runs_duckdb.json"
    _lock_name = "_runs_duckdb.lock"
    _ready_name = "_runs_duckdb_ready"
    _pid_name = "_runs_duckdb_pid"

    def _spawn_cmd(self) -> list[str]:
        return [
            sys.executable,
            "-m",
            "litmus.data._runs_duckdb_daemon",
            str(self._dir),
        ]


# Module-level convenience — RunStore uses these directly.


def acquire(runs_dir: Path) -> str:
    """Acquire a reference to the runs DuckDB daemon, starting it if needed.

    Returns the gRPC location string for Flight queries.
    """
    mgr = RunsDuckDBManager(runs_dir)
    mgr.acquire()
    state = mgr.read_state()
    location = state.get("location")
    if not location:
        port_file = runs_dir / "_runs_duckdb_flight_port"
        location = port_file.read_text().strip()
    return location


def release(runs_dir: Path) -> None:
    """Release our reference to the runs DuckDB daemon."""
    RunsDuckDBManager(runs_dir).release()
