"""Shared steps / measurement_facts / lanes projection SQL — sibling of
``run_projection``.

The step-, measurement-, and lane-grain projections that turn measurement-grain
per-run Parquet into flat rows. ``source_sql`` is any relation exposing the
measurement-grain columns (e.g. ``read_parquet([...], filename=true, union_by_name=true)``
— local paths or ``s3://``). Pure SQL builders, no I/O — same discipline as
``run_projection``. ``lanes_projection_select`` (docs/25 #70 stage 1) is the
inputs/outputs EAV counterpart to ``measurement_facts_projection_select``:
same carrier rows and grain-key expressions, over the ``inputs``/``outputs``
LIST<STRUCT> lanes instead of ``measurements``.

**Who uses this (accurately).** The cloud serving tier (`testerkit-server`) imports
these builders directly, so its ``steps`` / ``measurement_facts`` shape is derived
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

**On ``measurement_facts`` and ``step_name`` (intentional, not a divergence).**
Locally, ``step_name`` lives on the fuller ``measurements`` VIEW (a join of the lean
fact grain to ``steps``); DuckDB runs that join in-process over one bench's data for
next to nothing, so the local fact table stays lean and normalized. The cloud serves
**every measurement across every tenant** from BigQuery, where a per-query join of
the all-tenant fact table against ``steps`` is a shuffle join to avoid — so the
cloud **materializes** the ``measurements``-view shape once at derive time by
denormalizing ``step_name`` (available directly on the carrier row) onto each fact
row. Same values as local's ``measurements`` view; the difference is materialize-at-
derive (cloud, fleet scale) vs join-at-read (local, in-process) — a deliberate,
engine-driven materialization, single-sourced here.

Mirrors the daemon's ``_bulk_insert_steps`` ``grain`` CTE and
``_measurement_unnest_insert`` (``_occurrence_index_expr`` + the UNNEST shape), but
denormalizes run/UUT/station context directly off the raw row (ANY_VALUE — constant
per run) instead of a separate ``runs_materialized`` join, since a projection runs
once per derive and needs no persistent runs table. Column tuples are drift-guarded
against these SELECTs by ``tests/test_data/test_measurement_projection.py``.
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

# ``measurement_facts`` flat-row columns, in order — MUST match
# `measurement_facts_projection_select`'s SELECT list order exactly (drift-guarded).
# Denormalized run context + the measurement's own grain key + payload; `step_name`
# is denormalized (see module docstring — the cloud's fleet-scale materialization of
# local's `measurements` view).
MEASUREMENT_FACTS_COLUMNS: tuple[tuple[str, str], ...] = (
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
    ("vector_index", "INTEGER"),
    ("vector_outer_index", "INTEGER"),
    ("vector_retry", "INTEGER"),
    ("ordinal", "INTEGER"),
    ("occurrence_index", "INTEGER"),
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
)

# Computed step/vector duration in seconds — same formula as
# `run_projection.DURATION_S_EXPR`, applied to the step's own timing columns.
STEP_DURATION_S_EXPR = """ROUND(
            CASE
                WHEN step_ended_at IS NOT NULL AND step_started_at IS NOT NULL
                THEN EPOCH(step_ended_at) - EPOCH(step_started_at)
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
                step_index,
                step_name,
                {WORST_STEP_OUTCOME_EXPR},
                ANY_VALUE(step_started_at) AS step_started_at,
                ANY_VALUE(step_ended_at) AS step_ended_at,
                CAST(COALESCE(SUM(len(measurements)), 0) AS INTEGER) AS measurement_count,
                ANY_VALUE(step_markers) AS markers
            FROM {source_sql}
            WHERE run_id IS NOT NULL AND record_type = 'step'
            GROUP BY
                filename, run_id, step_path, COALESCE(step_retry, 0),
                vector_outer_index, step_index, step_name
        )
        SELECT
            run_id, file_path,
            {", ".join(_RUN_CONTEXT_COLUMNS)},
            run_outcome,
            step_path, step_retry_norm AS step_retry, vector_outer_index, step_index, step_name,
            outcome,
            step_started_at AS started_at, step_ended_at AS ended_at,
            {STEP_DURATION_S_EXPR},
            measurement_count, markers
        FROM grain"""


# ``lanes`` (inputs/outputs EAV) flat-row columns, in order — MUST match
# `lanes_projection_select`'s SELECT list order exactly (drift-guarded).
# Denormalized run context (same set as `MEASUREMENT_FACTS_COLUMNS`) + the
# lane's carrier grain key + its own ``(role, name, value)`` payload.
LANE_ROW_COLUMNS: tuple[tuple[str, str], ...] = (
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

# Which lane list column feeds which ``role`` literal — the EAV split is a
# UNION ALL over both, not two separate tables (unlike the local daemon's
# ``inputs``/``outputs`` tables — see `_lane_unnest_select`'s docstring).
_LANE_ROLES: tuple[tuple[str, str], ...] = (
    ("inputs", "input"),
    ("outputs", "output"),
)


def _lane_value_json_expr(alias: str) -> str:
    """Reconstruct a lane entry's raw value as JSON text.

    The at-rest lane struct (``_row_helpers.LANE_FIELDS``) is a type-dispatched
    EAV, not a single raw-value column: ``value_type`` selects exactly one
    ``value_*`` lane (``_lane_entry`` / ``_lane_value``), and only ``list``/
    ``dict`` entries are pre-encoded as JSON text in ``value_json`` at write
    time. This expression is the SQL twin of ``_lane_value`` fused with
    ``json.dumps``: it dispatches on ``value_type`` the same way, and uses
    DuckDB's ``to_json`` to serialize whichever typed lane holds the value so
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


