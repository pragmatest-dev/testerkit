"""DuckDB event index daemon.

Spawned as a detached process by ``DuckDBDaemonManager.acquire()``.
Maintains a persistent DuckDB index rebuilt incrementally from Arrow IPC
files. Clients push new events via ``do_put`` and query via ``do_get``.

Startup is O(new files since last run): the daemon opens the existing
``_index.duckdb``, signals ready immediately, then ingests only files not
yet recorded in the ``_ingested`` table via a background thread.

Usage: python -m litmus.data._duckdb_daemon <events_dir>
"""

from __future__ import annotations

import os
import sys
import threading
import warnings
from pathlib import Path

import duckdb
import pyarrow as pa

from litmus.data._duckdb_flight_server import DuckDBFlightServer
from litmus.data._ipc_writer import read_ipc_batches
from litmus.data.duckdb_manager import DuckDBDaemonManager

_EVENT_COLUMNS = ["id", "event_type", "occurred_at", "received_at", "session_id", "run_id", "json"]

# ON CONFLICT DO NOTHING deduplicates events that arrived via do_put during
# the previous session and are now also present in the IPC files.
_INSERT_SQL = (
    "INSERT INTO events (id, event_type, occurred_at, received_at, session_id, run_id, json) "
    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING"
)

_SCHEMA_VERSION = 1


def _table_to_rows(table: pa.Table) -> list[tuple]:
    """Convert Arrow table to list of tuples for SQL param insert.

    DuckDB segfaults when inserting Arrow tables with large strings via
    the register/INSERT path.  SQL parameter binding works fine.
    """
    cols = [table.column(c).to_pylist() for c in _EVENT_COLUMNS]
    return list(zip(*cols))


# ── Schema management ────────────────────────────────────────────────


def _open_index(index_path: Path) -> duckdb.DuckDBPyConnection:
    """Open or create the persistent DuckDB index; rebuild on schema mismatch."""
    conn = duckdb.connect(str(index_path))
    needs_rebuild = False
    try:
        row = conn.execute("SELECT v FROM _schema_version LIMIT 1").fetchone()
        if row is None or row[0] != _SCHEMA_VERSION:
            warnings.warn("Schema version mismatch — rebuilding event index", stacklevel=2)
            needs_rebuild = True
    except duckdb.Error:
        needs_rebuild = True  # Fresh DB or corrupt — rebuild silently
    if needs_rebuild:
        _rebuild_schema(conn)
    return conn


