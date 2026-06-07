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

import json
import logging
import os
import sys
import threading
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from litmus.data._accumulator_pool import (
    EMPTY_INFLIGHT_MEASUREMENTS,
    EMPTY_INFLIGHT_RUNS,
    EMPTY_INFLIGHT_STEPS,
    INFLIGHT_MEASUREMENTS_SCHEMA,
    INFLIGHT_RUNS_SCHEMA,
    INFLIGHT_STEPS_SCHEMA,
    AccumulatorPool,
    register_empty_inflight,
)
from litmus.data._daemon_lifecycle import _pid_alive
from litmus.data._duckdb_flight_server import (
    shutdown_flight_server_in_daemon,
    start_flight_server_in_daemon,
)
from litmus.data._sql_helpers import sql_escape as _sql_escape
from litmus.data.backends.parquet import materialize_run_to_parquet
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

    - ``runs_materialized`` / ``steps_materialized`` — TABLES populated by
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

    # ── runs_materialized ──────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs_materialized (
            run_id VARCHAR PRIMARY KEY,
            file_path VARCHAR,
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
        conn.execute(f"ALTER TABLE runs_materialized ADD COLUMN IF NOT EXISTS {col} {sql_type}")

    # ── steps_materialized ─────────────────────────────────────────────
    # PK is (run_id, step_path, vector_index) so each (step, vector)
    # execution is its own row. step_index is kept as a sort hint but
    # is no longer part of PK — sweep variants share step_index.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS steps_materialized (
            run_id VARCHAR NOT NULL,
            step_path VARCHAR NOT NULL,
            vector_index BIGINT NOT NULL DEFAULT 0,
            step_index INTEGER,
            file_path VARCHAR,
            session_id VARCHAR,
            slot_id VARCHAR,
            step_name VARCHAR,
            parent_path VARCHAR,
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
            dynamic_attrs MAP(VARCHAR, VARCHAR),
            PRIMARY KEY (run_id, step_path, vector_index)
        )
    """)
    for col, sql_type in _STEPS_PERSISTED_COLUMNS:
        conn.execute(f"ALTER TABLE steps_materialized ADD COLUMN IF NOT EXISTS {col} {sql_type}")

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
            session_short VARCHAR NOT NULL,
            -- Item 1d: full session_id (UUID) is what FileStore.write
            -- needs to scope materialized channel refs into the right
            -- session dir. session_short stays for compatibility with
            -- channel-store path naming (8-char prefix).
            session_id VARCHAR
        )
    """)
    # Item 1d schema migration: pre-1d DuckDB files have a
    # ``measurement_refs`` table without ``session_id``. CREATE TABLE
    # IF NOT EXISTS won't add the column to an existing table — do it
    # explicitly. ALTER TABLE … ADD COLUMN IF NOT EXISTS is a no-op
    # when the column already exists, so this is safe across versions.
    conn.execute("""
        ALTER TABLE measurement_refs ADD COLUMN IF NOT EXISTS session_id VARCHAR
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
        CREATE TABLE IF NOT EXISTS measurements_materialized (
            file_path             VARCHAR NOT NULL,
            record_type           VARCHAR NOT NULL DEFAULT 'measurement',
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
            vector_retry        BIGINT,
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
            f"ALTER TABLE measurements_materialized ADD COLUMN IF NOT EXISTS {col} {sql_type}"
        )

    # ── indexes ─────────────────────────────────────────────────────
    for index_sql in (
        "CREATE INDEX IF NOT EXISTS idx_runs_run_id ON runs_materialized(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_runs_session ON runs_materialized(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_runs_started ON runs_materialized(started_at)",
        "CREATE INDEX IF NOT EXISTS idx_runs_fp ON runs_materialized(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_steps_run ON steps_materialized(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_steps_fp ON steps_materialized(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_meas_name ON measurement_stats(measurement_name)",
        "CREATE INDEX IF NOT EXISTS idx_meas_run ON measurement_stats(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_meas_fp ON measurement_stats(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_mrefs_name ON measurement_refs(measurement_name)",
        "CREATE INDEX IF NOT EXISTS idx_mrefs_session ON measurement_refs(session_short)",
        "CREATE INDEX IF NOT EXISTS idx_mio_fp ON measurement_io_schema(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_mp_fp   ON measurements_materialized(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_mp_run  ON measurements_materialized(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_mp_name ON measurements_materialized(measurement_name)",
    ):
        conn.execute(index_sql)


# Columns that should exist on ``runs_materialized`` / ``steps_materialized``
# regardless of when the on-disk DB was created. ``CREATE TABLE IF NOT
# EXISTS`` covers the fresh case; ``ALTER TABLE ADD COLUMN IF NOT
# EXISTS`` (driven from these lists) covers the upgrade case where an
# older DB is missing a column added since.
_RUNS_PERSISTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "VARCHAR"),
    ("file_path", "VARCHAR"),
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
    # 0-based retry rollup: max(vector_retry) over the step's measurement
    # rows, with COALESCE(..., 0). Reads as the count of retries that
    # actually happened: 0 when the step recorded a non-NULL outcome on
    # its first attempt (or didn't go through the retry loop at all —
    # container steps, action steps with no measurements); N when the
    # step retried N times (i.e. produced measurements at vector_retry
    # values 0..N).
    ("retry_count", "INTEGER"),
    ("markers", "VARCHAR"),
    ("dut_serial", "VARCHAR"),
    ("station_id", "VARCHAR"),
    ("dynamic_attrs", "MAP(VARCHAR, VARCHAR)"),
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
    ("vector_retry", "BIGINT"),
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
                        regexp_extract("{escaped_col}", '[?&]session=([^&]+)', 1)
                            AS session_id,
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
                           col_name, row_idx, uri, channel_id, session_short, session_id
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
    "measurements_materialized",
)


def _delete_file_rows(conn: duckdb.DuckDBPyConnection, path_str: str) -> None:
    """Delete rows associated with a vanished parquet file from all tables.

    The unified per-run parquet is referenced as ``file_path`` in
    every persistent index table (runs / steps / measurements /
    measurement_stats / measurement_io_schema / measurement_refs).
    One DELETE per table is enough; no separate sidecar to clean up.
    """
    conn.execute("DELETE FROM runs_materialized WHERE file_path = ?", [path_str])
    conn.execute("DELETE FROM steps_materialized WHERE file_path = ?", [path_str])
    for table in _INDEX_TABLES_BY_FILE_PATH:
        conn.execute(f"DELETE FROM {table} WHERE file_path = ?", [path_str])
    conn.execute("DELETE FROM _ingested WHERE path = ?", [path_str])


# ── Bulk ingest ─────────────────────────────────────────────────────


_OPTIONAL_MEAS_LIMITS = ("measurement_units", "limit_low", "limit_high", "limit_nominal")

# Fixed column names that go directly into measurements_materialized as
# named columns. Any parquet column NOT in this set and not in
# _MEAS_SKIP_COLS gets packed into the dynamic_attrs MAP(VARCHAR,VARCHAR).
_MEAS_FIXED_COLS: frozenset[str] = frozenset(
    {
        "record_type",
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
        "vector_retry",
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
        WHERE record_type = 'measurement'
        GROUP BY
            filename, run_id, session_id, step_index,
            measurement_name{opt_group}
    """)


