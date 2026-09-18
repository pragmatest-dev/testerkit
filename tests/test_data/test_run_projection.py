"""Unit test for the shared runs projection SQL (run_projection.py).

This SQL is the single source of truth for the run-list projection, used by both the
local runs daemon (INSERT INTO runs_materialized) and the cloud query service. The test
pins the columns and runs the projection over a tiny in-memory measurement-grain
relation, so a drift or transcription error is caught without spawning a daemon.
"""

from __future__ import annotations

import duckdb

from testerkit.data.run_projection import DURATION_S_EXPR, runs_projection_select


def test_projection_columns_present() -> None:
    sql = runs_projection_select("some_source")
    for col in (
        "run_id",
        "filename AS file_path",
        "run_outcome AS outcome",
        "run_started_at AS started_at",
        "run_ended_at AS ended_at",
        "num_measurements",
        "num_steps",
        "uut_part_number",
        "station_hostname",
        "machine_id",
        "test_phase",
    ):
        assert col in sql, f"projection missing {col!r}"
    assert "FROM some_source" in sql
    assert "GROUP BY" in sql


def test_projection_runs_over_measurement_grain_rows() -> None:
    """Two measurement rows for one run collapse to a single run row with rollups."""
    con = duckdb.connect()
    # Minimal measurement-grain source with the columns the projection references.
    con.execute("""
        CREATE TABLE src AS
        SELECT * FROM (VALUES
            ('run1','f.parquet','sess1',0,'s',
             'SN1','PN1','A','LOT1','st1','St 1','host1','mach1','fix1',
             'passed', TIMESTAMP '2026-01-01 00:00:00', TIMESTAMP '2026-01-01 00:01:00',
             'measurement', [], 'prod','part1','Part 1','A','ICT','L1','op1','Op 1','proj1',
             'c','b','r','3.12','0.4','fp'),
            ('run1','f.parquet','sess1',0,'s',
             'SN1','PN1','A','LOT1','st1','St 1','host1','mach1','fix1',
             'passed', TIMESTAMP '2026-01-01 00:00:00', TIMESTAMP '2026-01-01 00:01:00',
             'step', [], 'prod','part1','Part 1','A','ICT','L1','op1','Op 1','proj1',
             'c','b','r','3.12','0.4','fp')
        ) AS t(run_id, filename, session_id, site_index, site_name,
            uut_serial_number, uut_part_number, uut_revision, uut_lot_number,
            station_id, station_name, station_hostname, machine_id, fixture_id,
            run_outcome, run_started_at, run_ended_at,
            record_type, measurements, test_phase, part_id, part_name, part_revision,
            station_type, station_location, operator_id, operator_name, project_name,
            git_commit, git_branch, git_remote,
            python_version, testerkit_version, env_fingerprint)
    """)
    rows = con.execute(
        f"SELECT *, {DURATION_S_EXPR} FROM ({runs_projection_select('src')})"
    ).fetchall()
    cols = [d[0] for d in con.description]
    assert len(rows) == 1  # collapsed to one run
    row = dict(zip(cols, rows[0], strict=True))
    assert row["run_id"] == "run1"
    assert row["outcome"] == "passed"
    assert row["station_hostname"] == "host1"
    assert row["machine_id"] == "mach1"
    assert row["num_steps"] == 1
    assert row["duration_s"] == 60.0
    con.close()