def _rebuild_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Drop all managed tables and recreate with current schema."""
    conn.execute("DROP TABLE IF EXISTS events")
    conn.execute("DROP TABLE IF EXISTS _ingested")
    conn.execute("DROP TABLE IF EXISTS _schema_version")
    conn.execute("CREATE TABLE _schema_version (v INTEGER PRIMARY KEY)")
    conn.execute(f"INSERT INTO _schema_version VALUES ({_SCHEMA_VERSION})")
    conn.execute("""
        CREATE TABLE events (
            id VARCHAR PRIMARY KEY,
            event_type VARCHAR NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            received_at TIMESTAMPTZ,
            session_id VARCHAR,
            run_id VARCHAR,
            json VARCHAR,
        )
    """)
    conn.execute("""
        CREATE TABLE _ingested (
            path VARCHAR PRIMARY KEY,
            mtime DOUBLE NOT NULL,
            size BIGINT NOT NULL,
            row_count BIGINT NOT NULL DEFAULT 0,
            status VARCHAR NOT NULL DEFAULT 'ok',
            error VARCHAR,
            last_attempt TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


# ── Background ingest ────────────────────────────────────────────────


def _ingest_ipc_files(index_path: Path, events_dir: Path) -> None:
    """Background thread: ingest new/changed IPC files into the events index.

    Uses a single batched anti-join against _ingested (O(1) round trips
    regardless of file count). Opens its own connection so the Flight
    server's connection is never blocked by bulk ingest I/O.
    """
    conn = duckdb.connect(str(index_path))
    try:
        disk_entries: list[tuple[str, float, int, os.stat_result]] = []
        for fpath in sorted(events_dir.glob("*/*.arrow")):
            try:
                stat = fpath.stat()
                disk_entries.append((str(fpath), stat.st_mtime, stat.st_size, stat))
            except OSError:
                continue

        if not disk_entries:
            return

        disk_table = pa.table(
            {
                "path": [e[0] for e in disk_entries],
                "mtime": pa.array([e[1] for e in disk_entries], type=pa.float64()),
                "size": pa.array([e[2] for e in disk_entries], type=pa.int64()),
            }
        )
        conn.register("_disk_snapshot", disk_table)

        needs_ingest = conn.execute("""
            SELECT d.path
            FROM _disk_snapshot d
            LEFT JOIN _ingested i
                ON d.path = i.path
               AND d.mtime = i.mtime
               AND d.size = i.size
               AND i.status = 'ok'
            WHERE i.path IS NULL
        """).fetchall()

        stat_map = {e[0]: e[3] for e in disk_entries}
        for (path_str,) in needs_ingest:
            stat = stat_map.get(path_str)
            if stat is not None:
                _ingest_one_file(conn, Path(path_str), stat)

        conn.unregister("_disk_snapshot")
    finally:
        try:
            conn.close()
        except Exception as exc:
            warnings.warn(f"Ingest connection close failed: {exc}", stacklevel=2)


def _ingest_one_file(
    conn: duckdb.DuckDBPyConnection,
    fpath: Path,
    stat: os.stat_result,
) -> None:
    """Ingest a single IPC file. Records status in _ingested."""
    path_str = str(fpath)

    def _mark(status: str, error: str | None = None, row_count: int = 0) -> None:
        conn.execute(
            "INSERT INTO _ingested (path, mtime, size, row_count, status, error, last_attempt) "
            "VALUES (?, ?, ?, ?, ?, ?, now()) "
            "ON CONFLICT (path) DO UPDATE SET "
            "mtime=excluded.mtime, size=excluded.size, row_count=excluded.row_count, "
            "status=excluded.status, error=excluded.error, last_attempt=now()",
            [path_str, stat.st_mtime, stat.st_size, row_count, status, error],
        )

    table = read_ipc_batches(fpath)
    if table is None:
        warnings.warn(f"Skipping unreadable IPC file: {fpath.name}", stacklevel=2)
        _mark("quarantined", "no valid batches")
        return

    try:
        table = table.select(_EVENT_COLUMNS)
    except Exception as exc:
        warnings.warn(f"Skipping bad schema in {fpath.name}: {exc}", stacklevel=2)
        _mark("quarantined", str(exc))
        return

    try:
        rows = _table_to_rows(table)
        conn.executemany(_INSERT_SQL, rows)
        _mark("ok", row_count=len(rows))
    except Exception as exc:
        warnings.warn(f"Skipping bad data in {fpath.name}: {exc}", stacklevel=2)
        _mark("quarantined", str(exc))


# ── Daemon entry point ───────────────────────────────────────────────


def daemon_run(events_dir: Path) -> None:
    """Entry point for the daemon process. Blocks until idle timeout."""
    mgr = DuckDBDaemonManager(events_dir)

    index_path = events_dir / "_index.duckdb"
    conn = _open_index(index_path)

    # Start Flight server for cross-process queries and inserts.
    server = DuckDBFlightServer("grpc://127.0.0.1:0")
    server.register("events", conn)

    def _events_put_hook(table: pa.Table) -> None:
        # Tuple-bind path: safe with large strings (register+INSERT segfaults).
        rows = _table_to_rows(table)
        conn.executemany(_INSERT_SQL, rows)

    server.register_put_hook("events", _events_put_hook)
    location = f"grpc://127.0.0.1:{server.port}"
    port_file = events_dir / "_duckdb_flight_port"
    port_file.write_text(location)
    threading.Thread(target=server.serve, daemon=True, name="duckdb-flight").start()

    # Signal ready BEFORE background ingest. Events are write-heavy and
    # callers emit new events immediately — blocking on a historical replay
    # of potentially hundreds of IPC files would blow the 10 s deadline.
    # The runs daemon inverts this only on a fresh/rebuild start because its
    # first query is typically list_runs(), which needs the index populated.
    mgr.write_ready()
    mgr.update_state(location=location)

    # Ingest IPC files that aren't yet in the index via a background thread.
    threading.Thread(
        target=_ingest_ipc_files,
        args=(index_path, events_dir),
        daemon=True,
        name="duckdb-ingest",
    ).start()

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
