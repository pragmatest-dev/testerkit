"""Shared steps / measurements / IO projection SQL — sibling of
``run_projection``.

The step-, measurement-, and IO-grain projections that turn measurement-grain
per-run Parquet into flat rows. ``source_sql`` is any relation exposing the
measurement-grain columns (e.g. ``read_parquet([...], filename=true, union_by_name=true)``
— local paths or ``s3://``). Pure SQL builders, no I/O — same discipline as
``run_projection``. ``io_projection_select`` (docs/25 #70 stage 1) is the
inputs/outputs EAV counterpart to ``measurements_projection_select``:
same carrier rows and grain-key expressions, over the ``inputs``/``outputs``
LIST<STRUCT> IO lists instead of ``measurements``.

**Who uses this (accurately).** The cloud serving tier (`testerkit-server`) imports
these builders directly, so its ``steps`` / ``measurements`` shape is derived
from testerkit, not a cloud copy. The local runs daemon retains its OWN equivalent
derivation (``_runs_duckdb_daemon._bulk_insert_steps`` / ``_measurement_unnest_insert``)
— it is NOT yet rewired to import this module (that would be an additive daemon change,
a flagged follow-up). The two are separate implementations of the same projection;
what keeps them honest is that BOTH are validated against testerkit's real
event→accumulator→unified-rows output (this module by
``tests/test_data/test_measurement_projection.py``'s real-derive parity; the daemon by
its own ``StepsQuery``/``MeasurementsQuery`` tests), so neither can silently diverge
from the derived truth. A direct projection==daemon SQL-equality test is the
defense-in-depth follow-up.

**On this projection's name and ``step_name`` (intentional, not a divergence).**
Locally, ``step_name`` lives on the fuller ``measurements`` VIEW (a join of the lean
fact grain to ``steps``); DuckDB runs that join in-process over one bench's data for
next to nothing, so the local fact table stays lean and normalized. The cloud serves
**every measurement across every tenant** from BigQuery, where a per-query join of
the all-tenant fact table against ``steps`` is a shuffle join to avoid — so the
cloud **materializes** the ``measurements``-view shape once at derive time by
denormalizing ``step_name`` (available directly on the carrier row) onto each fact
row. Same values as local's ``measurements`` view; the difference is materialize-at-
derive (cloud, fleet scale) vs join-at-read (local, in-process) — a deliberate,
engine-driven materialization, single-sourced here. This module's builder is named
``measurements_projection_select`` (and its columns ``MEASUREMENTS_COLUMNS``) to
match local's own ``measurements`` view name — not the lean ``measurement_facts``
view, which this projection's FULL shape does not correspond to.

Mirrors the daemon's ``_bulk_insert_steps`` ``grain`` CTE and
``_measurement_unnest_insert`` (``_occurrence_index_expr`` + the UNNEST shape), but
denormalizes run/UUT/station context directly off the raw row (ANY_VALUE — constant
per run) instead of a separate ``runs_materialized`` join, since a projection runs
once per derive and needs no persistent runs table. Column tuples are drift-guarded
against these SELECTs by ``tests/test_data/test_measurement_projection.py``.

**Canonical grain keys** (the served tables' uniqueness contract — docs/36 P3 root-
cause fix) live in the PRIVATE ``testerkit.data._schema_keys`` module, not here:
this module is public library surface (a bench client's own code may import
``STEPS_COLUMNS``/etc.), and a served table's storage-dedup grain is an internal
implementation detail, not something to promote into that surface. See
``_schema_keys.py``'s docstring.
"""

from __future__ import annotations

# ``steps`` flat-row columns, in order — MUST match `steps_projection_select`'s
# SELECT list order exactly (drift-guarded). Denormalized run/UUT/station context
# (constant per run) + the step's own identity, timing, and rollups.
STEPS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "STRING"),
    ("file_path", "STRING"),
    ("session_id", "STRING"),
    ("site_index", "INTEGER"),
    ("site_name", "STRING"),
    ("uut_serial_number", "STRING"),
    ("uut_part_number", "STRING"),
    ("uut_revision", "STRING"),
    ("uut_lot_number", "STRING"),
    ("station_id", "STRING"),
    ("station_name", "STRING"),
    ("station_hostname", "STRING"),
    ("fixture_id", "STRING"),
    ("test_phase", "STRING"),
    ("part_id", "STRING"),
    ("part_name", "STRING"),
    ("part_revision", "STRING"),
    ("station_type", "STRING"),
    ("station_location", "STRING"),
    ("operator_id", "STRING"),
    ("operator_name", "STRING"),
    ("project_name", "STRING"),
    ("run_outcome", "STRING"),
    ("step_path", "STRING"),
    ("step_retry", "INTEGER"),
    ("vector_outer_index", "INTEGER"),
    ("step_index", "INTEGER"),
    ("step_name", "STRING"),
    ("outcome", "STRING"),
    ("started_at", "TIMESTAMP"),
    ("ended_at", "TIMESTAMP"),
    ("duration_s", "FLOAT64"),
    ("measurement_count", "INTEGER"),
    ("markers", "STRING"),
)

