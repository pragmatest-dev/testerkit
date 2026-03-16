"""DuckDB event index daemon.

Spawned as a detached process by ``DuckDBDaemonManager.acquire()``.
Maintains an in-memory DuckDB index rebuilt from Arrow IPC files on startup.
Clients push new events via ``do_put`` and query via ``do_get``.

Usage: python -m litmus.data._duckdb_daemon <events_dir>
"""

from __future__ import annotations

import sys
import threading
import warnings
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.ipc as ipc

from litmus.data._duckdb_flight_server import DuckDBFlightServer
from litmus.data.duckdb_manager import DuckDBDaemonManager

_EVENT_COLUMNS = ["id", "event_type", "occurred_at", "received_at", "session_id", "run_id", "json"]

_INSERT_SQL = (
    "INSERT INTO events (id, event_type, occurred_at, received_at, session_id, run_id, json) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def _table_to_rows(table: pa.Table) -> list[tuple]:
    """Convert Arrow table to list of tuples for SQL param insert.

    DuckDB segfaults when inserting Arrow tables with large strings via
    the register/INSERT path.  SQL parameter binding works fine.
    """
    cols = [table.column(c).to_pylist() for c in _EVENT_COLUMNS]
    return list(zip(*cols))


def daemon_run(events_dir: Path) -> None:
    """Entry point for the daemon process. Blocks until idle timeout."""
    mgr = DuckDBDaemonManager(events_dir)

    conn = duckdb.connect()  # in-memory, no file
    conn.execute("""
        CREATE TABLE events (
            id VARCHAR NOT NULL,
            event_type VARCHAR NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            received_at TIMESTAMPTZ,
            session_id VARCHAR,
            run_id VARCHAR,
            json VARCHAR,
        )
    """)

    # Bulk rebuild from Arrow IPC files using SQL params (not Arrow register).
    # Insert per-file so one corrupt file doesn't block the rest.
    # Bad files are moved to _quarantine/ so the daemon won't crash on
    # every restart, but data is preserved for manual recovery.
    quarantine = events_dir / "_quarantine"
    ipc_files = sorted(events_dir.glob("*/*.arrow"))
    for fpath in ipc_files:
        try:
            data = fpath.read_bytes()
            if data[:6] == b"ARROW1":
                # Old file-format IPC; incompatible with open_stream.
                warnings.warn(f"Quarantining legacy file-format Arrow: {fpath.name}", stacklevel=2)
                quarantine.mkdir(parents=True, exist_ok=True)
                fpath.rename(quarantine / fpath.name)
                continue
            reader = ipc.open_stream(data)
            table = reader.read_all().select(_EVENT_COLUMNS)
        except Exception as exc:
            warnings.warn(f"Quarantining unreadable {fpath.name}: {exc}", stacklevel=2)
            quarantine.mkdir(parents=True, exist_ok=True)
            fpath.rename(quarantine / fpath.name)
            continue
        try:
            rows = _table_to_rows(table)
            conn.executemany(_INSERT_SQL, rows)
        except Exception as exc:
            warnings.warn(f"Quarantining bad data in {fpath.name}: {exc}", stacklevel=2)
            quarantine.mkdir(parents=True, exist_ok=True)
            fpath.rename(quarantine / fpath.name)

    # Start Flight server for cross-process queries and inserts.
    # Use a put hook to insert via SQL params instead of Arrow register
    # (DuckDB segfaults on large Arrow strings via the register path).
    server = DuckDBFlightServer("grpc://127.0.0.1:0")
    server.register("events", conn)

    def _events_put_hook(table: pa.Table) -> None:
        rows = _table_to_rows(table)
        conn.executemany(_INSERT_SQL, rows)

    server.register_put_hook("events", _events_put_hook)
    location = f"grpc://127.0.0.1:{server.port}"
    port_file = events_dir / "_duckdb_flight_port"
    port_file.write_text(location)
    threading.Thread(target=server.serve, daemon=True, name="duckdb-flight").start()

    # Signal ready and store Flight location in state
    mgr.write_ready()
    mgr.update_state(location=location)

    # Block until idle timeout
    mgr.monitor_refs()

    # Shut down
    server.shutdown()
    port_file.unlink(missing_ok=True)
    try:
        conn.close()
    except Exception as exc:
        warnings.warn(f"Failed to close DuckDB connection: {exc}", stacklevel=2)

    mgr.cleanup_state_files()


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
