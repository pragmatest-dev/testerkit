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
import re
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
from litmus.data._live_runs_subscriber import (
    LiveRunsSubscriber,
    register_empty_inflight,
)
from litmus.data._sql_helpers import sql_escape as _sql_escape
from litmus.data.models import Outcome
from litmus.data.runs_duckdb_manager import RunsDuckDBManager
from litmus.models.enums import Comparator

# Columns whose semantic type is a closed enum (Pydantic StrEnum), not
# a free string. DuckDB ENUM types validate at insert and store as
# int8 — keeps types end-to-end with the data models.

logger = logging.getLogger(__name__)

# ── Schema management ────────────────────────────────────────────────


def _open_index(index_path: Path) -> tuple[duckdb.DuckDBPyConnection, bool]:
    """Open the persistent DuckDB index and ensure schema is current.

    ``is_fresh`` reflects whether the file already existed; the
    caller uses it to decide foreground vs background ingest on
    first launch. The schema itself is always idempotently aligned
    with the code via :func:`_ensure_schema` — no version checks,
    no drop-and-recreate.
    """
    is_fresh = not index_path.exists()
    conn = duckdb.connect(str(index_path))
    _ensure_schema(conn)
    return conn, is_fresh


def _ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Idempotently align the on-disk schema with the code.

    Storage layering:

    - ``runs_persisted`` / ``steps_persisted`` — TABLES populated by
      parquet ingest. ``runs`` / ``steps`` are UNION VIEWS (created
      in :func:`_create_views`) that splice these tables with the
      in-memory ``AccumulatorPool`` snapshot.
    - ``measurements`` — VIEW over the parquet glob. Dynamic
      ``in_*`` / ``out_*`` / ``custom_*`` columns make table
      materialization impractical; aggregates for the hot path live
      in ``measurement_stats``.
    - ``measurement_stats`` — TABLE of per-(file, step, measurement)
      aggregates for cardinality / pareto / Cpk queries.
    - ``measurement_io_schema``, ``measurement_refs`` — secondary
      per-file indexes.
    - ``_ingested`` — TABLE ledger of files seen, for incremental
      sweep. Persistent across launches.

    Idempotent strategy:
    * ``CREATE TABLE IF NOT EXISTS`` for every table — fresh DBs
      get the full current schema; existing DBs are untouched.
    * ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` for every
      column — fresh DBs no-op (column already created), existing
      DBs gain the missing column with NULL for old rows.
    * Same for indexes: ``CREATE INDEX IF NOT EXISTS``.

    Adding a new column = add it to the DDL below. Existing
    ``_index.duckdb`` files migrate automatically on next spawn,
    no re-ingest, no version bump, no special migration code.
    """
    # ENUM types — DuckDB has no CREATE TYPE IF NOT EXISTS. Try
    # to create; treat the "already exists" CatalogException as a
    # no-op. If the enum's value list ever changes, that needs a
    # dedicated migration (drop columns using the type, drop type,
    # recreate, re-add columns) — out of scope for the additive
    # changes this idempotent path supports.
    for type_name, members in (
        ("outcome_kind", Outcome),
        ("comparator_kind", Comparator),
    ):
        values = ", ".join(f"'{m.value}'" for m in members)
        try:
            conn.execute(f"CREATE TYPE {type_name} AS ENUM ({values})")
        except duckdb.CatalogException as exc:
            if "already exists" not in str(exc).lower():
                raise

    # ── runs_persisted ──────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs_persisted (
            run_id VARCHAR PRIMARY KEY,
            file_path VARCHAR,
            steps_file_path VARCHAR,
            session_id VARCHAR,
            slot_id VARCHAR,
            dut_serial VARCHAR,
            dut_part_number VARCHAR,
            dut_lot_number VARCHAR,
            station_id VARCHAR,
            station_name VARCHAR,
            station_hostname VARCHAR,
            fixture_id VARCHAR,
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
    for col, sql_type in _RUNS_PERSISTED_COLUMNS:
        conn.execute(f"ALTER TABLE runs_persisted ADD COLUMN IF NOT EXISTS {col} {sql_type}")

    # ── steps_persisted ─────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS steps_persisted (
            run_id VARCHAR NOT NULL,
            step_index INTEGER NOT NULL,
            file_path VARCHAR,
            session_id VARCHAR,
            slot_id VARCHAR,
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
            station_id VARCHAR,
            PRIMARY KEY (run_id, step_index)
        )
    """)
    for col, sql_type in _STEPS_PERSISTED_COLUMNS:
        conn.execute(f"ALTER TABLE steps_persisted ADD COLUMN IF NOT EXISTS {col} {sql_type}")

    # ── measurement_stats / io_schema / refs ────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurement_stats (
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
        CREATE TABLE IF NOT EXISTS measurement_io_schema (
            file_path VARCHAR NOT NULL,
            step_index INTEGER,
            column_name VARCHAR NOT NULL,
            category VARCHAR NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurement_refs (
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurements_persisted (
            file_path             VARCHAR NOT NULL,
            run_id                VARCHAR,
            session_id            VARCHAR,
            slot_id               VARCHAR,
            run_started_at        TIMESTAMPTZ,
            run_ended_at          TIMESTAMPTZ,
            run_outcome           VARCHAR,
            dut_serial            VARCHAR,
            dut_part_number       VARCHAR,
            dut_revision          VARCHAR,
            dut_lot_number        VARCHAR,
            product_id            VARCHAR,
            station_id            VARCHAR,
            station_hostname      VARCHAR,
            fixture_id            VARCHAR,
            test_phase            VARCHAR,
            project_name          VARCHAR,
            operator_id           VARCHAR,
            step_name             VARCHAR,
            step_index            INTEGER,
            step_path             VARCHAR,
            step_outcome          VARCHAR,
            step_started_at       TIMESTAMPTZ,
            step_ended_at         TIMESTAMPTZ,
            vector_index          BIGINT,
            vector_attempt        BIGINT,
            vector_outcome        VARCHAR,
            measurement_name      VARCHAR,
            measurement_value     DOUBLE,
            measurement_outcome   VARCHAR,
            measurement_units     VARCHAR,
            measurement_timestamp TIMESTAMPTZ,
            limit_low             DOUBLE,
            limit_high            DOUBLE,
            limit_nominal         DOUBLE,
            limit_comparator      VARCHAR,
            characteristic_id     VARCHAR,
            spec_ref              VARCHAR,
            dut_pin               VARCHAR,
            fixture_connection    VARCHAR,
            instrument_name       VARCHAR,
            instrument_resource   VARCHAR,
            instrument_channel    VARCHAR,
            git_commit            VARCHAR,
            git_branch            VARCHAR,
            git_remote            VARCHAR,
            python_version        VARCHAR,
            litmus_version        VARCHAR,
            env_fingerprint       VARCHAR,
            product_name          VARCHAR,
            product_revision      VARCHAR,
            station_name          VARCHAR,
            station_type          VARCHAR,
            station_location      VARCHAR,
            operator_name         VARCHAR,
            dynamic_attrs         MAP(VARCHAR, VARCHAR)
        )
    """)
    for col, sql_type in _MEASUREMENTS_PERSISTED_COLUMNS:
        conn.execute(
            f"ALTER TABLE measurements_persisted ADD COLUMN IF NOT EXISTS {col} {sql_type}"
        )

    # ── indexes ─────────────────────────────────────────────────────
    for index_sql in (
        "CREATE INDEX IF NOT EXISTS idx_runs_run_id ON runs_persisted(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_runs_session ON runs_persisted(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_runs_started ON runs_persisted(started_at)",
        "CREATE INDEX IF NOT EXISTS idx_runs_fp ON runs_persisted(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_steps_run ON steps_persisted(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_steps_fp ON steps_persisted(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_meas_name ON measurement_stats(measurement_name)",
        "CREATE INDEX IF NOT EXISTS idx_meas_run ON measurement_stats(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_meas_fp ON measurement_stats(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_mrefs_name ON measurement_refs(measurement_name)",
        "CREATE INDEX IF NOT EXISTS idx_mrefs_session ON measurement_refs(session_short)",
        "CREATE INDEX IF NOT EXISTS idx_mio_fp ON measurement_io_schema(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_mp_fp   ON measurements_persisted(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_mp_run  ON measurements_persisted(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_mp_name ON measurements_persisted(measurement_name)",
    ):
        conn.execute(index_sql)


# Columns that should exist on ``runs_persisted`` / ``steps_persisted``
# regardless of when the on-disk DB was created. ``CREATE TABLE IF NOT
# EXISTS`` covers the fresh case; ``ALTER TABLE ADD COLUMN IF NOT
# EXISTS`` (driven from these lists) covers the upgrade case where an
# older DB is missing a column added since.
_RUNS_PERSISTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "VARCHAR"),
    ("file_path", "VARCHAR"),
    ("steps_file_path", "VARCHAR"),
    ("session_id", "VARCHAR"),
    ("slot_id", "VARCHAR"),
    ("dut_serial", "VARCHAR"),
    ("dut_part_number", "VARCHAR"),
    ("dut_lot_number", "VARCHAR"),
    ("station_id", "VARCHAR"),
    ("station_name", "VARCHAR"),
    ("station_hostname", "VARCHAR"),
    ("fixture_id", "VARCHAR"),
    ("outcome", "outcome_kind"),
    ("started_at", "TIMESTAMPTZ"),
    ("ended_at", "TIMESTAMPTZ"),
    ("num_measurements", "INTEGER"),
    ("num_steps", "INTEGER"),
    ("test_phase", "VARCHAR"),
    ("product_id", "VARCHAR"),
    ("operator_id", "VARCHAR"),
    ("project_name", "VARCHAR"),
)
_STEPS_PERSISTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "VARCHAR"),
    ("step_index", "INTEGER"),
    ("file_path", "VARCHAR"),
    ("session_id", "VARCHAR"),
    ("slot_id", "VARCHAR"),
    ("step_name", "VARCHAR"),
    ("step_path", "VARCHAR"),
    ("outcome", "outcome_kind"),
    ("started_at", "TIMESTAMPTZ"),
    ("ended_at", "TIMESTAMPTZ"),
    ("duration_s", "DOUBLE"),
    ("has_measurements", "BOOLEAN"),
    ("measurement_count", "INTEGER"),
    ("vector_count", "INTEGER"),
    ("markers", "VARCHAR"),
    ("dut_serial", "VARCHAR"),
    ("station_id", "VARCHAR"),
)

_MEASUREMENTS_PERSISTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("file_path", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("session_id", "VARCHAR"),
    ("slot_id", "VARCHAR"),
    ("run_started_at", "TIMESTAMPTZ"),
    ("run_ended_at", "TIMESTAMPTZ"),
    ("run_outcome", "VARCHAR"),
    ("dut_serial", "VARCHAR"),
    ("dut_part_number", "VARCHAR"),
    ("dut_revision", "VARCHAR"),
    ("dut_lot_number", "VARCHAR"),
    ("product_id", "VARCHAR"),
    ("product_name", "VARCHAR"),
    ("product_revision", "VARCHAR"),
    ("station_id", "VARCHAR"),
    ("station_name", "VARCHAR"),
    ("station_hostname", "VARCHAR"),
    ("station_type", "VARCHAR"),
    ("station_location", "VARCHAR"),
    ("fixture_id", "VARCHAR"),
    ("test_phase", "VARCHAR"),
    ("project_name", "VARCHAR"),
    ("operator_id", "VARCHAR"),
    ("operator_name", "VARCHAR"),
    ("git_commit", "VARCHAR"),
    ("git_branch", "VARCHAR"),
    ("git_remote", "VARCHAR"),
    ("python_version", "VARCHAR"),
    ("litmus_version", "VARCHAR"),
    ("env_fingerprint", "VARCHAR"),
    ("step_name", "VARCHAR"),
    ("step_index", "INTEGER"),
    ("step_path", "VARCHAR"),
    ("step_outcome", "VARCHAR"),
    ("step_started_at", "TIMESTAMPTZ"),
    ("step_ended_at", "TIMESTAMPTZ"),
    ("vector_index", "BIGINT"),
    ("vector_attempt", "BIGINT"),
    ("vector_outcome", "VARCHAR"),
    ("measurement_name", "VARCHAR"),
    ("measurement_value", "DOUBLE"),
    ("measurement_outcome", "VARCHAR"),
    ("measurement_units", "VARCHAR"),
    ("measurement_timestamp", "TIMESTAMPTZ"),
    ("limit_low", "DOUBLE"),
    ("limit_high", "DOUBLE"),
    ("limit_nominal", "DOUBLE"),
    ("limit_comparator", "VARCHAR"),
    ("characteristic_id", "VARCHAR"),
    ("spec_ref", "VARCHAR"),
    ("dut_pin", "VARCHAR"),
    ("fixture_connection", "VARCHAR"),
    ("instrument_name", "VARCHAR"),
    ("instrument_resource", "VARCHAR"),
    ("instrument_channel", "VARCHAR"),
    ("dynamic_attrs", "MAP(VARCHAR, VARCHAR)"),
)


# ── Ingest helpers ──────────────────────────────────────────────────


def _is_steps_file(filename: str) -> bool:
    """Return True if *filename* is a steps parquet sidecar."""
    return filename.endswith("_steps.parquet")


def _file_list_sql(paths: list[str]) -> str:
    """Build a DuckDB list literal from file paths."""
    return "[" + ", ".join(f"'{_sql_escape(p)}'" for p in paths) + "]"


def _parquet_columns(conn: duckdb.DuckDBPyConnection, path: str) -> set[str]:
    """Return TOP-LEVEL column names present in a parquet file.

    Uses DESCRIBE rather than parquet_schema() — the latter returns nested
    sub-field names (``element``, ``list``, ``key``, ``value``) for array/map
    typed columns, which are not valid column references in a SELECT clause.
    DESCRIBE returns only the top-level column names as DuckDB sees them.
    """
    escaped = _sql_escape(path)
    return {
        r[0]
        for r in conn.execute(
            f"DESCRIBE (SELECT * FROM read_parquet('{escaped}') LIMIT 0)"
        ).fetchall()
    }


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
    "measurements_persisted",
)


def _delete_file_rows(conn: duckdb.DuckDBPyConnection, path_str: str) -> None:
    """Delete rows associated with a vanished parquet file from all tables.

    Measurement parquets land in the per-file index tables keyed by
    ``file_path`` (including ``measurements_persisted``); the matching
    ``_steps.parquet`` lands in ``runs`` / ``steps`` keyed by
    ``steps_file_path`` / ``file_path`` respectively.
    """
    is_steps = _is_steps_file(Path(path_str).name)
    if is_steps:
        # `runs_persisted` rows reference the steps file path under
        # `steps_file_path`; `steps_persisted` rows reference it as
        # `file_path`.
        conn.execute("DELETE FROM runs_persisted WHERE steps_file_path = ?", [path_str])
        conn.execute("DELETE FROM steps_persisted WHERE file_path = ?", [path_str])
    else:
        for table in _INDEX_TABLES_BY_FILE_PATH:
            conn.execute(f"DELETE FROM {table} WHERE file_path = ?", [path_str])
    conn.execute("DELETE FROM _ingested WHERE path = ?", [path_str])


# ── Bulk ingest ─────────────────────────────────────────────────────


_OPTIONAL_MEAS_LIMITS = ("measurement_units", "limit_low", "limit_high", "limit_nominal")

# Fixed column names that go directly into measurements_persisted as
# named columns. Any parquet column NOT in this set and not in
# _MEAS_SKIP_COLS gets packed into the dynamic_attrs MAP(VARCHAR,VARCHAR).
_MEAS_FIXED_COLS: frozenset[str] = frozenset(
    {
        "run_id",
        "session_id",
        "slot_id",
        "run_started_at",
        "run_ended_at",
        "run_outcome",
        "dut_serial",
        "dut_part_number",
        "dut_revision",
        "dut_lot_number",
        "product_id",
        "product_name",
        "product_revision",
        "station_id",
        "station_name",
        "station_hostname",
        "station_type",
        "station_location",
        "fixture_id",
        "test_phase",
        "project_name",
        "operator_id",
        "operator_name",
        "git_commit",
        "git_branch",
        "git_remote",
        "python_version",
        "litmus_version",
        "env_fingerprint",
        "step_name",
        "step_index",
        "step_path",
        "step_outcome",
        "step_started_at",
        "step_ended_at",
        "vector_index",
        "vector_attempt",
        "vector_outcome",
        "measurement_name",
        "measurement_value",
        "measurement_outcome",
        "measurement_units",
        "measurement_timestamp",
        "limit_low",
        "limit_high",
        "limit_nominal",
        "limit_comparator",
        "characteristic_id",
        "spec_ref",
        "dut_pin",
        "fixture_connection",
        "instrument_name",
        "instrument_resource",
        "instrument_channel",
    }
)
# Instrument array columns — complex multi-valued types not stored in
# the MAP (they're available via steps/runs for instrument detail queries).
_MEAS_SKIP_COLS: frozenset[str] = frozenset(
    {
        "step_instruments_cal_certificate",
        "step_instruments_cal_due",
        "step_instruments_cal_lab",
        "step_instruments_cal_last",
        "step_instruments_driver",
        "step_instruments_firmware",
        "step_instruments_id",
        "step_instruments_manufacturer",
        "step_instruments_mocked",
        "step_instruments_model",
        "step_instruments_name",
        "step_instruments_protocol",
        "step_instruments_resource",
        "step_instruments_serial",
    }
)


def _meas_fixed_select(available: set[str]) -> str:
    """Build the fixed-column SELECT fragment for measurement inserts."""
    return ", ".join(c if c in available else f"NULL AS {c}" for c in sorted(_MEAS_FIXED_COLS))


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

    # ``INSERT BY NAME`` matches SELECT output column names to
    # destination column names — aliases are load-bearing. A
    # missing / misspelled / misordered alias becomes a SQL error
    # at INSERT time, not silently miscolumned data.
    conn.execute(f"""
        INSERT INTO measurement_stats BY NAME
        SELECT
            filename AS file_path,
            run_id,
            session_id,
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


def _bulk_insert_measurement_rows(conn: duckdb.DuckDBPyConnection, fkey: str) -> None:
    """Insert raw measurement rows from one parquet into ``measurements_persisted``.

    Fixed columns go to named columns. Dynamic (in_/out_/custom_) columns
    are packed into ``dynamic_attrs MAP(VARCHAR, VARCHAR)``. One-time cost
    at ingest; all subsequent queries hit the native table at ~1ms instead
    of re-scanning all parquet footers (which cost 150–500ms per query due
    to DuckDB's ``union_by_name`` footer-read during planning).
    """
    available = _parquet_columns(conn, fkey)
    # Any column not in the fixed schema and not in the skip set goes
    # into the MAP — this captures in_*/out_*/custom_* prefixed columns
    # AND non-prefixed custom columns (e.g. "value", "units", "nominal").
    dynamic_present = sorted(
        c for c in available if c not in _MEAS_FIXED_COLS and c not in _MEAS_SKIP_COLS
    )

    # Build SELECT list for fixed columns — NULL-coalesce any absent in
    # this (possibly older) parquet so INSERT BY NAME always has every col.
    fixed_select = _meas_fixed_select(available)

    if dynamic_present:
        keys_sql = ", ".join(f"'{_sql_escape(c)}'" for c in dynamic_present)
        vals_sql = ", ".join(f"TRY_CAST({c} AS VARCHAR)" for c in dynamic_present)
        map_expr = f"MAP([{keys_sql}], [{vals_sql}])"
    else:
        map_expr = "MAP(ARRAY[]::VARCHAR[], ARRAY[]::VARCHAR[])"

    escaped = _sql_escape(fkey)
    # DELETE first so re-ingest is idempotent (mirrors ON CONFLICT DO UPDATE
    # for runs/steps but at file granularity since measurement rows have no
    # single-column unique key across files).
    conn.execute("DELETE FROM measurements_persisted WHERE file_path = ?", [fkey])
    conn.execute(f"""
        INSERT INTO measurements_persisted BY NAME
        SELECT
            '{escaped}' AS file_path,
            {fixed_select},
            {map_expr} AS dynamic_attrs
        FROM read_parquet('{escaped}', union_by_name=true)
        WHERE measurement_name IS NOT NULL
    """)


def _bulk_insert_runs(conn: duckdb.DuckDBPyConnection, steps_paths: list[str]) -> None:
    """Populate the ``runs`` TABLE from ``_steps.parquet`` files.

    Steps carries every run-level field we need (``run_outcome``,
    ``run_started_at``, ``run_ended_at``, denormalized run context),
    plus per-step ``measurement_count`` we sum into ``num_measurements``.
    Cheaper than scanning the (large) measurements parquet for the
    same aggregation.

    ``file_path`` is NULL'd out for runs whose derived measurements
    parquet doesn't exist on disk. That happens for runs that emitted
    only setup / action steps (or had everything skipped) — the
    streaming subscriber writes ``_steps.parquet`` unconditionally
    but only writes the measurements parquet when there are
    measurement rows. The runs row should still appear (with its
    step rollup); the UI shouldn't try to load a phantom file.
    """
    # Resolve which derived measurements paths actually exist on disk
    # so the runs row carries an honest ``file_path`` (or NULL).
    # Explicit string schema — pyarrow infers null-type columns when
    # every value is None, which DuckDB then can't join cleanly.
    filenames: list[str] = []
    meas_paths: list[str | None] = []
    for sp in steps_paths:
        derived = re.sub(r"_steps\.parquet$", ".parquet", sp)
        filenames.append(sp)
        meas_paths.append(derived if Path(derived).exists() else None)
    existence_table = pa.table(
        {"filename": filenames, "measurements_path": meas_paths},
        schema=pa.schema(
            [
                ("filename", pa.string()),
                ("measurements_path", pa.string()),
            ]
        ),
    )
    conn.register("_meas_existence", existence_table)

    flist = _file_list_sql(steps_paths)
    try:
        # ``INSERT BY NAME`` matches SELECT aliases to destination
        # column names — a misnamed alias is a SQL error rather than
        # a silent miscolumned row. ``ON CONFLICT (run_id)`` covers
        # re-ingest of the same parquet path; in-flight rows live
        # in the in-memory accumulator pool, not in this table.
        conn.execute(f"""
            INSERT INTO runs_persisted BY NAME
            SELECT
                s.run_id AS run_id,
                ANY_VALUE(e.measurements_path) AS file_path,
                s.filename AS steps_file_path,
                s.session_id AS session_id,
                ANY_VALUE(s.slot_id) AS slot_id,
                ANY_VALUE(s.dut_serial) AS dut_serial,
                ANY_VALUE(s.dut_part_number) AS dut_part_number,
                ANY_VALUE(s.dut_lot_number) AS dut_lot_number,
                ANY_VALUE(s.station_id) AS station_id,
                ANY_VALUE(s.station_name) AS station_name,
                ANY_VALUE(s.station_hostname) AS station_hostname,
                ANY_VALUE(s.fixture_id) AS fixture_id,
                ANY_VALUE(s.run_outcome) AS outcome,
                ANY_VALUE(s.run_started_at) AS started_at,
                ANY_VALUE(s.run_ended_at) AS ended_at,
                CAST(SUM(s.measurement_count) AS INTEGER) AS num_measurements,
                CAST(COUNT(*) AS INTEGER) AS num_steps,
                ANY_VALUE(s.test_phase) AS test_phase,
                ANY_VALUE(s.product_id) AS product_id,
                ANY_VALUE(s.operator_id) AS operator_id,
                ANY_VALUE(s.project_name) AS project_name
            FROM read_parquet({flist}, filename=true, union_by_name=true) s
            LEFT JOIN _meas_existence e ON s.filename = e.filename
            WHERE s.run_id IS NOT NULL
            GROUP BY s.filename, s.run_id, s.session_id
            ON CONFLICT (run_id) DO UPDATE SET
                file_path = excluded.file_path,
                steps_file_path = excluded.steps_file_path,
                session_id = excluded.session_id,
                slot_id = excluded.slot_id,
                dut_serial = excluded.dut_serial,
                dut_part_number = excluded.dut_part_number,
                dut_lot_number = excluded.dut_lot_number,
                station_id = excluded.station_id,
                station_name = excluded.station_name,
                station_hostname = excluded.station_hostname,
                fixture_id = excluded.fixture_id,
                outcome = excluded.outcome,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at,
                num_measurements = excluded.num_measurements,
                num_steps = excluded.num_steps,
                test_phase = excluded.test_phase,
                product_id = excluded.product_id,
                operator_id = excluded.operator_id,
                project_name = excluded.project_name
        """)
    finally:
        conn.unregister("_meas_existence")


def _bulk_insert_steps(conn: duckdb.DuckDBPyConnection, steps_paths: list[str]) -> None:
    """Populate the ``steps_persisted`` TABLE from ``_steps.parquet`` files.

    The ``ON CONFLICT`` is for re-ingest of the same parquet path
    (e.g. on schema rebuild); in-flight rows live in the in-memory
    accumulator pool, not in this table.
    """
    flist = _file_list_sql(steps_paths)
    conn.execute(f"""
        INSERT INTO steps_persisted BY NAME
        SELECT
            run_id AS run_id,
            "index" AS step_index,
            filename AS file_path,
            session_id AS session_id,
            slot_id AS slot_id,
            name AS step_name,
            step_path AS step_path,
            outcome AS outcome,
            started_at AS started_at,
            ended_at AS ended_at,
            duration_s AS duration_s,
            has_measurements AS has_measurements,
            measurement_count AS measurement_count,
            vector_count AS vector_count,
            markers AS markers,
            dut_serial AS dut_serial,
            station_id AS station_id
        FROM read_parquet({flist}, filename=true, union_by_name=true)
        ON CONFLICT (run_id, step_index) DO UPDATE SET
            file_path = excluded.file_path,
            session_id = excluded.session_id,
            slot_id = excluded.slot_id,
            step_name = excluded.step_name,
            step_path = excluded.step_path,
            outcome = excluded.outcome,
            started_at = excluded.started_at,
            ended_at = excluded.ended_at,
            duration_s = excluded.duration_s,
            has_measurements = excluded.has_measurements,
            measurement_count = excluded.measurement_count,
            vector_count = excluded.vector_count,
            markers = excluded.markers,
            dut_serial = excluded.dut_serial,
            station_id = excluded.station_id
    """)


# ── Background ingest ────────────────────────────────────────────────


def _ingest_parquet_files(
    conn: duckdb.DuckDBPyConnection,
    runs_dir: Path,
    lock: threading.Lock,
) -> None:
    """Ingest new/changed parquet files into the runs index, newest first.

    Uses the daemon's main DuckDB connection — protected by ``lock`` —
    so all DuckDB writes (Flight queries, ingest, _on_put) are
    serialized through one connection. This eliminates the catalog-lock
    deadlock that occurred when the background ingest opened its own
    connection and competed with the Flight server's pre_query_hook
    on DuckDB's global catalog lock.

    Per-file: each ``_ingest_one_file`` acquires the lock, ingests one
    file, releases. Flight queries get the lock between files (~30ms
    slots); during fresh-install ingest (1100 files, ~30s) queries see
    bounded latency, no hangs.

    Order: newest mtime first. The most recent runs are what operators
    actually want to see; old data backfills behind. If the daemon
    idle-shuts-down mid-ingest, the next spawn picks up where we left
    off via the ``_ingested`` ledger.
    """
    disk_entries: list[tuple[str, float, int, os.stat_result]] = []
    for pq_file in runs_dir.rglob("*.parquet"):
        if pq_file.name.endswith(".tmp.parquet"):
            continue
        try:
            stat = pq_file.stat()
            disk_entries.append((str(pq_file), stat.st_mtime, stat.st_size, stat))
        except OSError:
            continue

    if not disk_entries:
        return

    # Read _ingested under the lock — short read, no contention.
    with lock:
        ingested_keys: set[tuple[str, float, int]] = {
            (row[0], row[1], row[2])
            for row in conn.execute("SELECT path, mtime, size FROM _ingested").fetchall()
        }
    needs_ingest = sorted(
        (e for e in disk_entries if (e[0], e[1], e[2]) not in ingested_keys),
        key=lambda e: e[1],  # sort by mtime
        reverse=True,  # newest first so operators see recent runs fast
    )

    # Per-file ingest — release the lock between files so Flight
    # queries can interleave (~30ms slots per file).
    # Collect newly-indexed measurement files for the batch insert below.
    # Steps/runs are already inserted per-file via _index_steps_file (fast,
    # ~30ms each); measurement rows are batched to keep lock holds short.
    new_meas_files: list[str] = []
    for path_str, _, _, stat in needs_ingest:
        with lock:
            _ingest_one_file(conn, Path(path_str), stat)
        if not _is_steps_file(Path(path_str).name):
            new_meas_files.append(path_str)

    # Batch insert measurement rows — one lock hold per 100 files instead of
    # N × (read parquet + insert) per file. Empty in steady state → no-op.
    _MEAS_BATCH = 100
    for i in range(0, len(new_meas_files), _MEAS_BATCH):
        batch = new_meas_files[i : i + _MEAS_BATCH]
        try:
            with lock:
                _batch_insert_measurement_rows(conn, batch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Batch measurement insert failed (%d files): %s", len(batch), exc)

    # Cascade-delete rows whose source parquet is gone from disk.
    disk_paths = {e[0] for e in disk_entries}
    with lock:
        gone = [
            row[0]
            for row in conn.execute("SELECT path FROM _ingested WHERE status = 'ok'").fetchall()
            if row[0] not in disk_paths
        ]
        for path_str in gone:
            _delete_file_rows(conn, path_str)
            warnings.warn(
                f"Indexed run file gone from disk: {Path(path_str).name}",
                stacklevel=2,
            )

        # Flush WAL → main file so next daemon start opens instantly
        # without replaying a large WAL.
        try:
            conn.execute("CHECKPOINT")
        except Exception:  # noqa: BLE001 — best-effort
            pass


def _ingest_one_file(
    conn: duckdb.DuckDBPyConnection,
    fpath: Path,
    stat: os.stat_result,
) -> None:
    """Ingest a single parquet file. Used by ``_on_put`` for real-time notifications.

    Measurement parquets populate ``measurement_stats`` and the IO/ref
    indexes. Step sidecars (``*_steps.parquet``) populate ``runs`` and
    ``steps``.

    Idempotent: if ``_ingested`` already records this file with a
    matching (mtime, size) and ``ok`` status, skip re-insert. Without
    this guard, a fresh daemon would double-insert when ``_pre_ready``
    ingests existing files and a near-simultaneous ``notify_new_run``
    fires ``_on_put`` for the same files.
    """
    path_str = str(fpath)
    already = conn.execute(
        "SELECT 1 FROM _ingested WHERE path = ? AND mtime = ? AND size = ? AND status = 'ok'",
        [path_str, stat.st_mtime, stat.st_size],
    ).fetchone()
    if already:
        return

    if _is_steps_file(fpath.name):
        error = _index_steps_file(conn, path_str)
    else:
        error = _index_parquet_file(conn, path_str)
    _mark_ingested(conn, path_str, stat, "ok" if error is None else "quarantined", error)


_LEGACY_OUTCOME_HINT = (
    "Outcome value is not in the canonical Outcome enum "
    "(passed/failed/skipped/errored/terminated/aborted/done). "
    "This parquet was written by an older Litmus version. "
    "Either delete the file or re-run the test."
)


def _quarantine_message(fkey: str, exc: Exception) -> str:
    """Format a single human-readable quarantine reason for the daemon log.

    Pulls the error class + message into one line so an operator can grep
    `_daemon.log` for ``Quarantined parquet`` and immediately see which
    file failed and why. Calls out legacy-outcome hints specifically since
    that's the most-common cause across the upgrade boundary.
    """
    msg = str(exc)
    body = f"Quarantined parquet {fkey}: {type(exc).__name__}: {msg}"
    if "Could not convert string" in msg and "outcome" in msg.lower():
        body += f"\n  → {_LEGACY_OUTCOME_HINT}"
    return body


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
        warnings.warn(_quarantine_message(fkey, exc), stacklevel=2)
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
        # File gone during ingest (will retry next run) — transient, not a quarantine.
        logger.debug("File gone during ingest (will retry next run): %s — %s", fkey, exc)
        return f"file unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001 — per-file ingest tolerance: warn + skip
        warnings.warn(_quarantine_message(fkey, exc), stacklevel=2)
        return str(exc)


# ── Read-side views over parquet ────────────────────────────────────


def _create_views(conn: duckdb.DuckDBPyConnection, _runs_dir: Path) -> None:
    """Create or replace the runtime views over the index tables.

    All three data views follow the same UNION pattern: persistent rows
    from the on-disk tables UNION ALL in-flight rows from the AccumulatorPool.

    * ``runs`` / ``steps`` / ``measurements``: persisted TABLE rows UNION ALL
      inflight Arrow snapshots, with finalized rows suppressed from the
      inflight side so the parquet always wins once ingested.
    * ``measurements_persisted`` stores raw measurement rows materialized from
      parquet during ingest (O(1) query instead of O(n_files) parquet glob).
    * ``inflight_measurements`` carries the current live measurement snapshot
      so a running test's measurements are visible immediately, completing the
      Run → Step → Measurements live hierarchy.
    """
    # measurements: persistent TABLE + inflight live snapshot.
    # UNION BY NAME matches columns by name rather than position, so the
    # inflight schema doesn't need to list columns in exactly the same order
    # as measurements_persisted. Columns absent from the inflight side
    # (file_path, dynamic_attrs) are automatically NULL.
    conn.execute("""
        CREATE OR REPLACE VIEW measurements AS
        SELECT * FROM measurements_persisted
        UNION BY NAME
        SELECT
            run_id, session_id, slot_id,
            run_started_at, run_ended_at, run_outcome,
            dut_serial, dut_part_number, dut_revision, dut_lot_number,
            product_id, product_name, product_revision,
            station_id, station_name, station_hostname, station_type, station_location,
            fixture_id, test_phase, project_name, operator_id, operator_name,
            git_commit, git_branch, git_remote,
            python_version, litmus_version, env_fingerprint,
            step_name, step_index, step_path, step_outcome,
            step_started_at, step_ended_at,
            vector_index, vector_attempt, vector_outcome,
            measurement_name, measurement_value, measurement_outcome,
            measurement_units, measurement_timestamp,
            limit_low, limit_high, limit_nominal, limit_comparator,
            characteristic_id, spec_ref, dut_pin, fixture_connection,
            instrument_name, instrument_resource, instrument_channel
        FROM inflight_measurements
        WHERE run_id NOT IN (
            SELECT DISTINCT run_id FROM measurements_persisted
            WHERE run_id IS NOT NULL
        )
    """)

    # ``runs`` and ``steps`` are UNION views: persistent rows from
    # the on-disk tables (parquet ingest) UNION ALL in-flight rows
    # from the in-memory temp tables (refreshed from the
    # ``AccumulatorPool`` via the Flight server's pre-query hook).
    # Suppress in-flight rows whose ``run_id`` already has a
    # finalized parquet — parquet has won and the in-flight
    # projection is stale.
    conn.execute("""
        CREATE OR REPLACE VIEW runs AS
        SELECT * FROM runs_persisted
        UNION ALL
        SELECT
            run_id, file_path, steps_file_path, session_id, slot_id,
            dut_serial, dut_part_number, dut_lot_number, station_id, station_name,
            station_hostname, fixture_id,
            TRY_CAST(outcome AS outcome_kind) AS outcome,
            started_at, ended_at,
            num_measurements, num_steps, test_phase, product_id,
            operator_id, project_name
        FROM inflight_runs
        WHERE run_id NOT IN (SELECT run_id FROM runs_persisted)
    """)
    conn.execute("""
        CREATE OR REPLACE VIEW steps AS
        SELECT * FROM steps_persisted
        UNION ALL
        SELECT
            run_id, step_index, file_path, session_id, slot_id,
            step_name, step_path,
            TRY_CAST(outcome AS outcome_kind) AS outcome,
            started_at, ended_at,
            duration_s, has_measurements, measurement_count, vector_count,
            markers, dut_serial, station_id
        FROM inflight_steps
        WHERE run_id NOT IN (SELECT run_id FROM runs_persisted)
    """)


# Inflight TEMP-table setup + materialization moved into
# :class:`~litmus.data._live_runs_subscriber.LiveRunsSubscriber`.


def _batch_insert_measurement_rows(
    conn: duckdb.DuckDBPyConnection,
    paths: list[str],
) -> None:
    """Insert measurement rows for all *paths* in a single SQL statement.

    Used by the background ingest sweep to insert measurement rows for a
    batch of newly-discovered files. One lock hold per batch instead of
    N × (parquet-read + insert). Idempotent: DELETEs existing rows for
    each file before inserting so re-ingest is safe (mirrors ON CONFLICT
    DO UPDATE semantics for runs/steps, at file granularity).

    Dynamic column names come from a DESCRIBE on the actual unioned batch
    relation — using ``measurement_io_schema`` directly would reference columns
    that exist in *some* file in the project but not in any file in *this* batch,
    yielding a Binder error.
    """
    flist = "[" + ", ".join(f"'{_sql_escape(p)}'" for p in paths) + "]"

    # Remove any existing rows for these files (idempotent re-ingest)
    placeholders = ", ".join("?" for _ in paths)
    conn.execute(f"DELETE FROM measurements_persisted WHERE file_path IN ({placeholders})", paths)

    # Columns actually present across this batch's parquets (union by name).
    available = {
        r[0]
        for r in conn.execute(
            f"DESCRIBE (SELECT * FROM read_parquet({flist}, union_by_name=true) LIMIT 0)"
        ).fetchall()
    }
    dynamic_cols = sorted(
        c for c in available if c not in _MEAS_FIXED_COLS and c not in _MEAS_SKIP_COLS
    )

    # NULL-coalesce fixed columns absent from every file in the batch so
    # INSERT BY NAME always has every fixed column.
    fixed_select = _meas_fixed_select(available)

    if dynamic_cols:
        keys_sql = ", ".join(f"'{_sql_escape(c)}'" for c in dynamic_cols)
        vals_sql = ", ".join(f"TRY_CAST({c} AS VARCHAR)" for c in dynamic_cols)
        map_expr = f"MAP([{keys_sql}], [{vals_sql}])"
    else:
        map_expr = "MAP(ARRAY[]::VARCHAR[], ARRAY[]::VARCHAR[])"

    conn.execute(f"""
        INSERT INTO measurements_persisted BY NAME
        SELECT
            filename AS file_path,
            {fixed_select},
            {map_expr} AS dynamic_attrs
        FROM read_parquet({flist}, union_by_name=true, filename=true)
        WHERE measurement_name IS NOT NULL
    """)


def daemon_run(runs_dir: Path) -> None:
    """Entry point for the runs daemon process. Blocks until idle timeout.

    Ready-ordering: signal ready immediately, ingest in the
    background. Queries during ingest see partial data, with newest
    runs first (``_ingest_parquet_files`` orders by ``mtime DESC``)
    so the most operationally relevant rows surface fastest.
    """
    mgr = RunsDuckDBManager(runs_dir)

    index_path = runs_dir / "_index.duckdb"
    conn, _ = _open_index(index_path)

    # Single shared lock — serializes all DuckDB operations on the
    # daemon's main connection (Flight queries, _on_put writes,
    # background ingest, pre_query_hook conn.register). Eliminates
    # the catalog-lock deadlock that occurred when the background
    # ingest opened its own connection and competed with the Flight
    # server's pre_query_hook on DuckDB's global catalog under GIL
    # contention.
    write_lock = threading.Lock()

    # Live-runs subscriber — owns the events-daemon attach loop,
    # the per-run accumulator pool, the inflight-table
    # materialization, and the orphan sweep.
    live_subscriber = LiveRunsSubscriber(runs_dir.parent)

    # Bind ``inflight_runs`` / ``inflight_steps`` to empty Arrow
    # tables so the UNION views in ``_create_views`` can compile.
    register_empty_inflight(conn)
    _create_views(conn, runs_dir)

    def _on_put(table: pa.Table) -> None:
        # Already runs under the Flight server's lock (which IS our
        # ``write_lock``), so direct conn access is safe here.
        # Real-time delivery path: insert measurement rows immediately so
        # clients see them as soon as the ACK is drained.
        for row in table.to_pylist():
            fpath = row.get("file_path", "")
            if not fpath:
                continue
            try:
                stat = Path(fpath).stat()
            except OSError:
                continue
            _ingest_one_file(conn, Path(fpath), stat)
            if not _is_steps_file(Path(fpath).name):
                try:
                    _bulk_insert_measurement_rows(conn, fpath)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("measurement row insert failed for %s: %s", fpath, exc)
        _create_views(conn, runs_dir)

    server, port_file, *_ = start_flight_server_in_daemon(
        mgr=mgr,
        daemon_dir=runs_dir,
        db_name="runs",
        conn=conn,
        put_hook=_on_put,
        port_file_name="_runs_duckdb_flight_port",
        thread_name="runs-duckdb-flight",
        pre_ready=None,
        pre_query_hook=live_subscriber.refresh,
        lock=write_lock,
    )

    # Background sweep — picks up parquets that exist on disk but
    # weren't delivered via Flight ``do_put`` (fresh installs,
    # daemon-was-down recovery). Per-file ingest under ``write_lock``
    # alternates with Flight queries, no deadlock.
    threading.Thread(
        target=_ingest_parquet_files,
        args=(conn, runs_dir, write_lock),
        daemon=True,
        name="runs-ingest",
    ).start()

    live_subscriber.start()

    mgr.monitor_refs()

    live_subscriber.stop()

    shutdown_flight_server_in_daemon(server, port_file, conn)
    mgr.cleanup_state_files()


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