def _bulk_insert_measurement_rows(conn: duckdb.DuckDBPyConnection, fkey: str) -> None:
    """Insert raw measurement rows from one parquet into ``measurements_materialized``.

    Fixed columns go to named columns (``INSERT BY NAME`` aligns them
    with ``RUN_ROW_SCHEMA``). Dynamic (in_/out_/custom_) columns are
    packed into ``dynamic_attrs MAP(VARCHAR, VARCHAR)``. One-time cost
    at ingest; all subsequent queries hit the native table at ~1ms
    instead of re-scanning all parquet footers (which cost 150–500ms
    per query due to DuckDB's ``union_by_name`` footer-read during
    planning).
    """
    available = _parquet_columns(conn, fkey)
    # Any column not in the fixed schema and not in the skip set goes
    # into the MAP — this captures in_*/out_*/custom_* prefixed columns
    # AND non-prefixed custom columns (e.g. "value", "units", "nominal").
    dynamic_present = sorted(
        c for c in available if c not in _MEAS_FIXED_COLS and c not in _MEAS_SKIP_COLS
    )

    if dynamic_present:
        keys_sql = ", ".join(f"'{_sql_escape(c)}'" for c in dynamic_present)
        vals_sql = ", ".join(f"TRY_CAST({c} AS VARCHAR)" for c in dynamic_present)
        map_expr = f"MAP([{keys_sql}], [{vals_sql}])"
    else:
        map_expr = "MAP(ARRAY[]::VARCHAR[], ARRAY[]::VARCHAR[])"

    # Fixed columns listed explicitly so INSERT BY NAME has a stable
    # ordering. ``union_by_name=true`` on read_parquet pads any
    # column missing from the file with NULL so we can trust the
    # RUN_ROW_SCHEMA contract here — same pattern as ``_bulk_insert_runs``
    # / ``_bulk_insert_steps``.
    fixed_select = ", ".join(sorted(_MEAS_FIXED_COLS))

    escaped = _sql_escape(fkey)
    # DELETE first so re-ingest is idempotent (mirrors ON CONFLICT DO UPDATE
    # for runs/steps but at file granularity since measurement rows have no
    # single-column unique key across files).
    conn.execute("DELETE FROM measurements_materialized WHERE file_path = ?", [fkey])
    conn.execute(f"""
        INSERT INTO measurements_materialized BY NAME
        SELECT
            '{escaped}' AS file_path,
            {fixed_select},
            {map_expr} AS dynamic_attrs
        FROM read_parquet('{escaped}', union_by_name=true)
        WHERE record_type = 'measurement'
    """)


