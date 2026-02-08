"""PyArrow data loading and filtering for analysis.

Loads Parquet files via pyarrow and normalizes schemas for multi-file scanning.
All filtering uses pyarrow.compute — no DuckDB, no pandas.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from litmus.data.backends.parquet import _enforce_schema

# Columns needed for analysis (subset of full schema)
_ANALYSIS_COLUMNS = [
    "run_id", "run_started_at", "run_ended_at", "run_outcome",
    "dut_serial", "dut_part_number", "dut_lot_number",
    "product_id", "station_id", "station_name",
    "test_phase", "step_name", "step_started_at", "step_ended_at",
    "measurement_name", "value", "units", "outcome",
    "low_limit", "high_limit", "nominal",
]


def load_runs(results_dir: str | Path) -> pa.Table:
    """Load all Parquet run data as a PyArrow Table.

    Reads only the columns needed for analysis, normalizes types via
    _enforce_schema per-file, then concatenates with promote_options.

    Args:
        results_dir: Path to results directory (contains runs/ subdirectory).

    Returns:
        PyArrow Table with analysis-relevant columns.
    """
    runs_dir = Path(results_dir) / "runs"
    if not runs_dir.exists():
        return pa.table({})

    parquet_files = [
        f for f in runs_dir.rglob("*.parquet")
        if "_ref" not in f.parent.name
    ]

    if not parquet_files:
        return pa.table({})

    tables = []
    for f in parquet_files:
        try:
            t = pq.read_table(f)
            available = [c for c in _ANALYSIS_COLUMNS if c in t.column_names]
            t = t.select(available)
            t = _enforce_schema(t)
            tables.append(t)
        except Exception:
            continue

    if not tables:
        return pa.table({})

    return pa.concat_tables(tables, promote_options="default")


def load_measurements(results_dir: str | Path) -> pa.Table:
    """Load full measurement table (alias for load_runs).

    Same data, but named for clarity when used for Cpk/Pareto analysis.
    """
    return load_runs(results_dir)


def deduplicate_runs(table: pa.Table) -> list[dict]:
    """Deduplicate to one row per run_id for yield calculations.

    Takes the first row per run_id (all rows in a run share the same
    run-level metadata).

    Args:
        table: PyArrow Table with measurement rows.

    Returns:
        List of dicts, one per unique run_id.
    """
    if table.num_rows == 0:
        return []

    rows = table.to_pylist()
    seen: set[str] = set()
    result = []
    for row in rows:
        run_id = row.get("run_id")
        if run_id and run_id not in seen:
            seen.add(run_id)
            result.append(row)
    return result


def filter_by_phase(table: pa.Table, phases: list[str] | None = None) -> pa.Table:
    """Filter by test phase. Excludes 'development' by default.

    Args:
        table: Input table.
        phases: List of phases to include. None = exclude development.
                Pass ["all"] to include everything.

    Returns:
        Filtered table.
    """
    if table.num_rows == 0 or "test_phase" not in table.column_names:
        return table

    if phases and "all" in phases:
        return table

    if phases:
        mask = pc.is_in(table["test_phase"], value_set=pa.array(phases))
    else:
        # Default: exclude development
        mask = pc.not_equal(
            pc.if_else(pc.is_null(table["test_phase"]), pa.scalar(""), table["test_phase"]),
            pa.scalar("development"),
        )

    return table.filter(mask)


def filter_by_date_range(
    table: pa.Table,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
) -> pa.Table:
    """Filter by date range on run_started_at.

    Args:
        table: Input table.
        since: Start date (inclusive). ISO format string or datetime.
        until: End date (inclusive). ISO format string or datetime.

    Returns:
        Filtered table.
    """
    if table.num_rows == 0 or "run_started_at" not in table.column_names:
        return table

    col = table["run_started_at"]

    if since is not None:
        if isinstance(since, str):
            since = datetime.fromisoformat(since)
        since_scalar = pa.scalar(since, type=col.type)
        mask = pc.greater_equal(col, since_scalar)
        table = table.filter(mask)

    if until is not None:
        if isinstance(until, str):
            until = datetime.fromisoformat(until)
        until_scalar = pa.scalar(until, type=col.type)
        mask = pc.less_equal(table["run_started_at"], until_scalar)
        table = table.filter(mask)

    return table


def filter_by_product(table: pa.Table, product_id: str) -> pa.Table:
    """Filter by dut_part_number (preferred) or product_id (fallback)."""
    if table.num_rows == 0:
        return table
    # Prefer dut_part_number for manufacturing traceability
    if "dut_part_number" in table.column_names:
        mask = pc.equal(table["dut_part_number"], pa.scalar(product_id))
        filtered = table.filter(mask)
        if filtered.num_rows > 0:
            return filtered
    # Fall back to product_id
    if "product_id" not in table.column_names:
        return table
    mask = pc.equal(table["product_id"], pa.scalar(product_id))
    return table.filter(mask)


def filter_by_station(table: pa.Table, station_id: str) -> pa.Table:
    """Filter by station_name (preferred) or station_id (fallback)."""
    if table.num_rows == 0:
        return table
    # Prefer station_name for human-readable filtering
    if "station_name" in table.column_names:
        mask = pc.equal(table["station_name"], pa.scalar(station_id))
        filtered = table.filter(mask)
        if filtered.num_rows > 0:
            return filtered
    # Fall back to station_id
    if "station_id" not in table.column_names:
        return table
    mask = pc.equal(table["station_id"], pa.scalar(station_id))
    return table.filter(mask)


def filter_by_lot(table: pa.Table, lot: str) -> pa.Table:
    """Filter by dut_lot_number."""
    if table.num_rows == 0 or "dut_lot_number" not in table.column_names:
        return table
    mask = pc.equal(table["dut_lot_number"], pa.scalar(lot))
    return table.filter(mask)
