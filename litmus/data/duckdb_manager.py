"""DuckDB event index daemon manager.

Subclasses ``DaemonManager`` for the DuckDB-specific daemon.
Clients call ``acquire()`` / ``release()``.

JSONL files remain the crash-safe source of truth.  The DuckDB file is
disposable — deleted and rebuilt from JSONL on every daemon start.
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


def db_path(events_dir: Path) -> Path:
    """Path to the DuckDB index file."""
    return events_dir / "index.duckdb"


# Module-level convenience — EventStore uses these directly.

def acquire(events_dir: Path) -> Path:
    """Acquire a reference to the DuckDB daemon, starting it if needed.

    Returns the path to ``index.duckdb`` for read-only queries.
    """
    DuckDBDaemonManager(events_dir).acquire()
    return db_path(events_dir)


def release(events_dir: Path) -> None:
    """Release our reference to the DuckDB daemon."""
    DuckDBDaemonManager(events_dir).release()