# ``measurements`` flat-row columns, in order — MUST match
# `measurements_projection_select`'s SELECT list order exactly (drift-guarded).
# Denormalized run context + the measurement's own grain key + payload; `step_name`
# is denormalized (see module docstring — the cloud's fleet-scale materialization of
# local's `measurements` view).
MEASUREMENTS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "STRING"),
    ("file_path", "STRING"),
    ("session_id", "STRING"),
    ("site_index", "INTEGER"),
    ("site_name", "STRING"),
    ("uut_serial_number", "STRING"),
    ("uut_part_number", "STRING"),
    ("uut_revision", "STRING"),
    ("uut_lot_number", "STRING"),
    ("station_id", "STRING"),
    ("station_name", "STRING"),
    ("station_hostname", "STRING"),
    ("fixture_id", "STRING"),
    ("test_phase", "STRING"),
    ("part_id", "STRING"),
    ("part_name", "STRING"),
    ("part_revision", "STRING"),
    ("station_type", "STRING"),
    ("station_location", "STRING"),
    ("operator_id", "STRING"),
    ("operator_name", "STRING"),
    ("project_name", "STRING"),
    ("run_started_at", "TIMESTAMP"),
    ("run_ended_at", "TIMESTAMP"),
    ("run_outcome", "STRING"),
    ("step_index", "INTEGER"),
    ("step_path", "STRING"),
    ("step_retry", "INTEGER"),
    ("step_name", "STRING"),
    ("step_outcome", "STRING"),
    ("step_started_at", "TIMESTAMP"),
    ("step_ended_at", "TIMESTAMP"),
    ("vector_index", "INTEGER"),
    ("vector_outer_index", "INTEGER"),
    ("vector_retry", "INTEGER"),
    ("vector_outcome", "STRING"),
    ("ordinal", "INTEGER"),
    ("index", "INTEGER"),
    ("measurement_name", "STRING"),
    ("measurement_value", "FLOAT64"),
    ("measurement_outcome", "STRING"),
    ("measurement_unit", "STRING"),
    ("measurement_timestamp", "TIMESTAMP"),
    ("limit_low", "FLOAT64"),
    ("limit_high", "FLOAT64"),
    ("limit_nominal", "FLOAT64"),
    ("limit_comparator", "STRING"),
    ("characteristic_id", "STRING"),
    ("spec_ref", "STRING"),
    ("uut_pin", "STRING"),
    ("fixture_connection", "STRING"),
    ("instrument_name", "STRING"),
    ("instrument_resource", "STRING"),
    ("instrument_channel", "STRING"),
    ("git_commit", "STRING"),
    ("git_branch", "STRING"),
    ("git_remote", "STRING"),
    ("python_version", "STRING"),
    ("testerkit_version", "STRING"),
    ("env_fingerprint", "STRING"),
)


def _duration_s_expr(*, started_at: str, ended_at: str) -> str:
    """Computed duration in seconds — same formula as
    `run_projection.DURATION_S_EXPR`, parameterized over whichever pair of
    timing columns (step or vector) the caller is rolling up."""
    return f"""ROUND(
            CASE
                WHEN {ended_at} IS NOT NULL AND {started_at} IS NOT NULL
                THEN EPOCH({ended_at}) - EPOCH({started_at})
                ELSE NULL
            END, 6
        ) AS duration_s"""


# Worst-wins collapse of `step_outcome` across a swept step's grouped variant
# rows (a swept step can emit multiple `record_type='step'` rows sharing the
# same grain key, one per sweep variant). `ANY_VALUE` picked an arbitrary
# variant's outcome, so a PASSED variant could hide a FAILED one. This ranks
# each outcome by severity and keeps the worst — the SQL twin of
# `models.escalate_outcome` / `models._OUTCOME_SEVERITY`
# (ABORTED=7 > TERMINATED=6 > ERRORED=5 > FAILED=4 > PASSED=3 > DONE=2 >
# SKIPPED=1; unjudged/NULL ranks below everything). Keep these ranks in sync
# with `models._OUTCOME_SEVERITY` if that ladder ever changes.
WORST_STEP_OUTCOME_EXPR = """CASE MAX(CASE step_outcome
                WHEN 'aborted' THEN 7
                WHEN 'terminated' THEN 6
                WHEN 'errored' THEN 5
                WHEN 'failed' THEN 4
                WHEN 'passed' THEN 3
                WHEN 'done' THEN 2
                WHEN 'skipped' THEN 1
                ELSE 0
            END)
                WHEN 7 THEN 'aborted'
                WHEN 6 THEN 'terminated'
                WHEN 5 THEN 'errored'
                WHEN 4 THEN 'failed'
                WHEN 3 THEN 'passed'
                WHEN 2 THEN 'done'
                WHEN 1 THEN 'skipped'
                ELSE NULL
            END AS outcome"""

