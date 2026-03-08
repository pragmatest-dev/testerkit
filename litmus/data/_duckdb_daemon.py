"""DuckDB event index daemon.

Spawned as a detached process by ``DuckDBDaemonManager.acquire()``.
Maintains a file-backed DuckDB index (``events/index.duckdb``) that
clients query via read-only connections.

Usage: python -m litmus.data._duckdb_daemon <events_dir>
"""

from __future__ import annotations

import sys
import threading
import warnings
from pathlib import Path

import duckdb

from litmus.data.duckdb_manager import DuckDBDaemonManager, db_path

_INGEST_INTERVAL = 0.5  # seconds between JSONL ingest sweeps


def daemon_run(events_dir: Path) -> None:
    """Entry point for the daemon process. Blocks until idle timeout."""
    mgr = DuckDBDaemonManager(events_dir)

    # Delete any stale index and rebuild fresh from JSONL
    idx = db_path(events_dir)
    idx.unlink(missing_ok=True)
    for suffix in (".wal", ".tmp"):
        p = events_dir / f"index.duckdb{suffix}"
        p.unlink(missing_ok=True)

    conn = duckdb.connect(str(idx))
    conn.execute("""
        CREATE TABLE events (
            id VARCHAR NOT NULL,
            event_type VARCHAR NOT NULL,
            occurred_at VARCHAR NOT NULL,
            received_at VARCHAR,
            session_id VARCHAR,
            run_id VARCHAR,
        )
    """)
    conn.execute("""
        CREATE TABLE _ingested_files (
            file_path VARCHAR PRIMARY KEY
        )
    """)

    # Initial bulk ingest
    _ingest_new_files(conn, events_dir)

    # Signal ready
    mgr.write_ready()

    # Start ingest loop in background
    ingest_stop = threading.Event()
    threading.Thread(
        target=_ingest_loop,
        args=(conn, events_dir, ingest_stop),
        daemon=True,
        name="duckdb-ingest",
    ).start()

    # Block until idle timeout
    mgr.monitor_refs()

    # Shut down
    ingest_stop.set()
    try:
        conn.close()
    except Exception as exc:
        warnings.warn(f"Failed to close DuckDB connection: {exc}", stacklevel=2)

    mgr.cleanup_state_files()


def _ingest_new_files(conn: duckdb.DuckDBPyConnection, events_dir: Path) -> int:
    """Ingest any JSONL files not yet in _ingested_files. Returns count ingested."""
    known = {
        row[0]
        for row in conn.execute("SELECT file_path FROM _ingested_files").fetchall()
    }
    ingested = 0

    for jsonl_file in sorted(events_dir.glob("*/*.jsonl")):
        fkey = str(jsonl_file)
        if fkey in known:
            continue

        if _ingest_one_file(conn, fkey):
            conn.execute("INSERT INTO _ingested_files VALUES (?)", [fkey])
            known.add(fkey)
            ingested += 1

    return ingested


def _ingest_one_file(conn: duckdb.DuckDBPyConnection, fkey: str) -> bool:
    """Ingest a single JSONL file into the events table. Returns True on success."""
    try:
        conn.execute(f"""
            INSERT INTO events BY NAME
            SELECT id, event_type, occurred_at, received_at,
                   session_id, run_id
            FROM read_json_auto('{fkey}',
                 format='newline_delimited',
                 ignore_errors=true,
                 union_by_name=true)
        """)
        return True
    except duckdb.IOException:
        return False
    except duckdb.BinderException as exc:
        warnings.warn(f"Schema mismatch ingesting {fkey}: {exc}", stacklevel=2)
        return False


def _ingest_loop(
    conn: duckdb.DuckDBPyConnection,
    events_dir: Path,
    stop: threading.Event,
) -> None:
    """Periodically scan for new JSONL files and ingest into DuckDB."""
    while not stop.is_set():
        try:
            _ingest_new_files(conn, events_dir)
        except Exception as exc:
            warnings.warn(f"Ingest loop error: {exc}", stacklevel=2)
        stop.wait(timeout=_INGEST_INTERVAL)


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
