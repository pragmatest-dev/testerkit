"""DuckDB run index daemon.

Spawned as a detached process by ``RunsDuckDBManager.acquire()``.
Maintains a persistent DuckDB index rebuilt incrementally from parquet files.
Clients push new runs via ``do_put`` and query via ``do_get``.

Startup is O(new files since last run): the daemon opens its content-addressed
``_index.<fp>.duckdb`` (see "Derived-index versioning" below), signals ready
immediately, then ingests only files not yet recorded in the ``_ingested``
table via a background thread.

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

import hashlib
import json
import logging
import os
import queue
import sys
import threading
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from litmus.data import _index_epoch
from litmus.data._accumulator_pool import (
    EMPTY_INFLIGHT_MEASUREMENTS,
    EMPTY_INFLIGHT_RUNS,
    EMPTY_INFLIGHT_STEPS,
    INFLIGHT_MEASUREMENTS_SCHEMA,
    INFLIGHT_RUNS_SCHEMA,
    INFLIGHT_STEPS_SCHEMA,
    AccumulatorPool,
)
from litmus.data._daemon_lifecycle import _installed_version, _pid_alive
from litmus.data._duckdb_flight_server import (
    shutdown_flight_server_in_daemon,
    start_flight_server_in_daemon,
)
from litmus.data._sql_helpers import sql_escape as _sql_escape
from litmus.data.backends.parquet import materialize_run_to_parquet
from litmus.data.models import Outcome
from litmus.data.runs_duckdb_manager import RunsDuckDBManager
from litmus.data.schema_dispatch import (
    _ADAPTERS,
    SchemaVersionRefused,
    dispatch,
    report_schema_refusal,
    stamp_from_arrow_metadata,
)
from litmus.data.schema_versions import CURRENT_SCHEMA_VERSION, KNOWN_SCHEMA_VERSIONS, SchemaStore
from litmus.models.data_options import RUN_ORPHAN_TIMEOUT_SECONDS
from litmus.models.enums import Comparator

# Columns whose semantic type is a closed enum (Pydantic StrEnum), not
# a free string. DuckDB ENUM types validate at insert and store as
# int8 — keeps types end-to-end with the data models.

logger = logging.getLogger(__name__)


class _EventSequenceMonitor:
    """Per-writer emit-sequence contiguity check on ingested event rows.

    Each EventLog writer stamps a per-instance ``writer_key`` and a
    monotonic ``event_offset`` on every row it appends. The runs daemon
    consumes those rows; a hole in a writer's offset stream means records
    were truncated or lost in transit. This detect-and-flags: it logs and
    counts gaps, never drops/blocks/crashes. Rows lacking the columns (the
    in-process live emit path, which carries neither) are ignored.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[str, int] = {}  # writer_key → last-seen event_offset
        self.gap_count = 0
        self.out_of_order_count = 0

    def check(self, evt: dict[str, Any]) -> None:
        writer_key = evt.get("writer_key")
        offset = evt.get("event_offset")
        if writer_key is None or offset is None:
            return
        offset = int(offset)
        with self._lock:
            last = self._last.get(writer_key)
            if last is None or offset == last + 1:
                self._last[writer_key] = offset
                return
            if offset <= last:
                self.out_of_order_count += 1
                logger.warning(
                    "Event out-of-order for writer %s: offset %d arrived after %d",
                    writer_key,
                    offset,
                    last,
                )
                return
            # offset > last + 1: a hole.
            self.gap_count += 1
            self._last[writer_key] = offset
            logger.warning(
                "Event sequence gap for writer %s: expected offset %d, got %d "
                "(%d record(s) missing)",
                writer_key,
                last + 1,
                offset,
                offset - last - 1,
            )


# ── Schema management ────────────────────────────────────────────────


# ``_index_file_is_the_cause`` is store-agnostic (#64) — moved to
# ``_index_epoch.py``; kept here as a thin re-export because ``_open_index``
# injects it into ``_index_epoch.open_index``.
_index_file_is_the_cause = _index_epoch.index_file_is_the_cause


# ── Derived-index versioning (#47, widened by #53 P1) ───────────────
#
# See docs/_internal/explorations/derived-index-versioning.md §3/§10/§11.
# The derived index is versioned by content-addressing the FILENAME, not by
# comparing an in-file stamp on open:
#
#   * the FILENAME (``_index.<fp>.duckdb``) is the coexistence gate — ``fp``
#     is a single widened fingerprint = sha256(projection DDL + the
#     registered adapter-registry keys + the schema whitelist) for
#     ``SchemaStore.RUNS``. A daemon computes its own fingerprint and opens
#     ONLY the file named after it; every other ``_index.*.duckdb`` in the
#     dir is left untouched (another version's file, or none yet).
#   * the in-file ``_index_meta`` is PROVENANCE + a build-complete marker,
#     not a shape gate — because the filename already encodes the exact
#     read-path this code produces, a file at this path can only ever be
#     shaped like this code's projection. What's left to guard is a crash
#     mid-build (a correctly-named but incomplete file): the build-complete
#     marker (``built_at``, written last) is absent → discard + rebuild.
#   * the CORRUPT-FILE self-heal (Path 1 below, gated by
#     ``_index_file_is_the_cause``) is unchanged — a DuckDB storage-format
#     problem is orthogonal to fingerprinting.


def _shape_ddl_prefixes() -> tuple[str, ...]:
    return ("CREATE TABLE", "CREATE OR REPLACE VIEW", "ALTER TABLE", "CREATE INDEX", "CREATE TYPE")


