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

import logging
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

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


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
        if row is None or row[0] < _SCHEMA_VERSION:
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
            num_measurements INTEGER,
            test_phase VARCHAR,
            product_id VARCHAR,
            operator_id VARCHAR,
            project_name VARCHAR
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
    conn.execute("""
        CREATE TABLE measurements (
            file_path VARCHAR NOT NULL,
            run_id VARCHAR,
            session_id VARCHAR,
            step_index INTEGER,
            step_name VARCHAR,
            measurement_name VARCHAR NOT NULL,
            measurement_units VARCHAR,
            limit_low DOUBLE,
            limit_high DOUBLE,
            limit_nominal DOUBLE,
            count INTEGER NOT NULL,
            pass_count INTEGER NOT NULL,
            fail_count INTEGER NOT NULL,
            min_value DOUBLE,
            max_value DOUBLE,
            mean_value DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE measurement_io_schema (
            file_path VARCHAR NOT NULL,
            step_index INTEGER,
            column_name VARCHAR NOT NULL,
            category VARCHAR NOT NULL
        )
    """)
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

    conn.execute("CREATE INDEX idx_meas_name ON measurements(measurement_name)")
    conn.execute("CREATE INDEX idx_meas_run ON measurements(run_id)")
    conn.execute("CREATE INDEX idx_meas_fp ON measurements(file_path)")
    conn.execute("CREATE INDEX idx_mrefs_name ON measurement_refs(measurement_name)")
    conn.execute("CREATE INDEX idx_mrefs_session ON measurement_refs(session_short)")
    conn.execute("CREATE INDEX idx_mio_fp ON measurement_io_schema(file_path)")


# ── Ingest helpers ──────────────────────────────────────────────────


def _is_steps_file(filename: str) -> bool:
    """Return True if *filename* is a steps parquet sidecar."""
    return filename.endswith("_steps.parquet")


def _file_list_sql(paths: list[str]) -> str:
    """Build a DuckDB list literal from file paths."""
    return "[" + ", ".join(f"'{_sql_escape(p)}'" for p in paths) + "]"


def _parquet_columns(conn: duckdb.DuckDBPyConnection, path: str) -> set[str]:
    """Return column names present in a parquet file."""
    escaped = _sql_escape(path)
    return {r[0] for r in conn.execute(f"SELECT name FROM parquet_schema('{escaped}')").fetchall()}


def _col_or_null(col: str, available: set[str], *, cast: str = "VARCHAR") -> str:
    """SQL expression: CAST(col AS cast) if available, else NULL."""
    if col in available:
        return f"CAST({col} AS {cast})"
    return "NULL"


def _mark_ingested(
    conn: duckdb.DuckDBPyConnection,
    path_str: str,
    stat: os.stat_result,
    status: str,
    error: str | None = None,
) -> None:
    """Record a file's ingest status in _ingested."""
    conn.execute(
        "INSERT INTO _ingested (path, mtime, size, row_count, status, error, last_attempt) "
        "VALUES (?, ?, ?, 0, ?, ?, now()) "
        "ON CONFLICT (path) DO UPDATE SET "
        "mtime=excluded.mtime, size=excluded.size, row_count=excluded.row_count, "
        "status=excluded.status, error=excluded.error, last_attempt=now()",
        [path_str, stat.st_mtime, stat.st_size, status, error],
    )


# ── IO schema SQL (shared by bulk and per-file paths) ───────────────

_IO_SCHEMA_QUERY = """
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
"""