def _lane_unnest_select(source_sql: str, *, col: str, role: str) -> str:
    """One role's half of the ``lanes_projection_select`` UNION ALL —
    UNNESTs ``v.{col}`` (``inputs`` or ``outputs``) from step AND vector rows,
    tagging every row with the literal ``role``.

    Grain-key expressions (``step_index``/``step_path``/``step_retry``/
    ``vector_index``/``vector_outer_index``/``vector_retry``) are byte-identical
    to `measurement_facts_projection_select`'s, so a lane row joins cleanly to
    its carrier's measurement-fact rows on that key (same discipline the
    daemon's ``_lane_insert`` documents for its own ``step_retry``/
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
            {_lane_value_json_expr("u")} AS value_json,
            u.unit
        FROM {source_sql} AS v, UNNEST(v.{col}) AS t(u)
        WHERE v.run_id IS NOT NULL AND v.record_type IN ('step', 'vector')"""


def lanes_projection_select(source_sql: str) -> str:
    """Flat, long/EAV rows over the nested ``inputs``/``outputs`` lanes — one
    row per lane entry, UNNESTed from step AND vector rows.

    Mirrors `measurement_facts_projection_select`'s shape (denormalized run
    context, same carrier rows, same grain-key expressions) but over the
    ``inputs``/``outputs`` LIST<STRUCT> columns instead of ``measurements``.
    ``role`` distinguishes an ``inputs`` entry from an ``outputs`` one (a
    UNION ALL of both lists into one relation, not the local daemon's two
    separate ``inputs``/``outputs`` tables — a projection is a single SELECT).

    ``value_json`` is the lane's raw value reconstructed as JSON text (see
    :func:`_lane_value_json_expr` — the lane struct is a type-dispatched EAV,
    not a single raw-value column at rest); ``value`` is a ``TRY_CAST`` of
    that JSON to DOUBLE, so a numeric lane (a swept input, a scalar output) is
    queryable as a number and a non-numeric one (a string, a URI, a JSON list/
    dict) comes through as NULL rather than raising.
    """
    role_selects = [
        _lane_unnest_select(source_sql, col=col, role=role) for col, role in _LANE_ROLES
    ]
    union_sql = "\n            UNION ALL\n            ".join(role_selects)
    return f"""
        WITH lanes AS ({union_sql}
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
        FROM lanes"""


def _occurrence_index_expr(*, vector_index_expr: str) -> str:
    """Same formula as the daemon's `_occurrence_index_expr` — 0-based DENSE_RANK of
    a measurement's occurrence, partitioned by (run_id, name), ordered by execution
    position. A pure SQL expression (not an engine), single-sourced here."""
    return (
        "CAST(DENSE_RANK() OVER (PARTITION BY v.run_id, m.name "
        f"ORDER BY v.step_index, v.step_path, COALESCE({vector_index_expr}, -1)) - 1 AS BIGINT)"
    )


def measurement_facts_projection_select(source_sql: str) -> str:
    """Flat measurement-fact rows (one per measurement occurrence), UNNESTed from
    the nested ``measurements`` list on step AND vector rows.

    Mirrors `_measurement_unnest_insert` fused with the local `measurement_facts`
    view's column set, plus the denormalized ``step_name`` (see module docstring —
    the cloud's fleet-scale materialization of local's `measurements` view).
    """
    proj_vi = "CASE WHEN v.record_type = 'vector' THEN v.vector_index END"
    index_expr = _occurrence_index_expr(vector_index_expr=proj_vi)
    ctx_v = ",\n            ".join(f"v.{c}" for c in _RUN_CONTEXT_COLUMNS)
    return f"""
        SELECT
            v.run_id, v.filename AS file_path,
            {ctx_v},
            v.run_started_at, v.run_ended_at,
            CAST(v.run_outcome AS VARCHAR) AS run_outcome,
            v.step_index, v.step_path, COALESCE(v.step_retry, 0) AS step_retry,
            v.step_name,
            {proj_vi} AS vector_index,
            v.vector_outer_index,
            CASE WHEN v.record_type = 'vector' THEN COALESCE(v.vector_retry, 0) END
                AS vector_retry,
            CAST(ord AS BIGINT) - 1 AS ordinal,
            {index_expr} AS occurrence_index,
            m.name AS measurement_name,
            m.value AS measurement_value,
            m.outcome AS measurement_outcome,
            m.unit AS measurement_unit,
            m.timestamp AS measurement_timestamp,
            m.limit_low, m.limit_high, m.limit_nominal, m.limit_comparator,
            m.characteristic_id, m.spec_ref, m.uut_pin, m.fixture_connection,
            m.instrument_name, m.instrument_resource, m.instrument_channel
        FROM {source_sql} AS v, UNNEST(v.measurements) WITH ORDINALITY AS t(m, ord)
        WHERE v.run_id IS NOT NULL AND v.record_type IN ('step', 'vector')"""