def _projection_fingerprint() -> str:
    """Deterministic content-address of the daemon's full read-path.

    Single-sourced from the SAME strings :func:`_ensure_schema` +
    :func:`_create_views` run — a recording proxy captures every ``execute()``
    SQL as those functions build the schema on a throwaway ``:memory:`` DB, so
    the DDL half of the hash CANNOT drift from the real schema. Only
    shape-defining DDL (``CREATE TABLE`` / ``CREATE OR REPLACE VIEW`` /
    ``ALTER TABLE`` / ``CREATE INDEX`` / ``CREATE TYPE``) is hashed,
    whitespace-normalized so indentation is irrelevant; nothing volatile
    (timestamps, paths) appears in that DDL.

    Widened (#53 P1) beyond DDL to a true read-path content-address: folds in
    the registered adapter-registry keys and the schema whitelist for
    ``SchemaStore.RUNS`` (both sorted, so the hash is order-independent and
    reproducible). This makes the fingerprint — and the index filename it
    names (:func:`_index_file_name`) — fork on any read-*semantics* change,
    not just a DDL change: registering a new adapter (which also adds its
    version to the whitelist, see ``register_adapter``) changes the
    fingerprint even when the projection DDL is untouched.

    Residual edge (accepted pre-1.0): an adapter *code* change that does NOT
    change its registered ``source_version`` key (e.g. a bugfix to an
    existing adapter's transform) is invisible to this hash — the DDL,
    adapter keys, and whitelist are all unchanged, so the fingerprint (and
    the file it names) do not fork. A real fix for that case needs an
    explicit adapter-version bump folded into this hash; out of scope here.

    ``_create_inflight_tables`` runs on the raw connection (its DROP/ATTACH and
    Arrow-API table creation are NOT projection shape and NOT recorded) purely
    so ``_create_views`` can resolve its ``overlay.*`` references and execute.
    """
    recorded: list[str] = []

    class _Recorder:
        def __init__(self, real: duckdb.DuckDBPyConnection) -> None:
            self._real = real

        def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
            recorded.append(sql)
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    scratch = duckdb.connect(":memory:")
    try:
        _ensure_schema(_Recorder(scratch))  # type: ignore[arg-type]
        _create_inflight_tables(scratch)  # raw: attach overlay + inflight tables (not recorded)
        _create_views(_Recorder(scratch))  # type: ignore[arg-type]
    finally:
        scratch.close()

    prefixes = _shape_ddl_prefixes()
    ddl = [norm for sql in recorded if (norm := " ".join(sql.split())).upper().startswith(prefixes)]
    adapter_keys = sorted(_ADAPTERS[SchemaStore.RUNS])
    whitelist = sorted(KNOWN_SCHEMA_VERSIONS[SchemaStore.RUNS])
    payload = "\n".join(
        ["--ddl--", *ddl, "--adapters--", *adapter_keys, "--whitelist--", *whitelist]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ``_index_file_name`` is store-agnostic (#64) — moved to ``_index_epoch.py``;
# kept importable here under its original name as a thin re-export.
_index_file_name = _index_epoch.index_file_name


def _current_provenance() -> tuple[str, str, str]:
    """The ``(litmus_version, schema_version, projection_fingerprint)`` a
    fresh build of this code would stamp."""
    return (
        _installed_version(),
        CURRENT_SCHEMA_VERSION[SchemaStore.RUNS],
        _projection_fingerprint(),
    )


def _stamp_index_meta(conn: duckdb.DuckDBPyConnection) -> None:
    """Write this build's provenance into ``_index_meta`` and mark it complete.

    Thin runs-specific wrapper (#64) around the store-agnostic
    :func:`litmus.data._index_epoch.stamp_index_meta` — supplies the runs
    daemon's own ``(litmus_version, schema_version, fingerprint)`` triple via
    :func:`_current_provenance`. See that shared function's docstring for the
    build-complete-marker-written-last rationale.
    """
    litmus_version, schema_version, fingerprint = _current_provenance()
    _index_epoch.stamp_index_meta(
        conn,
        litmus_version=litmus_version,
        schema_version=schema_version,
        fingerprint=fingerprint,
    )


# ``_read_index_meta`` is store-agnostic (#64) — moved to ``_index_epoch.py``;
# kept importable here under its original name as a thin re-export.
_read_index_meta = _index_epoch.read_index_meta


def _open_index(index_path: Path) -> tuple[duckdb.DuckDBPyConnection, bool]:
    """Open the content-addressed derived index at *index_path* (named
    ``_index.<fp>.duckdb`` by the caller, see :func:`_index_file_name`) and
    ensure its schema is current.

    Thin runs-specific wrapper (#64) around the store-agnostic
    :func:`litmus.data._index_epoch.open_index`, injecting this store's own
    :func:`_ensure_schema`, :func:`_stamp_index_meta`, and
    :func:`_index_file_is_the_cause`. See the shared function's docstring for
    the full two-path self-heal rationale (unreadable file vs. build-incomplete).
    """
    return _index_epoch.open_index(
        index_path,
        ensure_schema=_ensure_schema,
        stamp_meta=_stamp_index_meta,
        index_file_is_the_cause=_index_file_is_the_cause,
    )


# ``_stamp_epochs_ledger`` / ``_read_epochs_ledger`` / ``_remove_epochs_ledger_entries``
# are store-agnostic (#64) — moved to ``_index_epoch.py``; kept importable here
# under their original names as thin re-exports.
_stamp_epochs_ledger = _index_epoch.stamp_epochs_ledger
_read_epochs_ledger = _index_epoch.read_epochs_ledger
_remove_epochs_ledger_entries = _index_epoch.remove_epochs_ledger_entries


def _ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Idempotently align the on-disk schema with the code.

    Storage layering:

    - ``runs_materialized`` / ``steps_materialized`` / ``measurements_materialized``
      / ``instruments_materialized`` — TABLES populated by parquet ingest,
      each carrying only its own grain's columns + the ``run_id`` FK (star
      schema, 0.3.1 — run identity lives once, in ``runs_materialized``).
      ``runs`` / ``steps`` / ``step_vectors`` / ``measurements`` /
      ``instruments`` are VIEWS (created in :func:`_create_views`) that JOIN
      ``runs_materialized`` back in for identity and splice in the
      in-memory ``AccumulatorPool`` snapshot.
    - ``inputs`` / ``outputs`` — long/EAV projections of the nested lane
      lists, one honestly-named table per role. Aggregates for the hot path
      live in ``measurement_stats``.
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

    Adding a new column = add it to the DDL below. This idempotent path keeps
    a *given* index file self-migrating (no special migration code) across
    additive DDL changes made before that file's fingerprint was computed
    from it — e.g. re-running an already-current daemon after an in-place
    code edit. It does NOT retrofit an already-built file after a real
    release: a new column changes ``_projection_fingerprint()``, so the next
    daemon opens a new, distinct ``_index.<fp>.duckdb`` and rebuilds from
    parquet rather than ``ALTER``-ing the old file in place (content-address
    isolation, see "Derived-index versioning" above; cheap copy-seed birth
    instead of a full rescan is P2, not yet built).
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
            site_index BIGINT,
            site_name VARCHAR,
            uut_serial_number VARCHAR,
            uut_part_number VARCHAR,
            uut_revision VARCHAR,
            uut_lot_number VARCHAR,
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
            part_id VARCHAR,
            operator_id VARCHAR,
            project_name VARCHAR
        )
    """)
    for col, sql_type in _RUNS_PERSISTED_COLUMNS:
        conn.execute(f"ALTER TABLE runs_materialized ADD COLUMN IF NOT EXISTS {col} {sql_type}")

    # ── steps_materialized (LOGICAL steps only) ────────────────────────
    # Star schema: step's OWN columns + the run_id FK only. NO run identity
    # (session_id / site_index / site_name / uut_serial_number / station_id) —
    # that lives once in runs_materialized; the ``steps`` VIEW (see
    # _create_views) reconstructs it via JOIN. Same for the step's
    # inputs/outputs — no ``dynamic_attrs`` column; the view derives
    # ``inputs_map``/``outputs_map`` from the ``inputs``/``outputs`` tables.
    # Full snowflake (0.3.1 phase 6): the swept condition points ('vector'
    # rows) SPLIT OUT into ``vectors_materialized`` — this table now holds ONE
    # row per LOGICAL step (record_type='step', vector_index always NULL at
    # rest), never a vector. PK includes ``vector_outer_index`` because a
    # method step run under different outer class-sweep iterations is a
    # distinct execution (its own inputs/outcome) — the contract's stated
    # 3-col steps PK omits it; see progress log.
    # vector_outer_index_key = COALESCE(vector_outer_index, -1): PK cannot be
    # NULL, -1 is the top-level (no enclosing outer sweep) sentinel.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS steps_materialized (
            run_id VARCHAR NOT NULL,
            step_path VARCHAR NOT NULL,
            step_retry BIGINT NOT NULL DEFAULT 0,
            vector_outer_index BIGINT,
            vector_outer_index_key BIGINT NOT NULL DEFAULT -1,
            step_index INTEGER,
            file_path VARCHAR,
            step_name VARCHAR,
            outcome outcome_kind,
            started_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            duration_s DOUBLE,
            measurement_count INTEGER,
            markers VARCHAR,
            PRIMARY KEY (run_id, step_path, step_retry, vector_outer_index_key)
        )
    """)
    for col, sql_type in _STEPS_PERSISTED_COLUMNS:
        conn.execute(f"ALTER TABLE steps_materialized ADD COLUMN IF NOT EXISTS {col} {sql_type}")

    # ── vectors_materialized (swept condition points only) ─────────────
    # Full snowflake (0.3.1 phase 6): SPLIT OUT of the old dual-grain
    # steps table. One row per condition point ('vector' at rest — a sweep
    # variant / in-body loop iteration). PK = the enclosing step's key +
    # (vector_index, vector_retry), both always concrete on a vector. NO
    # step-grain data (step_name / step_index) — the ``step_vectors`` VIEW
    # joins ``steps_materialized`` for those; NO run identity — joined from
    # ``runs`` in the view.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vectors_materialized (
            run_id VARCHAR NOT NULL,
            step_path VARCHAR NOT NULL,
            step_retry BIGINT NOT NULL DEFAULT 0,
            vector_outer_index BIGINT,
            vector_outer_index_key BIGINT NOT NULL DEFAULT -1,
            vector_index BIGINT NOT NULL,
            vector_retry BIGINT NOT NULL DEFAULT 0,
            file_path VARCHAR,
            outcome outcome_kind,
            started_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            duration_s DOUBLE,
            measurement_count INTEGER,
            PRIMARY KEY (run_id, step_path, step_retry, vector_outer_index_key,
                         vector_index, vector_retry)
        )
    """)
    for col, sql_type in _VECTORS_PERSISTED_COLUMNS:
        conn.execute(f"ALTER TABLE vectors_materialized ADD COLUMN IF NOT EXISTS {col} {sql_type}")

    # ── measurement_stats / io_schema / refs ────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurement_stats (
            file_path VARCHAR NOT NULL,
            run_id VARCHAR,
            session_id VARCHAR,
            step_index INTEGER,
            step_name VARCHAR,
            measurement_name VARCHAR NOT NULL,
            measurement_unit VARCHAR,
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
            role VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            value_type VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurement_refs (
            file_path VARCHAR NOT NULL,
            step_index INTEGER,
            measurement_name VARCHAR,
            col_name VARCHAR NOT NULL,
            role VARCHAR NOT NULL DEFAULT 'output',
            row_idx INTEGER NOT NULL,
            uri VARCHAR NOT NULL,
            channel_id VARCHAR NOT NULL,
            session_short VARCHAR NOT NULL,
            session_id VARCHAR
        )
    """)
    # Schema migrations for measurement_refs: pre-existing DuckDB files may
    # be missing columns added since. ALTER TABLE … ADD COLUMN IF NOT EXISTS
    # is a no-op when the column already exists.
    conn.execute("ALTER TABLE measurement_refs ADD COLUMN IF NOT EXISTS session_id VARCHAR")
    conn.execute(
        "ALTER TABLE measurement_refs ADD COLUMN IF NOT EXISTS role VARCHAR DEFAULT 'output'"
    )
    # measurement_io_schema migration: older builds stored ``column_name``
    # (prefixed, e.g. ``out_v_rail``) + ``category``. New schema stores
    # ``(role, name, value_type)`` directly. Add the new columns; old rows
    # keep NULL values for them (pre-1b data, harmless).
    # TODO(post-0.2.0): DROP COLUMN column_name, category once installs are upgraded.
    for col_def in (
        "role VARCHAR",
        "name VARCHAR",
        "value_type VARCHAR",
    ):
        conn.execute(f"ALTER TABLE measurement_io_schema ADD COLUMN IF NOT EXISTS {col_def}")
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
    # The daemon's FLAT measurement-fact projection — built at ingest by
    # UNNESTing the nested ``measurements`` list off each at-rest step/vector
    # row. The at-rest parquet has NO ``record_type='measurement'`` rows (only
    # run/step/vector); the default below stamps the projected fact rows.
    # Full snowflake (0.3.1 phase 7): measurement's OWN fields ONLY, plus its
    # grain key. NO run identity, NO step DATA (step_name/outcome/timing), NO
    # vector_outcome — the ``measurements`` VIEW reconstructs all of those by
    # joining ``runs`` / ``steps`` / ``vectors`` (see _create_views). The
    # coordinate columns (step_index / step_path / step_retry / vector coords)
    # STAY: they ARE the grain key (and the EAV-join key + the index-window
    # sort key). ``ordinal`` = the UNNEST-WITH-ORDINALITY position within the
    # carrier's nested measurements list (the true PK discriminator — a name
    # can repeat on one carrier: two measure("v_rail")). ``index`` = the
    # run-wide, per-name, retry-stable occurrence ordinal, MATERIALIZED once at
    # ingest (a window during the UNNEST) instead of a query-time DENSE_RANK.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurements_materialized (
            file_path             VARCHAR NOT NULL,
            run_id                VARCHAR,
            step_index            INTEGER,
            step_path             VARCHAR,
            step_retry            BIGINT,
            vector_index          BIGINT,
            vector_outer_index    BIGINT,
            vector_retry        BIGINT,
            ordinal               BIGINT,
            index                 BIGINT,
            measurement_name      VARCHAR,
            measurement_value     DOUBLE,
            measurement_outcome   VARCHAR,
            measurement_unit     VARCHAR,
            measurement_timestamp TIMESTAMPTZ,
            limit_low             DOUBLE,
            limit_high            DOUBLE,
            limit_nominal         DOUBLE,
            limit_comparator      VARCHAR,
            characteristic_id     VARCHAR,
            spec_ref              VARCHAR,
            uut_pin               VARCHAR,
            fixture_connection    VARCHAR,
            instrument_name       VARCHAR,
            instrument_resource   VARCHAR,
            instrument_channel    VARCHAR
        )
    """)
    for col, sql_type in _MEASUREMENTS_PERSISTED_COLUMNS:
        conn.execute(
            f"ALTER TABLE measurements_materialized ADD COLUMN IF NOT EXISTS {col} {sql_type}"
        )

    # Long/EAV projection of the nested inputs/outputs lanes, split into two
    # honestly-named tables (the table IS the role — no ``role`` column, no
    # UNION-able ambiguity a bare query could issue). One row per (vector,
    # name), keyed on the natural vector identity PLUS ``step_path``
    # (``step_index`` alone resets per parent bucket — see
    # ``_collection_indices.assign_indices`` — so two unswept steps at
    # step_index=0 with NULL vector coords would otherwise collide and
    # cross-join their dynamic values) PLUS ``step_retry`` (needed so the
    # ``steps``/``step_vectors`` views' query-time inputs_map/outputs_map
    # join — see _create_views — doesn't fan-out across pytest-rerunfailures
    # reruns of the same step; the OLD ingest-time ``dynamic_attrs`` computation
    # scoped by step_retry for free by reading straight off each raw parquet
    # row, a property the new query-time join must preserve explicitly).
    # ``value_type`` is the value-type tag selecting which value_* lane holds
    # the value (see _row_helpers). Index only ``name`` — high-cardinality ART
    # indexes (the vector key) don't spill and OOM at scale; hash joins don't
    # use them anyway (benched: bench_index_scale.py). file_path pruning is via
    # zonemaps (file-clustered ingest), not an index.
    lane_cols = ", ".join(f"{col} {sql_type}" for col, sql_type in _LANE_PERSISTED_COLUMNS)
    conn.execute(f"CREATE TABLE IF NOT EXISTS inputs ({lane_cols})")
    conn.execute(f"CREATE TABLE IF NOT EXISTS outputs ({lane_cols})")

    # ── instruments_materialized ──────────────────────────────────────
    # Grain: one row per instrument per run. UNNESTed from the run row's
    # ``instruments`` LIST<STRUCT> at ingest. Powers the /instruments page
    # via the Query API instead of ad-hoc parquet array scans. Star schema:
    # instrument's OWN struct fields + the run_id FK only — NO run identity
    # (lives once in runs_materialized; the ``instruments`` VIEW joins it
    # back — see _create_views).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instruments_materialized (
            file_path         VARCHAR NOT NULL,
            run_id            VARCHAR,
            role              VARCHAR,
            instrument_id     VARCHAR,
            driver            VARCHAR,
            resource          VARCHAR,
            protocol          VARCHAR,
            manufacturer      VARCHAR,
            model             VARCHAR,
            serial_number     VARCHAR,
            firmware          VARCHAR,
            cal_due           VARCHAR,
            cal_last          VARCHAR,
            cal_certificate   VARCHAR,
            cal_lab           VARCHAR,
            mocked            BOOLEAN
        )
    """)
    for col, sql_type in _INSTRUMENTS_PERSISTED_COLUMNS:
        conn.execute(
            f"ALTER TABLE instruments_materialized ADD COLUMN IF NOT EXISTS {col} {sql_type}"
        )

    # ── indexes ─────────────────────────────────────────────────────
    for index_sql in (
        "CREATE INDEX IF NOT EXISTS idx_runs_run_id ON runs_materialized(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_runs_session ON runs_materialized(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_runs_started ON runs_materialized(started_at)",
        "CREATE INDEX IF NOT EXISTS idx_runs_fp ON runs_materialized(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_steps_run ON steps_materialized(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_steps_fp ON steps_materialized(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_vectors_run ON vectors_materialized(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_vectors_fp ON vectors_materialized(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_meas_name ON measurement_stats(measurement_name)",
        "CREATE INDEX IF NOT EXISTS idx_meas_run ON measurement_stats(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_meas_fp ON measurement_stats(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_mrefs_name ON measurement_refs(measurement_name)",
        "CREATE INDEX IF NOT EXISTS idx_mrefs_session ON measurement_refs(session_short)",
        "CREATE INDEX IF NOT EXISTS idx_mio_fp ON measurement_io_schema(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_mp_fp   ON measurements_materialized(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_mp_run  ON measurements_materialized(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_mp_name ON measurements_materialized(measurement_name)",
        "CREATE INDEX IF NOT EXISTS idx_inputs_name ON inputs(name)",
        "CREATE INDEX IF NOT EXISTS idx_outputs_name ON outputs(name)",
        "CREATE INDEX IF NOT EXISTS idx_instr_run_id ON instruments_materialized(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_instr_fp ON instruments_materialized(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_instr_id ON instruments_materialized(instrument_id)",
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
    ("site_index", "BIGINT"),
    ("site_name", "VARCHAR"),
    ("uut_serial_number", "VARCHAR"),
    ("uut_part_number", "VARCHAR"),
    ("uut_revision", "VARCHAR"),
    ("uut_lot_number", "VARCHAR"),
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
    ("part_id", "VARCHAR"),
    ("part_name", "VARCHAR"),
    ("part_revision", "VARCHAR"),
    ("station_type", "VARCHAR"),
    ("station_location", "VARCHAR"),
    ("operator_id", "VARCHAR"),
    ("operator_name", "VARCHAR"),
    ("project_name", "VARCHAR"),
    ("git_commit", "VARCHAR"),
    ("git_branch", "VARCHAR"),
    ("git_remote", "VARCHAR"),
    ("python_version", "VARCHAR"),
    ("litmus_version", "VARCHAR"),
    ("env_fingerprint", "VARCHAR"),
)
_STEPS_PERSISTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "VARCHAR"),
    ("step_index", "INTEGER"),
    ("file_path", "VARCHAR"),
    ("step_name", "VARCHAR"),
    ("step_path", "VARCHAR"),
    ("outcome", "outcome_kind"),
    ("started_at", "TIMESTAMPTZ"),
    ("ended_at", "TIMESTAMPTZ"),
    ("duration_s", "DOUBLE"),
    # 0-based outer (item) retry — pytest-rerunfailures rerun count of this
    # step. Part of the PK so a rerun is a distinct row (the de-fuse), never
    # overwriting the prior attempt.
    ("step_retry", "BIGINT"),
    # vector_outer_index: which enclosing outer (class) sweep iteration this
    # step ran under (part of the PK); vector_index is GONE — logical steps
    # are always NULL there, and swept points moved to vectors_materialized.
    ("vector_outer_index", "BIGINT"),
    ("measurement_count", "INTEGER"),
    ("markers", "VARCHAR"),
)

