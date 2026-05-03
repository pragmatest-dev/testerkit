"""DuckDB run index daemon.

Spawned as a detached process by ``RunsDuckDBManager.acquire()``.
Maintains a persistent DuckDB index rebuilt incrementally from parquet files.
Clients push new runs via ``do_put`` and query via ``do_get``.

Startup is O(new files since last run): the daemon opens the existing
``_index.duckdb``, signals ready immediately, then ingests only files not
yet recorded in the ``_ingested`` table via a background thread.

Architectural rule: every storage shape that callers can query is a
**precomputed TABLE**, not a view. Views over ``read_parquet(glob)``
pay per-file footer overhead on every query (~80μs/file) — at 1k
files that's a 80ms floor; at 100k files, 8s. Tables read DuckDB's
columnar storage at constant cost regardless of file count.

API consumers can issue any aggregation or filter combination, so
we can't rely on caller discipline. Tables are the only safe answer.

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

from litmus.data._duckdb_flight_server import (
    shutdown_flight_server_in_daemon,
    start_flight_server_in_daemon,
)
from litmus.data._sql_helpers import sql_escape as _sql_escape
from litmus.data.models import Outcome
from litmus.data.runs_duckdb_manager import RunsDuckDBManager
from litmus.models.enums import Comparator

# Columns whose semantic type is a closed enum (Pydantic StrEnum), not
# a free string. DuckDB ENUM types validate at insert and store as
# int8 — keeps types end-to-end with the data models.

logger = logging.getLogger(__name__)

# Bump on schema-incompatible changes. v2: runs/steps tables restored
# (were views in v1.5/Step B), measurements promoted to a real TABLE.
_SCHEMA_VERSION = 2


def _create_enum_types(conn: duckdb.DuckDBPyConnection) -> None:
    """Create DuckDB ENUM types mirroring the Pydantic StrEnums.

    Used by the ``runs`` and ``steps`` tables for the ``outcome``
    column. Mirrors :class:`litmus.data.models.Outcome` and
    :class:`litmus.models.enums.Comparator` so the type list updates
    automatically when those enums change.

    Note: parquet on disk stores these as plain strings (pyarrow
    doesn't have a portable ENUM type with universal ecosystem
    support). The Python Pydantic enum is the source of truth;
    DuckDB validates at INSERT time as a defense-in-depth.
    """
    for type_name in ("outcome_kind", "comparator_kind"):
        conn.execute(f"DROP TYPE IF EXISTS {type_name}")
    outcome_values = ", ".join(f"'{m.value}'" for m in Outcome)
    comparator_values = ", ".join(f"'{m.value}'" for m in Comparator)
    conn.execute(f"CREATE TYPE outcome_kind AS ENUM ({outcome_values})")
    conn.execute(f"CREATE TYPE comparator_kind AS ENUM ({comparator_values})")


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
    """Drop all managed tables and recreate with current schema.

    Storage layering:

    - ``runs`` — TABLE, one row per run, populated from
      ``_steps.parquet`` at ingest. Hot path for ``RunStore.list_runs``.
    - ``steps`` — TABLE, one row per step, populated from
      ``_steps.parquet``. Hot path for ``RunStore.get_steps``.
    - ``measurements`` — VIEW over the parquet glob (created in
      :func:`_create_views`). Stays a view because dynamic
      ``in_*`` / ``out_*`` / ``custom_*`` columns vary per test;
      materializing would require ALTER-on-first-sight or JSON
      extras. The scaling answer is parquet compaction (fewer
      larger files), tracked in ROADMAP.
    - ``measurement_stats`` — per-(file, step, measurement_name)
      aggregates for cardinality / pareto queries.
    - ``measurement_io_schema``, ``measurement_refs`` — secondary
      per-file indexes.
    - ``_ingested`` — ledger of files seen, for incremental sweep.
    """
    for tbl in (
        "runs",
        "steps",
        "measurement_stats",
        "measurement_io_schema",
        "measurement_refs",
        "_ingested",
        "_schema_version",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    # Drop legacy view names from earlier (Step B) daemon versions
    # so a stale ``_index.duckdb`` doesn't conflict on table create.
    for view in ("runs", "steps", "measurements"):
        conn.execute(f"DROP VIEW IF EXISTS {view}")

    _create_enum_types(conn)

    conn.execute("CREATE TABLE _schema_version (v INTEGER PRIMARY KEY)")
    conn.execute(f"INSERT INTO _schema_version VALUES ({_SCHEMA_VERSION})")

    conn.execute("""
        CREATE TABLE runs (
            file_path VARCHAR NOT NULL,
            steps_file_path VARCHAR,
            run_id VARCHAR,
            session_id VARCHAR,
            dut_serial VARCHAR,
            dut_part_number VARCHAR,
            station_id VARCHAR,
            station_name VARCHAR,
            outcome outcome_kind,
            started_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            num_measurements INTEGER,
            num_steps INTEGER,
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
            outcome outcome_kind,
            started_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            duration_s DOUBLE,
            has_measurements BOOLEAN,
            measurement_count INTEGER,
            vector_count INTEGER,
            markers VARCHAR,
            dut_serial VARCHAR,
            station_id VARCHAR
        )
    """)
    # ``measurements`` stays a VIEW over the parquet glob (created
    # in :func:`_create_views`) — promoting it to a table would mean
    # dynamic-column ALTERs and/or row-level JSON for in_*/out_*
    # columns that vary per test. Compaction (fewer larger parquet
    # files) is the right scaling answer; tracked in ROADMAP.

    conn.execute("""
        CREATE TABLE measurement_stats (
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

    conn.execute("CREATE INDEX idx_runs_run_id ON runs(run_id)")
    conn.execute("CREATE INDEX idx_runs_session ON runs(session_id)")
    conn.execute("CREATE INDEX idx_runs_started ON runs(started_at)")
    conn.execute("CREATE INDEX idx_runs_fp ON runs(file_path)")
    conn.execute("CREATE INDEX idx_steps_run ON steps(run_id)")
    conn.execute("CREATE INDEX idx_steps_fp ON steps(file_path)")
    conn.execute("CREATE INDEX idx_meas_name ON measurement_stats(measurement_name)")
    conn.execute("CREATE INDEX idx_meas_run ON measurement_stats(run_id)")
    conn.execute("CREATE INDEX idx_meas_fp ON measurement_stats(file_path)")
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
    except Exception as exc:  # noqa: BLE001 — per-file ingest tolerance: warn + skip
        warnings.warn(f"Error indexing io/refs for {fkey}: {exc}", stacklevel=2)
        return str(exc)


# ── Cascade delete when a parquet file vanishes ─────────────────────

_INDEX_TABLES_BY_FILE_PATH = (
    "measurement_stats",
    "measurement_io_schema",
    "measurement_refs",
)


def _delete_file_rows(conn: duckdb.DuckDBPyConnection, path_str: str) -> None:
    """Delete rows associated with a vanished parquet file from all tables.

    The ``measurements`` view re-reads parquet glob on every query, so
    nothing to delete there — the file is just gone from the glob.
    Measurement parquets land in the per-file index tables keyed by
    ``file_path``; the matching `_steps.parquet` lands in `runs` /
    `steps` keyed by ``steps_file_path`` / ``file_path`` respectively.
    """
    is_steps = _is_steps_file(Path(path_str).name)
    if is_steps:
        # `runs` rows reference the steps file path under
        # `steps_file_path`; `steps` rows reference it as `file_path`.
        conn.execute("DELETE FROM runs WHERE steps_file_path = ?", [path_str])
        conn.execute("DELETE FROM steps WHERE file_path = ?", [path_str])
    else:
        for table in _INDEX_TABLES_BY_FILE_PATH:
            conn.execute(f"DELETE FROM {table} WHERE file_path = ?", [path_str])
    conn.execute("DELETE FROM _ingested WHERE path = ?", [path_str])


# ── Bulk ingest ─────────────────────────────────────────────────────


_OPTIONAL_MEAS_LIMITS = ("measurement_units", "limit_low", "limit_high", "limit_nominal")


def _bulk_insert_measurements(conn: duckdb.DuckDBPyConnection, meas_paths: list[str]) -> None:
    """Bulk INSERT per-(file, step, measurement_name) aggregates into ``measurement_stats``.

    The raw ``measurements`` view reads parquet on every query — this
    table is the precomputed aggregate side used by analytics queries
    that don't need raw values (yield, pareto, distinct measurement
    names per file, etc.).
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
        "SUM(CASE WHEN measurement_outcome = 'passed' THEN 1 ELSE 0 END)" if has_outcome else "0"
    )
    fail_expr = (
        "SUM(CASE WHEN measurement_outcome = 'failed' THEN 1 ELSE 0 END)" if has_outcome else "0"
    )
    min_expr = "MIN(measurement_value)" if has_value else "NULL"
    max_expr = "MAX(measurement_value)" if has_value else "NULL"
    avg_expr = "AVG(measurement_value)" if has_value else "NULL"

    conn.execute(f"""
        INSERT INTO measurement_stats
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


def _bulk_insert_runs(conn: duckdb.DuckDBPyConnection, steps_paths: list[str]) -> None:
    """Populate the ``runs`` TABLE from ``_steps.parquet`` files.

    Steps carries every run-level field we need (``run_outcome``,
    ``run_started_at``, ``run_ended_at``, denormalized run context),
    plus per-step ``measurement_count`` we sum into ``num_measurements``.
    Cheaper than scanning the (large) measurements parquet for the
    same aggregation.
    """
    flist = _file_list_sql(steps_paths)
    conn.execute(f"""
        INSERT INTO runs
        SELECT
            regexp_replace(filename, '_steps\\.parquet$', '.parquet') AS file_path,
            filename AS steps_file_path,
            run_id,
            CAST(session_id AS VARCHAR) AS session_id,
            ANY_VALUE(dut_serial) AS dut_serial,
            ANY_VALUE(dut_part_number) AS dut_part_number,
            ANY_VALUE(station_id) AS station_id,
            ANY_VALUE(station_name) AS station_name,
            ANY_VALUE(run_outcome) AS outcome,
            ANY_VALUE(run_started_at) AS started_at,
            ANY_VALUE(run_ended_at) AS ended_at,
            CAST(SUM(measurement_count) AS INTEGER) AS num_measurements,
            CAST(COUNT(*) AS INTEGER) AS num_steps,
            ANY_VALUE(test_phase) AS test_phase,
            ANY_VALUE(product_id) AS product_id,
            ANY_VALUE(operator_id) AS operator_id,
            ANY_VALUE(project_name) AS project_name
        FROM read_parquet({flist}, filename=true, union_by_name=true)
        WHERE run_id IS NOT NULL
        GROUP BY filename, run_id, session_id
    """)


def _bulk_insert_steps(conn: duckdb.DuckDBPyConnection, steps_paths: list[str]) -> None:
    """Populate the ``steps`` TABLE from ``_steps.parquet`` files."""
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
            started_at,
            ended_at,
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
                        _bulk_insert_measurements(conn, meas_paths)
                    if steps_paths:
                        _bulk_insert_runs(conn, steps_paths)
                        _bulk_insert_steps(conn, steps_paths)
                    conn.commit()
                except Exception as exc:  # noqa: BLE001 — bulk ingest fallback to per-file
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

        # Cascade-delete rows whose source parquet is gone from disk.
        # Each ``_delete_file_rows`` call also removes the ``_ingested``
        # row so the file isn't re-processed on the next sweep.
        for (path_str,) in gone:
            _delete_file_rows(conn, path_str)
            warnings.warn(f"Indexed run file gone from disk: {Path(path_str).name}", stacklevel=2)

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
    """Ingest a single parquet file. Used by ``_on_put`` for real-time notifications.

    Measurement parquets populate ``measurement_stats`` and the IO/ref
    indexes. Step sidecars (``*_steps.parquet``) populate ``runs`` and
    ``steps``.
    """
    path_str = str(fpath)

    if _is_steps_file(fpath.name):
        error = _index_steps_file(conn, path_str)
    else:
        error = _index_parquet_file(conn, path_str)
    _mark_ingested(conn, path_str, stat, "ok" if error is None else "quarantined", error)


def _index_steps_file(conn: duckdb.DuckDBPyConnection, fkey: str) -> str | None:
    """Index a single ``_steps.parquet`` into the ``runs`` and ``steps`` tables."""
    try:
        _bulk_insert_runs(conn, [fkey])
        _bulk_insert_steps(conn, [fkey])
        return None
    except duckdb.IOException as exc:
        logger.debug("Steps file gone during ingest (will retry next run): %s — %s", fkey, exc)
        return f"file unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001 — per-file ingest tolerance: warn + skip
        warnings.warn(f"Error ingesting steps {fkey}: {exc}", stacklevel=2)
        return str(exc)


def _index_parquet_file(conn: duckdb.DuckDBPyConnection, fkey: str) -> str | None:
    """Index a single measurement parquet file into all index tables.

    Delegates to the bulk insert functions with a single-element list so
    column lists are defined in exactly one place.
    Returns None on success or an error string.
    """
    try:
        _bulk_insert_measurements(conn, [fkey])
        io_error = _index_io_and_refs(conn, fkey)
        if io_error:
            warnings.warn(f"io/refs indexing partial for {fkey}: {io_error}", stacklevel=2)
        return None
    except duckdb.IOException as exc:
        logger.debug("File gone during ingest (will retry next run): %s — %s", fkey, exc)
        return f"file unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001 — per-file ingest tolerance: warn + skip
        warnings.warn(f"Error ingesting {fkey}: {exc}", stacklevel=2)
        return str(exc)


# ── Read-side views over parquet ────────────────────────────────────


def _create_views(conn: duckdb.DuckDBPyConnection, runs_dir: Path) -> None:
    """Register the ``measurements`` view over the parquet glob.

    ``measurements`` stays a view because dynamic ``in_*`` / ``out_*``
    / ``custom_*`` columns vary per test — materializing into a table
    would force ALTER-on-first-sight or row-level JSON for the
    dynamic columns. Per-file footer overhead is the actual scaling
    cost; parquet compaction (fewer larger files) is the proper fix
    and is tracked in ROADMAP.

    ``runs`` and ``steps`` are TABLES (see :func:`_rebuild_schema`)
    populated incrementally at ingest — they're hot-path query
    targets and need constant-cost lookups.

    If no parquet files exist yet, the view is deferred — it gets
    created after the first file is ingested via ``_on_put``.
    """
    meas_glob = _sql_escape(str(runs_dir / "**" / "*.parquet"))
    try:
        conn.execute(f"""
            CREATE OR REPLACE VIEW measurements AS
            SELECT * FROM read_parquet('{meas_glob}',
                union_by_name=true, filename=true)
            WHERE filename NOT LIKE '%\\_steps.parquet' ESCAPE '\\'
              AND filename NOT LIKE '%\\_ref/%' ESCAPE '\\'
        """)
    except duckdb.IOException:
        logger.debug("No parquet files yet in %s — measurements view deferred", runs_dir)


# ── Daemon entry point ───────────────────────────────────────────────


def daemon_run(runs_dir: Path) -> None:
    """Entry point for the runs daemon process. Blocks until idle timeout.

    Ready-ordering: on a fresh / rebuilt index, run a foreground
    ``_ingest_parquet_files`` BEFORE signalling ready so the first
    query (typically ``list_runs()``) sees a populated index.
    Steady-state startup (existing index) signals ready immediately
    and lets the background thread top up. The events daemon inverts
    this — its queries are write-heavy and latency-sensitive, so it
    always signals ready first.
    """
    mgr = RunsDuckDBManager(runs_dir)

    index_path = runs_dir / "_index.duckdb"
    conn, is_fresh = _open_index(index_path)
    _create_views(conn, runs_dir)

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
        _create_views(conn, runs_dir)

    def _pre_ready() -> None:
        if is_fresh:
            _ingest_parquet_files(index_path, runs_dir)
            _create_views(conn, runs_dir)

    server, port_file, _location = start_flight_server_in_daemon(
        mgr=mgr,
        daemon_dir=runs_dir,
        db_name="runs",
        conn=conn,
        put_hook=_on_put,
        port_file_name="_runs_duckdb_flight_port",
        thread_name="runs-duckdb-flight",
        pre_ready=_pre_ready,
    )

    threading.Thread(
        target=_ingest_parquet_files,
        args=(index_path, runs_dir),
        daemon=True,
        name="runs-ingest",
    ).start()

    mgr.monitor_refs()

    shutdown_flight_server_in_daemon(server, port_file, conn)
    mgr.cleanup_state_files()


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
