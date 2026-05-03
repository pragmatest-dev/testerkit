"""Gold layer analytics — SQL on silver daemon view.

Pre-aggregated manufacturing metrics computed on-the-fly.  Each
``MetricsStore`` method sends an analytics SQL query to the runs DuckDB
daemon via Arrow Flight.  The daemon exposes a ``silver`` VIEW that
lazily reads raw measurement parquet (``read_parquet(glob,
union_by_name=true)``), so gold always sees current data without
maintaining its own DuckDB connection.

Bronze → Silver → Gold:
  - Bronze: raw parquet files written by the test runner
  - Silver: DuckDB daemon indexes runs + exposes ``silver`` view
  - Gold: analytics queries through silver daemon (this module)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from litmus.data import runs_duckdb_manager
from litmus.data._flight_query import FlightQueryClient
from litmus.data._sql_helpers import sql_escape
from litmus.data.results_dir import resolve_results_dir

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str) -> str:
    """Reject any identifier that isn't a bare SQL column name.

    Parametric queries take user-chosen Y/X/group_by columns. We only
    accept the silver view's flat column names — no dotted paths, no
    quotes, no expressions.
    """
    if not _IDENT_RE.match(name):
        raise ValueError(f"invalid column identifier: {name!r}")
    return name


logger = logging.getLogger(__name__)


def _build_filter_clauses(
    *,
    product: str | None = None,
    station: str | None = None,
    phase: str | None = None,
    since: str | None = None,
    until: str | None = None,
    product_expr: str = "product",
    station_expr: str = "station",
    phase_expr: str = "phase",
    date_expr: str = "period_day",
) -> list[str]:
    """Build SQL filter clauses from parameters.

    Phase filtering:
      - phase=None (default): excludes ``development`` phase
      - phase='all': no phase filter (includes development)
      - phase='<value>': filters to that specific phase

    The ``*_expr`` parameters control the SQL column/expression names,
    allowing the same filter logic to work in both subquery-based queries
    (where columns are aliased) and inline queries (where raw column
    names with COALESCE wrappers are used).
    """
    clauses: list[str] = []
    if phase and phase != "all":
        clauses.append(f"{phase_expr} = '{sql_escape(phase)}'")
    elif not phase:
        clauses.append(f"{phase_expr} != 'development'")
    if product:
        clauses.append(f"{product_expr} = '{sql_escape(product)}'")
    if station:
        clauses.append(f"{station_expr} = '{sql_escape(station)}'")
    if since:
        clauses.append(f"{date_expr} >= '{sql_escape(since)}'")
    if until:
        clauses.append(f"{date_expr} <= '{sql_escape(until)}'")
    return clauses


def _build_where(
    *,
    product: str | None = None,
    station: str | None = None,
    phase: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> str:
    """Build a SQL ``WHERE`` clause for subquery-based queries.

    Uses aliased column names (product, station, phase, period_day).
    """
    clauses = _build_filter_clauses(
        product=product,
        station=station,
        phase=phase,
        since=since,
        until=until,
    )
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


def _build_and_clauses(
    *,
    product: str | None = None,
    station: str | None = None,
    phase: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> str:
    """Build SQL ``AND`` clauses for inline queries (pareto, cpk).

    Uses raw silver column names with COALESCE wrappers.
    """
    clauses = _build_filter_clauses(
        product=product,
        station=station,
        phase=phase,
        since=since,
        until=until,
        product_expr="COALESCE(dut_part_number, product_id, 'unknown')",
        station_expr="COALESCE(station_name, station_id, 'unknown')",
        phase_expr="COALESCE(test_phase, 'unknown')",
        date_expr="CAST(run_started_at AS DATE)",
    )
    return ("\n    AND " + "\n    AND ".join(clauses)) if clauses else ""


def _period_col(period: str) -> str:
    """Return the SQL expression for a period bucket."""
    if period == "week":
        return "DATE_TRUNC('week', run_started_at::TIMESTAMP)::DATE"
    if period == "month":
        return "DATE_TRUNC('month', run_started_at::TIMESTAMP)::DATE"
    return "CAST(run_started_at AS DATE)"


# ---------------------------------------------------------------------------
# SQL templates — reference the daemon's ``silver`` VIEW directly
# ---------------------------------------------------------------------------

_YIELD_SQL = """
WITH runs AS (
    SELECT DISTINCT ON (run_id)
        run_id,
        COALESCE(dut_part_number, product_id, 'unknown') AS product,
        COALESCE(station_name, station_id, 'unknown') AS station,
        COALESCE(test_phase, 'unknown') AS phase,
        dut_serial,
        run_outcome,
        run_started_at,
        run_ended_at,
        {period_expr} AS period_day
    FROM silver
    ORDER BY run_id
),
first_runs AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY dut_serial, product, station, phase
            ORDER BY run_started_at
        ) AS rn
    FROM runs
),
last_runs AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY dut_serial, product, station, phase
            ORDER BY run_started_at DESC
        ) AS rn
    FROM runs
)
SELECT
    product,
    station,
    phase,
    period_day AS period,
    COUNT(*) AS total_runs,
    COUNT(*) FILTER (WHERE run_outcome = 'passed') AS passed,
    COUNT(*) FILTER (WHERE run_outcome = 'failed') AS failed,
    COUNT(*) FILTER (WHERE run_outcome = 'errored') AS errored,
    COUNT(DISTINCT dut_serial) AS unique_serials,
    COUNT(DISTINCT dut_serial) FILTER (
        WHERE run_id IN (SELECT run_id FROM first_runs WHERE rn = 1)
    ) AS first_pass_total,
    COUNT(DISTINCT dut_serial) FILTER (
        WHERE run_id IN (SELECT run_id FROM first_runs WHERE rn = 1 AND run_outcome = 'passed')
    ) AS first_pass_passed,
    COUNT(DISTINCT dut_serial) FILTER (
        WHERE run_id IN (SELECT run_id FROM last_runs WHERE rn = 1 AND run_outcome = 'passed')
    ) AS final_passed,
    ROUND(AVG(EPOCH(run_ended_at::TIMESTAMP - run_started_at::TIMESTAMP)), 2) AS avg_duration_s,
    ROUND(QUANTILE_CONT(EPOCH(run_ended_at::TIMESTAMP - run_started_at::TIMESTAMP), 0.95), 2)
        AS p95_duration_s