# Run-context columns denormalized onto every measurement-grain row (see
# `schemas.RUN_ROW_SCHEMA`) — pulled with ANY_VALUE since they are constant within
# one (filename, run_id) group, same discipline as `run_projection._GROUP_COLUMNS`.
_RUN_CONTEXT_COLUMNS = (
    "session_id",
    "site_index",
    "site_name",
    "uut_serial_number",
    "uut_part_number",
    "uut_revision",
    "uut_lot_number",
    "station_id",
    "station_name",
    "station_hostname",
    "fixture_id",
    "test_phase",
    "part_id",
    "part_name",
    "part_revision",
    "station_type",
    "station_location",
    "operator_id",
    "operator_name",
    "project_name",
)


def steps_projection_select(source_sql: str) -> str:
    """Flat, one-row-per-LOGICAL-step projection over a measurement-grain
    ``source_sql`` (a ``read_parquet(...)`` relation, local paths or ``s3://``).

    Mirrors the daemon's ``steps_materialized`` ``grain`` CTE
    (`_bulk_insert_steps`), but denormalizes run/UUT/station context directly off
    the raw row (ANY_VALUE — constant per run) instead of a separate
    `runs_materialized` join, since there is no persistent runs table on the
    projection path (this SQL IS the projection, run once per derive).

    Rollups (kept in lockstep with the daemon's own `_bulk_insert_steps`,
    docs/36 P1a):
      * ``started_at``/``ended_at`` are ``MIN``/``MAX`` over a swept step's
        grouped variant rows, not ``ANY_VALUE`` — a swept step's variants
        execute at different wall-clock times, so ``ANY_VALUE`` understated
        the step's true execution window (picked one arbitrary variant's
        timing instead of the full span).
      * ``measurement_count`` is the step's OWN nested ``measurements`` PLUS
        the summed ``measurement_count`` of every vector nested under it (a
        swept step's measurements ride its ``record_type='vector'`` rows, not
        its own ``record_type='step'`` row, which was landing 0 for any swept
        step before this fix).
    """
    ctx_any = ",\n            ".join(f"ANY_VALUE({c}) AS {c}" for c in _RUN_CONTEXT_COLUMNS)
    return f"""
        WITH vector_measurement_counts AS (
            SELECT
                run_id,
                step_path,
                COALESCE(step_retry, 0) AS step_retry,
                COALESCE(vector_outer_index, -1) AS vector_outer_index_key,
                CAST(COALESCE(SUM(len(measurements)), 0) AS INTEGER) AS vector_measurement_count
            FROM {source_sql}
            WHERE run_id IS NOT NULL AND record_type = 'vector'
            GROUP BY
                run_id, step_path, COALESCE(step_retry, 0), COALESCE(vector_outer_index, -1)
        ),
        grain AS (
            SELECT
                run_id,
                filename AS file_path,
                {ctx_any},
                ANY_VALUE(CAST(run_outcome AS VARCHAR)) AS run_outcome,
                step_path,
                COALESCE(step_retry, 0) AS step_retry_norm,
                vector_outer_index,
                step_index,
                step_name,
                {WORST_STEP_OUTCOME_EXPR},
                MIN(step_started_at) AS step_started_at,
                MAX(step_ended_at) AS step_ended_at,
                CAST(COALESCE(SUM(len(measurements)), 0) AS INTEGER) AS own_measurement_count,
                ANY_VALUE(step_markers) AS markers
            FROM {source_sql}
            WHERE run_id IS NOT NULL AND record_type = 'step'
            GROUP BY
                filename, run_id, step_path, COALESCE(step_retry, 0),
                vector_outer_index, step_index, step_name
        )
        SELECT
            g.run_id, g.file_path,
            {", ".join(f"g.{c}" for c in _RUN_CONTEXT_COLUMNS)},
            g.run_outcome,
            g.step_path, g.step_retry_norm AS step_retry, g.vector_outer_index,
            g.step_index, g.step_name,
            g.outcome,
            g.step_started_at AS started_at, g.step_ended_at AS ended_at,
            {_duration_s_expr(started_at="g.step_started_at", ended_at="g.step_ended_at")},
            g.own_measurement_count + COALESCE(vc.vector_measurement_count, 0)
                AS measurement_count,
            g.markers
        FROM grain g
        LEFT JOIN vector_measurement_counts vc
            ON vc.run_id = g.run_id AND vc.step_path = g.step_path
            AND vc.step_retry = g.step_retry_norm
            AND vc.vector_outer_index_key = COALESCE(g.vector_outer_index, -1)"""


