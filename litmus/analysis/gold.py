"""Gold layer analytics — DuckDB SQL on silver Parquet.

Pre-aggregated manufacturing metrics computed on-the-fly from silver
measurement Parquet files.  Each ``GoldStore`` method opens an ephemeral
DuckDB connection, scans silver via ``read_parquet(glob)``, runs an
aggregate SQL query, and returns ``list[dict]``.

No materialized files, no daemon changes, no refresh step — always
reads current silver data.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import duckdb

from litmus.data._sql_helpers import sql_escape
from litmus.data.results_dir import resolve_results_dir

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
# SQL templates
# ---------------------------------------------------------------------------

_SILVER_CTE = """
WITH silver AS (
    SELECT * FROM read_parquet('{glob}', union_by_name=true, filename=true)
    WHERE filename NOT LIKE '%\\_steps.parquet' ESCAPE '\\'
      AND filename NOT LIKE '%\\_ref/%' ESCAPE '\\'
)"""

_YIELD_SQL = """
{silver_cte},
-- Deduplicate: one row per run_id (silver has one row per measurement)
runs AS (
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
    COUNT(*) FILTER (WHERE run_outcome = 'pass') AS passed,
    COUNT(*) FILTER (WHERE run_outcome = 'fail') AS failed,
    COUNT(*) FILTER (WHERE run_outcome = 'error') AS errored,
    COUNT(DISTINCT dut_serial) AS unique_serials,
    COUNT(DISTINCT dut_serial) FILTER (
        WHERE run_id IN (SELECT run_id FROM first_runs WHERE rn = 1)
    ) AS first_pass_total,
    COUNT(DISTINCT dut_serial) FILTER (
        WHERE run_id IN (SELECT run_id FROM first_runs WHERE rn = 1 AND run_outcome = 'pass')
    ) AS first_pass_passed,
    COUNT(DISTINCT dut_serial) FILTER (
        WHERE run_id IN (SELECT run_id FROM last_runs WHERE rn = 1 AND run_outcome = 'pass')
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
{silver_cte}
SELECT
    COALESCE(dut_part_number, product_id, 'unknown') AS product,
    COALESCE(station_name, station_id, 'unknown') AS station,
    step_name,
    measurement_name,
    COUNT(*) AS total_count,
    COUNT(*) FILTER (WHERE outcome = 'fail') AS fail_count,
    ROUND(COUNT(*) FILTER (WHERE outcome = 'fail') * 100.0
          / NULLIF(COUNT(*), 0), 2) AS fail_rate
FROM silver
WHERE measurement_name IS NOT NULL
    {and_clauses}
GROUP BY product, station, step_name, measurement_name
HAVING COUNT(*) FILTER (WHERE outcome = 'fail') > 0
ORDER BY fail_count DESC
LIMIT {top_n}
"""

_CPK_SQL = """
{silver_cte}
SELECT
    COALESCE(dut_part_number, product_id, 'unknown') AS product,
    COALESCE(station_name, station_id, 'unknown') AS station,
    measurement_name,
    COUNT(*) AS n,
    ROUND(AVG(value), 6) AS mean,
    ROUND(STDDEV_SAMP(value), 6) AS sigma,
    MIN(low_limit) AS lsl,
    MAX(high_limit) AS usl,
    CASE WHEN STDDEV_SAMP(value) > 0
         AND MIN(low_limit) IS NOT NULL AND MAX(high_limit) IS NOT NULL
        THEN ROUND((MAX(high_limit) - MIN(low_limit))
                    / (6 * STDDEV_SAMP(value)), 3)
    END AS cp,
    CASE WHEN STDDEV_SAMP(value) > 0
         AND (MIN(low_limit) IS NOT NULL OR MAX(high_limit) IS NOT NULL)
        THEN ROUND(LEAST(
            COALESCE((MAX(high_limit) - AVG(value))
                     / (3 * STDDEV_SAMP(value)), 1e9),
            COALESCE((AVG(value) - MIN(low_limit))
                     / (3 * STDDEV_SAMP(value)), 1e9)
        ), 3)
    END AS cpk
FROM silver
WHERE value IS NOT NULL AND measurement_name IS NOT NULL
    {and_clauses}
GROUP BY product, station, measurement_name
HAVING COUNT(*) >= {min_samples}
ORDER BY cpk ASC NULLS LAST
"""

_TREND_SQL = """
{silver_cte},
runs AS (
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
    COUNT(*) FILTER (WHERE run_outcome = 'pass') AS passed,
    ROUND(COUNT(*) FILTER (WHERE run_outcome = 'pass') * 100.0
          / NULLIF(COUNT(*), 0), 1) AS yield_pct
FROM runs
{where}
GROUP BY product, station, phase, period_day
ORDER BY period_day
"""

_RETEST_SQL = """
{silver_cte},
runs AS (
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
{silver_cte},
runs AS (
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
    ROUND(SUM(duration_s) FILTER (WHERE run_outcome = 'pass'), 2) AS pass_time_s,
    ROUND(SUM(duration_s) FILTER (WHERE run_outcome = 'fail'), 2) AS fail_time_s,
    ROUND(SUM(duration_s) FILTER (WHERE run_outcome = 'error'), 2) AS error_time_s
FROM runs
{where}
GROUP BY product, station, phase, period_day
ORDER BY period_day
"""


# ---------------------------------------------------------------------------
# GoldStore
# ---------------------------------------------------------------------------


class GoldStore:
    """Query pre-aggregated manufacturing metrics via DuckDB SQL on silver Parquet.

    Opens an ephemeral in-memory DuckDB connection per query.  No daemon,
    no materialized files, no refresh — always reads current silver data.

    Usage::

        store = GoldStore()
        rows = store.yield_summary(product="PN-123", period="week")
        store.close()  # optional — no persistent resources
    """

    def __init__(self, *, _results_dir: Path | str | None = None) -> None:
        results_dir = resolve_results_dir(_results_dir)
        self._runs_dir = results_dir / "runs"
        self._glob = str(self._runs_dir / "**" / "*.parquet")

    def _query_dicts(self, sql: str) -> list[dict[str, Any]]:
        """Execute SQL and return list of dicts with column names."""
        if not self._runs_dir.exists():
            logger.debug("Runs directory does not exist: %s", self._runs_dir)
            return []
        try:
            conn = duckdb.connect()
            try:
                result = conn.execute(sql)
                columns = [desc[0] for desc in result.description]
                rows = result.fetchall()
                return [dict(zip(columns, row)) for row in rows]
            finally:
                conn.close()
        except (duckdb.IOException, duckdb.CatalogException) as exc:
            logger.debug("No parquet data or missing columns: %s", exc)
            return []
        except duckdb.Error as exc:
            logger.warning("DuckDB error in gold query: %s", exc)
            return []

    def _silver_cte(self) -> str:
        """Build the silver CTE with the glob path."""
        return _SILVER_CTE.format(glob=sql_escape(self._glob))

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
            silver_cte=self._silver_cte(),
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
            silver_cte=self._silver_cte(),
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
            silver_cte=self._silver_cte(),
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
            silver_cte=self._silver_cte(),
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
            silver_cte=self._silver_cte(),
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
            silver_cte=self._silver_cte(),
            period_expr=_period_col(period),
            where=where,
        )
        return self._query_dicts(sql)
