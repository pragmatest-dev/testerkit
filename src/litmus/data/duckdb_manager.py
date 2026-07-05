"""DuckDB event index daemon manager.

Subclasses ``DaemonManager`` for the DuckDB-specific daemon.
Clients call ``acquire()`` / ``release()``.

Arrow IPC files are the crash-safe source of truth. The in-memory
DuckDB index is rebuilt from IPC files on every daemon start.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from litmus.data._daemon_lifecycle import DaemonManager, _installed_version, wait_for_location
from litmus.data._flight_query import probe_sql


class DuckDBDaemonManager(DaemonManager):
    """Manages the DuckDB event index daemon."""

    _state_name = "_duckdb.json"
    _lock_name = "_duckdb.lock"
    _ready_name = "_duckdb_ready"
    _pid_name = "_duckdb_pid"
    _daemon_module = "litmus.data._duckdb_daemon"
    _port_file = "_duckdb_flight_port"

    # Events keys daemon reuse on the projection FINGERPRINT, not just the
    # litmus version (the base-class default) — parity with
    # ``RunsDuckDBManager`` (#64). ``_projection_fingerprint`` is imported
    # lazily inside the methods to break the import cycle (the daemon module
    # imports this manager).

    def _daemon_identity(self) -> dict[str, Any]:
        """Stamp the projection fingerprint (plus the version, for provenance)
        into the state file, so ``_can_reuse`` can compare it."""
        from litmus.data._duckdb_daemon import _projection_fingerprint

        return {"litmus_version": _installed_version(), "fingerprint": _projection_fingerprint()}

    def _can_reuse(self, running_state: dict[str, Any]) -> bool:
        """Reuse only a daemon whose projection fingerprint matches ours exactly.
        A different — or missing (a pre-fingerprint daemon) — fingerprint means it
        serves a different-shaped index, so respawn rather than send it queries
        it can't answer."""
        from litmus.data._duckdb_daemon import _projection_fingerprint

        return running_state.get("fingerprint") == _projection_fingerprint()


# Module-level convenience — EventStore uses these directly.


def acquire(events_dir: Path) -> str:
    """Acquire a reference to the DuckDB daemon, starting it if needed.

    Returns the gRPC location string for Flight queries. Probes the daemon
    after acquiring: if its Flight thread is wedged or dead (PID alive but not
    responding), it's killed and respawned so callers get a working connection.
    """
    mgr = DuckDBDaemonManager(events_dir)
    mgr.acquire()
    location = wait_for_location(mgr, events_dir, "events")
    if not probe_sql(location, "events"):
        warnings.warn(
            f"Events daemon at {location} is not responding — killing and respawning.",
            stacklevel=2,
        )
        mgr.force_restart()
        mgr.acquire()
        location = wait_for_location(mgr, events_dir, "events")
    return location


def release(events_dir: Path) -> None:
    """Release our reference to the DuckDB daemon."""
    DuckDBDaemonManager(events_dir).release()
