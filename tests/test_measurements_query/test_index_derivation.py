"""The occurrence ``index`` — 0-based, sweep-aware, retry-STABLE.

``index`` is a ``DENSE_RANK`` projection, MATERIALIZED at ingest (full
snowflake, 0.3.1 phase 8) via the daemon's single-source
``_occurrence_index_expr`` — one SQL builder shared by the measurements fact
and the inputs/outputs lane tables (and re-computed for not-yet-materialized
inflight rows by the ``measurements`` view). Its whole correctness claim is:

  * it is **0-based** per ``(run_id, measurement_name)``,
  * a **sweep** yields ``0..N-1`` across its condition points,
  * **retried** attempts of one position **share** an index — retry-stability
    inherited from the step/vector coordinates (which already encode retries
    correctly), leaving **no gap** in the sequence.

The tests import the single-source expression itself, so any future edit to
the definition that breaks retry-stability fails right here. The retry-share
property is the ``get-last`` foundation (task #62): because retries share an
index, "keep the final attempt per occurrence" is a well-defined collapse.
"""

from __future__ import annotations

import duckdb

from litmus.analysis.measurements_query import _FIXED_COLUMNS, _PLOTTABLE_FIXED_COLUMNS
from litmus.data._runs_duckdb_daemon import _occurrence_index_expr

# (run_id, measurement_name, step_index, step_path, vector_index,
#  vector_retry, step_retry) — the columns the index expr orders/partitions
# on, plus the retry axes that make retried attempts distinct rows.
_COLS = "run_id, measurement_name, step_index, step_path, vector_index, vector_retry, step_retry"

# The production single-source expression, bound to the hand-built table's
# bare column names (the daemon binds it to ``v.``/``ctx.``-qualified ones at
# ingest — same expression, same semantics).
_INDEX_EXPR = _occurrence_index_expr(
    run_id="run_id",
    name="measurement_name",
    step_index="step_index",
    step_path="step_path",
    vector_index="vector_index",
)


def _indices(rows: list[tuple]) -> list[int]:
    """Apply the production index expr to hand-built rows, return the index
    per row in stable (execution-position, then retry) order."""
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE measurements("
        " run_id VARCHAR, measurement_name VARCHAR,"
        " step_index INTEGER, step_path VARCHAR,"
        " vector_index INTEGER, vector_retry INTEGER, step_retry INTEGER)"
    )
    con.executemany(f"INSERT INTO measurements ({_COLS}) VALUES (?,?,?,?,?,?,?)", rows)
    out = con.execute(
        f"SELECT {_INDEX_EXPR} AS idx FROM measurements"
        " ORDER BY run_id, step_index, step_path,"
        " COALESCE(vector_index, -1), step_retry, vector_retry"
    ).fetchall()
    con.close()
    return [r[0] for r in out]


def test_non_swept_measurement_is_index_zero() -> None:
    # measured once, no sweep → the sole occurrence is index 0
    assert _indices([("r", "vin", 0, "check_vin", None, 0, 0)]) == [0]


def test_sweep_is_zero_based_sequence() -> None:
    rows = [
        ("r", "i_out", 2, "sweep", 0, 0, 0),
        ("r", "i_out", 2, "sweep", 1, 0, 0),
        ("r", "i_out", 2, "sweep", 2, 0, 0),
    ]
    assert _indices(rows) == [0, 1, 2]


def test_retried_non_swept_measurement_shares_index() -> None:
    # v_rail failed then re-ran at the SAME position (step_retry 0 → 1).
    # NOT a blind increment: both attempts are the same occurrence → index 0.
    rows = [
        ("r", "v_rail", 1, "test_rail", None, 0, 0),
        ("r", "v_rail", 1, "test_rail", None, 0, 1),
    ]
    assert _indices(rows) == [0, 0]


def test_retried_vector_shares_index_no_gap() -> None:
    # sweep 0,1,2 with vector 1 retried → 0,1,1,2: retry shares, sequence
    # continues without a gap (2 stays 2, not bumped to 3).
    rows = [
        ("r", "i_out", 2, "sweep", 0, 0, 0),
        ("r", "i_out", 2, "sweep", 1, 0, 0),
        ("r", "i_out", 2, "sweep", 1, 1, 0),
        ("r", "i_out", 2, "sweep", 2, 0, 0),
    ]
    assert _indices(rows) == [0, 1, 1, 2]


def test_index_is_per_name_and_per_run() -> None:
    # Each name's first (only) occurrence is 0, independently, in each run.
    rows = [
        ("r1", "vin", 0, "check_vin", None, 0, 0),
        ("r1", "v_rail", 1, "test_rail", None, 0, 0),
        ("r2", "vin", 0, "check_vin", None, 0, 0),
    ]
    assert _indices(rows) == [0, 0, 0]


def test_index_registered_as_fixed_plottable_column() -> None:
    # Wiring: index is a bare fixed column (resolves to m.index — now a stored
    # view column) and a plottable axis — so /explore's picker offers it and
    # _default_x can select it.
    assert "index" in _FIXED_COLUMNS
    assert ("index", "BIGINT") in _PLOTTABLE_FIXED_COLUMNS


def test_index_expr_is_the_materialized_window() -> None:
    # The single-source expr is a 0-based DENSE_RANK over (run, name) — the SQL
    # the daemon stamps into the ``index`` column at ingest.
    assert "DENSE_RANK" in _INDEX_EXPR
    assert "PARTITION BY run_id, measurement_name" in _INDEX_EXPR
