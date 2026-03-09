"""DuckDB event index daemon manager.

Subclasses ``DaemonManager`` for the DuckDB-specific daemon.
Clients call ``acquire()`` / ``release()``.

Arrow IPC files are the crash-safe source of truth. The in-memory
DuckDB index is rebuilt from IPC files on every daemon start.
"""

from __future__ import annotations

import sys
from pathlib import Path

from litmus.data._daemon_lifecycle import DaemonManager


class DuckDBDaemonManager(DaemonManager):
    """Manages the DuckDB event index daemon."""

    _state_name = "_duckdb.json"
    _lock_name = "_duckdb.lock"
    _ready_name = "_duckdb_ready"
    _pid_name = "_duckdb_pid"

    def _spawn_cmd(self) -> list[str]:
        return [
            sys.executable, "-m", "litmus.data._duckdb_daemon",
            str(self._dir),
        ]


# Module-level convenience — EventStore uses these directly.

def acquire(events_dir: Path) -> str:
    """Acquire a reference to the DuckDB daemon, starting it if needed.

    Returns the gRPC location string for Flight queries.
    """
    mgr = DuckDBDaemonManager(events_dir)
    mgr.acquire()
    state = mgr.read_state()
    location = state.get("location")
    if not location:
        # Fallback: read from port file (daemon writes it before ready)
        port_file = events_dir / "_duckdb_flight_port"
        location = port_file.read_text().strip()
    return location


def release(events_dir: Path) -> None:
    """Release our reference to the DuckDB daemon."""
    DuckDBDaemonManager(events_dir).release()