# ``vectors`` flat-row columns, in order — MUST match `vectors_projection_select`'s
# SELECT list order exactly (drift-guarded). One row per condition-point execution
# (a sweep variant / in-body ``vectors`` loop iteration) — the grain the daemon's
# ``vectors_materialized`` table holds (`_bulk_insert_steps`'s second INSERT). The
# gap this closes (docs/36 P1a): the shared projection had no vectors grain at
# all before this — a swept step's per-variant rows (load_regulation-shape runs:
# 6 vectors, two container passes as distinct rows) were unrepresented.
VECTORS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "STRING"),
    ("file_path", "STRING"),
    ("session_id", "STRING"),
    ("site_index", "INTEGER"),
    ("site_name", "STRING"),
    ("uut_serial_number", "STRING"),
    ("uut_part_number", "STRING"),
    ("uut_revision", "STRING"),
    ("uut_lot_number", "STRING"),
    ("station_id", "STRING"),
    ("station_name", "STRING"),
    ("station_hostname", "STRING"),
    ("fixture_id", "STRING"),
    ("test_phase", "STRING"),
    ("part_id", "STRING"),
    ("part_name", "STRING"),
    ("part_revision", "STRING"),
    ("station_type", "STRING"),
    ("station_location", "STRING"),
    ("operator_id", "STRING"),
    ("operator_name", "STRING"),
    ("project_name", "STRING"),
    ("run_outcome", "STRING"),
    ("step_path", "STRING"),
    ("step_retry", "INTEGER"),
    ("vector_outer_index", "INTEGER"),
    ("vector_index", "INTEGER"),
    ("vector_retry", "INTEGER"),
    ("step_index", "INTEGER"),
    ("step_name", "STRING"),
    ("outcome", "STRING"),
    ("started_at", "TIMESTAMP"),
    ("ended_at", "TIMESTAMP"),
    ("duration_s", "FLOAT64"),
    ("measurement_count", "INTEGER"),
)


def vectors_projection_select(source_sql: str) -> str:
    """Flat, one-row-per-CONDITION-POINT projection over a measurement-grain
    ``source_sql`` — the vectors grain `steps_projection_select` doesn't cover
    (docs/36 P1a: the shape linchpin the cloud had no projection for at all).

    Mirrors the daemon's ``vectors_materialized`` ``grain`` CTE
    (`_bulk_insert_steps`'s second INSERT) exactly — same grain key
    (``run_id, step_path, step_retry, vector_outer_index, vector_index,
    vector_retry``), same ``ANY_VALUE`` rollup for outcome/timing (unlike
    steps, a vector's grain key is not expected to collapse multiple variant
    rows — each condition point is its own execution — so there is no
    worst-wins collapse here, matching the daemon). ``step_name``/
    ``step_index`` are denormalized straight off the vector's own carrier row
    (the enclosing step's identity travels with every vector at write time —
    see ``build_vector_row``), not joined from a separate steps relation.

    Denormalizes run/UUT/station context directly off the raw row (ANY_VALUE
    — constant per run), same discipline as `steps_projection_select`.
    """
    ctx_any = ",\n            ".join(f"ANY_VALUE({c}) AS {c}" for c in _RUN_CONTEXT_COLUMNS)
    return f"""
        WITH grain AS (
            SELECT
                run_id,
                filename AS file_path,
                {ctx_any},
                ANY_VALUE(CAST(run_outcome AS VARCHAR)) AS run_outcome,
                step_path,
                COALESCE(step_retry, 0) AS step_retry_norm,
                vector_outer_index,
                vector_index,
                COALESCE(vector_retry, 0) AS vector_retry_norm,
                ANY_VALUE(step_index) AS step_index,
                ANY_VALUE(step_name) AS step_name,
                ANY_VALUE(vector_outcome) AS outcome,
                ANY_VALUE(vector_started_at) AS vector_started_at,
                ANY_VALUE(vector_ended_at) AS vector_ended_at,
                CAST(COALESCE(SUM(len(measurements)), 0) AS INTEGER) AS measurement_count
            FROM {source_sql}
            WHERE run_id IS NOT NULL AND record_type = 'vector'
            GROUP BY
                filename, run_id, step_path, COALESCE(step_retry, 0),
                vector_outer_index, vector_index, COALESCE(vector_retry, 0)
        )
        SELECT
            run_id, file_path,
            {", ".join(_RUN_CONTEXT_COLUMNS)},
            run_outcome,
            step_path, step_retry_norm AS step_retry, vector_outer_index,
            vector_index, vector_retry_norm AS vector_retry,
            step_index, step_name,
            outcome,
            vector_started_at AS started_at, vector_ended_at AS ended_at,
            {_duration_s_expr(started_at="vector_started_at", ended_at="vector_ended_at")},
            measurement_count
        FROM grain"""


