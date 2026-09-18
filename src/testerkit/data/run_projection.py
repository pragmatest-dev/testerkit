"""Shared runs projection SQL — measurement-grain parquet → one row per run.

Single source of truth for the run-list projection, used by BOTH:
- the local runs daemon (``_bulk_insert_runs`` → ``INSERT INTO runs_materialized``), and
- the cloud query service (``SELECT`` over per-run parquet in object storage).

Keeping the projection in one place means the bench and the cloud can never derive a
different ``runs`` shape from the same data. ``source_sql`` is any relation expression
that exposes ``RUN_ROW_SCHEMA`` columns plus a ``filename`` column, e.g.
``read_parquet([...], filename=true, union_by_name=true)`` (local paths or ``s3://``).
"""

from __future__ import annotations

# Columns of the projection, in order. Run-level context is denormalized onto every
# measurement-grain row, so it is constant within a (filename, run_id) group and is
# listed in GROUP BY; the only aggregates are the rollups (num_measurements/num_steps).
_GROUP_COLUMNS = (
    "filename",
    "run_id",
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
    "machine_id",
    "fixture_id",
    "run_outcome",
    "run_started_at",
    "run_ended_at",
    "test_phase",
    "part_id",
    "part_name",
    "part_revision",
    "station_type",
    "station_location",
    "operator_id",
    "operator_name",
    "project_name",
    "git_commit",
    "git_branch",
    "git_remote",
    "python_version",
    "testerkit_version",
    "env_fingerprint",
)


def runs_projection_select(source_sql: str) -> str:
    """The one-row-per-run projection SELECT over a measurement-grain ``source_sql``."""
    group_by = ",\n            ".join(_GROUP_COLUMNS)
    return f"""
        SELECT
            run_id,
            filename AS file_path,
            session_id,
            site_index,
            site_name,
            uut_serial_number, uut_part_number, uut_revision, uut_lot_number,
            station_id, station_name, station_hostname,
            machine_id,
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
            python_version, testerkit_version, env_fingerprint
        FROM {source_sql}
        WHERE run_id IS NOT NULL
        GROUP BY
            {group_by}"""


# Computed run duration in seconds (rounded to µs to match the overlay's Python
# ``total_seconds()`` exactly). Appended to the projection by consumers that expose it.
DURATION_S_EXPR = """ROUND(
            CASE
                WHEN ended_at IS NOT NULL AND started_at IS NOT NULL
                THEN EPOCH(ended_at) - EPOCH(started_at)
                ELSE NULL
            END, 6
        ) AS duration_s"""
