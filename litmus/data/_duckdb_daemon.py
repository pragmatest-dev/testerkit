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

from litmus.data._duckdb_flight_server import DuckDBFlightServer
from litmus.data.duckdb_manager import DuckDBDaemonManager


def daemon_run(events_dir: Path) -> None:
    """Entry point for the daemon process. Blocks until idle timeout."""
    mgr = DuckDBDaemonManager(events_dir)

    conn = duckdb.connect()  # in-memory, no file
    conn.execute("""
        CREATE TABLE events (
            id VARCHAR NOT NULL,
            event_type VARCHAR NOT NULL,
            occurred_at VARCHAR NOT NULL,
            received_at VARCHAR,
            session_id VARCHAR,
            run_id VARCHAR,
            json VARCHAR,
        )
    """)

    # Bulk rebuild from Arrow IPC files
    ipc_files = sorted(events_dir.glob("*/*.arrow"))
    if ipc_files:
        globs = [str(f) for f in ipc_files]
        for fpath in globs:
            try:
                conn.execute(f"""
                    INSERT INTO events BY NAME
                    SELECT id, event_type, occurred_at, received_at,
                           session_id, run_id, json
                    FROM read_ipc('{fpath}')
                """)
            except Exception as exc:
                warnings.warn(
                    f"Failed to rebuild from {fpath}: {exc}", stacklevel=2
                )

    # Start Flight server for cross-process queries and inserts
    server = DuckDBFlightServer("grpc://127.0.0.1:0")
    server.register("events", conn)
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
