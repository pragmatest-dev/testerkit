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

_PORT_FILE = "_duckdb_flight_port"


class DuckDBDaemonManager(DaemonManager):
    """Manages the DuckDB event index daemon."""

    _state_name = "_duckdb.json"
    _lock_name = "_duckdb.lock"
    _ready_name = "_duckdb_ready"
    _pid_name = "_duckdb_pid"

    def _spawn_cmd(self) -> list[str]:
        return [
            sys.executable,
            "-m",
            "litmus.data._duckdb_daemon",
            str(self._dir),
        ]

    def _post_spawn_state(self) -> dict:
        """Capture the daemon's gRPC location from the port file.

        The daemon writes the port file before signalling ready, so
        :meth:`DaemonManager._spawn` returns only after this file exists.
        """
        return {"location": (self._dir / _PORT_FILE).read_text().strip()}


# Module-level convenience — EventStore uses these directly.


def acquire(events_dir: Path) -> str:
    """Acquire a reference to the DuckDB daemon, starting it if needed.

    Returns the gRPC location string for Flight queries.
    """
    mgr = DuckDBDaemonManager(events_dir)
    mgr.acquire()
    location = mgr.read_state().get("location")
    if not location:
        raise RuntimeError(f"DuckDB daemon started but no location in state: {events_dir}")
    return location


def release(events_dir: Path) -> None:
    """Release our reference to the DuckDB daemon."""
    DuckDBDaemonManager(events_dir).release()
