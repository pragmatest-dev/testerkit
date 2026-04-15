"""PyArrow data loading and filtering for analysis.

Loads Parquet files via RunStore and normalizes schemas for multi-file scanning.
All filtering uses pyarrow.compute — no DuckDB, no pandas.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as _pc
import pyarrow.parquet as pq

from litmus.analysis._common import parse_datetime
from litmus.data.schemas import _enforce_schema

# pyarrow.compute has dynamic attributes that pyright can't see
pc: Any = _pc

logger = logging.getLogger(__name__)

# Columns needed for analysis (subset of full schema)
_ANALYSIS_COLUMNS = [
    "run_id",
    "run_started_at",
    "run_ended_at",
    "run_outcome",
    "dut_serial",
    "dut_part_number",
    "dut_lot_number",
    "product_id",
    "station_id",
    "station_name",
    "test_phase",
    "step_name",
    "step_started_at",
    "step_ended_at",
    "measurement_name",
    "value",
    "units",
    "outcome",
    "low_limit",
    "high_limit",
    "nominal",
]


def load_runs(results_dir: str | Path) -> pa.Table:
    """Load all Parquet run data as a PyArrow Table.

    Uses RunStore to discover run files via DuckDB index, then reads
    columns needed for analysis and normalizes types via _enforce_schema.

    Args:
        results_dir: Path to results directory (contains runs/ subdirectory).

    Returns:
        PyArrow Table with analysis-relevant columns.
    """
    from litmus.data.run_store import RunStore

    results_path = Path(results_dir)
    runs_dir = results_path / "runs"
    if not runs_dir.exists():
        return pa.table({})

    # Use RunStore to discover run files via DuckDB index
    parquet_files: list[Path] = []
    try:
        run_store = RunStore(_results_dir=results_path)
        try:
            runs = run_store.list_runs(limit=10000)
            parquet_files = [
                Path(r["_file"]) for r in runs if r.get("_file") and Path(r["_file"]).exists()
            ]
        finally:
            run_store.close()
    except Exception:
        pass

    # Fallback: direct file scan if RunStore found nothing (mixed schemas, etc.)
    if not parquet_files:
        parquet_files = [f for f in runs_dir.rglob("*.parquet") if "_ref" not in f.parent.name]

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
        except (pa.ArrowInvalid, OSError) as exc:
            logger.debug("Skipping unreadable parquet file %s: %s", f, exc)
            continue

    if not tables:
        return pa.table({})

    return pa.concat_tables(tables, promote_options="default")


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
        since_dt = parse_datetime(since)
        if since_dt is not None:
            since_scalar = pa.scalar(since_dt, type=col.type)
            mask = pc.greater_equal(col, since_scalar)
            table = table.filter(mask)

    if until is not None:
        until_dt = parse_datetime(until)
        if until_dt is not None:
            until_scalar = pa.scalar(until_dt, type=col.type)
            mask = pc.less_equal(table["run_started_at"], until_scalar)
            table = table.filter(mask)

    return table


def _filter_by_column(
    table: pa.Table,
    value: str,
    primary_col: str,
    fallback_col: str,
) -> pa.Table:
    """Filter table by value in primary column, falling back to fallback column.

    Uses the first column that exists. Does NOT fall back if the primary
    column exists but yields zero rows.
    """
    if table.num_rows == 0:
        return table
    col_name = primary_col if primary_col in table.column_names else fallback_col
    if col_name not in table.column_names:
        return table
    mask = pc.equal(table[col_name], pa.scalar(value))
    return table.filter(mask)


def filter_by_product(table: pa.Table, product_id: str) -> pa.Table:
    """Filter by dut_part_number (preferred) or product_id."""
    return _filter_by_column(table, product_id, "dut_part_number", "product_id")


def filter_by_station(table: pa.Table, station_id: str) -> pa.Table:
    """Filter by station_name (preferred) or station_id."""
    return _filter_by_column(table, station_id, "station_name", "station_id")


def filter_by_lot(table: pa.Table, lot: str) -> pa.Table:
    """Filter by dut_lot_number."""
    if table.num_rows == 0 or "dut_lot_number" not in table.column_names:
        return table
    mask = pc.equal(table["dut_lot_number"], pa.scalar(lot))
    return table.filter(mask)


def apply_all_filters(
    table: pa.Table,
    *,
    phase: str | None = None,
    product_id: str | None = None,
    station_id: str | None = None,
    lot: str | None = None,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
) -> pa.Table:
    """Apply all standard filters in a consistent order.

    Args:
        table: Input table.
        phase: Test phase (None = exclude development, "all" = no filter).
        product_id: Product filter (dut_part_number or product_id).
        station_id: Station filter (station_name or station_id).
        lot: Lot number filter.
        since: Start date (inclusive).
        until: End date (inclusive).

    Returns:
        Filtered table.
    """
    if phase == "all":
        phases = ["all"]
    elif phase:
        phases = [phase]
    else:
        phases = None
    table = filter_by_phase(table, phases)
    table = filter_by_date_range(table, since=since, until=until)
    if product_id:
        table = filter_by_product(table, product_id)
    if station_id:
        table = filter_by_station(table, station_id)
    if lot:
        table = filter_by_lot(table, lot)
    return table


def get_unique_column_values(table: pa.Table, column_name: str) -> list[str]:
    """Extract unique non-null values from a table column, sorted.

    Args:
        table: Input table.
        column_name: Column to extract values from.

    Returns:
        Sorted list of unique string values.
    """
    if table.num_rows == 0 or column_name not in table.column_names:
        return []
    col = table[column_name]
    unique = pc.unique(col)
    return sorted(str(v) for v in unique.to_pylist() if v is not None)