def _bulk_insert_runs(conn: duckdb.DuckDBPyConnection, parquet_paths: list[str]) -> None:
    """Populate ``runs_materialized`` from the unified per-run parquet files.

    Every parquet conforms to ``RUN_ROW_SCHEMA``. Run-level context is
    denormalized onto every row, so the GROUP BY just lists those
    columns — they're constant within a (filename, run_id) group by
    construction. Aggregates are only the actual rollups
    (``num_measurements``, ``num_steps``).
    """
    flist = _file_list_sql(parquet_paths)
    conn.execute(f"""
        INSERT INTO runs_materialized BY NAME
        SELECT
            run_id,
            filename AS file_path,
            session_id,
            slot_id,
            dut_serial, dut_part_number, dut_lot_number,
            station_id, station_name, station_hostname,
            fixture_id,
            run_outcome AS outcome,
            run_started_at AS started_at,
            run_ended_at AS ended_at,
            CAST(COUNT(*) FILTER (WHERE record_type = 'measurement') AS INTEGER)
                AS num_measurements,
            CAST(COUNT(*) FILTER (WHERE record_type = 'step') AS INTEGER)
                AS num_steps,
            test_phase, product_id, operator_id, project_name
        FROM read_parquet({flist}, filename=true, union_by_name=true)
        WHERE run_id IS NOT NULL
        GROUP BY
            filename, run_id, session_id, slot_id,
            dut_serial, dut_part_number, dut_lot_number,
            station_id, station_name, station_hostname,
            fixture_id,
            run_outcome, run_started_at, run_ended_at,
            test_phase, product_id, operator_id, project_name
        ON CONFLICT (run_id) DO UPDATE SET
            file_path = excluded.file_path,
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


def _bulk_insert_steps(conn: duckdb.DuckDBPyConnection, parquet_paths: list[str]) -> None:
    """Populate ``steps_materialized`` from the unified per-run parquets.

    GROUP BY ``(run_id, step_path, vector_index)`` plus all the
    step-level columns that are denormalized onto every row of a
    given step (step_name, step_outcome, step_started_at, step_ended_at,
    parent_path, step_vector_count, step_markers, …). Aggregates are
    only the actual rollups (``measurement_count``, ``has_measurements``,
    ``duration_s``).
    """
    flist = _file_list_sql(parquet_paths)
    available: set[str] = set()
    for p in parquet_paths:
        available |= _parquet_columns(conn, p)
    dynamic = sorted(c for c in available if c.startswith(("in_", "out_", "custom_")))
    if dynamic:
        keys = ", ".join(f"'{_sql_escape(c)}'" for c in dynamic)
        vals = ", ".join(f"ANY_VALUE(TRY_CAST({c} AS VARCHAR))" for c in dynamic)
        map_expr = f"MAP([{keys}], [{vals}])"
    else:
        map_expr = "MAP(ARRAY[]::VARCHAR[], ARRAY[]::VARCHAR[])"
    conn.execute(f"""
        INSERT INTO steps_materialized BY NAME
        SELECT
            run_id,
            step_path,
            vector_index,
            step_index,
            filename AS file_path,
            session_id,
            slot_id,
            step_name,
            parent_path,
            step_outcome AS outcome,
            step_started_at AS started_at,
            step_ended_at AS ended_at,
            CASE
                WHEN step_ended_at IS NOT NULL AND step_started_at IS NOT NULL
                THEN EPOCH(step_ended_at) - EPOCH(step_started_at)
                ELSE NULL
            END AS duration_s,
            CAST(COUNT(*) FILTER (WHERE record_type = 'measurement') AS INTEGER)
                AS measurement_count,
            (COUNT(*) FILTER (WHERE record_type = 'measurement') > 0)
                AS has_measurements,
            CAST(step_vector_count AS INTEGER) AS vector_count,
            CAST(
                COALESCE(MAX(vector_retry) FILTER (WHERE record_type = 'measurement'), 0)
                AS INTEGER
            ) AS retry_count,
            step_markers AS markers,
            dut_serial,
            station_id,
            {map_expr} AS dynamic_attrs
        FROM read_parquet({flist}, filename=true, union_by_name=true)
        WHERE run_id IS NOT NULL
        GROUP BY
            filename, run_id,
            step_path, vector_index, step_index,
            session_id, slot_id, step_name, parent_path,
            step_outcome, step_started_at, step_ended_at,
            step_vector_count, step_markers,
            dut_serial, station_id
        ON CONFLICT (run_id, step_path, vector_index) DO UPDATE SET
            step_index = excluded.step_index,
            file_path = excluded.file_path,
            session_id = excluded.session_id,
            slot_id = excluded.slot_id,
            step_name = excluded.step_name,
            parent_path = excluded.parent_path,
            outcome = excluded.outcome,
            started_at = excluded.started_at,
            ended_at = excluded.ended_at,
            duration_s = excluded.duration_s,
            has_measurements = excluded.has_measurements,
            measurement_count = excluded.measurement_count,
            vector_count = excluded.vector_count,
            retry_count = excluded.retry_count,
            markers = excluded.markers,
            dut_serial = excluded.dut_serial,
            station_id = excluded.station_id,
            dynamic_attrs = excluded.dynamic_attrs
    """)


# ── Background ingest ────────────────────────────────────────────────


def _ingest_parquet_files(
    conn: duckdb.DuckDBPyConnection,
    runs_dir: Path,
    lock: threading.Lock,
    on_ingested: Callable[[list[str]], None] | None = None,
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
    # _ingest_one_file populates runs / steps / measurement_stats /
    # measurement_io_schema / measurement_refs from the unified
    # parquet; raw measurement rows go into measurements_materialized
    # via the batch insert below to keep per-file lock holds short.
    new_files: list[str] = []
    new_run_ids: list[str] = []
    for path_str, _, _, stat in needs_ingest:
        with lock:
            _ingest_one_file(conn, Path(path_str), stat)
            try:
                rows = conn.execute(
                    "SELECT run_id FROM runs_materialized WHERE file_path = ?",
                    [path_str],
                ).fetchall()
                new_run_ids.extend(str(r[0]) for r in rows if r[0])
            except Exception as exc:  # noqa: BLE001
                logger.debug("run_id lookup after ingest failed: %s", exc)
        new_files.append(path_str)
    if on_ingested is not None and new_run_ids:
        try:
            on_ingested(new_run_ids)
        except Exception as exc:  # noqa: BLE001
            logger.debug("on_ingested callback failed: %s", exc)

    # Batch insert raw measurement rows — one lock hold per 100 files
    # instead of N × (read parquet + insert) per file. Empty in steady
    # state → no-op. Each file's WHERE record_type = 'measurement'
    # filter inside _bulk_insert_measurement_rows skips step-summary
    # rows (which have measurement_name NULL).
    _MEAS_BATCH = 100
    for i in range(0, len(new_files), _MEAS_BATCH):
        batch = new_files[i : i + _MEAS_BATCH]
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
    """Ingest a single unified per-run parquet file.

    Used by ``_on_put`` for real-time notifications. Each parquet
    populates every persistent index in one pass via
    ``_index_unified_parquet`` — runs, steps, measurement_stats, and
    the IO/ref indexes.

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

    error = _index_unified_parquet(conn, path_str)
    _mark_ingested(conn, path_str, stat, "ok" if error is None else "quarantined", error)


def _quarantine_message(fkey: str, exc: Exception) -> str:
    """One-line quarantine reason; an operator can grep ``_daemon.log``
    for ``Quarantined parquet`` and immediately see which file failed
    and why."""
    return f"Quarantined parquet {fkey}: {type(exc).__name__}: {exc}"


def _index_unified_parquet(conn: duckdb.DuckDBPyConnection, fkey: str) -> str | None:
    """Index one unified per-run parquet into runs / steps / measurements tables.

    Runs through every persistent index in one pass:
      * ``runs_materialized`` — one row per ``run_id``, aggregated.
      * ``steps_materialized`` — one row per ``(run_id, step_path,
        vector_index)``, aggregated; sweep variants get distinct rows.
      * ``measurement_stats`` — per-(file, step, name) rollup over
        rows where ``record_type = 'measurement'``.
      * ``measurements_materialized`` — raw measurement rows packed
        with dynamic in_*/out_*/custom_* columns into a MAP.
      * ``measurement_io_schema`` / ``measurement_refs`` — IO schema
        cache + ref-path index for the measurement rows in this file.

    Returns ``None`` on success or an error string when the file
    can't be parsed (the caller marks it quarantined; the operator
    sees the warning and decides what to do).
    """
    try:
        _bulk_insert_runs(conn, [fkey])
        _bulk_insert_steps(conn, [fkey])
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


def _create_views(conn: duckdb.DuckDBPyConnection) -> None:
    """Create or replace the runtime views over the index tables.

    All three data views follow the same UNION pattern: persistent rows
    from the on-disk tables UNION ALL in-flight rows from the AccumulatorPool.

    * ``runs`` / ``steps`` / ``measurements``: persisted TABLE rows UNION ALL
      inflight Arrow snapshots, with finalized rows suppressed from the
      inflight side so the parquet always wins once ingested.
    * ``measurements_materialized`` stores raw measurement rows materialized from
      parquet during ingest (O(1) query instead of O(n_files) parquet glob).
    * ``inflight_measurements`` carries the current live measurement snapshot
      so a running test's measurements are visible immediately, completing the
      Run → Step → Measurements live hierarchy.
    """
    # measurements: persistent TABLE + inflight live snapshot.
    # UNION BY NAME matches columns by name rather than position, so the
    # inflight schema doesn't need to list columns in exactly the same order
    # as measurements_materialized. Columns absent from the inflight side
    # (file_path, dynamic_attrs) are automatically NULL.
    conn.execute("""
        CREATE OR REPLACE VIEW measurements AS
        SELECT * FROM measurements_materialized
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
            vector_index, vector_retry, vector_outcome,
            measurement_name, measurement_value, measurement_outcome,
            measurement_units, measurement_timestamp,
            limit_low, limit_high, limit_nominal, limit_comparator,
            characteristic_id, spec_ref, dut_pin, fixture_connection,
            instrument_name, instrument_resource, instrument_channel,
            dynamic_attrs
        FROM inflight_measurements
        WHERE run_id NOT IN (
            SELECT DISTINCT run_id FROM measurements_materialized
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
        SELECT * FROM runs_materialized
        UNION ALL BY NAME
        SELECT
            run_id, file_path, session_id, slot_id,
            dut_serial, dut_part_number, dut_lot_number, station_id, station_name,
            station_hostname, fixture_id,
            TRY_CAST(outcome AS outcome_kind) AS outcome,
            started_at, ended_at,
            num_measurements, num_steps, test_phase, product_id,
            operator_id, project_name
        FROM inflight_runs
        WHERE run_id NOT IN (SELECT run_id FROM runs_materialized)
    """)
    conn.execute("""
        CREATE OR REPLACE VIEW steps AS
        SELECT * FROM steps_materialized
        UNION ALL BY NAME
        SELECT
            run_id,
            COALESCE(step_path, '') AS step_path,
            vector_index,
            step_index, file_path, session_id, slot_id,
            step_name,
            parent_path,
            TRY_CAST(outcome AS outcome_kind) AS outcome,
            started_at, ended_at,
            duration_s, has_measurements, measurement_count, vector_count,
            markers, dut_serial, station_id,
            dynamic_attrs
        FROM inflight_steps
        WHERE run_id NOT IN (SELECT run_id FROM runs_materialized)
    """)


# Inflight TEMP-table setup + materialization moved into
# the daemon's in-memory accumulator pool.


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
    conn.execute(
        f"DELETE FROM measurements_materialized WHERE file_path IN ({placeholders})",
        paths,
    )

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

    # Fixed columns listed explicitly so INSERT BY NAME has a stable
    # ordering. ``union_by_name=true`` on read_parquet pads any column
    # missing from the batch with NULL, so we trust RUN_ROW_SCHEMA
    # rather than null-coalescing per-column. Same pattern as
    # ``_bulk_insert_runs`` / ``_bulk_insert_steps``.
    fixed_select = ", ".join(sorted(_MEAS_FIXED_COLS))

    if dynamic_cols:
        keys_sql = ", ".join(f"'{_sql_escape(c)}'" for c in dynamic_cols)
        vals_sql = ", ".join(f"TRY_CAST({c} AS VARCHAR)" for c in dynamic_cols)
        map_expr = f"MAP([{keys_sql}], [{vals_sql}])"
    else:
        map_expr = "MAP(ARRAY[]::VARCHAR[], ARRAY[]::VARCHAR[])"

    conn.execute(f"""
        INSERT INTO measurements_materialized BY NAME
        SELECT
            filename AS file_path,
            {fixed_select},
            {map_expr} AS dynamic_attrs
        FROM read_parquet({flist}, union_by_name=true, filename=true)
        WHERE record_type = 'measurement'
    """)


def daemon_run(runs_dir: Path) -> None:
    """Entry point for the runs daemon process. Blocks until idle timeout.

    Architecture (Kafka-Streams shape): the daemon subscribes to the
    EventStore, accumulates per-run state in an in-memory
    :class:`AccumulatorPool`, and on ``RunEnded`` materializes the
    accumulated state to a per-run parquet file, ingests it into the
    local DuckDB index, then emits ``RunMaterialized`` to the events
    bus so any other consumers (the daemon itself, retention, audit)
    learn the run is durable.

    Threads owned by this daemon:

    * Flight server threads — serve ``do_get`` queries against the
      DuckDB index + inflight Arrow snapshots. Pre-query hook
      re-registers the inflight tables when the pool is dirty.
    * Background ingest sweep — picks up parquets on disk that
      pre-date this daemon's lifetime (cold-start recovery; not
      the live write path).
    * Events-attach loop — polls for the events daemon, subscribes
      with ``replay="unmaterialized_runs"`` on first sight.
    * Orphan sweep — every 30s, finalizes runs whose producer pid
      is dead by synthesizing ``RunEnded(outcome="aborted")``. The
      synthetic event flows through the same dispatch path; the
      daemon materializes the run from its in-memory accumulator
      state. No external write path needed for orphan recovery.
    """
    mgr = RunsDuckDBManager(runs_dir)

    index_path = runs_dir / "_index.duckdb"
    conn, _ = _open_index(index_path)

    # Single shared lock — serializes all DuckDB operations on the
    # daemon's main connection (Flight queries, materialize ingest,
    # background recovery, pre_query_hook conn.register). Eliminates
    # the catalog-lock deadlock that occurred when the background
    # ingest opened its own connection and competed with the Flight
    # server's pre_query_hook on DuckDB's global catalog under GIL
    # contention.
    write_lock = threading.Lock()

    # ── Materializer state ──────────────────────────────────────────
    pool = AccumulatorPool()
    # Generation of the pool that the pre-query hook last registered as
    # Arrow tables. Starts at -1 so the first refresh runs even if the
    # pool is empty (registers the empty Arrow tables; UNION views need
    # the registration to compile).
    last_refreshed_generation = [-1]
    stop_event = threading.Event()
    event_store_box: list[Any] = [None]  # set when the attach loop succeeds
    unsubscribe_box: list[Callable[[], None] | None] = [None]
    # Materialization queue — RunEnded events route ``run_id`` strings
    # here so a worker thread handles the slow parquet-write + ingest +
    # emit sequence off the events-dispatch hot path. Without this, the
    # watcher's per-event dispatch holds ``event_store._lock`` while
    # the daemon materializes (~tens of ms per run), starving the
    # watcher loop and letting the events backlog grow under bursty
    # load. Live-runs UI would lag by seconds when many runs finish
    # in close succession.
    import queue as _queue

    materialize_queue: _queue.Queue[tuple[str, str | None]] = _queue.Queue()

    # Bind ``inflight_runs`` / ``inflight_steps`` / ``inflight_measurements``
    # to empty Arrow tables so the UNION views in ``_create_views`` can compile.
    register_empty_inflight(conn)
    _create_views(conn)

    # ── Pre-query hook: refresh inflight Arrow tables from pool ─────
    def _pre_query_refresh(c: duckdb.DuckDBPyConnection) -> None:
        """Re-bind inflight Arrow tables when pool state has changed.

        Flight server's pre-query hook calls this on every ``do_get``.
        Uses :meth:`AccumulatorPool.snapshot_if_changed` which compares
        the pool's monotonic generation against the last-refreshed
        value, under the pool's lock. The atomic check-and-snapshot
        closes the race where a separate dirty-flag could be set
        AFTER the dispatch released its lock — a query that landed
        in that window would skip refresh and serve stale inflight
        tables. Now: generation advances under the same lock as the
        state change, so any reader that sees a newer generation is
        guaranteed to see the corresponding pool state.
        """
        snapshot = pool.snapshot_if_changed(last_refreshed_generation[0])
        if snapshot is None:
            return
        gen, run_rows, step_rows, meas_rows = snapshot
        last_refreshed_generation[0] = gen

        c.register(
            "inflight_runs",
            pa.Table.from_pylist(run_rows, schema=INFLIGHT_RUNS_SCHEMA)
            if run_rows
            else EMPTY_INFLIGHT_RUNS,
        )
        c.register(
            "inflight_steps",
            pa.Table.from_pylist(step_rows, schema=INFLIGHT_STEPS_SCHEMA)
            if step_rows
            else EMPTY_INFLIGHT_STEPS,
        )
        c.register(
            "inflight_measurements",
            pa.Table.from_pylist(meas_rows, schema=INFLIGHT_MEASUREMENTS_SCHEMA)
            if meas_rows
            else EMPTY_INFLIGHT_MEASUREMENTS,
        )

    # ── Materialize one run from the pool ───────────────────────────
    def _materialize_and_emit(run_id: str, outcome: str | None) -> None:
        """Write the run's parquet, ingest it, emit ``RunMaterialized``.

        Called from the materialize worker thread (NOT the event-
        dispatch path). The worker takes ``write_lock`` for the
        ingest section and then acquires ``event_store._lock`` to
        emit ``RunMaterialized``. This ordering is fine because the
        worker only holds ONE lock at a time during the
        ``event_store.emit`` call — write_lock is released before
        the emit happens. (An earlier version of this function
        ran inline in the watcher's dispatch path, holding
        event_store._lock the entire time and inverting the lock
        order against the worker; the deadlock was real and
        observable as the watcher silently stopping under load.)

        Idempotent: if the pool no longer holds an accumulator for
        ``run_id`` (already materialized and evicted), this is a
        no-op.
        """
        acc = pool.get(run_id)
        if acc is None:
            return
        # Diagnostic instrumentation for task #211 (intermittent partial
        # step materialization). When ``LITMUS_RUNS_DAEMON_DEBUG=1``, log
        # the accumulator's step-end count at materialize time + the
        # post-ingest steps_materialized row count, so a discrepancy is
        # visible in the server log if the race fires again. Zero cost
        # in the default path.
        debug_211 = os.environ.get("LITMUS_RUNS_DAEMON_DEBUG") == "1"
        if debug_211:
            logger.warning(
                "[211] materialize start run_id=%s step_ends_in_acc=%d "
                "measurements_in_acc=%d outcome=%s",
                run_id,
                len(acc._step_ends),
                len(acc._measurement_events),
                outcome,
            )
        try:
            parquet_path = materialize_run_to_parquet(acc, runs_dir, outcome=outcome)
        except Exception as exc:  # noqa: BLE001
            logger.warning("materialize_run_to_parquet failed for %s: %s", run_id, exc)
            return
        if parquet_path is None:
            # Nothing to write (no RunStarted seen, or empty run). Still
            # evict so the pool doesn't keep the entry around. Eviction
            # bumps the pool's generation; the next pre-query refresh
            # will pick it up.
            pool.evict(run_id)
            return

        # Ingest the freshly-written parquet into the runs daemon's
        # DuckDB index under write_lock (serialized with Flight queries).
        # Belt-and-suspenders: check ``runs_materialized`` under the
        # same lock; skip if already materialized (daemon-crash-mid-
        # materialize recovery case where replay re-dispatched events
        # for a run whose parquet+index already exists). The replay
        # filter excludes such runs in normal cases.
        try:
            stat = parquet_path.stat()
        except OSError as exc:
            logger.warning("Ingest stat failed for %s: %s", parquet_path, exc)
            return
        with write_lock:
            try:
                already = conn.execute(
                    "SELECT 1 FROM runs_materialized WHERE run_id = ? LIMIT 1",
                    [run_id],
                ).fetchone()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Materialized-guard query failed (non-fatal): %s", exc)
                already = None
            if already is not None:
                pool.evict(run_id)
                return
            try:
                _ingest_one_file(conn, parquet_path, stat)
                _bulk_insert_measurement_rows(conn, str(parquet_path))
                if debug_211:
                    try:
                        row = conn.execute(
                            "SELECT COUNT(*) FROM steps_materialized WHERE run_id = ?",
                            [run_id],
                        ).fetchone()
                        steps_in_db = row[0] if row is not None else -1
                        logger.warning(
                            "[211] materialize done run_id=%s steps_in_db=%d "
                            "(accumulator had %d step_ends — discrepancy = bug)",
                            run_id,
                            steps_in_db,
                            len(acc._step_ends),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[211] post-ingest count query failed: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ingest failed for %s: %s", parquet_path, exc)
                return
            _create_views(conn)

        # Emit RunMaterialized. The in-process subscriber (this daemon)
        # will receive it via ``_on_event`` and evict the pool entry.
        # Cross-process subscribers (retention, future audit) see it via
        # the events-daemon watcher.
        es = event_store_box[0]
        if es is not None and acc._run_started is not None:
            try:
                from litmus.data.events import RunMaterialized

                es.emit(
                    RunMaterialized(
                        session_id=acc._run_started.session_id,
                        run_id=acc._run_started.run_id,
                        materializer="parquet",
                        destination=str(parquet_path),
                        materialized_at=datetime.now(UTC),
                    )
                )
            except Exception as exc:  # noqa: BLE001 — best-effort emit
                logger.warning("RunMaterialized emit failed for %s: %s", run_id, exc)
                # Fall back to direct eviction so the pool doesn't leak.
                pool.evict(run_id)
        else:
            # No events daemon attached (shouldn't happen mid-dispatch,
            # but defensive). Evict directly.
            pool.evict(run_id)

    # ── Event dispatch ──────────────────────────────────────────────
    def _on_event(evt: dict[str, Any]) -> None:
        """Dispatch one event from the EventStore subscription.

        Fast path only — pool dispatch and queue handoff. Materialization
        runs on a separate worker thread (see ``_materialize_worker``)
        so the dispatch loop doesn't serialize on the slow
        parquet-write + DuckDB ingest sequence. Without this split, the
        watcher's per-event ``_dispatch_to_subscribers`` call holds
        ``event_store._lock`` for the duration of materialize (~tens
        of ms), and the events backlog grows faster than it drains
        under burst load — operator UI live-runs would lag by seconds.
        """
        et = evt.get("event_type")
        if et == "run.materialized":
            rid = evt.get("run_id")
            if rid:
                pool.evict(str(rid))
            return

        try:
            pool.dispatch(evt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pool dispatch failed for %s: %s", et, exc)
            return

        # Order-independent materialize trigger: any event for a run
        # whose accumulator now has both ``_run_started`` and
        # ``_run_ended`` set is ready to materialize. Queue it for the
        # worker thread; return immediately so the dispatch loop can
        # process the next event without waiting for the parquet write.
        rid = evt.get("run_id")
        if rid:
            run_id_str = str(rid)
            acc = pool.get(run_id_str)
            if acc is not None and acc._run_started is not None and acc._run_ended is not None:
                materialize_queue.put((run_id_str, acc._run_ended.outcome))

    # ── Materialize worker thread ────────────────────────────────────
    def _materialize_worker() -> None:
        """Drain the materialize queue, materializing one run at a time.

        Decoupled from the events-dispatch path so slow parquet writes
        + DuckDB ingest don't block the watcher loop. Multiple workers
        could be spawned for parallel materialization under high
        concurrency; one is enough for typical workloads (tens of
        finished runs per second is far above hardware-test cadence).
        """
        while not stop_event.is_set():
            try:
                item = materialize_queue.get(timeout=0.5)
            except _queue.Empty:
                continue
            try:
                run_id, outcome = item
                _materialize_and_emit(run_id, outcome)
            except Exception as exc:  # noqa: BLE001
                logger.warning("materialize worker error: %s", exc)
            finally:
                materialize_queue.task_done()

    # ── Events-daemon attach loop ────────────────────────────────────
    def _attach_loop() -> None:
        """Poll for a live events daemon; subscribe on first sight."""
        events_dir = runs_dir.parent / "events"
        while not stop_event.is_set():
            if _events_daemon_alive(events_dir):
                if _try_attach():
                    logger.info("Runs daemon attached to events daemon")
                    return
            stop_event.wait(timeout=0.5)

    def _try_attach() -> bool:
        from litmus.data.event_store import EventStore

        try:
            es = EventStore(_data_dir=runs_dir.parent)
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventStore open failed (will retry): %s", exc)
            return False
        try:
            unsub = es.on_event(_on_event, replay="unmaterialized_runs")
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventStore.on_event failed (will retry): %s", exc)
            try:
                es.close()
            except Exception:  # noqa: BLE001
                pass
            return False
        event_store_box[0] = es
        unsubscribe_box[0] = unsub
        return True

    # ── Orphan sweep ────────────────────────────────────────────────
    def _sweep_loop() -> None:
        """Periodic orphan finalization.

        For each open accumulator whose producer pid is dead (or has
        had no events for ``orphan_timeout`` seconds), emit a
        synthetic ``RunEnded(outcome="aborted")`` into the events bus.
        The synthetic event flows through the dispatch loop → pool
        absorbs it → ``_materialize_and_emit`` writes the parquet,
        ingests, emits ``RunMaterialized``. Same code path as a clean
        producer-side close.
        """
        orphan_timeout = 3600.0
        while not stop_event.is_set():
            stop_event.wait(timeout=30.0)
            if stop_event.is_set():
                return
            try:
                _sweep_once(orphan_timeout)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Orphan sweep failed: %s", exc)

    def _sweep_once(orphan_timeout: float) -> None:
        es = event_store_box[0]
        if es is None:
            return  # not yet attached; nothing to emit through
        now = datetime.now(UTC)
        for run_id, _acc, pid, last_event_at in pool.open_runs():
            is_orphan = False
            reason = ""
            if pid is not None:
                alive = _check_pid_liveness(pid)
                if alive is False:
                    is_orphan = True
                    reason = f"producer pid {pid} no longer exists"
            if not is_orphan and last_event_at is not None:
                if (now - last_event_at).total_seconds() > orphan_timeout:
                    is_orphan = True
                    reason = f"no events for {orphan_timeout:.0f}s"
            if not is_orphan:
                continue
            try:
                _emit_synthetic_run_ended(es, run_id, now)
                logger.info("Finalizing orphan run %s as aborted (%s)", run_id, reason)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to emit synthetic RunEnded for %s: %s", run_id, exc)

    def _emit_synthetic_run_ended(es: Any, run_id: str, occurred_at: datetime) -> None:
        """Emit ``RunEnded(outcome="aborted")`` for an orphan.

        Reuses the accumulator's cached RunStarted for session/run_id
        identity. The synthetic event flows through ``_on_event``,
        which dispatches into the pool (setting ``_run_ended``) and
        then calls ``_materialize_and_emit`` — same path as a real
        clean close.
        """
        acc = pool.get(run_id)
        if acc is None or acc._run_started is None:
            return
        from litmus.data.events import RunEnded

        es.emit(
            RunEnded(
                session_id=acc._run_started.session_id,
                run_id=acc._run_started.run_id,
                occurred_at=occurred_at,
                outcome="aborted",
            )
        )

    def _on_put(table: pa.Table) -> None:
        """Receive externally-built parquets and ingest them.

        Live path doesn't use this — the daemon materializes its own
        parquets from the events bus. Kept as an entry point for:

        * **Tests** that construct parquets via ``ParquetBackend`` and
          push them in for end-to-end query coverage.
        * **External tooling** that may want to inject a parquet into
          the daemon's index (no current consumer).

        Already runs under the Flight server's lock (which IS our
        ``write_lock``), so direct conn access is safe.
        """
        for row in table.to_pylist():
            fpath = row.get("file_path", "")
            if not fpath:
                continue
            try:
                stat = Path(fpath).stat()
            except OSError:
                continue
            _ingest_one_file(conn, Path(fpath), stat)
            try:
                _bulk_insert_measurement_rows(conn, fpath)
            except Exception as exc:  # noqa: BLE001
                logger.debug("measurement row insert failed for %s: %s", fpath, exc)
        _create_views(conn)

    server, port_file, *_ = start_flight_server_in_daemon(
        mgr=mgr,
        daemon_dir=runs_dir,
        db_name="runs",
        conn=conn,
        put_hook=_on_put,
        port_file_name="_runs_duckdb_flight_port",
        thread_name="runs-duckdb-flight",
        pre_ready=None,
        pre_query_hook=_pre_query_refresh,
        lock=write_lock,
    )

    # Background sweep — picks up parquets that exist on disk but
    # pre-date this daemon's lifetime (fresh install with pre-existing
    # parquets, daemon-was-down recovery). Per-file ingest under
    # ``write_lock`` alternates with Flight queries, no deadlock.
    threading.Thread(
        target=_ingest_parquet_files,
        args=(conn, runs_dir, write_lock, None),
        daemon=True,
        name="runs-ingest",
    ).start()

    # Start the events-attach and orphan-sweep threads.
    threading.Thread(target=_attach_loop, daemon=True, name="runs-events-attach").start()
    threading.Thread(target=_sweep_loop, daemon=True, name="runs-orphan-sweep").start()
    threading.Thread(target=_materialize_worker, daemon=True, name="runs-materialize").start()

    mgr.monitor_refs()

    # Shutdown
    stop_event.set()
    if unsubscribe_box[0] is not None:
        try:
            unsubscribe_box[0]()
        except Exception as exc:  # noqa: BLE001
            logger.debug("unsubscribe cleanup failed: %s", exc)
    if event_store_box[0] is not None:
        try:
            event_store_box[0].close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("event_store close failed: %s", exc)

    shutdown_flight_server_in_daemon(server, port_file, conn)
    mgr.cleanup_state_files()


# ── Helpers used by daemon_run ──────────────────────────────────────


def _events_daemon_alive(events_dir: Path) -> bool:
    """Return ``True`` iff a live events daemon is running for ``events_dir``.

    Reads the events daemon's state file (``_duckdb.json``) and
    checks the recorded pid. **Inspection only, no spawn.** The runs
    daemon attaches to an existing events daemon; it never spawns one.

    Why no spawn: the events daemon should be spawned by the actual
    emitter (pytest plugin, ``StationConnection``, ``SlotRunner``, the
    UI's serve-level acquire) — those processes need to write events
    anyway. The runs daemon emits ``RunMaterialized`` after attach
    (post-spawn), so it has no need to bring the events daemon up itself.
    """
    state = events_dir / "_duckdb.json"
    if not state.exists():
        return False
    try:
        data = json.loads(state.read_text())
        pid = data.get("pid")
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(pid, int) and _pid_alive(pid)


def _check_pid_liveness(pid: int) -> bool | None:
    """``True`` if pid exists, ``False`` if not, ``None`` if we can't tell."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
