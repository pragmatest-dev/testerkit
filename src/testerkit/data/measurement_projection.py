"""Shared steps / measurement_facts projection SQL — sibling of ``run_projection``.

Single source of truth for the step- and measurement-grain projections that turn
measurement-grain per-run Parquet into flat rows, used by BOTH the local daemon's
derivation and the cloud serving tier (`testerkit-server`), so a bench and the
cloud can never derive a different ``steps`` / ``measurement_facts`` shape from the
same data. ``source_sql`` is any relation exposing the measurement-grain columns
(e.g. ``read_parquet([...], filename=true, union_by_name=true)`` — local paths or
``s3://``). Pure SQL builders, no I/O — same discipline as ``run_projection``.

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
                ANY_VALUE(step_outcome) AS outcome,
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