# LEGACY (testerkit-server's legacy serving paths still import this shape —
# kept in place, unchanged, for them): a combined role-tagged EAV relation
# over BOTH ``inputs``/``outputs``, with a ``role`` column and a collapsed
# single ``value``/``value_json``. This violates docs/44 §1 ("no EAV rows, no
# `role` column, no collapsed single `value`") — the honestly-named,
# two-table replacement is `IO_TABLE_COLUMNS`/`io_table_select`/
# `inputs_projection_select`/`outputs_projection_select` below. Removed once
# the server's legacy paths go.
#
# ``inputs``/``outputs`` (IO EAV) flat-row columns, in order — MUST match
# `io_projection_select`'s SELECT list order exactly (drift-guarded).
# Denormalized run context (same set as `MEASUREMENTS_COLUMNS`) + the
# IO entry's carrier grain key + its own ``(role, name, value)`` payload.
IO_ROW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "STRING"),
    ("file_path", "STRING"),
    ("session_id", "STRING"),
    ("site_index", "INTEGER"),
    ("site_name", "STRING"),
    ("uut_serial_number", "STRING"),
    ("uut_part_number", "STRING"),
    ("uut_revision", "STRING"),
    ("uut_lot_number", "STRING"),
    ("station_id", "STRING"),
    ("station_name", "STRING"),
    ("station_hostname", "STRING"),
    ("fixture_id", "STRING"),
    ("test_phase", "STRING"),
    ("part_id", "STRING"),
    ("part_name", "STRING"),
    ("part_revision", "STRING"),
    ("station_type", "STRING"),
    ("station_location", "STRING"),
    ("operator_id", "STRING"),
    ("operator_name", "STRING"),
    ("project_name", "STRING"),
    ("run_started_at", "TIMESTAMP"),
    ("run_ended_at", "TIMESTAMP"),
    ("run_outcome", "STRING"),
    ("step_index", "INTEGER"),
    ("step_path", "STRING"),
    ("step_retry", "INTEGER"),
    ("vector_index", "INTEGER"),
    ("vector_outer_index", "INTEGER"),
    ("vector_retry", "INTEGER"),
    ("role", "STRING"),
    ("name", "STRING"),
    ("value_json", "STRING"),
    ("value", "FLOAT64"),
    ("unit", "STRING"),
)

# Which IO list column feeds which ``role`` literal — the EAV split is a
# UNION ALL over both, not two separate tables (unlike the local daemon's
# ``inputs``/``outputs`` tables — see `_io_unnest_select`'s docstring).
_IO_ROLES: tuple[tuple[str, str], ...] = (
    ("inputs", "input"),
    ("outputs", "output"),
)


def _io_value_json_expr(alias: str) -> str:
    """Reconstruct an IO entry's raw value as JSON text.

    The at-rest IO struct (``_row_helpers.IO_FIELDS``) is a type-dispatched
    EAV, not a single raw-value column: ``value_type`` selects exactly one
    ``value_*`` field (``_io_entry`` / ``_io_value``), and only ``list``/
    ``dict`` entries are pre-encoded as JSON text in ``value_json`` at write
    time. This expression is the SQL twin of ``_io_value`` fused with
    ``json.dumps``: it dispatches on ``value_type`` the same way, and uses
    DuckDB's ``to_json`` to serialize whichever typed field holds the value so
    every entry — scalar or nested — has one JSON-text representation.
    """
    return f"""CASE
                WHEN {alias}.value_type IN ('list', 'dict') THEN {alias}.value_json
                WHEN {alias}.value_type = 'scalar:bool' THEN to_json({alias}.value_bool)
                WHEN {alias}.value_type = 'scalar:int' THEN to_json({alias}.value_int)
                WHEN {alias}.value_type = 'scalar:float' THEN to_json({alias}.value_double)
                WHEN {alias}.value_type = 'scalar:datetime' THEN to_json({alias}.value_timestamp)
                ELSE to_json({alias}.value_text)
            END"""