_VECTORS_PERSISTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "VARCHAR"),
    ("step_path", "VARCHAR"),
    ("step_retry", "BIGINT"),
    ("vector_outer_index", "BIGINT"),
    ("vector_index", "BIGINT"),
    ("vector_retry", "BIGINT"),
    ("file_path", "VARCHAR"),
    ("outcome", "outcome_kind"),
    ("started_at", "TIMESTAMPTZ"),
    ("ended_at", "TIMESTAMPTZ"),
    ("duration_s", "DOUBLE"),
    ("measurement_count", "INTEGER"),
)

_MEASUREMENTS_PERSISTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("file_path", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("step_index", "INTEGER"),
    ("step_path", "VARCHAR"),
    ("step_retry", "BIGINT"),
    ("vector_index", "BIGINT"),
    ("vector_outer_index", "BIGINT"),
    ("vector_retry", "BIGINT"),
    ("ordinal", "BIGINT"),
    ("index", "BIGINT"),
    ("measurement_name", "VARCHAR"),
    ("measurement_value", "DOUBLE"),
    ("measurement_outcome", "VARCHAR"),
    ("measurement_unit", "VARCHAR"),
    ("measurement_timestamp", "TIMESTAMPTZ"),
    ("limit_low", "DOUBLE"),
    ("limit_high", "DOUBLE"),
    ("limit_nominal", "DOUBLE"),
    ("limit_comparator", "VARCHAR"),
    ("characteristic_id", "VARCHAR"),
    ("spec_ref", "VARCHAR"),
    ("uut_pin", "VARCHAR"),
    ("fixture_connection", "VARCHAR"),
    ("instrument_name", "VARCHAR"),
    ("instrument_resource", "VARCHAR"),
    ("instrument_channel", "VARCHAR"),
)

_INSTRUMENTS_PERSISTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("file_path", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("role", "VARCHAR"),
    ("instrument_id", "VARCHAR"),
    ("driver", "VARCHAR"),
    ("resource", "VARCHAR"),
    ("protocol", "VARCHAR"),
    ("manufacturer", "VARCHAR"),
    ("model", "VARCHAR"),
    ("serial_number", "VARCHAR"),
    ("firmware", "VARCHAR"),
    ("cal_due", "VARCHAR"),
    ("cal_last", "VARCHAR"),
    ("cal_certificate", "VARCHAR"),
    ("cal_lab", "VARCHAR"),
    ("mocked", "BOOLEAN"),
)

# Canonical column list for ``inputs``/``outputs`` (both tables share this
# DDL — the table IS the role). FK coordinates + the lane's own fields
# (``LANE_FIELDS`` from _row_helpers, unaliased — splitting the EAV by role
# renames nothing). Exposed for test_ingestion_drift's per-nested-struct-
# table uniform rule.
_LANE_PERSISTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("file_path", "VARCHAR NOT NULL"),
    ("run_id", "VARCHAR"),
    ("step_index", "INTEGER"),
    ("step_path", "VARCHAR"),
    ("step_retry", "BIGINT"),
    ("vector_index", "BIGINT"),
    ("vector_outer_index", "BIGINT"),
    ("vector_retry", "BIGINT"),
    ("ordinal", "BIGINT"),
    ("index", "BIGINT"),
    ("name", "VARCHAR NOT NULL"),
    ("value_type", "VARCHAR"),
    ("value_int", "BIGINT"),
    ("value_double", "DOUBLE"),
    ("value_bool", "BOOLEAN"),
    ("value_text", "VARCHAR"),
    ("value_timestamp", "TIMESTAMPTZ"),
    ("value_json", "VARCHAR"),
    ("unit", "VARCHAR"),
    ("uut_pin", "VARCHAR"),
)


# ── Ingest helpers ──────────────────────────────────────────────────


def _file_list_sql(paths: list[str]) -> str:
    """Build a DuckDB list literal from file paths."""
    return "[" + ", ".join(f"'{_sql_escape(p)}'" for p in paths) + "]"


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


# ── IO schema / refs SQL (shared by bulk and per-file paths) ────────
#
# Both source from the nested ``inputs``/``outputs`` lanes (the at-rest
# EAV form). The catalog stores ``(role, name, value_type)`` pairs —
# the query client reads these directly to build FieldRef-based selectors.
# Signal-path lane names (``*_instrument`` / ``*_resource`` / ``*_channel`` /
# ``*_uut_pin`` / ``*_fixture_connection``) are excluded, same as before.
_SIGNAL_PATH_SUFFIX_PRED = (
    "u.name NOT LIKE '%\\_instrument' ESCAPE '\\' "
    "AND u.name NOT LIKE '%\\_resource' ESCAPE '\\' "
    "AND u.name NOT LIKE '%\\_channel' ESCAPE '\\' "
    "AND u.name NOT LIKE '%\\_uut\\_pin' ESCAPE '\\' "
    "AND u.name NOT LIKE '%\\_fixture\\_connection' ESCAPE '\\'"
)
# Distinct from _DYNAMIC_ROLES (same values, different use): _IO_ROLES drives
# io_schema/refs indexing; _DYNAMIC_ROLES drives the EAV unnest.
_IO_ROLES: tuple[tuple[str, str], ...] = (
    ("inputs", "input"),
    ("outputs", "output"),
)