FROM runs
{where}
GROUP BY product, station, phase, period_day
ORDER BY period_day
"""

_PARETO_SQL = """
SELECT
    COALESCE(dut_part_number, product_id, 'unknown') AS product,
    COALESCE(station_name, station_id, 'unknown') AS station,
    step_name,
    measurement_name,
    COUNT(*) AS total_count,
    COUNT(*) FILTER (WHERE measurement_outcome = 'failed') AS fail_count,
    ROUND(COUNT(*) FILTER (WHERE measurement_outcome = 'failed') * 100.0
          / NULLIF(COUNT(*), 0), 2) AS fail_rate
FROM silver
WHERE measurement_name IS NOT NULL
    {and_clauses}
GROUP BY product, station, step_name, measurement_name
HAVING COUNT(*) FILTER (WHERE measurement_outcome = 'failed') > 0
ORDER BY fail_count DESC
LIMIT {top_n}
"""

_CPK_SQL = """
SELECT
    COALESCE(dut_part_number, product_id, 'unknown') AS product,
    COALESCE(station_name, station_id, 'unknown') AS station,
    measurement_name,
    COUNT(*) AS n,
    ROUND(AVG(measurement_value), 6) AS mean,
    ROUND(STDDEV_SAMP(measurement_value), 6) AS sigma,
    MIN(limit_low) AS lsl,
    MAX(limit_high) AS usl,
    CASE WHEN STDDEV_SAMP(measurement_value) > 0
         AND MIN(limit_low) IS NOT NULL AND MAX(limit_high) IS NOT NULL
        THEN ROUND((MAX(limit_high) - MIN(limit_low))
                    / (6 * STDDEV_SAMP(measurement_value)), 3)
    END AS cp,
    CASE WHEN STDDEV_SAMP(measurement_value) > 0
         AND (MIN(limit_low) IS NOT NULL OR MAX(limit_high) IS NOT NULL)
        THEN ROUND(LEAST(
            COALESCE((MAX(limit_high) - AVG(measurement_value))
                     / (3 * STDDEV_SAMP(measurement_value)), 1e9),
            COALESCE((AVG(measurement_value) - MIN(limit_low))
                     / (3 * STDDEV_SAMP(measurement_value)), 1e9)
        ), 3)
    END AS cpk