def _io_unnest_select(source_sql: str, *, col: str, role: str) -> str:
    """One role's half of the ``io_projection_select`` UNION ALL —
    UNNESTs ``v.{col}`` (``inputs`` or ``outputs``) from step AND vector rows,
    tagging every row with the literal ``role``.

    Grain-key expressions (``step_index``/``step_path``/``step_retry``/
    ``vector_index``/``vector_outer_index``/``vector_retry``) are byte-identical
    to `measurements_projection_select`'s, so an IO row joins cleanly to
    its carrier's measurement rows on that key (same discipline the
    daemon's ``_io_insert`` documents for its own ``step_retry``/
    ``vector_retry`` normalization).
    """
    proj_vi = "CASE WHEN v.record_type = 'vector' THEN v.vector_index END"
    proj_vr = "CASE WHEN v.record_type = 'vector' THEN COALESCE(v.vector_retry, 0) END"
    ctx_v = ",\n            ".join(f"v.{c}" for c in _RUN_CONTEXT_COLUMNS)
    return f"""
        SELECT
            v.run_id, v.filename AS file_path,
            {ctx_v},
            v.run_started_at, v.run_ended_at,
            CAST(v.run_outcome AS VARCHAR) AS run_outcome,
            v.step_index, v.step_path, COALESCE(v.step_retry, 0) AS step_retry,
            {proj_vi} AS vector_index,
            v.vector_outer_index,
            {proj_vr} AS vector_retry,
            '{role}' AS role,
            u.name AS name,
            {_io_value_json_expr("u")} AS value_json,
            u.unit
        FROM {source_sql} AS v, UNNEST(v.{col}) AS t(u)
        WHERE v.run_id IS NOT NULL AND v.record_type IN ('step', 'vector')"""


def io_projection_select(source_sql: str) -> str:
    """LEGACY (kept only for testerkit-server's legacy serving paths — see
    `IO_ROW_COLUMNS`'s docstring; violates docs/44 §1's "no EAV rows, no
    `role` column, no collapsed single `value`"; removed when those paths go.
    Prefer `inputs_projection_select`/`outputs_projection_select`).

    Flat, long/EAV rows over the nested ``inputs``/``outputs`` IO lists — one
    row per IO entry, UNNESTed from step AND vector rows.

    Mirrors `measurements_projection_select`'s shape (denormalized run
    context, same carrier rows, same grain-key expressions) but over the
    ``inputs``/``outputs`` LIST<STRUCT> columns instead of ``measurements``.
    ``role`` distinguishes an ``inputs`` entry from an ``outputs`` one (a
    UNION ALL of both lists into one relation, not the local daemon's two
    separate ``inputs``/``outputs`` tables — a projection is a single SELECT).

    ``value_json`` is the IO entry's raw value reconstructed as JSON text (see
    :func:`_io_value_json_expr` — the IO struct is a type-dispatched EAV,
    not a single raw-value column at rest); ``value`` is a ``TRY_CAST`` of
    that JSON to DOUBLE, so a numeric entry (a swept input, a scalar output) is
    queryable as a number and a non-numeric one (a string, a URI, a JSON list/
    dict) comes through as NULL rather than raising.
    """
    role_selects = [_io_unnest_select(source_sql, col=col, role=role) for col, role in _IO_ROLES]
    union_sql = "\n            UNION ALL\n            ".join(role_selects)
    return f"""
        WITH io_rows AS ({union_sql}
        )
        SELECT
            run_id, file_path,
            {", ".join(_RUN_CONTEXT_COLUMNS)},
            run_started_at, run_ended_at, run_outcome,
            step_index, step_path, step_retry,
            vector_index, vector_outer_index, vector_retry,
            role, name, value_json,
            TRY_CAST(value_json AS DOUBLE) AS value,
            unit
        FROM io_rows"""


# ── Canonical ``inputs``/``outputs`` table projection (docs/44 §1) ─────────
#
# The honestly-named replacement for the LEGACY combined shape above: two
# separate relations (the table IS the role — no ``role`` column), carrier
# keys + ``ordinal`` (0-based UNNEST-WITH-ORDINALITY position) + ``index``
# (per-name occurrence, `_occurrence_index_expr`) + the typed
# ``value_*``/``unit``/``uut_pin`` fields — byte-identical to what the local
# runs daemon's ``inputs``/``outputs`` tables hold. Single-sourced: the
# daemon's ``_io_insert`` composes ``INSERT INTO {table} BY NAME`` around
# `io_table_select`'s exact SELECT; `inputs_projection_select`/
# `outputs_projection_select` run the SAME SELECT for the cloud read models.

# Canonical column list for ``inputs``/``outputs`` (both tables share this
# shape — the table IS the role) — IS `_runs_duckdb_daemon._IO_PERSISTED_COLUMNS`
# (imported from here, never hand-duplicated). DuckDB SQL types, not this
# module's usual BigQuery-style STRING/INTEGER tuples: the daemon uses this
# SAME tuple to generate its ``CREATE TABLE inputs``/``outputs`` DDL, so the
# type strings must stay DuckDB-valid.
IO_TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
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

# The IO entry's own typed fields, unaliased, read off the UNNESTed struct
# alias ``u`` — the tail of `io_table_select`'s SELECT list.
_IO_TABLE_SELECT = (
    "u.name, u.value_type, u.value_int, u.value_double, u.value_bool, "
    "u.value_text, u.value_timestamp, u.value_json, u.unit, u.uut_pin"
)