def _index_io_and_refs(conn: duckdb.DuckDBPyConnection, fkey: str) -> str | None:
    """Index measurement_io_schema and measurement_refs for one file.

    Reads the nested ``inputs``/``outputs`` lanes. ``io_schema`` records
    ``(role, name, value_type)`` per step_index; ``refs`` extracts
    ``channel://`` URIs from the output lanes' ``uri``-value_type values.
    """
    escaped = _sql_escape(fkey)
    src = f"read_parquet('{escaped}')"
    try:
        io_parts = [
            f"SELECT DISTINCT step_index, '{role}' AS role, "
            f"u.name AS name, u.value_type AS value_type "
            f"FROM {src}, UNNEST({col}) AS t(u) "
            f"WHERE u.name IS NOT NULL AND {_SIGNAL_PATH_SUFFIX_PRED}"
            for col, role in _IO_ROLES
        ]
        try:
            conn.execute(
                f"""
                INSERT INTO measurement_io_schema
                SELECT ? AS file_path, step_index, role, name, value_type
                FROM ({" UNION ALL ".join(io_parts)})
            """,
                [fkey],
            )
        except duckdb.Error as exc:
            warnings.warn(f"Could not index I/O schema for {fkey}: {exc}", stacklevel=2)

        # refs: channel:// URIs ride in the output lanes' value_text (kind='uri').
        try:
            conn.execute(
                f"""
                INSERT INTO measurement_refs
                    (file_path, step_index, measurement_name, col_name, role,
                     row_idx, uri, channel_id, session_short, session_id)
                SELECT ? AS file_path, step_index, NULL AS measurement_name,
                       u.name AS col_name, 'output' AS role,
                       (row_number() OVER ()) - 1 AS row_idx,
                       u.value_text AS uri,
                       regexp_extract(u.value_text, 'channel://([^?]+)', 1) AS channel_id,
                       left(regexp_extract(u.value_text, '[?&]session=([^&]+)', 1), 8)
                           AS session_short,
                       regexp_extract(u.value_text, '[?&]session=([^&]+)', 1) AS session_id
                FROM {src}, UNNEST(outputs) AS t(u)
                WHERE u.value_text IS NOT NULL
                  AND u.value_text LIKE 'channel://%'
                  AND regexp_extract(u.value_text, 'channel://([^?]+)', 1) != ''
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


def _batch_index_io_and_refs(conn: duckdb.DuckDBPyConnection, paths: list[str]) -> None:
    """Batched io_schema + refs indexing — one ``read_parquet([...])`` for the
    whole batch (``filename`` carries each row's file_path), instead of opening
    each parquet per file. Same rows as ``_index_io_and_refs``; this is the
    dominant catchup phase, so batching it is the big startup-drain win. The
    caller already holds the batch transaction; per-file fallback handles a
    corrupt file in the set.
    """
    if not paths:
        return
    src = f"read_parquet({_file_list_sql(paths)}, filename=true, union_by_name=true)"
    try:
        io_parts = [
            f"SELECT DISTINCT filename, step_index, '{role}' AS role, "
            f"u.name AS name, u.value_type AS value_type "
            f"FROM {src}, UNNEST({col}) AS t(u) "
            f"WHERE u.name IS NOT NULL AND {_SIGNAL_PATH_SUFFIX_PRED}"
            for col, role in _IO_ROLES
        ]
        conn.execute(f"""
            INSERT INTO measurement_io_schema
            SELECT filename AS file_path, step_index, role, name, value_type
            FROM ({" UNION ALL ".join(io_parts)})
        """)
    except duckdb.Error as exc:
        warnings.warn(f"Could not batch-index I/O schema: {exc}", stacklevel=2)

    try:
        conn.execute(f"""
            INSERT INTO measurement_refs
                (file_path, step_index, measurement_name, col_name, role,
                 row_idx, uri, channel_id, session_short, session_id)
            SELECT filename AS file_path, step_index, NULL AS measurement_name,
                   u.name AS col_name, 'output' AS role,
                   (row_number() OVER (PARTITION BY filename)) - 1 AS row_idx,
                   u.value_text AS uri,
                   regexp_extract(u.value_text, 'channel://([^?]+)', 1) AS channel_id,
                   left(regexp_extract(u.value_text, '[?&]session=([^&]+)', 1), 8)
                       AS session_short,
                   regexp_extract(u.value_text, '[?&]session=([^&]+)', 1) AS session_id
            FROM {src}, UNNEST(outputs) AS t(u)
            WHERE u.value_text IS NOT NULL
              AND u.value_text LIKE 'channel://%'
              AND regexp_extract(u.value_text, 'channel://([^?]+)', 1) != ''
        """)
    except duckdb.Error as exc:
        warnings.warn(f"Could not batch-scan refs: {exc}", stacklevel=2)


# ── Cascade delete when a parquet file vanishes ─────────────────────

_INDEX_TABLES_BY_FILE_PATH = (
    "measurement_stats",
    "measurement_io_schema",
    "measurement_refs",
    "measurements_materialized",
    "inputs",
    "outputs",
    "instruments_materialized",
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
    conn.execute("DELETE FROM vectors_materialized WHERE file_path = ?", [path_str])
    for table in _INDEX_TABLES_BY_FILE_PATH:
        conn.execute(f"DELETE FROM {table} WHERE file_path = ?", [path_str])
    conn.execute("DELETE FROM _ingested WHERE path = ?", [path_str])


# ── Bulk ingest ─────────────────────────────────────────────────────


# The nested lane columns (parquet) → the honestly-named table each UNNESTs
# into. No role tag/column — the table IS the role (measurements_dynamic's
# ``role`` column is gone; a role-scoped query selects the matching table).
_LANE_TABLES: tuple[tuple[str, str], ...] = (
    ("inputs", "inputs"),
    ("outputs", "outputs"),
)
_LANE_SELECT = (
    "u.name, u.value_type, u.value_int, u.value_double, u.value_bool, "
    "u.value_text, u.value_timestamp, u.value_json, u.unit, u.uut_pin"
)


def _lane_insert(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    col: str,
    source: str,
    *,
    file_path_expr: str,
    with_filename: bool = False,
) -> None:
    """INSERT one lane column (``inputs`` or ``outputs``) UNNESTed from ``source``.

    ``source`` is a relation expression (a ``read_parquet(...)`` call). Rows come
    from ``record_type IN ('step', 'vector')`` — the lane carriers. ``step_path``
    and ``step_retry`` ride along so the read-time EAV join (see
    ``_create_views``) can disambiguate two unswept steps sharing a
    ``step_index`` (resets per parent bucket — see
    ``_collection_indices.assign_indices``) and two reruns of the same step
    (``step_retry`` — pytest-rerunfailures). ``ordinal`` (0-based
    UNNEST-WITH-ORDINALITY position) discriminates repeats of a lane name on
    one carrier; ``index`` is the materialized per-name occurrence ordinal
    (see :func:`_occurrence_index_expr`), symmetric with measurements. With
    ``with_filename`` the context subquery also projects ``filename`` (requires
    ``source`` to read with ``filename=true``) so a multi-file batch keeps each
    row's own ``file_path``; single-file callers pass a constant instead.
    """
    prefix = "filename, " if with_filename else ""
    index_expr = _occurrence_index_expr(
        run_id="ctx.run_id",
        name="u.name",
        step_index="ctx.step_index",
        step_path="ctx.step_path",
        vector_index="ctx.vector_index",
    )
    # ``BY NAME`` (not positional): an UPGRADED on-disk index has the columns
    # added since (``ordinal`` / ``index``) ALTER-appended at the END, so a
    # positional INSERT would misalign them; matching by output-column NAME is
    # order-independent. ``u.name`` → column ``name``, ``u.value_type`` →
    # ``value_type``, etc., so ``_LANE_SELECT`` aligns by name unchanged.
    # ``step_retry`` / ``vector_retry`` are normalized IDENTICALLY to
    # ``_measurement_unnest_insert`` so a measurement and its inputs/outputs
    # land on the same join key: ``step_retry`` → 0-based (COALESCE NULL→0, as
    # a direct ``RunParquetRow`` writer may leave it NULL); ``vector_retry`` →
    # NULL for a step carrier (vector_index NULL at rest), 0-based for a vector
    # carrier. Without this, the EAV join compares 0-vs-NULL and misses.
    conn.execute(f"""
        INSERT INTO {table} BY NAME
        SELECT
            {file_path_expr} AS file_path, ctx.run_id, ctx.step_index, ctx.step_path,
            COALESCE(ctx.step_retry, 0) AS step_retry,
            ctx.vector_index, ctx.vector_outer_index,
            CASE WHEN ctx.vector_index IS NOT NULL THEN COALESCE(ctx.vector_retry, 0) END
                AS vector_retry,
            CAST(ord AS BIGINT) - 1 AS ordinal,
            {index_expr} AS index,
            {_LANE_SELECT}
        FROM (
            SELECT {prefix}run_id, step_index, step_path, step_retry, vector_index,
                   vector_outer_index, vector_retry, {col}
            FROM {source}
            WHERE record_type IN ('step', 'vector')
        ) AS ctx, UNNEST(ctx.{col}) WITH ORDINALITY AS t(u, ord)
    """)


# Empty ``MAP(VARCHAR, VARCHAR)`` literal — the constant the ``steps`` /
# ``step_vectors`` views emit for ``inputs_map`` / ``outputs_map`` on their
# MATERIALIZED side. The finalized inputs/outputs live in the ``inputs`` /
# ``outputs`` tables and are joined in by the step-detail query
# (``StepsQuery._STEP_IO_JOINS``) — keeping the shared views free of the
# aggregation so metrics reads (yield/pareto over ``steps``) aren't taxed.
# The INFLIGHT side passes its own ``inputs_map`` / ``outputs_map`` through, so
# a LIVE run's inputs/outputs render before its parquet exists (the finalized
# join yields nothing until ingest; StepsQuery COALESCEs the two).
_EMPTY_MAP = "MAP([]::VARCHAR[], []::VARCHAR[])"


# Nested measurement-struct field → flat fact column for the measurement
# UNNEST. Sourced from the nested struct ``m`` (not from the carrier row's
# context columns).
_MEAS_STRUCT_TO_FACT: tuple[tuple[str, str], ...] = (
    ("name", "measurement_name"),
    ("value", "measurement_value"),
    ("outcome", "measurement_outcome"),
    ("unit", "measurement_unit"),
    ("timestamp", "measurement_timestamp"),
    ("limit_low", "limit_low"),
    ("limit_high", "limit_high"),
    ("limit_nominal", "limit_nominal"),
    ("limit_comparator", "limit_comparator"),
    ("characteristic_id", "characteristic_id"),
    ("spec_ref", "spec_ref"),
    ("uut_pin", "uut_pin"),
    ("fixture_connection", "fixture_connection"),
    ("instrument_name", "instrument_name"),
    ("instrument_resource", "instrument_resource"),
    ("instrument_channel", "instrument_channel"),
)


def _occurrence_index_expr(
    *, run_id: str, name: str, step_index: str, step_path: str, vector_index: str
) -> str:
    """SQL for the materialized ``index`` — a measurement/lane's run-wide,
    per-name, retry-STABLE occurrence ordinal (the ``/explore`` X axis).

    0-based DENSE_RANK partitioned by (run, name), ordered by execution
    position (step_index, step_path, then the leaf vector_index with NULL —
    step-scope — sorting first). Retries are EXCLUDED from the ORDER BY, so the
    retried attempts of one position share an ``index`` (retry-stability is
    inherited from the coordinates). Computed ONCE at ingest during the UNNEST
    — the SQL twin of the former query-time ``DENSE_RANK`` (measurements_query
    ``_INDEX_EXPR``); the ``ORDER BY`` must stay byte-identical to it for parity.
    """
    return (
        f"CAST(DENSE_RANK() OVER (PARTITION BY {run_id}, {name} "
        f"ORDER BY {step_index}, {step_path}, COALESCE({vector_index}, -1)) - 1 AS BIGINT)"
    )


def _measurement_unnest_insert(src: str, *, file_path_expr: str) -> str:
    """INSERT that UNNESTs nested measurements from step AND vector rows.

    Coordinate columns come from the carrier row ``v`` (a vector row for a
    vector-scope measurement, a step row for a step-scope one); measurement
    payload from the nested struct ``m``. ``INSERT BY NAME`` aligns the SELECT
    output names with ``measurements_materialized`` (full snowflake, 0.3.1
    phase 7: no step DATA / vector_outcome — the view joins steps/vectors).

    ``vector_index`` carries two roles, kept apart here: a vector row stamps its
    OWN leaf index; a step row stamps NULL (literal — never the step's enclosing
    index, which is a chain-walk selector, not the fact's own coordinate). NULL
    is the load-bearing "belongs to the step itself" marker the NULL-safe joins
    key on. ``ordinal`` (0-based UNNEST-WITH-ORDINALITY position) discriminates
    repeats of a name on one carrier; ``index`` is the materialized occurrence
    ordinal (see :func:`_occurrence_index_expr`).
    """
    meas = ", ".join(f"m.{s} AS {f}" for s, f in _MEAS_STRUCT_TO_FACT)
    proj_vi = "CASE WHEN v.record_type = 'vector' THEN v.vector_index END"
    index_expr = _occurrence_index_expr(
        run_id="v.run_id",
        name="m.name",
        step_index="v.step_index",
        step_path="v.step_path",
        vector_index=proj_vi,
    )
    return f"""
        INSERT INTO measurements_materialized BY NAME
        SELECT
            {file_path_expr} AS file_path,
            v.run_id, v.step_index, v.step_path, COALESCE(v.step_retry, 0) AS step_retry,
            {proj_vi} AS vector_index,
            v.vector_outer_index AS vector_outer_index,
            CASE WHEN v.record_type = 'vector' THEN COALESCE(v.vector_retry, 0) END AS vector_retry,
            CAST(ord AS BIGINT) - 1 AS ordinal,
            {index_expr} AS index,
            {meas}
        FROM {src} AS v, UNNEST(v.measurements) WITH ORDINALITY AS t(m, ord)
        WHERE v.record_type IN ('step', 'vector')
    """


def _bulk_insert_measurements(conn: duckdb.DuckDBPyConnection, meas_paths: list[str]) -> None:
    """Bulk INSERT per-(file, step, measurement_name) aggregates into ``measurement_stats``.

    The raw ``measurements`` view reads parquet on every query — this
    table is the precomputed aggregate side used by analytics queries
    that don't need raw values (yield, pareto, distinct measurement
    names per file, etc.).
    """
    flist = _file_list_sql(meas_paths)

    # ``INSERT BY NAME`` matches SELECT output column names to destination
    # column names — aliases are load-bearing. Measurements are UNNESTed from
    # the vector row's nested ``measurements`` list.
    conn.execute(f"""
        INSERT INTO measurement_stats BY NAME
        SELECT
            v.filename AS file_path,
            v.run_id,
            v.session_id,
            v.step_index,
            v.step_name,
            m.name AS measurement_name,
            m.unit AS measurement_unit,
            m.limit_low AS limit_low,
            m.limit_high AS limit_high,
            m.limit_nominal AS limit_nominal,
            COUNT(*) AS count,
            SUM(CASE WHEN m.outcome = 'passed' THEN 1 ELSE 0 END) AS pass_count,
            SUM(CASE WHEN m.outcome = 'failed' THEN 1 ELSE 0 END) AS fail_count,
            MIN(m.value) AS min_value,
            MAX(m.value) AS max_value,
            AVG(m.value) AS mean_value
        FROM read_parquet({flist}, filename=true, union_by_name=true) AS v,
             UNNEST(v.measurements) AS t(m)
        WHERE v.record_type = 'vector'
        GROUP BY
            v.filename, v.run_id, v.session_id, v.step_index, v.step_name,
            m.name, m.unit, m.limit_low, m.limit_high, m.limit_nominal
    """)


def _bulk_insert_measurement_rows(conn: duckdb.DuckDBPyConnection, fkey: str) -> None:
    """Insert measurement rows from one parquet into the core + lane tables.

    Fixed columns go to ``measurements_materialized`` (``INSERT BY NAME`` aligns
    them with ``RUN_ROW_SCHEMA``). The nested ``inputs``/``outputs`` lanes are
    UNNESTed into the honestly-named ``inputs``/``outputs`` tables at vector
    grain (``DISTINCT`` collapses the per-measurement-row denormalization).
    One-time cost at ingest; subsequent queries hit native tables instead of
    re-scanning parquet footers.
    """
    escaped = _sql_escape(fkey)
    src = f"read_parquet('{escaped}', union_by_name=true)"

    # DELETE first so re-ingest is idempotent (file granularity — measurement
    # rows have no single-column unique key across files).
    conn.execute("DELETE FROM measurements_materialized WHERE file_path = ?", [fkey])
    conn.execute("DELETE FROM inputs WHERE file_path = ?", [fkey])
    conn.execute("DELETE FROM outputs WHERE file_path = ?", [fkey])

    conn.execute(_measurement_unnest_insert(src, file_path_expr=f"'{escaped}'"))

    # Long EAV projections — vector grain, one honestly-named table per role.
    # Drawn from step + vector rows (the lane carriers); DISTINCT (inside
    # _lane_insert) collapses any duplication back to one row per (vector, name).
    for col, table in _LANE_TABLES:
        _lane_insert(conn, table, col, src, file_path_expr=f"'{escaped}'")


def _instrument_unnest_insert(src: str, *, file_path_expr: str) -> str:
    """INSERT that UNNESTs nested instruments from the run row into the fact.

    Instruments are stored on the run row (``WHERE record_type='run'``) as a
    ``LIST<STRUCT>``; the grain is one row per instrument per run. Struct field
    ``name`` maps to column ``role`` and ``id`` maps to ``instrument_id`` to
    avoid shadowing run-level names; the rest are direct. ``INSERT BY NAME``
    aligns the SELECT output names with ``instruments_materialized`` (instrument's
    own fields + the run_id FK — no run identity; the ``instruments`` view joins
    ``runs`` for that — see _create_views).
    """
    return f"""
        INSERT INTO instruments_materialized BY NAME
        SELECT
            {file_path_expr} AS file_path,
            r.run_id,
            i.name AS role,
            i.id AS instrument_id,
            i.driver,
            i.resource,
            i.protocol,
            i.manufacturer,
            i.model,
            i.serial_number,
            i.firmware,
            i.cal_due,
            i.cal_last,
            i.cal_certificate,
            i.cal_lab,
            i.mocked
        FROM {src} AS r, UNNEST(r.instruments) AS t(i)
        WHERE r.record_type = 'run'
    """


def _bulk_insert_instrument_rows(conn: duckdb.DuckDBPyConnection, fkey: str) -> None:
    """Insert instrument rows from one parquet into ``instruments_materialized``.

    Grain: one row per instrument per run. Idempotent: DELETE by file_path first
    so re-ingest is safe.
    """
    escaped = _sql_escape(fkey)
    src = f"read_parquet('{escaped}', union_by_name=true)"
    conn.execute("DELETE FROM instruments_materialized WHERE file_path = ?", [fkey])
    conn.execute(_instrument_unnest_insert(src, file_path_expr=f"'{escaped}'"))


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
            site_index,
            site_name,
            uut_serial_number, uut_part_number, uut_revision, uut_lot_number,
            station_id, station_name, station_hostname,
            fixture_id,
            run_outcome AS outcome,
            run_started_at AS started_at,
            run_ended_at AS ended_at,
            CAST(COALESCE(
                SUM(len(measurements)) FILTER (WHERE record_type <> 'measurement'), 0
            ) AS INTEGER)
                AS num_measurements,
            CAST(COUNT(*) FILTER (WHERE record_type = 'step') AS INTEGER)
                AS num_steps,
            test_phase, part_id, part_name, part_revision,
            station_type, station_location, operator_id, operator_name, project_name,
            git_commit, git_branch, git_remote,
            python_version, litmus_version, env_fingerprint
FROM read_parquet({flist}, filename=true, union_by_name=true)
        WHERE run_id IS NOT NULL
        GROUP BY
            filename, run_id, session_id, site_index, site_name,
            uut_serial_number, uut_part_number, uut_revision, uut_lot_number,
            station_id, station_name, station_hostname,
            fixture_id,
            run_outcome, run_started_at, run_ended_at,
            test_phase, part_id, part_name, part_revision,
            station_type, station_location, operator_id, operator_name, project_name,
            git_commit, git_branch, git_remote,
            python_version, litmus_version, env_fingerprint
ON CONFLICT (run_id) DO UPDATE SET
            file_path = excluded.file_path,
            session_id = excluded.session_id,
            site_index = excluded.site_index,
            site_name = excluded.site_name,
            uut_serial_number = excluded.uut_serial_number,
            uut_part_number = excluded.uut_part_number,
            uut_revision = excluded.uut_revision,
            uut_lot_number = excluded.uut_lot_number,
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
            part_id = excluded.part_id,
            operator_id = excluded.operator_id,
            project_name = excluded.project_name,
            part_name = excluded.part_name,
            part_revision = excluded.part_revision,
            station_type = excluded.station_type,
            station_location = excluded.station_location,
            operator_name = excluded.operator_name,
            git_commit = excluded.git_commit,
            git_branch = excluded.git_branch,
            git_remote = excluded.git_remote,
            python_version = excluded.python_version,
            litmus_version = excluded.litmus_version,
            env_fingerprint = excluded.env_fingerprint
    """)


# Rounds EPOCH-difference seconds to microseconds — timestamps are
# ``timestamp("us")``, so duration is only meaningful to 6 decimals. Rounding
# also erases the float64 tail from EPOCH()'s large-double subtraction,
# matching the overlay's Python ``total_seconds()`` exactly (the inflight↔
# materialized equivalence guard).
_DURATION_S_EXPR = """ROUND(
            CASE
                WHEN ended_at IS NOT NULL AND started_at IS NOT NULL
                THEN EPOCH(ended_at) - EPOCH(started_at)
                ELSE NULL
            END, 6
        ) AS duration_s"""


def _bulk_insert_steps(conn: duckdb.DuckDBPyConnection, parquet_paths: list[str]) -> None:
    """Populate ``steps_materialized`` + ``vectors_materialized`` from the parquets.

    Full snowflake (0.3.1 phase 6): the two grains that used to share the
    dual-grain steps table are now SPLIT into two disjoint inserts —

      * ``steps_materialized`` — one row per LOGICAL step, drawn ONLY from
        ``record_type='step'`` rows (vector_index always NULL at rest). Its
        ``measurement_count`` is the step row's own nested measurements (0 for
        a swept step, whose measurements ride its vector rows).
      * ``vectors_materialized`` — one row per condition point, drawn ONLY from
        ``record_type='vector'`` rows, each carrying its own timing / outcome.

    No run identity, no step-grain data on vectors, no ``dynamic_attrs`` (star
    schema) — the ``steps``/``step_vectors`` views join ``runs`` for identity,
    ``steps`` for step_name/step_index (on the vectors side), and the
    ``inputs``/``outputs`` tables for the inline maps (see _create_views).
    ``step_retry`` stays in each grain key so a rerun is a distinct row.
    """
    flist = _file_list_sql(parquet_paths)
    conn.execute(f"""
        INSERT INTO steps_materialized BY NAME
        WITH grain AS (
            SELECT
                run_id,
                step_path,
                COALESCE(step_retry, 0) AS step_retry,
                vector_outer_index,
                COALESCE(vector_outer_index, -1) AS vector_outer_index_key,
                step_index,
                filename AS file_path,
                step_name,
                ANY_VALUE(step_outcome) AS outcome,
                ANY_VALUE(step_started_at) AS started_at,
                ANY_VALUE(step_ended_at) AS ended_at,
                CAST(COALESCE(SUM(len(measurements)), 0) AS INTEGER) AS measurement_count,
                ANY_VALUE(step_markers) AS markers
            FROM read_parquet({flist}, filename=true, union_by_name=true)
            WHERE run_id IS NOT NULL AND record_type = 'step'
            GROUP BY
                filename, run_id, step_path, COALESCE(step_retry, 0),
                vector_outer_index, step_index, step_name
        )
        SELECT *, {_DURATION_S_EXPR}
        FROM grain
        ON CONFLICT (run_id, step_path, step_retry, vector_outer_index_key)
        DO UPDATE SET
            step_index = excluded.step_index,
            file_path = excluded.file_path,
            step_name = excluded.step_name,
            outcome = excluded.outcome,
            started_at = excluded.started_at,
            ended_at = excluded.ended_at,
            duration_s = excluded.duration_s,
            measurement_count = excluded.measurement_count,
            markers = excluded.markers
    """)
    conn.execute(f"""
        INSERT INTO vectors_materialized BY NAME
        WITH grain AS (
            SELECT
                run_id,
                step_path,
                COALESCE(step_retry, 0) AS step_retry,
                vector_outer_index,
                COALESCE(vector_outer_index, -1) AS vector_outer_index_key,
                vector_index,
                COALESCE(vector_retry, 0) AS vector_retry,
                filename AS file_path,
                ANY_VALUE(vector_outcome) AS outcome,
                ANY_VALUE(vector_started_at) AS started_at,
                ANY_VALUE(vector_ended_at) AS ended_at,
                CAST(COALESCE(SUM(len(measurements)), 0) AS INTEGER) AS measurement_count
            FROM read_parquet({flist}, filename=true, union_by_name=true)
            WHERE run_id IS NOT NULL AND record_type = 'vector'
            GROUP BY
                filename, run_id, step_path, COALESCE(step_retry, 0),
                vector_outer_index, vector_index, COALESCE(vector_retry, 0)
        )
        SELECT *, {_DURATION_S_EXPR}
        FROM grain
        ON CONFLICT (run_id, step_path, step_retry, vector_outer_index_key,
                     vector_index, vector_retry)
        DO UPDATE SET
            file_path = excluded.file_path,
            outcome = excluded.outcome,
            started_at = excluded.started_at,
            ended_at = excluded.ended_at,
            duration_s = excluded.duration_s,
            measurement_count = excluded.measurement_count
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
    connection and competed with the Flight server's query handlers
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

    # Read _ingested under the lock — short read, no contention. This read
    # also FREEZES the cascade-delete candidate set (used at the bottom of
    # this function): only rows already in _ingested at sweep start are
    # deletion candidates, so a run a concurrent notify_new_run/do_put
    # ingests mid-sweep is never wrongly pruned (it isn't in this frozen set)
    # and stays live-served. One read; no per-candidate work added.
    with lock:
        ingested_rows = conn.execute("SELECT path, mtime, size, status FROM _ingested").fetchall()
    ingested_keys: set[tuple[str, float, int]] = {(r[0], r[1], r[2]) for r in ingested_rows}
    frozen_ok_paths: set[str] = {r[0] for r in ingested_rows if r[3] == "ok"}
    needs_ingest = sorted(
        (e for e in disk_entries if (e[0], e[1], e[2]) not in ingested_keys),
        key=lambda e: e[1],  # sort by mtime
        reverse=True,  # newest first so operators see recent runs fast
    )

    # Batched ingest — one ``read_parquet([...])`` per table per batch (runs,
    # steps, measurement_stats, raw measurement rows), instead of opening each
    # parquet ~4× per file. One lock hold per batch; reads stay lock-free
    # (parallel=True) so a longer write hold never blocks a query. A batch
    # that hits a corrupt file rolls back and retries per-file to isolate it.
    _BATCH = 100
    new_run_ids: list[str] = []
    for i in range(0, len(needs_ingest), _BATCH):
        batch = needs_ingest[i : i + _BATCH]
        with lock:
            new_run_ids.extend(
                _ingest_file_batch(conn, batch, collect_run_ids=on_ingested is not None)
            )
    if on_ingested is not None and new_run_ids:
        try:
            on_ingested(new_run_ids)
        except Exception as exc:  # noqa: BLE001
            logger.debug("on_ingested callback failed: %s", exc)

    # Cascade-delete rows whose source parquet is gone from disk. The
    # candidate set is FROZEN to the sweep-start _ingested snapshot (read
    # above, before this sweep's own ingest ran), so a run a concurrent
    # notify ingested mid-sweep is never a candidate — it can't be wrongly
    # pruned, and live/warm serving is never disrupted. Same cost as a plain
    # set-difference; no per-candidate stat.
    disk_paths = {e[0] for e in disk_entries}
    with lock:
        gone = frozen_ok_paths - disk_paths
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

    refusal = _refuse_parquet_version(path_str)
    if refusal is not None:
        report_schema_refusal(refusal, path_str)
        if refusal.deferrable:
            # Newer than this daemon — leave UNLEDGERED so the next (newer)
            # daemon re-reads and ingests it, instead of a permanent skip (#43).
            return
        _mark_ingested(conn, path_str, stat, "quarantined", str(refusal))
        return

    error = _index_unified_parquet(conn, path_str)
    _mark_ingested(conn, path_str, stat, "ok" if error is None else "quarantined", error)


def _quarantine_message(fkey: str, exc: Exception) -> str:
    """One-line quarantine reason; an operator can grep ``_daemon.log``
    for ``Quarantined parquet`` and immediately see which file failed
    and why."""
    return f"Quarantined parquet {fkey}: {type(exc).__name__}: {exc}"


def _refuse_parquet_version(fkey: str) -> SchemaVersionRefused | None:
    """Whitelist-dispatch a runs parquet's schema-version stamp (§1).

    Returns ``None`` when the version is known (ingest proceeds — the adapter is
    identity today, and runs data is read by DuckDB, so this is the whitelist
    guard; real forward-adaptation lands with the first future major). Otherwise
    returns the :class:`SchemaVersionRefused` (its ``.deferrable`` says whether to
    leave it re-attemptable, #43, or quarantine it permanently). An unreadable
    footer returns ``None`` so normal ingest quarantines it as a parse failure.
    """
    try:
        metadata = pq.ParquetFile(fkey).schema_arrow.metadata
    except Exception:  # noqa: BLE001 — unreadable footer: defer to ingest's parse-error path
        return None
    try:
        dispatch(SchemaStore.RUNS, stamp_from_arrow_metadata(metadata))
    except SchemaVersionRefused as exc:
        return exc
    return None


def _index_unified_parquet(conn: duckdb.DuckDBPyConnection, fkey: str) -> str | None:
    """Index one unified per-run parquet into runs / steps / measurements tables.

    Runs through every persistent index in one pass:
      * ``runs_materialized`` — one row per ``run_id``, aggregated.
      * ``steps_materialized`` — one row per ``(run_id, step_path,
        vector_index)``, aggregated; sweep variants get distinct rows.
      * ``measurement_stats`` — per-(file, step, name) rollup over the
        ``measurements_materialized`` fact rows (all-measurement by construction).
      * ``measurements_materialized`` — raw measurement rows (measurement's own
        fields only; no run identity, no ``dynamic_attrs``).
      * ``inputs`` / ``outputs`` — long/EAV projection of the nested lane lists.
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
        _bulk_insert_instrument_rows(conn, fkey)
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


def _ingest_file_batch(
    conn: duckdb.DuckDBPyConnection,
    batch: list[tuple[str, float, int, os.stat_result]],
    *,
    collect_run_ids: bool,
) -> list[str]:
    """Bulk-ingest a batch of NEW parquets — one ``read_parquet([...])`` per
    table for the whole batch instead of per file (the per-file path opened
    each parquet ~4×). On a batch read error (one corrupt file in the set),
    roll back and fall back to per-file ingest so the bad file is isolated +
    quarantined and the good ones still land.

    Caller holds the write lock; all rows for the batch commit atomically.
    Returns the ingested run_ids when ``collect_run_ids`` (else ``[]``).
    """
    # Whitelist-dispatch each file's schema version up front; refused files are
    # quarantined and dropped from the batch so one bad-version file can't block
    # the good ones (mirrors the corrupt-file isolation below). The batch path
    # reads all files in one ``read_parquet([...])``, so the per-file
    # ``_index_unified_parquet`` guard never runs here — the check must live at
    # this boundary too.
    kept: list[tuple[str, float, int, os.stat_result]] = []
    for entry in batch:
        refusal = _refuse_parquet_version(entry[0])
        if refusal is None:
            kept.append(entry)
            continue
        report_schema_refusal(refusal, entry[0])
        if not refusal.deferrable:
            _mark_ingested(conn, entry[0], entry[3], "quarantined", str(refusal))
    batch = kept
    if not batch:
        return []
    paths = [e[0] for e in batch]
    try:
        conn.execute("BEGIN")
        _bulk_insert_runs(conn, paths)
        _bulk_insert_steps(conn, paths)
        _bulk_insert_measurements(conn, paths)
        _batch_insert_measurement_rows(conn, paths)
        _batch_insert_instrument_rows(conn, paths)
        _batch_index_io_and_refs(conn, paths)
        for path_str, _mtime, _size, stat in batch:
            _mark_ingested(conn, path_str, stat, "ok", None)
        conn.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001 — a corrupt file in the set: isolate per-file
        try:
            conn.execute("ROLLBACK")
        except duckdb.Error:
            pass
        logger.warning(
            "Batch ingest of %d files failed (%s); retrying per-file to isolate", len(paths), exc
        )
        for path_str, _mtime, _size, stat in batch:
            _ingest_one_file(conn, Path(path_str), stat)
            try:
                _bulk_insert_measurement_rows(conn, path_str)
                _bulk_insert_instrument_rows(conn, path_str)
            except Exception as exc2:  # noqa: BLE001
                logger.debug("per-file raw-measurement insert failed for %s: %s", path_str, exc2)

    if not collect_run_ids:
        return []
    placeholders = ", ".join("?" * len(paths))
    try:
        return [
            str(r[0])
            for r in conn.execute(
                f"SELECT run_id FROM runs_materialized WHERE file_path IN ({placeholders})", paths
            ).fetchall()
            if r[0]
        ]
    except Exception as exc:  # noqa: BLE001
        logger.debug("run_id lookup after batch ingest failed: %s", exc)
        return []


# ── Inflight overlay — shared tables, lock-free parallel reads ───────


def _create_inflight_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the inflight overlay tables in an attached in-memory database.

    The live-runs overlay used to be per-connection ``register()`` temp
    views — which child cursors can't see, forcing every reader onto one
    locked connection (a read convoy). It needs to be real catalog tables
    (visible to ALL cursors → lock-free parallel reads), but it is also
    purely EPHEMERAL: a projection of the in-memory accumulator pool, which
    is itself rebuilt from the events replay (``unmaterialized_runs``) on
    every daemon start. So it lives in an attached ``:memory:`` database —
    fresh and empty each launch, never written to ``_index.duckdb``, yet
    shared across the connection's cursors. No persistence means no
    restart drop/recreate dance and no stale rows surviving a restart.

    Migration: earlier builds persisted ``inflight_*`` as MAIN tables (and
    the views depended on them). Drop the views, then those orphaned
    tables, so the on-disk catalog is clean; ``_create_views`` rebuilds the
    views against the overlay schema right after.
    """
    for view in ("runs", "steps", "measurements"):
        conn.execute(f"DROP VIEW IF EXISTS {view}")
    for name in ("inflight_runs", "inflight_steps", "inflight_measurements"):
        conn.execute(f"DROP TABLE IF EXISTS {name}")
    conn.execute("ATTACH ':memory:' AS overlay")
    for name, empty in (
        ("inflight_runs", EMPTY_INFLIGHT_RUNS),
        ("inflight_steps", EMPTY_INFLIGHT_STEPS),
        ("inflight_measurements", EMPTY_INFLIGHT_MEASUREMENTS),
    ):
        conn.from_arrow(empty).create(f"overlay.{name}")


# ── Read-side views over parquet ────────────────────────────────────


def _create_views(conn: duckdb.DuckDBPyConnection) -> None:
    """Create or replace the runtime views over the index tables.

    Star schema (projection-normalization, 0.3.1): the materialized tables
    each carry only their own grain's columns + the ``run_id`` FK — run
    identity lives once, in ``runs_materialized``. Every view below JOINs
    ``runs_materialized`` back in so the view's column set (and every
    query written against it) is unchanged from before the refactor —
    "reads JOIN for identity" happens HERE, not duplicated into every
    caller. ``dynamic_attrs`` is gone entirely (no stored MAP, no ``in_``/
    ``out_`` prefix anywhere) — a caller that needs a per-row inputs/outputs
    map derives it at query time from the ``inputs``/``outputs`` tables
    (see ``run_store.get_measurements`` / ``StepsQuery``), not baked into
    these shared views (would tax every yield/pareto/ppk query for a need
    only two call sites have).

    All data views follow the same UNION pattern: persistent rows from the
    on-disk tables UNION ALL in-flight rows from the AccumulatorPool
    (finalized rows suppressed from the inflight side so the parquet
    always wins once ingested). The inflight overlay tables stay
    denormalized (identity inline, no join) — they're an ephemeral,
    replay-rebuilt-on-restart projection, never the drift source the
    star schema targets; ``UNION ALL/BY NAME`` only needs matching
    column NAMES, so the rejoined materialized side and the
    still-denormalized inflight side line up without either needing to
    match the other's internal shape.
    """
    # measurements: full snowflake (0.3.1 phase 7). The materialized fact holds
    # only its own payload + grain key; the view reconstructs the old wide row
    # by JOINing ``runs`` (identity), ``steps`` (step_name/outcome/timing), and
    # ``vectors`` (vector_outcome). ``index`` is a STORED column on the fact
    # (materialized at ingest) — the view exposes it directly, replacing the
    # former query-time ``DENSE_RANK`` wrap; the inflight side computes the same
    # window (its rows aren't materialized yet). UNION BY NAME matches columns
    # by name, so the rejoined fact side and the still-denormalized inflight
    # side line up. file_path is absent from the inflight side (no parquet yet)
    # → automatically NULL.
    inflight_index = _occurrence_index_expr(
        run_id="run_id",
        name="measurement_name",
        step_index="step_index",
        step_path="step_path",
        vector_index="vector_index",
    )
    conn.execute(f"""
        CREATE OR REPLACE VIEW measurements AS
        SELECT
            m.file_path, m.run_id,
            r.session_id, r.site_index, r.site_name,
            r.started_at AS run_started_at, r.ended_at AS run_ended_at,
            CAST(r.outcome AS VARCHAR) AS run_outcome,
            r.uut_serial_number, r.uut_part_number, r.uut_revision, r.uut_lot_number,
            r.part_id, r.part_name, r.part_revision,
            r.station_id, r.station_name, r.station_hostname, r.station_type, r.station_location,
            r.fixture_id, r.test_phase, r.project_name, r.operator_id, r.operator_name,
            r.git_commit, r.git_branch, r.git_remote,
            r.python_version, r.litmus_version, r.env_fingerprint,
            st.step_name, m.step_index, m.step_path, m.step_retry,
            CAST(st.outcome AS VARCHAR) AS step_outcome,
            st.started_at AS step_started_at, st.ended_at AS step_ended_at,
            m.vector_index, m.vector_outer_index, m.vector_retry,
            CAST(ve.outcome AS VARCHAR) AS vector_outcome,
            m.ordinal, m.index,
            m.measurement_name, m.measurement_value, m.measurement_outcome,
            m.measurement_unit, m.measurement_timestamp,
            m.limit_low, m.limit_high, m.limit_nominal, m.limit_comparator,
            m.characteristic_id, m.spec_ref, m.uut_pin, m.fixture_connection,
            m.instrument_name, m.instrument_resource, m.instrument_channel
        FROM measurements_materialized m
        LEFT JOIN runs_materialized r ON r.run_id = m.run_id
        LEFT JOIN steps_materialized st
            ON st.run_id = m.run_id AND st.step_path = m.step_path
            AND st.step_retry = m.step_retry
            AND st.vector_outer_index_key = COALESCE(m.vector_outer_index, -1)
        LEFT JOIN vectors_materialized ve
            ON ve.run_id = m.run_id AND ve.step_path = m.step_path
            AND ve.step_retry = m.step_retry
            AND ve.vector_outer_index_key = COALESCE(m.vector_outer_index, -1)
            AND ve.vector_index = m.vector_index AND ve.vector_retry = m.vector_retry
        UNION BY NAME
        SELECT
            run_id, session_id, site_index, site_name,
            run_started_at, run_ended_at, run_outcome,
            uut_serial_number, uut_part_number, uut_revision, uut_lot_number,
            part_id, part_name, part_revision,
            station_id, station_name, station_hostname, station_type, station_location,
            fixture_id, test_phase, project_name, operator_id, operator_name,
            git_commit, git_branch, git_remote,
            python_version, litmus_version, env_fingerprint,
            step_name, step_index, step_path, step_retry,
            step_outcome, step_started_at, step_ended_at,
            vector_index, vector_outer_index, vector_retry, vector_outcome,
            CAST(NULL AS BIGINT) AS ordinal,
            CAST({inflight_index} AS BIGINT) AS index,
            measurement_name, measurement_value, measurement_outcome,
            measurement_unit, measurement_timestamp,
            limit_low, limit_high, limit_nominal, limit_comparator,
            characteristic_id, spec_ref, uut_pin, fixture_connection,
            instrument_name, instrument_resource, instrument_channel
        FROM overlay.inflight_measurements
        WHERE run_id NOT IN (
            SELECT DISTINCT run_id FROM measurements_materialized
            WHERE run_id IS NOT NULL
        )
    """)

    # ``measurement_facts`` — the LEAN sibling of ``measurements``: the fact
    # JOINed with ``runs`` for identity ONLY (no ``steps`` / ``vectors``
    # reconstruction). TARGETED optimization (0.3.1 phase 9, measured): the
    # pure-aggregate metric paths (yield / ppk / trend / retest / time_loss /
    # summary_counts) group by run identity + measurement-own fields and never
    # read ``step_name`` / ``step_outcome`` / ``vector_outcome`` — DuckDB does
    # NOT prune the unused LEFT JOINs from ``measurements``, so those two joins
    # are pure overhead there (~1ms / 3k rows, growing linearly). ``pareto``
    # (needs ``step_name``), the parametric/explore surfaces, and
    # ``get_measurements`` keep the full ``measurements`` view. Same
    # materialized ∪ inflight shape; no step/vector columns, no ordinal/index
    # (metrics don't select them → the inflight index window is skipped too).
    conn.execute("""
        CREATE OR REPLACE VIEW measurement_facts AS
        SELECT
            m.file_path, m.run_id,
            r.session_id, r.site_index, r.site_name,
            r.started_at AS run_started_at, r.ended_at AS run_ended_at,
            CAST(r.outcome AS VARCHAR) AS run_outcome,
            r.uut_serial_number, r.uut_part_number, r.uut_revision, r.uut_lot_number,
            r.part_id, r.part_name, r.part_revision,
            r.station_id, r.station_name, r.station_hostname, r.station_type, r.station_location,
            r.fixture_id, r.test_phase, r.project_name, r.operator_id, r.operator_name,
            r.git_commit, r.git_branch, r.git_remote,
            r.python_version, r.litmus_version, r.env_fingerprint,
            m.step_index, m.step_path, m.step_retry,
            m.vector_index, m.vector_outer_index, m.vector_retry,
            m.measurement_name, m.measurement_value, m.measurement_outcome,
            m.measurement_unit, m.measurement_timestamp,
            m.limit_low, m.limit_high, m.limit_nominal, m.limit_comparator,
            m.characteristic_id, m.spec_ref, m.uut_pin, m.fixture_connection,
            m.instrument_name, m.instrument_resource, m.instrument_channel
        FROM measurements_materialized m
        LEFT JOIN runs_materialized r ON r.run_id = m.run_id
        UNION BY NAME
        SELECT
            run_id, session_id, site_index, site_name,
            run_started_at, run_ended_at, run_outcome,
            uut_serial_number, uut_part_number, uut_revision, uut_lot_number,
            part_id, part_name, part_revision,
            station_id, station_name, station_hostname, station_type, station_location,
            fixture_id, test_phase, project_name, operator_id, operator_name,
            git_commit, git_branch, git_remote,
            python_version, litmus_version, env_fingerprint,
            step_index, step_path, step_retry,
            vector_index, vector_outer_index, vector_retry,
            measurement_name, measurement_value, measurement_outcome,
            measurement_unit, measurement_timestamp,
            limit_low, limit_high, limit_nominal, limit_comparator,
            characteristic_id, spec_ref, uut_pin, fixture_connection,
            instrument_name, instrument_resource, instrument_channel
        FROM overlay.inflight_measurements
        WHERE run_id NOT IN (
            SELECT DISTINCT run_id FROM measurements_materialized
            WHERE run_id IS NOT NULL
        )
    """)

    # ``runs`` is already the identity home — no join needed. Persistent rows
    # UNION ALL in-flight rows from the in-memory overlay (refreshed from the
    # ``AccumulatorPool``). Suppress in-flight rows whose ``run_id`` already
    # has a finalized parquet — parquet has won and the in-flight projection
    # is stale.
    conn.execute("""
        CREATE OR REPLACE VIEW runs AS
        SELECT * FROM runs_materialized
        UNION ALL BY NAME
        SELECT
            run_id, file_path, session_id, site_index, site_name,
            uut_serial_number, uut_part_number, uut_revision, uut_lot_number,
            station_id, station_name, station_hostname, station_type, station_location,
            fixture_id,
            TRY_CAST(outcome AS outcome_kind) AS outcome,
            started_at, ended_at,
            num_measurements, num_steps, test_phase, part_id, part_name, part_revision,
            operator_id, operator_name, project_name,
            git_commit, git_branch, git_remote
        FROM overlay.inflight_runs
        WHERE run_id NOT IN (SELECT run_id FROM runs_materialized)
    """)
    # Grain-explicit surfaces, now over TWO disjoint materialized tables
    # (full snowflake, 0.3.1 phase 6):
    #   * ``steps``        — one row per LOGICAL step (vector_index always NULL
    #                        here): the code node / ambient carrier. Over
    #                        ``steps_materialized``. Aggregators (yield, pareto,
    #                        dashboards) and the flat step list read this; a
    #                        swept step is ONE row.
    #   * ``step_vectors`` — one row per condition point (vector_index 0..N).
    #                        Over ``vectors_materialized``; JOINs
    #                        ``steps_materialized`` for the enclosing step's
    #                        ``step_name`` / ``step_index`` (not stored on the
    #                        vector grain). The step tree nests these under a step.
    # Both JOIN ``runs`` for identity (the inflight side carries it inline). The
    # column NAME set is identical across the two + their inflight branches so
    # callers see one shape regardless of grain or live/finalized (#24).
    #
    # ``inputs_map`` / ``outputs_map``: constant-empty on the materialized side
    # (the finalized values live in the ``inputs`` / ``outputs`` tables, joined
    # in by the step-detail query, not by these shared views — see _EMPTY_MAP);
    # passed through on the inflight side so a LIVE run still renders its
    # inputs/outputs. StepsQuery COALESCEs the two.
    conn.execute(f"""
        CREATE OR REPLACE VIEW steps AS
        SELECT
            sm.run_id, sm.step_path, sm.step_retry,
            CAST(NULL AS BIGINT) AS vector_index, sm.vector_outer_index,
            sm.step_index, sm.file_path,
            r.session_id, r.site_index, r.site_name,
            sm.step_name, sm.outcome, sm.started_at, sm.ended_at,
            sm.duration_s, sm.measurement_count, sm.markers,
            r.uut_serial_number, r.station_id,
            {_EMPTY_MAP} AS inputs_map, {_EMPTY_MAP} AS outputs_map
        FROM steps_materialized sm
        LEFT JOIN runs_materialized r ON r.run_id = sm.run_id
        UNION ALL BY NAME
        SELECT
            run_id,
            COALESCE(step_path, '') AS step_path,
            step_retry, vector_index, vector_outer_index,
            step_index, file_path, session_id, site_index, site_name,
            step_name,
            TRY_CAST(outcome AS outcome_kind) AS outcome,
            started_at, ended_at, duration_s, measurement_count,
            markers, uut_serial_number, station_id, inputs_map, outputs_map
        FROM overlay.inflight_steps
        WHERE vector_index IS NULL AND run_id NOT IN (SELECT run_id FROM runs_materialized)
    """)
    conn.execute(f"""
        CREATE OR REPLACE VIEW step_vectors AS
        SELECT
            vm.run_id, vm.step_path, vm.step_retry,
            vm.vector_index, vm.vector_outer_index,
            s.step_index, vm.file_path,
            r.session_id, r.site_index, r.site_name,
            s.step_name, vm.outcome, vm.started_at, vm.ended_at,
            vm.duration_s, vm.measurement_count, CAST(NULL AS VARCHAR) AS markers,
            r.uut_serial_number, r.station_id,
            {_EMPTY_MAP} AS inputs_map, {_EMPTY_MAP} AS outputs_map
        FROM vectors_materialized vm
        LEFT JOIN runs_materialized r ON r.run_id = vm.run_id
        LEFT JOIN steps_materialized s
            ON s.run_id = vm.run_id AND s.step_path = vm.step_path
            AND s.step_retry = vm.step_retry
            AND s.vector_outer_index_key = vm.vector_outer_index_key
        UNION ALL BY NAME
        SELECT
            run_id,
            COALESCE(step_path, '') AS step_path,
            step_retry, vector_index, vector_outer_index,
            step_index, file_path, session_id, site_index, site_name,
            step_name,
            TRY_CAST(outcome AS outcome_kind) AS outcome,
            started_at, ended_at, duration_s, measurement_count,
            markers, uut_serial_number, station_id, inputs_map, outputs_map
        FROM overlay.inflight_steps
        WHERE vector_index IS NOT NULL AND run_id NOT IN (SELECT run_id FROM runs_materialized)
    """)

    # ``instruments``: one row per instrument per run, materialized from the
    # run row's nested ``instruments`` LIST<STRUCT> at ingest, JOINed with
    # ``runs`` for identity. No inflight side — the inventory is a
    # finalized-run projection (utilization, which would need the live event
    # stream, is explicitly out of C5 scope).
    conn.execute("""
        CREATE OR REPLACE VIEW instruments AS
        SELECT
            im.file_path, im.run_id,
            r.session_id,
            r.started_at AS run_started_at, r.ended_at AS run_ended_at,
            CAST(r.outcome AS VARCHAR) AS run_outcome,
            r.uut_serial_number, r.uut_part_number, r.part_id,
            r.station_id, r.station_hostname, r.project_name, r.operator_name,
            im.role, im.instrument_id, im.driver, im.resource, im.protocol,
            im.manufacturer, im.model, im.serial_number, im.firmware,
            im.cal_due, im.cal_last, im.cal_certificate, im.cal_lab, im.mocked
        FROM instruments_materialized im
        LEFT JOIN runs_materialized r ON r.run_id = im.run_id
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

    The nested ``inputs``/``outputs`` lanes are UNNESTed into the
    honestly-named ``inputs``/``outputs`` tables at vector grain, each row
    keeping its own ``filename`` so multiple files coexist in one statement.
    """
    flist = "[" + ", ".join(f"'{_sql_escape(p)}'" for p in paths) + "]"

    # Remove any existing rows for these files (idempotent re-ingest)
    placeholders = ", ".join("?" for _ in paths)
    conn.execute(
        f"DELETE FROM measurements_materialized WHERE file_path IN ({placeholders})",
        paths,
    )
    conn.execute(f"DELETE FROM inputs WHERE file_path IN ({placeholders})", paths)
    conn.execute(f"DELETE FROM outputs WHERE file_path IN ({placeholders})", paths)

    # ``union_by_name=true`` pads any column missing from the batch with NULL,
    # so we trust RUN_ROW_SCHEMA rather than null-coalescing per-column.
    # Measurements are UNNESTed from the vector row's nested ``measurements``
    # list; ``filename`` (on the read) is the per-row file_path.
    src = f"read_parquet({flist}, union_by_name=true, filename=true)"

    conn.execute(_measurement_unnest_insert(src, file_path_expr="v.filename"))

    for col, table in _LANE_TABLES:
        _lane_insert(conn, table, col, src, file_path_expr="ctx.filename", with_filename=True)


def _batch_insert_instrument_rows(
    conn: duckdb.DuckDBPyConnection,
    paths: list[str],
) -> None:
    """Insert instrument rows for all *paths* in a single SQL statement.

    Idempotent: DELETEs existing rows for each file before inserting. Mirrors
    the ``_batch_insert_measurement_rows`` pattern.
    """
    flist = "[" + ", ".join(f"'{_sql_escape(p)}'" for p in paths) + "]"
    placeholders = ", ".join("?" for _ in paths)
    conn.execute(
        f"DELETE FROM instruments_materialized WHERE file_path IN ({placeholders})",
        paths,
    )
    src = f"read_parquet({flist}, union_by_name=true, filename=true)"
    conn.execute(_instrument_unnest_insert(src, file_path_expr="r.filename"))


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

    fingerprint = _projection_fingerprint()
    index_path = runs_dir / _index_file_name(fingerprint)
    conn, _ = _open_index(index_path)
    _stamp_epochs_ledger(runs_dir, fingerprint, _installed_version())

    # Writer lock — serializes the index WRITERS (materialize ingest,
    # do_put, background recovery) against each other on the daemon's
    # main connection. Reads do NOT take it: with ``parallel=True`` the
    # Flight server serves ``do_get`` lock-free on per-thread cursors
    # (MVCC snapshots), so concurrent queries never convoy behind a
    # writer or behind each other. (DuckDB serializes write COMMITs
    # internally anyway, so serializing the writers here costs nothing
    # and avoids a multi-writer conflict-retry storm.)
    write_lock = threading.Lock()

    # ── Materializer state ──────────────────────────────────────────
    pool = AccumulatorPool()
    seq_monitor = _EventSequenceMonitor()
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
    materialize_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()

    # Real shared inflight overlay tables (NOT per-connection temp views),
    # so the UNION views in ``_create_views`` resolve on every cursor and
    # reads need no per-query registration.
    _create_inflight_tables(conn)
    _create_views(conn)

    # ── Inflight overlay sync — write-driven, incremental, OFF the read path ─
    # Queries never refresh the overlay (no pre-query hook), so a slow sync
    # can never block a query or a connection probe. ONE background thread is
    # the sole overlay writer: it drains the pool's per-run delta on change
    # and rewrites only the affected runs' rows — O(changed runs), not O(pool).
    overlay_wake = threading.Event()

    def _overlay_sync_once(cur: duckdb.DuckDBPyConnection) -> None:
        """Apply one pool delta to the inflight overlay (sole writer)."""
        delta = pool.take_delta()
        if delta is None:
            return
        touched, run_rows, step_rows, meas_rows = delta
        cur.execute("BEGIN")
        try:
            if touched:
                # Clear the touched runs' rows (covers both re-inserted dirty
                # runs and removed evicted runs) via a registered id set, so a
                # large cold-spawn delta doesn't build a giant IN-list.
                cur.register("_touched", pa.table({"run_id": list(touched)}))
                for tbl in (
                    "overlay.inflight_runs",
                    "overlay.inflight_steps",
                    "overlay.inflight_measurements",
                ):
                    cur.execute(f"DELETE FROM {tbl} WHERE run_id IN (SELECT run_id FROM _touched)")
                cur.unregister("_touched")
            for rows, schema, tbl in (
                (run_rows, INFLIGHT_RUNS_SCHEMA, "overlay.inflight_runs"),
                (step_rows, INFLIGHT_STEPS_SCHEMA, "overlay.inflight_steps"),
                (meas_rows, INFLIGHT_MEASUREMENTS_SCHEMA, "overlay.inflight_measurements"),
            ):
                if rows:
                    cur.from_arrow(pa.Table.from_pylist(rows, schema=schema)).insert_into(tbl)
            cur.execute("COMMIT")
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except duckdb.Error:
                pass
            raise

    def _overlay_sync_loop() -> None:
        """Sole writer of the inflight overlay; drains the pool delta on change.

        Woken by ``overlay_wake`` (set after a pool mutation) with a short
        fallback poll so evicts the wake misses are still applied promptly.
        """
        cur = conn.cursor()
        while not stop_event.is_set():
            overlay_wake.wait(timeout=0.05)
            overlay_wake.clear()
            try:
                _overlay_sync_once(cur)
            except Exception as exc:  # noqa: BLE001 — never kill the sync thread
                logger.warning("overlay sync failed: %s", exc)

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
            # One atomic transaction per run: the six ingest statements
            # (runs / steps / measurements / io+refs / measurement-rows) commit
            # together, so a concurrent reader never sees a half-materialized
            # run (no partial-steps drift), and the daemon pays one commit
            # instead of six. Rolled back as a unit on any failure.
            try:
                conn.execute("BEGIN")
                _ingest_one_file(conn, parquet_path, stat)
                _bulk_insert_measurement_rows(conn, str(parquet_path))
                _bulk_insert_instrument_rows(conn, str(parquet_path))
                conn.execute("COMMIT")
            except Exception as exc:  # noqa: BLE001
                try:
                    conn.execute("ROLLBACK")
                except Exception as rb:  # noqa: BLE001
                    logger.debug("Rollback after failed ingest also failed: %s", rb)
                logger.warning("Ingest failed for %s: %s", parquet_path, exc)
                return
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
                        derived=True,  # daemon completion — exempt from the terminal fence
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
        # Per-writer sequence-gap check on the row columns (writer_key /
        # event_offset). Detect-and-flag only — never drops or blocks.
        seq_monitor.check(evt)
        if et == "run.materialized":
            rid = evt.get("run_id")
            if rid:
                pool.evict(str(rid))
                overlay_wake.set()  # remove this run's inflight overlay rows
            return

        try:
            pool.dispatch(evt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pool dispatch failed for %s: %s", et, exc)
            return
        overlay_wake.set()  # the pool changed — sync this run's overlay rows

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
            except queue.Empty:
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
        orphan_timeout = RUN_ORPHAN_TIMEOUT_SECONDS
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
                derived=True,  # daemon completion — exempt from the terminal fence
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

        Takes ``write_lock`` explicitly: with ``parallel=True`` the Flight
        server no longer wraps ``do_put`` in its own lock, so the writers
        serialize among themselves here while reads stay lock-free.
        """
        with write_lock:
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
                    _bulk_insert_instrument_rows(conn, fpath)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("measurement row insert failed for %s: %s", fpath, exc)

    server, port_file, *_ = start_flight_server_in_daemon(
        mgr=mgr,
        daemon_dir=runs_dir,
        db_name="runs",
        conn=conn,
        put_hook=_on_put,
        port_file_name="_runs_duckdb_flight_port",
        thread_name="runs-duckdb-flight",
        pre_ready=None,
        parallel=True,
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
    threading.Thread(target=_overlay_sync_loop, daemon=True, name="runs-overlay-sync").start()

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
    emitter (pytest plugin, ``StationConnection``, ``SiteRunner``, the
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