FROM silver
WHERE measurement_value IS NOT NULL AND measurement_name IS NOT NULL
    {and_clauses}
GROUP BY product, station, measurement_name
HAVING COUNT(*) >= {min_samples}
ORDER BY cpk ASC NULLS LAST
"""

_TREND_SQL = """
WITH runs AS (
    SELECT DISTINCT ON (run_id)
        run_id,
        COALESCE(dut_part_number, product_id, 'unknown') AS product,
        COALESCE(station_name, station_id, 'unknown') AS station,
        COALESCE(test_phase, 'unknown') AS phase,
        run_outcome,
        {period_expr} AS period_day
    FROM silver
    ORDER BY run_id
)
SELECT
    product,
    station,
    phase,
    period_day AS period,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE run_outcome = 'passed') AS passed,
    ROUND(COUNT(*) FILTER (WHERE run_outcome = 'passed') * 100.0
          / NULLIF(COUNT(*), 0), 1) AS yield_pct
FROM runs
{where}
GROUP BY product, station, phase, period_day
ORDER BY period_day
"""

_RETEST_SQL = """
WITH runs AS (
    SELECT DISTINCT ON (run_id)
        run_id,
        COALESCE(dut_part_number, product_id, 'unknown') AS product,
        COALESCE(station_name, station_id, 'unknown') AS station,
        COALESCE(test_phase, 'unknown') AS phase,
        dut_serial,
        {period_expr} AS period_day
    FROM silver
    ORDER BY run_id
),
serial_counts AS (
    SELECT product, station, phase, period_day,
           dut_serial, COUNT(*) AS attempts
    FROM runs
    WHERE dut_serial IS NOT NULL
    GROUP BY product, station, phase, period_day, dut_serial
)
SELECT
    product,
    station,
    phase,
    period_day AS period,
    COUNT(*) AS total_serials,
    COUNT(*) FILTER (WHERE attempts > 1) AS retested_count,
    ROUND(COUNT(*) FILTER (WHERE attempts > 1) * 100.0
          / NULLIF(COUNT(*), 0), 2) AS retest_rate,
    ROUND(AVG(attempts), 2) AS avg_attempts
FROM serial_counts
{where}
GROUP BY product, station, phase, period_day
ORDER BY period_day
"""

_TIME_LOSS_SQL = """
WITH runs AS (
    SELECT DISTINCT ON (run_id)
        run_id,
        COALESCE(dut_part_number, product_id, 'unknown') AS product,
        COALESCE(station_name, station_id, 'unknown') AS station,
        COALESCE(test_phase, 'unknown') AS phase,
        run_outcome,
        EPOCH(run_ended_at::TIMESTAMP - run_started_at::TIMESTAMP) AS duration_s,
        {period_expr} AS period_day
    FROM silver
    WHERE run_started_at IS NOT NULL AND run_ended_at IS NOT NULL
    ORDER BY run_id
)
SELECT
    product,
    station,
    phase,
    period_day AS period,
    ROUND(SUM(duration_s), 2) AS total_time_s,
    ROUND(SUM(duration_s) FILTER (WHERE run_outcome = 'passed'), 2) AS pass_time_s,
    ROUND(SUM(duration_s) FILTER (WHERE run_outcome = 'failed'), 2) AS fail_time_s,
    ROUND(SUM(duration_s) FILTER (WHERE run_outcome = 'errored'), 2) AS error_time_s
