"""The derived occurrence ``index`` — 0-based, sweep-aware, retry-STABLE.

``index`` is a query-layer ``DENSE_RANK`` projection (``_INDEX_EXPR``), not a
stored field and not a blind increment. Its whole correctness claim is:

  * it is **0-based** per ``(run_id, measurement_name)``,
  * a **sweep** yields ``0..N-1`` across its condition points,
  * **retried** attempts of one position **share** an index — retry-stability
    inherited from the step/vector coordinates (which already encode retries
    correctly), leaving **no gap** in the sequence.

The tests import the single-source expression itself, so any future edit to
the definition that breaks retry-stability fails right here. The last test is
the ``get-last`` foundation (task #62): because retries share an index, "keep
the final attempt per occurrence" is a well-defined collapse.
"""

from __future__ import annotations

import duckdb

from litmus.analysis.measurements_query import (
    _FIXED_COLUMNS,
    _INDEX_EXPR,
    _MEASUREMENTS_WITH_INDEX,
    _PLOTTABLE_FIXED_COLUMNS,
    _measurements_source,
)

# (run_id, measurement_name, step_index, step_path, vector_index,
#  vector_retry, step_retry) — the columns ``_INDEX_EXPR`` orders/partitions
# on, plus the retry axes that make retried attempts distinct rows.
_COLS = "run_id, measurement_name, step_index, step_path, vector_index, vector_retry, step_retry"


def _indices(rows: list[tuple]) -> list[int]:
    """Apply the production ``_INDEX_EXPR`` to hand-built rows, return the index
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
    # Wiring: index is a bare fixed column (resolves to m.index) and a plottable
    # axis — so /explore's picker offers it and _default_x can select it.
    assert "index" in _FIXED_COLUMNS
    assert ("index", "BIGINT") in _PLOTTABLE_FIXED_COLUMNS


def test_index_source_is_conditional() -> None:
    # The window-bearing subquery is chosen ONLY when a column references
    # m.index; ordinary queries keep the bare view (no window tax).
    assert _measurements_source("m.measurement_value", "m.vector_index") == "measurements"
    assert _measurements_source("m.index") == _MEASUREMENTS_WITH_INDEX
    assert "DENSE_RANK" in _MEASUREMENTS_WITH_INDEX