def io_table_select(
    source_sql: str,
    *,
    col: str,
    file_path_expr: str,
    with_filename: bool = False,
) -> str:
    """The exact SELECT the ``inputs``/``outputs`` tables are built from.

    ``source_sql`` is a relation exposing the measurement-grain columns
    (local: a ``read_parquet(...)`` relation; cloud: one run's in-memory
    Arrow table wrapped the same way). ``col`` picks which nested IO list to
    UNNEST (``inputs`` or ``outputs`` — the caller also picks the matching
    destination table name locally). Rows come from
    ``record_type IN ('step', 'vector')`` — the IO carriers.

    ``step_path`` and ``step_retry`` ride along so a read-time EAV join can
    disambiguate two unswept steps sharing a ``step_index`` (resets per
    parent bucket) and two reruns of the same step (pytest-rerunfailures).
    ``ordinal`` (0-based UNNEST-WITH-ORDINALITY position) discriminates
    repeats of an IO name on one carrier; ``index`` is the materialized
    per-name occurrence ordinal (`_occurrence_index_expr`), symmetric with
    measurements. ``step_retry``/``vector_retry`` are normalized IDENTICALLY
    to `measurements_projection_select` so an IO row and its carrier's
    measurement rows land on the same join key: ``step_retry`` → 0-based
    (COALESCE NULL→0), ``vector_retry`` → NULL for a step carrier
    (``vector_index`` NULL at rest), 0-based for a vector carrier.

    ``file_path_expr`` is the SQL expression stamped as ``file_path`` — a
    string literal for a single-file source, or a real ``filename`` column
    (pass ``with_filename=True`` so the inner subquery projects it) for a
    multi-file batch read / an already-filename-bearing relation.
    """
    prefix = "filename, " if with_filename else ""
    index_expr = _occurrence_index_expr(
        run_id="ctx.run_id",
        name="u.name",
        step_index="ctx.step_index",
        step_path="ctx.step_path",
        vector_index="ctx.vector_index",
    )
    return f"""
        SELECT
            {file_path_expr} AS file_path, ctx.run_id, ctx.step_index, ctx.step_path,
            COALESCE(ctx.step_retry, 0) AS step_retry,
            ctx.vector_index, ctx.vector_outer_index,
            CASE WHEN ctx.vector_index IS NOT NULL THEN COALESCE(ctx.vector_retry, 0) END
                AS vector_retry,
            CAST(ord AS BIGINT) - 1 AS ordinal,
            {index_expr} AS index,
            {_IO_TABLE_SELECT}
        FROM (
            SELECT {prefix}run_id, step_index, step_path, step_retry, vector_index,
                   vector_outer_index, vector_retry, {col}
            FROM {source_sql}
            WHERE record_type IN ('step', 'vector')
        ) AS ctx, UNNEST(ctx.{col}) WITH ORDINALITY AS t(u, ord)"""


def inputs_projection_select(source_sql: str) -> str:
    """One row per ``inputs`` entry, byte-identical to the local daemon's
    ``inputs`` table (docs/44 §1) — the honestly-named replacement for
    `io_projection_select`'s role-filtered legacy shape. ``source_sql`` is
    expected to expose a ``filename`` column (this module's usual
    ``source_sql`` contract), stamped through as ``file_path``."""
    return io_table_select(
        source_sql, col="inputs", file_path_expr="ctx.filename", with_filename=True
    )


def outputs_projection_select(source_sql: str) -> str:
    """One row per ``outputs`` entry, byte-identical to the local daemon's
    ``outputs`` table (docs/44 §1). See :func:`inputs_projection_select`."""
    return io_table_select(
        source_sql, col="outputs", file_path_expr="ctx.filename", with_filename=True
    )


def _occurrence_index_expr(
    *, run_id: str, name: str, step_index: str, step_path: str, vector_index: str
) -> str:
    """SQL for the materialized ``index`` — a measurement/IO entry's run-wide,
    per-name, retry-STABLE occurrence ordinal (the ``/explore`` X axis).

    0-based DENSE_RANK partitioned by (run, name), ordered by execution
    position (step_index, step_path, then the leaf vector_index with NULL —
    step-scope — sorting first). Retries are EXCLUDED from the ORDER BY, so
    the retried attempts of one position share an occurrence index
    (retry-stability is inherited from the coordinates).

    Single-sourced (merged here, docs/44 §1): the local runs daemon's
    ``measurements_materialized``/``inputs``/``outputs`` tables compute this
    SAME expression at ingest (formerly a daemon-private duplicate of this
    formula — the daemon now imports this function); the projections below
    compute it at derive time for the cloud. The ``ORDER BY`` must stay
    byte-identical across every caller for parity — see
    ``tests/test_measurements_query/test_index_derivation.py``.
    """
    return (
        f"CAST(DENSE_RANK() OVER (PARTITION BY {run_id}, {name} "
        f"ORDER BY {step_index}, {step_path}, COALESCE({vector_index}, -1)) - 1 AS BIGINT)"
    )