FROM runs
{where}
GROUP BY product, station, phase, period_day
ORDER BY period_day
"""


# ---------------------------------------------------------------------------
# MetricsStore
# ---------------------------------------------------------------------------


class MetricsStore:
    """Query pre-aggregated manufacturing metrics via the runs DuckDB daemon.

    Queries go through Arrow Flight to the runs daemon, which exposes a
    ``silver`` VIEW over raw measurement parquet files.

    Usage::

        store = MetricsStore()
        rows = store.yield_summary(product="PN-123", period="week")
        store.close()
    """

    def __init__(self, *, _results_dir: Path | str | None = None) -> None:
        results_dir = resolve_results_dir(_results_dir)
        self._runs_dir = results_dir / "runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)

        location = runs_duckdb_manager.acquire(self._runs_dir)
        self._flight = FlightQueryClient(
            location,
            "runs",
            reacquire=lambda: runs_duckdb_manager.acquire(self._runs_dir),
            label="MetricsStore",
        )

    def _query_dicts(self, sql: str) -> list[dict[str, Any]]:
        """Execute SQL via Flight and return list of dicts."""
        return self._flight.query(sql)

    def close(self) -> None:
        """Release daemon ref and close Flight client."""
        self._flight.close()
        runs_duckdb_manager.release(self._runs_dir)

    def yield_summary(
        self,
        *,
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ) -> list[dict[str, Any]]:
        """Yield summary: FPY, final yield, run counts, duration stats.

        Returns one row per (product, station, phase, period).
        """
        where = _build_where(
            product=product,
            station=station,
            phase=phase,
            since=since,
            until=until,
        )
        sql = _YIELD_SQL.format(
            period_expr=_period_col(period),
            where=where,
        )
        return self._query_dicts(sql)

    def pareto(
        self,
        *,
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        top_n: int = 10,
    ) -> list[dict[str, Any]]:
        """Pareto analysis: top failure modes by count.

        Returns one row per (product, station, step, measurement).
        """
        sql = _PARETO_SQL.format(
            and_clauses=_build_and_clauses(
                product=product,
                station=station,
                phase=phase,
                since=since,
                until=until,
            ),
            top_n=int(top_n),
        )
        return self._query_dicts(sql)

    def cpk(
        self,
        *,
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        min_samples: int = 10,
    ) -> list[dict[str, Any]]:
        """Process capability (Cpk/Cp) per measurement.

        Returns one row per (product, station, measurement_name).
        """
        sql = _CPK_SQL.format(
            and_clauses=_build_and_clauses(
                product=product,
                station=station,
                phase=phase,
                since=since,
                until=until,
            ),
            min_samples=int(min_samples),
        )
        return self._query_dicts(sql)

    def trend(
        self,
        *,
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ) -> list[dict[str, Any]]:
        """Yield trend over time.

        Returns one row per (product, station, phase, period).
        """
        where = _build_where(
            product=product,
            station=station,
            phase=phase,
            since=since,
            until=until,
        )
        sql = _TREND_SQL.format(
            period_expr=_period_col(period),
            where=where,
        )
        return self._query_dicts(sql)

    def retest(
        self,
        *,
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ) -> list[dict[str, Any]]:
        """Retest rates: how often DUTs require multiple attempts.

        Returns one row per (product, station, phase, period).
        """
        where = _build_where(
            product=product,
            station=station,
            phase=phase,
            since=since,
            until=until,
        )
        sql = _RETEST_SQL.format(
            period_expr=_period_col(period),
            where=where,
        )
        return self._query_dicts(sql)

    def time_loss(
        self,
        *,
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ) -> list[dict[str, Any]]:
        """Time lost to failures and errors.

        Returns one row per (product, station, phase, period).
        """
        where = _build_where(
            product=product,
            station=station,
            phase=phase,
            since=since,
            until=until,
        )
        sql = _TIME_LOSS_SQL.format(
            period_expr=_period_col(period),
            where=where,
        )
        return self._query_dicts(sql)

    # ------------------------------------------------------------------
    # Parametric viewer — generic Y/X query over silver
    # ------------------------------------------------------------------

    def describe_silver(self) -> list[dict[str, str]]:
        """Return the silver view's columns: ``[{name, type}, ...]``.

        Used by the parametric viewer UI to populate Y/X/group_by
        dropdowns from real schema rather than a hardcoded list.
        """
        return self._query_dicts("DESCRIBE silver")

    def parametric(
        self,
        *,
        y: str,
        x: str,
        filters: dict[str, str] | None = None,
        group_by: str | None = None,
        chart_type: str = "scatter",
        bins: int = 30,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Generic Y vs X query over silver, optionally split by ``group_by``.

        Returns long-format rows. Shape depends on ``chart_type``:

        - ``scatter`` / ``line``: one row per measurement
          ``{x, y, group}`` (group key is ``""`` when no group_by).
          ``line`` orders by X.
        - ``bar``: one row per (X, group) with ``y`` = AVG.
        - ``histogram``: one row per (bin, group) with ``y`` = COUNT,
          ``x`` = bin midpoint. ``y`` argument is the value being
          binned; ``x`` is ignored.

        ``filters`` is a flat ``{column: literal}`` dict — exact-match
        equality. Column names are validated as bare identifiers.
        """
        y_col = _safe_ident(y)
        x_col = _safe_ident(x) if chart_type != "histogram" else None
        group_col = _safe_ident(group_by) if group_by else None

        where_parts = [f"{y_col} IS NOT NULL"]
        if x_col is not None:
            where_parts.append(f"{x_col} IS NOT NULL")
        for col, value in (filters or {}).items():
            where_parts.append(f"{_safe_ident(col)} = '{sql_escape(value)}'")
        where = " WHERE " + " AND ".join(where_parts)

        group_expr = group_col if group_col else "''"
        group_clause = f", {group_col}" if group_col else ""

        if chart_type == "histogram":
            sql = f"""
            WITH stats AS (
                SELECT MIN({y_col}) AS lo, MAX({y_col}) AS hi
                FROM silver{where}
            ),
            bucketed AS (
                SELECT
                    LEAST(
                        CAST(FLOOR(({y_col} - stats.lo)
                            / NULLIF((stats.hi - stats.lo) / {int(bins)}, 0)) AS INTEGER),
                        {int(bins) - 1}
                    ) AS bin,
                    stats.lo AS lo, stats.hi AS hi,
                    {group_expr} AS "group"
                FROM silver, stats{where}
            )
            SELECT
                bin,
                lo + (bin + 0.5) * (hi - lo) / {int(bins)} AS x,
                COUNT(*) AS y,
                "group"
            FROM bucketed
            GROUP BY bin, lo, hi, "group"
            ORDER BY "group", bin
            """
        elif chart_type == "bar":
            assert x_col is not None
            sql = f"""
            SELECT
                {x_col} AS x,
                AVG({y_col}) AS y,
                {group_expr} AS "group"
            FROM silver{where}
            GROUP BY {x_col}{group_clause}
            ORDER BY {x_col}
            LIMIT {int(limit)}
            """
        else:
            assert x_col is not None
            order = f"ORDER BY {x_col}" if chart_type == "line" else ""
            sql = f"""
            SELECT
                {x_col} AS x,
                {y_col} AS y,
                {group_expr} AS "group"
            FROM silver{where}
            {order}
            LIMIT {int(limit)}
            """
        return self._query_dicts(sql)