def _index_io_and_refs(conn: duckdb.DuckDBPyConnection, fkey: str) -> str | None:
    """Index measurement_io_schema and measurement_refs for one file.

    Uses UNION ALL to consolidate all column checks into one query each,
    instead of one query per column. Returns None on success.
    """
    escaped = _sql_escape(fkey)
    try:
        schema_rows = conn.execute(_IO_SCHEMA_QUERY.format(escaped=escaped)).fetchall()
        io_cols: list[tuple[str, str]] = [(r[0], r[1]) for r in schema_rows]

        if not io_cols:
            return None

        # io_schema: one UNION ALL query for all columns
        io_parts = []
        for col_name, category in io_cols:
            escaped_col = col_name.replace('"', '""')
            esc_col = _sql_escape(col_name)
            esc_cat = _sql_escape(category)
            io_parts.append(
                f"SELECT DISTINCT step_index, '{esc_col}' AS column_name, '{esc_cat}' AS category "
                f"FROM read_parquet('{escaped}') WHERE \"{escaped_col}\" IS NOT NULL"
            )
        try:
            conn.execute(
                f"""
                INSERT INTO measurement_io_schema
                SELECT ? AS file_path, step_index, column_name, category
                FROM ({" UNION ALL ".join(io_parts)})
            """,
                [fkey],
            )
        except duckdb.Error as exc:
            warnings.warn(f"Could not index I/O schema for {fkey}: {exc}", stacklevel=2)

        # refs: one UNION ALL query for all out_* columns
        out_cols = [c for c, _ in io_cols if c.startswith("out_")]
        if out_cols:
            ref_parts = []
            for col_name in out_cols:
                escaped_col = col_name.replace('"', '""')
                esc_col = _sql_escape(col_name)
                ref_parts.append(f"""
                    SELECT step_index, measurement_name,
                        '{esc_col}' AS col_name,
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
            try:
                conn.execute(
                    f"""
                    INSERT INTO measurement_refs
                    SELECT ? AS file_path, step_index, measurement_name,
                           col_name, row_idx, uri, channel_id, session_short
                    FROM ({" UNION ALL ".join(ref_parts)})
                """,
                    [fkey],
                )
            except duckdb.Error as exc:
                warnings.warn(f"Could not scan refs for {fkey}: {exc}", stacklevel=2)

        return None
    except duckdb.IOException as exc:
        logger.debug("File gone during io/refs ingest: %s — %s", fkey, exc)
        return f"file unavailable: {exc}"
    except Exception as exc:
        warnings.warn(f"Error indexing io/refs for {fkey}: {exc}", stacklevel=2)
        return str(exc)


# ── Bulk ingest (batch of files → 3 queries) ────────────────────────


_OPTIONAL_RUN_COLS = ("test_phase", "product_id", "operator_id", "project_name")


def _bulk_insert_runs(conn: duckdb.DuckDBPyConnection, meas_paths: list[str]) -> None:
    """Bulk INSERT into runs from all measurement files in one query.

    Columns in _OPTIONAL_RUN_COLS are inserted as NULL when absent from the
    parquet files (backwards compat with older schema versions).

    Schema check uses the first file; ``union_by_name=true`` handles column
    differences across files, but NULL logic follows the first file's schema.
    """
    flist = _file_list_sql(meas_paths)
    available = _parquet_columns(conn, meas_paths[0])

    opt_select = ", ".join(f"{_col_or_null(c, available)} AS {c}" for c in _OPTIONAL_RUN_COLS)
    present = [c for c in _OPTIONAL_RUN_COLS if c in available]
    opt_group = (", " + ", ".join(present)) if present else ""

    conn.execute(f"""
        INSERT INTO runs
        SELECT
            filename AS file_path,
            run_id,
            CAST(session_id AS VARCHAR) AS session_id,
            CAST(dut_serial AS VARCHAR) AS dut_serial,
            CAST(station_id AS VARCHAR) AS station_id,
            CAST(run_outcome AS VARCHAR) AS outcome,
            CAST(run_started_at AT TIME ZONE 'UTC' AS VARCHAR) AS started_at,
            COUNT(*) AS num_measurements,
            {opt_select}
        FROM read_parquet({flist}, filename=true, union_by_name=true)
        GROUP BY filename, run_id, session_id, dut_serial, station_id,
                 run_outcome, run_started_at{opt_group}
    """)


_OPTIONAL_MEAS_LIMITS = ("measurement_units", "limit_low", "limit_high", "limit_nominal")


def _bulk_insert_measurements(conn: duckdb.DuckDBPyConnection, meas_paths: list[str]) -> None:
    """Bulk INSERT into measurements from all measurement files in one query.

    Handles missing columns gracefully — older parquet files may lack
    step_name, measurement_units, limits, measurement_outcome, or
    measurement_value.
    """
    flist = _file_list_sql(meas_paths)
    available = _parquet_columns(conn, meas_paths[0])

    opt_group_all = ("step_index", "step_name", *_OPTIONAL_MEAS_LIMITS)
    step_idx_expr = "step_index" if "step_index" in available else "NULL AS step_index"
    step_name_expr = "step_name" if "step_name" in available else "NULL AS step_name"
    limits_select = ", ".join(
        c if c in available else f"NULL AS {c}" for c in _OPTIONAL_MEAS_LIMITS
    )
    opt_group_cols = [c for c in opt_group_all if c in available]
    opt_group = (", " + ", ".join(opt_group_cols)) if opt_group_cols else ""

    has_outcome = "measurement_outcome" in available
    has_value = "measurement_value" in available
    pass_expr = (
        "SUM(CASE WHEN measurement_outcome = 'pass' THEN 1 ELSE 0 END)" if has_outcome else "0"
    )
    fail_expr = (
        "SUM(CASE WHEN measurement_outcome = 'fail' THEN 1 ELSE 0 END)" if has_outcome else "0"
    )
    min_expr = "MIN(measurement_value)" if has_value else "NULL"
    max_expr = "MAX(measurement_value)" if has_value else "NULL"
    avg_expr = "AVG(measurement_value)" if has_value else "NULL"

    conn.execute(f"""
        INSERT INTO measurements
        SELECT
            filename AS file_path,
            run_id,
            CAST(session_id AS VARCHAR) AS session_id,
            {step_idx_expr},
            {step_name_expr},
            measurement_name,
            {limits_select},
            COUNT(*) AS count,
            {pass_expr} AS pass_count,
            {fail_expr} AS fail_count,
            {min_expr} AS min_value,
            {max_expr} AS max_value,
            {avg_expr} AS mean_value
        FROM read_parquet({flist}, filename=true, union_by_name=true)
        WHERE measurement_name IS NOT NULL
        GROUP BY
            filename, run_id, session_id, step_index,
            measurement_name{opt_group}
    """)


def _bulk_insert_steps(conn: duckdb.DuckDBPyConnection, steps_paths: list[str]) -> None:
    """Bulk INSERT into steps from all steps files in one query."""
    flist = _file_list_sql(steps_paths)
    conn.execute(f"""
        INSERT INTO steps
        SELECT
            filename AS file_path,
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
        FROM read_parquet({flist}, filename=true, union_by_name=true)
    """)


# ── Background ingest ────────────────────────────────────────────────


def _ingest_parquet_files(index_path: Path, runs_dir: Path) -> None:
    """Ingest new/changed parquet files into the runs index.

    Phase 1: bulk INSERT runs + measurements + steps (3 queries total).
    Phase 2: per-file io_schema + refs (UNION ALL per file).
    Phase 3: health check for vanished files.

    Opens its own connection so the Flight server's connection is never
    blocked by bulk ingest I/O.
    """
    conn = duckdb.connect(str(index_path))
    try:
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

        disk_table = pa.table(
            {
                "path": [e[0] for e in disk_entries],
                "mtime": pa.array([e[1] for e in disk_entries], type=pa.float64()),
                "size": pa.array([e[2] for e in disk_entries], type=pa.int64()),
            }
        )
        gone: list[tuple] = []
        conn.register("_disk_snapshot", disk_table)
        try:
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

            if needs_ingest:
                stat_map = {e[0]: e[3] for e in disk_entries}
                new_paths = [p for (p,) in needs_ingest]
                meas_paths = [p for p in new_paths if not _is_steps_file(Path(p).name)]
                steps_paths = [p for p in new_paths if _is_steps_file(Path(p).name)]

                try:
                    conn.begin()
                    if meas_paths:
                        _bulk_insert_runs(conn, meas_paths)
                        _bulk_insert_measurements(conn, meas_paths)
                    if steps_paths:
                        _bulk_insert_steps(conn, steps_paths)
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    warnings.warn(
                        f"Bulk ingest failed, falling back to per-file: {exc}", stacklevel=2
                    )
                    for path_str in new_paths:
                        stat = stat_map.get(path_str)
                        if stat is not None:
                            _ingest_one_file(conn, Path(path_str), stat)
                else:
                    for path_str in meas_paths:
                        error = _index_io_and_refs(conn, path_str)
                        stat = stat_map.get(path_str)
                        if stat is not None:
                            _mark_ingested(
                                conn,
                                path_str,
                                stat,
                                "ok" if error is None else "quarantined",
                                error,
                            )
                    for path_str in steps_paths:
                        stat = stat_map.get(path_str)
                        if stat is not None:
                            _mark_ingested(conn, path_str, stat, "ok")

            gone = conn.execute("""
                SELECT i.path FROM _ingested i
                LEFT JOIN _disk_snapshot d ON i.path = d.path
                WHERE i.status = 'ok' AND d.path IS NULL
            """).fetchall()
        finally:
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
    fpath: Path,
    stat: os.stat_result,
) -> None:
    """Ingest a single parquet file. Used by _on_put for real-time notifications."""
    path_str = str(fpath)

    if _is_steps_file(fpath.name):
        error = _index_steps_file(conn, path_str)
    else:
        error = _index_parquet_file(conn, path_str)

    _mark_ingested(conn, path_str, stat, "ok" if error is None else "quarantined", error)


def _index_parquet_file(conn: duckdb.DuckDBPyConnection, fkey: str) -> str | None:
    """Index a single measurement parquet file into all index tables.

    Delegates to the bulk insert functions with a single-element list so
    column lists are defined in exactly one place.
    Returns None on success or an error string.
    """
    try:
        _bulk_insert_runs(conn, [fkey])
        _bulk_insert_measurements(conn, [fkey])
        io_error = _index_io_and_refs(conn, fkey)
        if io_error:
            warnings.warn(f"io/refs indexing partial for {fkey}: {io_error}", stacklevel=2)
        return None
    except duckdb.IOException as exc:
        logger.debug("File gone during ingest (will retry next run): %s — %s", fkey, exc)
        return f"file unavailable: {exc}"
    except Exception as exc:
        warnings.warn(f"Error ingesting {fkey}: {exc}", stacklevel=2)
        return str(exc)


def _index_steps_file(conn: duckdb.DuckDBPyConnection, fkey: str) -> str | None:
    """Index a steps parquet file into the steps table.

    Returns None on success or an error string.
    """
    try:
        _bulk_insert_steps(conn, [fkey])
        return None
    except duckdb.IOException as exc:
        logger.debug("File gone during ingest (will retry next run): %s — %s", fkey, exc)
        return f"file unavailable: {exc}"
    except Exception as exc:
        warnings.warn(f"Error ingesting steps file {fkey}: {exc}", stacklevel=2)
        return str(exc)


# ── Silver view (gold-layer analytics) ───────────────────────────────


def _create_silver_view(conn: duckdb.DuckDBPyConnection, runs_dir: Path) -> None:
    """Register the ``silver`` view for gold-layer analytics.

    Gold queries reference this view instead of reading raw parquet directly,
    keeping the medallion layering: bronze (parquet) → silver (daemon) → gold.

    If no parquet files exist yet, the view is not created — it will be
    created after the first file is ingested via ``_on_put``.
    """
    glob = _sql_escape(str(runs_dir / "**" / "*.parquet"))
    try:
        conn.execute(f"""
            CREATE OR REPLACE VIEW silver AS
            SELECT * FROM read_parquet('{glob}',
                union_by_name=true, filename=true)
            WHERE filename NOT LIKE '%\\_steps.parquet' ESCAPE '\\'
              AND filename NOT LIKE '%\\_ref/%' ESCAPE '\\'
        """)
    except duckdb.IOException:
        logger.debug("No parquet files yet in %s — silver view deferred", runs_dir)


# ── Daemon entry point ───────────────────────────────────────────────


def daemon_run(runs_dir: Path) -> None:
    """Entry point for the daemon process. Blocks until idle timeout."""
    mgr = RunsDuckDBManager(runs_dir)

    index_path = runs_dir / "_index.duckdb"
    conn, is_fresh = _open_index(index_path)
    _create_silver_view(conn, runs_dir)

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
        _create_silver_view(conn, runs_dir)

    server = DuckDBFlightServer("grpc://127.0.0.1:0")
    server.register("runs", conn)
    server.register_put_hook("runs", _on_put)
    location = f"grpc://127.0.0.1:{server.port}"
    port_file = runs_dir / "_runs_duckdb_flight_port"
    port_file.write_text(location)
    threading.Thread(target=server.serve, daemon=True, name="runs-duckdb-flight").start()

    if is_fresh:
        _ingest_parquet_files(index_path, runs_dir)
        _create_silver_view(conn, runs_dir)

    mgr.write_ready()
    mgr.update_state(location=location)

    threading.Thread(
        target=_ingest_parquet_files,
        args=(index_path, runs_dir),
        daemon=True,
        name="runs-ingest",
    ).start()

    mgr.monitor_refs()

    server.shutdown()
    port_file.unlink(missing_ok=True)
    try:
        conn.close()
    except Exception as exc:
        warnings.warn(f"Failed to close DuckDB connection: {exc}", stacklevel=2)

    mgr.cleanup_state_files()


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
