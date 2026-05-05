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

from litmus.data._duckdb_flight_server import (
    shutdown_flight_server_in_daemon,
    start_flight_server_in_daemon,
)
from litmus.data._ipc_writer import read_ipc_batches
from litmus.data.duckdb_manager import DuckDBDaemonManager

_EVENT_COLUMNS = ["id", "event_type", "occurred_at", "received_at", "session_id", "run_id", "json"]

# ON CONFLICT DO NOTHING deduplicates events that arrived via do_put during
# the previous session and are now also present in the IPC files.
_INSERT_SQL = (
    "INSERT INTO events (id, event_type, occurred_at, received_at, session_id, run_id, json) "
    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING"
)


def _table_to_rows(table: pa.Table) -> list[tuple]:
    """Convert Arrow table to list of tuples for SQL param insert.

    DuckDB segfaults when inserting Arrow tables with large strings via
    the register/INSERT path.  SQL parameter binding works fine.
    """
    cols = [table.column(c).to_pylist() for c in _EVENT_COLUMNS]
    return list(zip(*cols))


# ── Schema management ────────────────────────────────────────────────


def _open_index(index_path: Path) -> duckdb.DuckDBPyConnection:
    """Open the persistent DuckDB index and idempotently align the schema.

    No version checks, no drop-and-recreate. ``_ensure_schema`` uses
    ``CREATE TABLE IF NOT EXISTS`` and ``ALTER TABLE ADD COLUMN IF NOT
    EXISTS`` so adding a new column to the code below auto-migrates
    existing DBs on next spawn — no re-ingest, no version bump.
    """
    conn = duckdb.connect(str(index_path))
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Idempotently align the on-disk schema with the code.

    Critical: ``ORDER BY received_at DESC LIMIT N`` (the latest-N
    pattern the events page uses) is a full table scan + sort
    without an index on ``received_at``. With ~millions of rows /
    ~GB of data, that's ~10s per query — exactly the page-load
    pain operators hit.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id VARCHAR PRIMARY KEY,
            event_type VARCHAR NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            received_at TIMESTAMPTZ,
            session_id VARCHAR,
            run_id VARCHAR,
            json VARCHAR
        )
    """)
    for col, sql_type in _EVENTS_COLUMNS:
        conn.execute(f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {col} {sql_type}")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _ingested (
            path VARCHAR PRIMARY KEY,
            mtime DOUBLE NOT NULL,
            size BIGINT NOT NULL,
            row_count BIGINT NOT NULL DEFAULT 0,
            status VARCHAR NOT NULL DEFAULT 'ok',
            error VARCHAR,
            last_attempt TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    for index_sql in (
        "CREATE INDEX IF NOT EXISTS idx_events_received_at ON events(received_at)",
        "CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id)",
    ):
        conn.execute(index_sql)


# Columns that should exist on ``events`` regardless of when the
# on-disk DB was created. Drives ``ALTER TABLE ADD COLUMN IF NOT
# EXISTS`` for upgrade migration; ``CREATE TABLE IF NOT EXISTS``
# above covers the fresh case.
_EVENTS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("id", "VARCHAR"),
    ("event_type", "VARCHAR"),
    ("occurred_at", "TIMESTAMPTZ"),
    ("received_at", "TIMESTAMPTZ"),
    ("session_id", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("json", "VARCHAR"),
)


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

        # Skip files already in ``_ingested`` at this exact (mtime,
        # size) regardless of status — quarantined files stay
        # quarantined unless re-written.
        needs_ingest = conn.execute("""
            SELECT d.path
            FROM _disk_snapshot d
            LEFT JOIN _ingested i
                ON d.path = i.path
               AND d.mtime = i.mtime
               AND d.size = i.size
            WHERE i.path IS NULL
        """).fetchall()

        stat_map = {e[0]: e[3] for e in disk_entries}
        for (path_str,) in needs_ingest:
            stat = stat_map.get(path_str)
            if stat is None:
                continue
            try:
                _ingest_one_file(conn, Path(path_str), stat)
            except Exception as exc:  # noqa: BLE001
                # Belt-and-suspenders: ``_ingest_one_file`` already
                # quarantines known bad-data classes, but anything
                # unexpected (e.g. transient FS error) must not kill
                # the ingest thread — that would silently stop ALL
                # future event indexing for the daemon's lifetime.
                warnings.warn(f"Ingest skipped {path_str}: {exc}", stacklevel=2)

        conn.unregister("_disk_snapshot")
    finally:
        try:
            conn.close()
        except Exception as exc:  # noqa: BLE001 — cleanup: best-effort conn close
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
    except (KeyError, pa.ArrowInvalid) as exc:
        warnings.warn(f"Skipping bad schema in {fpath.name}: {exc}", stacklevel=2)
        _mark("quarantined", str(exc))
        return

    try:
        rows = _table_to_rows(table)
        conn.executemany(_INSERT_SQL, rows)
        _mark("ok", row_count=len(rows))
    except (duckdb.Error, UnicodeDecodeError, pa.ArrowException, ValueError) as exc:
        # ``UnicodeDecodeError`` happens when an IPC file has a torn /
        # partial string buffer (mid-write crash, FS bit-flip).
        # ``pa.ArrowException`` covers other arrow decode failures.
        # Quarantining keeps the ingest thread alive — a single bad
        # file can otherwise stop ALL future event ingestion (was
        # observed in pytest suite runs: events daemon's ingest
        # thread died on first 0xff byte, runs daemon's subscriber
        # then saw zero new events for the rest of the session).
        warnings.warn(f"Skipping bad data in {fpath.name}: {exc}", stacklevel=2)
        _mark("quarantined", str(exc))


# ── Daemon entry point ───────────────────────────────────────────────


def daemon_run(events_dir: Path) -> None:
    """Entry point for the events daemon process. Blocks until idle timeout.

    Ready-ordering: signal ready BEFORE background ingest. Events are
    write-heavy and callers emit new events immediately — blocking on
    a historical replay of potentially hundreds of IPC files would
    blow the 10 s spawn deadline. The runs daemon inverts this on a
    fresh/rebuild start because its first query is typically
    ``list_runs()``, which needs the index populated.
    """
    mgr = DuckDBDaemonManager(events_dir)

    index_path = events_dir / "_index.duckdb"
    conn = _open_index(index_path)

    def _events_put_hook(table: pa.Table) -> None:
        # Tuple-bind path: safe with large strings (register+INSERT segfaults).
        rows = _table_to_rows(table)
        conn.executemany(_INSERT_SQL, rows)

    server, port_file, _location = start_flight_server_in_daemon(
        mgr=mgr,
        daemon_dir=events_dir,
        db_name="events",
        conn=conn,
        put_hook=_events_put_hook,
        port_file_name="_duckdb_flight_port",
        thread_name="duckdb-flight",
    )

    # Ingest IPC files that aren't yet in the index via a background thread.
    threading.Thread(
        target=_ingest_ipc_files,
        args=(index_path, events_dir),
        daemon=True,
        name="duckdb-ingest",
    ).start()

    # Block until idle timeout
    mgr.monitor_refs()

    shutdown_flight_server_in_daemon(server, port_file, conn)
    mgr.cleanup_state_files()


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
