"""DuckDB run index daemon.

Spawned as a detached process by ``RunsDuckDBManager.acquire()``.
Maintains a persistent DuckDB index rebuilt incrementally from parquet files.
Clients push new runs via ``do_put`` and query via ``do_get``.

Startup is O(new files since last run): the daemon opens the existing
``_index.duckdb``, signals ready immediately, then ingests only files not
yet recorded in the ``_ingested`` table via a background thread.

Usage: python -m litmus.data._runs_duckdb_daemon <runs_dir>
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
from litmus.data._sql_helpers import sql_escape as _sql_escape
from litmus.data.runs_duckdb_manager import RunsDuckDBManager

_SCHEMA_VERSION = 2


# ── Schema management ────────────────────────────────────────────────


def _open_index(index_path: Path) -> tuple[duckdb.DuckDBPyConnection, bool]:
    """Open or create the persistent DuckDB index; rebuild on schema mismatch.

    Returns (conn, is_fresh) where is_fresh=True means the schema was just
    rebuilt and a foreground ingest is needed before signaling ready.
    """
    conn = duckdb.connect(str(index_path))
    needs_rebuild = False
    try:
        row = conn.execute("SELECT v FROM _schema_version LIMIT 1").fetchone()
        if row is None or row[0] != _SCHEMA_VERSION:
            warnings.warn("Schema version mismatch — rebuilding run index", stacklevel=2)
            needs_rebuild = True
    except duckdb.Error:
        needs_rebuild = True  # Fresh DB or corrupt — rebuild silently
    if needs_rebuild:
        _rebuild_schema(conn)
    return conn, needs_rebuild


def _rebuild_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Drop all managed tables and recreate with current schema."""
    for tbl in (
        "runs",
        "steps",
        "measurements",
        "measurement_io_schema",
        "measurement_refs",
        "run_channel_refs",  # run_channel_refs: v1 name
        "_ingested",
        "_schema_version",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")

    conn.execute("CREATE TABLE _schema_version (v INTEGER PRIMARY KEY)")
    conn.execute(f"INSERT INTO _schema_version VALUES ({_SCHEMA_VERSION})")

    conn.execute("""
        CREATE TABLE runs (
            file_path VARCHAR NOT NULL,
            run_id VARCHAR,
            session_id VARCHAR,
            dut_serial VARCHAR,
            station_id VARCHAR,
            outcome VARCHAR,
            started_at VARCHAR,
            num_measurements INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE steps (
            file_path VARCHAR NOT NULL,
            run_id VARCHAR,
            session_id VARCHAR,
            step_index INTEGER,
            step_name VARCHAR,
            step_path VARCHAR,
            outcome VARCHAR,
            started_at VARCHAR,
            ended_at VARCHAR,
            duration_s DOUBLE,
            has_measurements BOOLEAN,
            measurement_count INTEGER,
            vector_count INTEGER,
            markers VARCHAR,
            dut_serial VARCHAR,
            station_id VARCHAR
        )
    """)
    # Aggregated per (run × step × measurement_name × limits regime).
    # Limits are in the implicit group key because they can vary per row
    # (condition-varying specs, per-vector overrides, programmatic limits).
    conn.execute("""
        CREATE TABLE measurements (
            file_path VARCHAR NOT NULL,
            run_id VARCHAR,
            session_id VARCHAR,
            step_index INTEGER,
            step_name VARCHAR,
            measurement_name VARCHAR NOT NULL,
            units VARCHAR,
            low_limit DOUBLE,
            high_limit DOUBLE,
            nominal DOUBLE,
            count INTEGER NOT NULL,
            pass_count INTEGER NOT NULL,
            fail_count INTEGER NOT NULL,
            min_value DOUBLE,
            max_value DOUBLE,
            mean_value DOUBLE
        )
    """)
    # Per-step observed I/O column vocabulary (no scalar values — parquet is source).
    conn.execute("""
        CREATE TABLE measurement_io_schema (
            file_path VARCHAR NOT NULL,
            step_index INTEGER,
            column_name VARCHAR NOT NULL,
            category VARCHAR NOT NULL
        )
    """)
    # channel:// URI references found in out_* measurement columns.
    conn.execute("""
        CREATE TABLE measurement_refs (
            file_path VARCHAR NOT NULL,
            step_index INTEGER,
            measurement_name VARCHAR,
            col_name VARCHAR NOT NULL,
            row_idx INTEGER NOT NULL,
            uri VARCHAR NOT NULL,
            channel_id VARCHAR NOT NULL,
            session_short VARCHAR NOT NULL
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

    # Indices for common analytical query patterns.
    conn.execute("CREATE INDEX idx_meas_name ON measurements(measurement_name)")
    conn.execute("CREATE INDEX idx_meas_run ON measurements(run_id)")
    conn.execute("CREATE INDEX idx_meas_fp ON measurements(file_path)")
    conn.execute("CREATE INDEX idx_mrefs_name ON measurement_refs(measurement_name)")
    conn.execute("CREATE INDEX idx_mio_fp ON measurement_io_schema(file_path)")


# ── Background ingest ────────────────────────────────────────────────


def _ingest_parquet_files(index_path: Path, runs_dir: Path) -> None:
    """Background thread: ingest new/changed parquet files into the runs index.

    Uses a single batched anti-join against _ingested (O(1) round trips
    regardless of file count), then runs a health check to mark files that
    have disappeared from disk.

    Opens its own connection so the Flight server's connection is never
    blocked by bulk ingest I/O.
    """
    conn = duckdb.connect(str(index_path))
    try:
        # Walk filesystem once and snapshot (path, mtime, size, stat).
        disk_entries: list[tuple[str, float, int, os.stat_result]] = []
        for pq_file in sorted(runs_dir.rglob("*.parquet")):
            if pq_file.name.endswith(".tmp.parquet"):
                continue
            try:
                stat = pq_file.stat()
                disk_entries.append((str(pq_file), stat.st_mtime, stat.st_size, stat))
            except OSError:
                continue

        if not disk_entries:
            return

        # Register disk snapshot for DuckDB anti-join.
        disk_table = pa.table(
            {
                "path": [e[0] for e in disk_entries],
                "mtime": pa.array([e[1] for e in disk_entries], type=pa.float64()),
                "size": pa.array([e[2] for e in disk_entries], type=pa.int64()),
            }
        )
        conn.register("_disk_snapshot", disk_table)

        # Files that need ingest: absent from _ingested with matching (path, mtime, size, ok).
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

        # Health check: previously-ok files that have vanished.
        gone = conn.execute("""
            SELECT i.path FROM _ingested i
            LEFT JOIN _disk_snapshot d ON i.path = d.path
            WHERE i.status = 'ok' AND d.path IS NULL
        """).fetchall()

        conn.unregister("_disk_snapshot")

        for (path_str,) in gone:
            conn.execute(
                "UPDATE _ingested SET status='missing', last_attempt=now() WHERE path=?",
                [path_str],
            )
            warnings.warn(f"Indexed run file gone from disk: {Path(path_str).name}", stacklevel=2)

    finally:
        try:
            conn.close()
        except Exception as exc:
            warnings.warn(f"Ingest connection close failed: {exc}", stacklevel=2)


def _ingest_one_file(
    conn: duckdb.DuckDBPyConnection,
    pq_file: Path,
    stat: os.stat_result,
) -> None:
    """Ingest a single parquet file. Records status in _ingested."""
    path_str = str(pq_file)

    def _mark(status: str, error: str | None = None, row_count: int = 0) -> None:
        conn.execute(
            "INSERT INTO _ingested (path, mtime, size, row_count, status, error, last_attempt) "
            "VALUES (?, ?, ?, ?, ?, ?, now()) "
            "ON CONFLICT (path) DO UPDATE SET "
            "mtime=excluded.mtime, size=excluded.size, row_count=excluded.row_count, "
            "status=excluded.status, error=excluded.error, last_attempt=now()",
            [path_str, stat.st_mtime, stat.st_size, row_count, status, error],
        )

    if _is_steps_file(pq_file.name):
        ok = _index_steps_file(conn, path_str)
    else:
        ok = _index_parquet_file(conn, path_str)

    _mark("ok" if ok else "quarantined", error=None if ok else "index returned no rows")


# ── Daemon entry point ───────────────────────────────────────────────


def daemon_run(runs_dir: Path) -> None:
    """Entry point for the daemon process. Blocks until idle timeout."""
    mgr = RunsDuckDBManager(runs_dir)

    index_path = runs_dir / "_index.duckdb"
    conn, is_fresh = _open_index(index_path)

    # Custom do_put hook: receives a table with a single "file_path" column
    def _on_put(table: pa.Table) -> None:
        for row in table.to_pylist():
            fpath = row.get("file_path", "")
            if not fpath:
                continue
            try:
                stat = Path(fpath).stat()
            except OSError:
                continue
            _ingest_one_file(conn, Path(fpath), stat)

    # Start Flight server
    server = DuckDBFlightServer("grpc://127.0.0.1:0")
    server.register("runs", conn)
    server.register_put_hook("runs", _on_put)
    location = f"grpc://127.0.0.1:{server.port}"
    port_file = runs_dir / "_runs_duckdb_flight_port"
    port_file.write_text(location)
    threading.Thread(target=server.serve, daemon=True, name="runs-duckdb-flight").start()

    if is_fresh:
        # Fresh index: ingest foreground so the first query sees data.
        # This only happens on first-ever start or schema version bump.
        _ingest_parquet_files(index_path, runs_dir)

    # Signal ready. On incremental restarts this is immediate (existing data
    # is already indexed). On fresh/schema-rebuild starts this comes after
    # foreground ingest completes.
    mgr.write_ready()
    mgr.update_state(location=location)

    # Ingest any new files that arrived since last run.
    threading.Thread(
        target=_ingest_parquet_files,
        args=(index_path, runs_dir),
        daemon=True,
        name="runs-ingest",
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


def _is_steps_file(filename: str) -> bool:
    """Return True if *filename* is a steps parquet sidecar."""
    return filename.endswith("_steps.parquet")


def _index_parquet_file(conn: duckdb.DuckDBPyConnection, fkey: str) -> bool:
    """Index a measurement parquet file into all index tables."""
    escaped = _sql_escape(fkey)
    try:
        # --- runs: one summary row per file ---
        result = conn.execute(f"""
            SELECT
                run_id,
                CAST(session_id AS VARCHAR) AS session_id,
                CAST(dut_serial AS VARCHAR) AS dut_serial,
                CAST(station_id AS VARCHAR) AS station_id,
                CAST(run_outcome AS VARCHAR) AS outcome,
                CAST(run_started_at AT TIME ZONE 'UTC' AS VARCHAR) AS started_at,
                COUNT(*) AS num_measurements
            FROM read_parquet('{escaped}')
            GROUP BY run_id, session_id, dut_serial, station_id, run_outcome, run_started_at
            LIMIT 1
        """).fetchone()

        if result is None:
            return False

        run_id, session_id, dut_serial, station_id, outcome, started_at, num_meas = result
        conn.execute(
            "INSERT INTO runs (file_path, run_id, session_id, dut_serial,"
            " station_id, outcome, started_at, num_measurements)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [fkey, run_id, session_id, dut_serial, station_id, outcome, started_at, num_meas],
        )

        # --- measurements: aggregated per (step × measurement_name × limits regime) ---
        # Limits are in the GROUP BY because they can legitimately vary per row
        # (condition-varying specs, per-vector overrides, programmatic limits).
        try:
            conn.execute(f"""
                INSERT INTO measurements
                SELECT
                    '{escaped}' AS file_path,
                    run_id,
                    CAST(session_id AS VARCHAR) AS session_id,
                    step_index,
                    step_name,
                    measurement_name,
                    units,
                    low_limit,
                    high_limit,
                    nominal,
                    COUNT(*) AS count,
                    SUM(CASE WHEN outcome = 'pass' THEN 1 ELSE 0 END) AS pass_count,
                    SUM(CASE WHEN outcome = 'fail' THEN 1 ELSE 0 END) AS fail_count,
                    MIN(value) AS min_value,
                    MAX(value) AS max_value,
                    AVG(value) AS mean_value
                FROM read_parquet('{escaped}')
                WHERE measurement_name IS NOT NULL
                GROUP BY
                    run_id, session_id, step_index, step_name,
                    measurement_name, units, low_limit, high_limit, nominal
            """)
        except duckdb.Error as exc:
            warnings.warn(f"Could not index measurements for {fkey}: {exc}", stacklevel=2)

        # --- Discover I/O columns (in_*, out_*, custom_*) from parquet schema ---
        # Exclude signal-path suffix columns (instrument/resource/channel/pin) —
        # these carry probe metadata, not measurement values.
        try:
            schema_rows = conn.execute(f"""
                SELECT
                    name,
                    CASE
                        WHEN name LIKE 'in\\_%' ESCAPE '\\' THEN 'input'
                        WHEN name LIKE 'out\\_%' ESCAPE '\\' THEN 'output'
                        ELSE 'custom'
                    END AS category
                FROM parquet_schema('{escaped}')
                WHERE (
                    name LIKE 'in\\_%' ESCAPE '\\'
                    OR name LIKE 'out\\_%' ESCAPE '\\'
                    OR name LIKE 'custom\\_%' ESCAPE '\\'
                )
                AND name NOT LIKE '%\\_instrument' ESCAPE '\\'
                AND name NOT LIKE '%\\_resource' ESCAPE '\\'
                AND name NOT LIKE '%\\_channel' ESCAPE '\\'
                AND name NOT LIKE '%\\_dut\\_pin' ESCAPE '\\'
                AND name NOT LIKE '%\\_fixture\\_point' ESCAPE '\\'
            """).fetchall()
            io_cols: list[tuple[str, str]] = [(r[0], r[1]) for r in schema_rows]
        except duckdb.Error as exc:
            warnings.warn(f"Could not read schema for {fkey}: {exc}", stacklevel=2)
            io_cols = []

        # --- measurement_io_schema: which steps used each I/O column ---
        for col_name, category in io_cols:
            escaped_col = col_name.replace('"', '""')
            esc_col_name = _sql_escape(col_name)
            esc_category = _sql_escape(category)
            try:
                conn.execute(f"""
                    INSERT INTO measurement_io_schema
                    SELECT '{escaped}', step_index, '{esc_col_name}', '{esc_category}'
                    FROM read_parquet('{escaped}')
                    WHERE "{escaped_col}" IS NOT NULL
                    GROUP BY step_index
                """)
            except duckdb.Error as exc:
                warnings.warn(
                    f"Could not index I/O column '{col_name}' in {fkey}: {exc}", stacklevel=2
                )

        # --- measurement_refs: channel:// URIs in out_* columns ---
        # URI parsing done in SQL via regexp_extract to avoid a Python loop.
        out_cols = [col for col, _ in io_cols if col.startswith("out_")]
        for col_name in out_cols:
            escaped_col = col_name.replace('"', '""')
            esc_col_name = _sql_escape(col_name)
            try:
                conn.execute(f"""
                    INSERT INTO measurement_refs
                    SELECT
                        '{escaped}' AS file_path,
                        step_index,
                        measurement_name,
                        '{esc_col_name}' AS col_name,
                        (row_number() OVER ()) - 1 AS row_idx,
                        "{escaped_col}" AS uri,
                        regexp_extract("{escaped_col}", 'channel://([^?]+)', 1) AS channel_id,
                        left(regexp_extract("{escaped_col}", '[?&]session=([^&]+)', 1), 8)
                            AS session_short
                    FROM read_parquet('{escaped}')
                    WHERE "{escaped_col}" IS NOT NULL
                      AND "{escaped_col}" LIKE 'channel://%'
                      AND regexp_extract("{escaped_col}", 'channel://([^?]+)', 1) != ''
                """)
            except duckdb.Error as exc:
                warnings.warn(
                    f"Could not scan refs in '{col_name}' for {fkey}: {exc}", stacklevel=2
                )

        return True
    except Exception as exc:
        if not isinstance(exc, duckdb.IOException):
            warnings.warn(f"Error ingesting {fkey}: {exc}", stacklevel=2)
        return False


def _index_steps_file(conn: duckdb.DuckDBPyConnection, fkey: str) -> bool:
    """Index a steps parquet file into the steps table."""
    escaped = _sql_escape(fkey)
    try:
        rows = conn.execute(f"""
            SELECT
                run_id,
                CAST(session_id AS VARCHAR) AS session_id,
                "index" AS step_index,
                name AS step_name,
                step_path,
                outcome,
                CAST(started_at AT TIME ZONE 'UTC' AS VARCHAR) AS started_at,
                CAST(ended_at AT TIME ZONE 'UTC' AS VARCHAR) AS ended_at,
                duration_s,
                has_measurements,
                measurement_count,
                vector_count,
                markers,
                CAST(dut_serial AS VARCHAR) AS dut_serial,
                CAST(station_id AS VARCHAR) AS station_id
            FROM read_parquet('{escaped}')
        """).fetchall()

        if not rows:
            return False

        conn.executemany(
            "INSERT INTO steps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [[fkey, *row] for row in rows],
        )
        return True
    except Exception as exc:
        if not isinstance(exc, duckdb.IOException):
            warnings.warn(f"Error ingesting steps file {fkey}: {exc}", stacklevel=2)
        return False


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
