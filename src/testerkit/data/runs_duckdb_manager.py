"""DuckDB run index daemon manager.

Subclasses ``DaemonManager`` for the runs DuckDB daemon.
Clients call ``acquire()`` / ``release()``.

Parquet files are the source of truth. The in-memory DuckDB index
is rebuilt from parquet on every daemon start.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from testerkit.data._daemon_lifecycle import DaemonManager, _installed_version, wait_for_location
from testerkit.data._flight_query import probe_sql


class RunsDuckDBManager(DaemonManager):
    """Manages the DuckDB runs index daemon."""

    _state_name = "_runs_duckdb.json"
    _lock_name = "_runs_duckdb.lock"
    _ready_name = "_runs_duckdb_ready"
    _pid_name = "_runs_duckdb_pid"
    _daemon_module = "testerkit.data._runs_duckdb_daemon"
    _port_file = "_runs_duckdb_flight_port"

    # Runs keys daemon reuse on the projection FINGERPRINT, not just the testerkit
    # version (the base-class default). The fingerprint is a content-address of
    # the read path (projection DDL + adapters + whitelist), so a daemon serves
    # exactly one index shape + SQL. Keying reuse on it means a client never
    # sends a daemon SQL for a different projection — the coexistence law of the
    # index-epoch design (derived-index-versioning.md §11.1). ``_projection_
    # fingerprint`` is imported lazily inside the methods to break the import
    # cycle (the daemon module imports this manager).

    def _daemon_identity(self) -> dict[str, Any]:
        """Stamp the projection fingerprint (plus the version, for provenance)
        into the state file, so ``_can_reuse`` can compare it. A projection
        change — even within one testerkit version (a dev edit / branch switch,
        invisible to the version ratchet) — yields a new fingerprint and thus a
        fresh daemon on the matching epoch."""
        from testerkit.data._runs_duckdb_daemon import _projection_fingerprint

        return {"testerkit_version": _installed_version(), "fingerprint": _projection_fingerprint()}

    def _can_reuse(self, running_state: dict[str, Any]) -> bool:
        """Reuse only a daemon whose projection fingerprint matches ours exactly.
        A different — or missing (a pre-fingerprint daemon) — fingerprint means it
        serves a different-shaped index/SQL, so respawn rather than send it queries
        it can't answer. Fingerprint equality subsumes the version ratchet: a
        version bump that changes the projection changes the fingerprint (respawn);
        one that doesn't keeps it (reuse)."""
        from testerkit.data._runs_duckdb_daemon import _projection_fingerprint

        return running_state.get("fingerprint") == _projection_fingerprint()


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
    location = wait_for_location(mgr, runs_dir, "runs")

    # Verify the Flight server is actually responding. The PID may be alive
    # but the Flight thread may have crashed (e.g. port conflict, OOM).
    # One cheap probe avoids leaving callers with a silent dead connection.
    if not probe_sql(location, "runs"):
        warnings.warn(
            f"Runs daemon at {location} is not responding — killing and respawning.",
            stacklevel=2,
        )
        mgr.force_restart()
        mgr.acquire()
        location = wait_for_location(mgr, runs_dir, "runs")

    return location


def release(runs_dir: Path) -> None:
    """Release our reference to the runs DuckDB daemon."""
    RunsDuckDBManager(runs_dir).release()