def measurements_projection_select(source_sql: str) -> str:
    """Flat measurement rows (one per measurement occurrence), UNNESTed from
    the nested ``measurements`` list on step AND vector rows.

    Mirrors `_measurement_unnest_insert` fused with the local FULL `measurements`
    view's column set (not the lean `measurement_facts` view — see module
    docstring), plus the denormalized ``step_name`` (the cloud's fleet-scale
    materialization of local's `measurements` view).

    docs/36 P1a closed a real gap here: this projection was missing
    ``step_outcome``/``step_started_at``/``step_ended_at``/``vector_outcome``
    (present on local's `measurements` view via its `steps`/`vectors` joins)
    and the ``git_*``/``python_version``/``testerkit_version``/
    ``env_fingerprint`` environment-traceability columns (present on every row
    — see ``run_context_from_run_started(..., include_env=True)``, called
    uniformly for run/step/vector rows — so ``ANY_VALUE`` per carrier row is
    exact, not an approximation). ``step_started_at``/``step_ended_at`` are
    denormalized straight off the carrier row (present on both step AND
    vector rows — see ``build_vector_row``); ``vector_outcome`` likewise
    (``NULL`` on a step-sourced fact, by construction). ``step_outcome`` is
    the one field that needs a join: a vector row's own ``step_outcome`` is
    ``NULL`` at rest (``build_vector_row`` never stamps it — only the step
    row carries the collapsed step-level verdict), so this recomputes the
    SAME worst-wins collapse `steps_projection_select` uses (kept in
    lockstep — see ``WORST_STEP_OUTCOME_EXPR``) and joins it in, exactly
    mirroring local's ``measurements`` view joining ``steps_materialized``
    (whose ``.outcome`` is that same persisted collapse).
    """
    proj_vi = "CASE WHEN v.record_type = 'vector' THEN v.vector_index END"
    index_expr = _occurrence_index_expr(
        run_id="v.run_id",
        name="m.name",
        step_index="v.step_index",
        step_path="v.step_path",
        vector_index=proj_vi,
    )
    ctx_v = ",\n            ".join(f"v.{c}" for c in _RUN_CONTEXT_COLUMNS)
    return f"""
        WITH step_outcomes AS (
            SELECT
                run_id,
                step_path,
                COALESCE(step_retry, 0) AS step_retry,
                COALESCE(vector_outer_index, -1) AS vector_outer_index_key,
                {WORST_STEP_OUTCOME_EXPR}
            FROM {source_sql}
            WHERE run_id IS NOT NULL AND record_type = 'step'
            GROUP BY
                run_id, step_path, COALESCE(step_retry, 0), COALESCE(vector_outer_index, -1)
        )
        SELECT
            v.run_id, v.filename AS file_path,
            {ctx_v},
            v.run_started_at, v.run_ended_at,
            CAST(v.run_outcome AS VARCHAR) AS run_outcome,
            v.step_index, v.step_path, COALESCE(v.step_retry, 0) AS step_retry,
            v.step_name,
            so.outcome AS step_outcome,
            v.step_started_at, v.step_ended_at,
            {proj_vi} AS vector_index,
            v.vector_outer_index,
            CASE WHEN v.record_type = 'vector' THEN COALESCE(v.vector_retry, 0) END
                AS vector_retry,
            v.vector_outcome,
            CAST(ord AS BIGINT) - 1 AS ordinal,
            {index_expr} AS index,
            m.name AS measurement_name,
            m.value AS measurement_value,
            m.outcome AS measurement_outcome,
            m.unit AS measurement_unit,
            m.timestamp AS measurement_timestamp,
            m.limit_low, m.limit_high, m.limit_nominal, m.limit_comparator,
            m.characteristic_id, m.spec_ref, m.uut_pin, m.fixture_connection,
            m.instrument_name, m.instrument_resource, m.instrument_channel,
            v.git_commit, v.git_branch, v.git_remote,
            v.python_version, v.testerkit_version, v.env_fingerprint
        FROM {source_sql} AS v
        CROSS JOIN UNNEST(v.measurements) WITH ORDINALITY AS t(m, ord)
        LEFT JOIN step_outcomes so
            ON so.run_id = v.run_id AND so.step_path = v.step_path
            AND so.step_retry = COALESCE(v.step_retry, 0)
            AND so.vector_outer_index_key = COALESCE(v.vector_outer_index, -1)
        WHERE v.run_id IS NOT NULL AND v.record_type IN ('step', 'vector')"""
