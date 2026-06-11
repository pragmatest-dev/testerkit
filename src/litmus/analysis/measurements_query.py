"""Read-only query client over the runs DuckDB daemon.

Each method sends SQL to the daemon over Arrow Flight against the
``measurements`` view (the ``measurements_materialized`` table plus the
in-flight overlay). Dynamic in_*/out_*/custom_* columns are read from the
``dynamic_attrs`` MAP. Returns typed rows (long-format, aggregated, or
schema descriptions).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from litmus.analysis.measurement_facets import (
    FacetOption,
    FilterSet,
    HistogramRow,
    ParametricRow,
    SummaryCounts,
)
from litmus.data import runs_duckdb_manager
from litmus.data._flight_query import FlightQueryClient
from litmus.data._sql_helpers import sql_escape
from litmus.data.data_dir import resolve_data_dir

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str) -> str:
    """Reject any identifier that isn't a bare SQL column name.

    Parametric queries take user-chosen Y/X/group_by columns. We only
    accept the measurements view's flat column names — no dotted
    paths, no quotes, no expressions.
    """
    if not _IDENT_RE.match(name):
        raise ValueError(f"invalid column identifier: {name!r}")
    return name


_DYNAMIC_COL_PREFIXES: tuple[str, ...] = ("in_", "out_", "custom_")


def _col_expr(col: str, cast_as: str = "DOUBLE") -> str:
    """SQL expression for a column reference in parametric queries.

    Fixed columns (``measurement_value``, ``run_started_at``, etc.) are
    returned as validated bare identifiers. Dynamic columns
    (``in_*``/``out_*``/``custom_*``) are MAP key lookups:
    ``dynamic_attrs['key'][1]`` cast to the requested type.

    ``cast_as='DOUBLE'`` for numeric Y/X axes. ``cast_as='VARCHAR'``
    for string-typed use such as filtering ``WHERE in_mode = 'X'``
    or grouping by a string input column.
    """
    _safe_ident(col)  # validate name is a bare identifier
    if any(col.startswith(p) for p in _DYNAMIC_COL_PREFIXES):
        safe_key = col.replace("'", "''")
        return f"TRY_CAST(dynamic_attrs['{safe_key}'][1] AS {cast_as})"
    return col


def _filter_clauses(filters: FilterSet | None) -> list[str]:
    """Build SQL WHERE-clause fragments from a ``FilterSet``.

    Multi-value filters become ``col IN ('a','b','c')``. Date range
    bounds become ``CAST(run_started_at AS DATE) >= 'YYYY-MM-DD'``.
    Empty / None filter sets contribute nothing.
    """
    if filters is None:
        return []
    clauses: list[str] = []
    for col, values in {**filters.string_filters, **filters.enum_filters}.items():
        if not values:
            continue
        escaped = ", ".join(f"'{sql_escape(v)}'" for v in values)
        clauses.append(f"{_safe_ident(col)} IN ({escaped})")
    if filters.since is not None:
        clauses.append(f"CAST(run_started_at AS DATE) >= '{filters.since.isoformat()}'")
    if filters.until is not None:
        clauses.append(f"CAST(run_started_at AS DATE) <= '{filters.until.isoformat()}'")
    return clauses


def _filters_excluding(filters: FilterSet | None, column: str) -> FilterSet | None:
    """Return a copy of ``filters`` with ``column`` removed — for cross-filtering.

    Used when populating a facet's own options: that facet's value
    must NOT be a constraint, otherwise the option list collapses to
    whatever the user already picked.
    """
    if filters is None:
        return None
    return FilterSet(
        string_filters={k: v for k, v in filters.string_filters.items() if k != column},
        enum_filters={k: v for k, v in filters.enum_filters.items() if k != column},
        since=filters.since,
        until=filters.until,
    )


logger = logging.getLogger(__name__)


def _build_filter_clauses(
    *,
    part: str | list[str] | None = None,
    station: str | list[str] | None = None,
    phase: str | list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    part_expr: str = "part",
    station_expr: str = "station",
    phase_expr: str = "phase",
    date_expr: str = "period_day",
) -> list[str]:
    """Build SQL filter clauses from parameters.

    Each filter accepts a single value, a list (multi-select), or
    ``None`` (no filter). Lists become ``IN (...)`` clauses; single
    values become ``= ...`` clauses.

    Phase filtering:
      - phase=None (default): excludes ``development`` phase
      - phase='all' or ['all']: no phase filter (includes development)
      - phase='<value>' or list: filters to those specific phases

    The ``*_expr`` parameters control the SQL column/expression names,
    allowing the same filter logic to work in both subquery-based queries
    (where columns are aliased) and inline queries (where raw column
    names with COALESCE wrappers are used).
    """
    clauses: list[str] = []
    phase_values = _coerce_filter_values(phase)
    if phase_values and "all" in phase_values:
        # 'all' is a sentinel meaning "no phase filter" — match
        # historical single-value behavior even when bundled in a
        # list (e.g. ['all', 'production'] disables filtering).
        pass
    elif phase_values:
        clauses.append(_in_or_eq(phase_expr, phase_values))
    else:
        # Default: hide development phase from analytics
        clauses.append(f"{phase_expr} != 'development'")
    part_values = _coerce_filter_values(part)
    if part_values:
        clauses.append(_in_or_eq(part_expr, part_values))
    station_values = _coerce_filter_values(station)
    if station_values:
        clauses.append(_in_or_eq(station_expr, station_values))
    if since:
        clauses.append(f"{date_expr} >= '{sql_escape(since)}'")
    if until:
        clauses.append(f"{date_expr} <= '{sql_escape(until)}'")
    return clauses


def _coerce_filter_values(value: str | list[str] | None) -> list[str]:
    """Normalize a filter value into a list of non-empty strings.

    ``None`` / empty string / empty list → ``[]`` (no filter).
    Single string → ``[value]``.
    List → filter out empty strings.
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    return [v for v in value if v]


def _in_or_eq(column_expr: str, values: list[str]) -> str:
    """Render ``column = 'x'`` for one value or ``column IN (...)`` for many.

    DuckDB handles both the same way at the planner level; the
    ``=`` form is just shorter and reads better in logs.
    """
    if len(values) == 1:
        return f"{column_expr} = '{sql_escape(values[0])}'"
    quoted = ", ".join(f"'{sql_escape(v)}'" for v in values)
    return f"{column_expr} IN ({quoted})"


def _build_where(
    *,
    part: str | list[str] | None = None,
    station: str | list[str] | None = None,
    phase: str | list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> str:
    """Build a SQL ``WHERE`` clause for subquery-based queries.

    Uses aliased column names (part, station, phase, period_day).
    """
    clauses = _build_filter_clauses(
        part=part,
        station=station,
        phase=phase,
        since=since,
        until=until,
    )
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


def _build_and_clauses(
    *,
    part: str | list[str] | None = None,
    station: str | list[str] | None = None,
    phase: str | list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> str:
    """Build SQL ``AND`` clauses for inline queries (pareto, cpk).

    Uses raw measurement column names with COALESCE wrappers.
    """
    clauses = _build_filter_clauses(
        part=part,
        station=station,
        phase=phase,
        since=since,
        until=until,
        part_expr="COALESCE(uut_part_number, part_id, 'unknown')",
        # Match the same column the operator's dropdown is built
        # from (``station_hostname`` first; see ``get_yield_filter_options``
        # in ``ui/shared/services.py``). ``station_name`` is admin-
        # facing — never used as a filter target.
        station_expr="COALESCE(station_hostname, station_id, 'unknown')",
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
# SQL templates — reference the daemon's ``measurements`` VIEW directly
# ---------------------------------------------------------------------------

_YIELD_SQL = """
WITH runs AS (
    SELECT DISTINCT ON (run_id)
        run_id,
        COALESCE(uut_part_number, part_id, 'unknown') AS part,
        COALESCE(station_hostname, station_id, 'unknown') AS station,
        COALESCE(test_phase, 'unknown') AS phase,
        uut_serial,
        run_outcome,
        run_started_at,
        run_ended_at,
        {period_expr} AS period_day
    FROM measurements
    ORDER BY run_id
),
first_runs AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY uut_serial, part, station, phase
            ORDER BY run_started_at
        ) AS rn
    FROM runs
),
last_runs AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY uut_serial, part, station, phase
            ORDER BY run_started_at DESC
        ) AS rn
    FROM runs
)
SELECT
    part,
    station,
    phase,
    period_day AS period,
    COUNT(*) AS total_runs,
    COUNT(*) FILTER (WHERE run_outcome = 'passed') AS passed,
    COUNT(*) FILTER (WHERE run_outcome = 'failed') AS failed,
    COUNT(*) FILTER (WHERE run_outcome = 'errored') AS errored,
    COUNT(DISTINCT uut_serial) AS unique_serials,
    COUNT(DISTINCT uut_serial) FILTER (
        WHERE run_id IN (SELECT run_id FROM first_runs WHERE rn = 1)
    ) AS first_pass_total,
    COUNT(DISTINCT uut_serial) FILTER (
        WHERE run_id IN (SELECT run_id FROM first_runs WHERE rn = 1 AND run_outcome = 'passed')
    ) AS first_pass_passed,
    COUNT(DISTINCT uut_serial) FILTER (
        WHERE run_id IN (SELECT run_id FROM last_runs WHERE rn = 1 AND run_outcome = 'passed')
    ) AS final_passed,
    ROUND(AVG(EPOCH(run_ended_at::TIMESTAMP - run_started_at::TIMESTAMP)), 2) AS avg_duration_s,
    ROUND(QUANTILE_CONT(EPOCH(run_ended_at::TIMESTAMP - run_started_at::TIMESTAMP), 0.95), 2)
        AS p95_duration_s
FROM runs
{where}
GROUP BY part, station, phase, period_day
ORDER BY period_day
"""

_PARETO_SQL = """
SELECT
    COALESCE(uut_part_number, part_id, 'unknown') AS part,
    COALESCE(station_hostname, station_id, 'unknown') AS station,
    step_name,
    measurement_name,
    COUNT(*) AS total_count,
    COUNT(*) FILTER (WHERE measurement_outcome = 'failed') AS fail_count,
    ROUND(COUNT(*) FILTER (WHERE measurement_outcome = 'failed') * 100.0
          / NULLIF(COUNT(*), 0), 2) AS fail_rate
FROM measurements
WHERE record_type = 'measurement'
    {and_clauses}
GROUP BY part, station, step_name, measurement_name
HAVING COUNT(*) FILTER (WHERE measurement_outcome = 'failed') > 0
ORDER BY fail_count DESC
LIMIT {top_n}
"""

_CPK_SQL = """
SELECT
    COALESCE(uut_part_number, part_id, 'unknown') AS part,
    COALESCE(station_hostname, station_id, 'unknown') AS station,
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
FROM measurements
WHERE record_type = 'measurement' AND measurement_value IS NOT NULL
    {and_clauses}
GROUP BY part, station, measurement_name
HAVING COUNT(*) >= {min_samples}
ORDER BY cpk ASC NULLS LAST
"""

_TREND_SQL = """
WITH runs AS (
    SELECT DISTINCT ON (run_id)
        run_id,
        COALESCE(uut_part_number, part_id, 'unknown') AS part,
        COALESCE(station_hostname, station_id, 'unknown') AS station,
        COALESCE(test_phase, 'unknown') AS phase,
        run_outcome,
        {period_expr} AS period_day
    FROM measurements
    ORDER BY run_id
)
SELECT
    part,
    station,
    phase,
    period_day AS period,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE run_outcome = 'passed') AS passed,
    ROUND(COUNT(*) FILTER (WHERE run_outcome = 'passed') * 100.0
          / NULLIF(COUNT(*), 0), 1) AS yield_pct
FROM runs
{where}
GROUP BY part, station, phase, period_day
ORDER BY period_day
"""

_RETEST_SQL = """
WITH runs AS (
    SELECT DISTINCT ON (run_id)
        run_id,
        COALESCE(uut_part_number, part_id, 'unknown') AS part,
        COALESCE(station_hostname, station_id, 'unknown') AS station,
        COALESCE(test_phase, 'unknown') AS phase,
        uut_serial,
        {period_expr} AS period_day
    FROM measurements
    ORDER BY run_id
),
serial_counts AS (
    SELECT part, station, phase, period_day,
           uut_serial, COUNT(*) AS executions
    FROM runs
    WHERE uut_serial IS NOT NULL
    GROUP BY part, station, phase, period_day, uut_serial
)
SELECT
    part,
    station,
    phase,
    period_day AS period,
    COUNT(*) AS total_serials,
    COUNT(*) FILTER (WHERE executions > 1) AS retested_count,
    ROUND(COUNT(*) FILTER (WHERE executions > 1) * 100.0
          / NULLIF(COUNT(*), 0), 2) AS retest_rate,
    ROUND(AVG(executions - 1), 2) AS avg_retries
FROM serial_counts
{where}
GROUP BY part, station, phase, period_day
ORDER BY period_day
"""

_TIME_LOSS_SQL = """
WITH runs AS (
    SELECT DISTINCT ON (run_id)
        run_id,
        COALESCE(uut_part_number, part_id, 'unknown') AS part,
        COALESCE(station_hostname, station_id, 'unknown') AS station,
        COALESCE(test_phase, 'unknown') AS phase,
        run_outcome,
        EPOCH(run_ended_at::TIMESTAMP - run_started_at::TIMESTAMP) AS duration_s,
        {period_expr} AS period_day
    FROM measurements
    WHERE run_started_at IS NOT NULL AND run_ended_at IS NOT NULL
    ORDER BY run_id
)
SELECT
    part,
    station,
    phase,
    period_day AS period,
    ROUND(SUM(duration_s), 2) AS total_time_s,
    ROUND(SUM(duration_s) FILTER (WHERE run_outcome = 'passed'), 2) AS pass_time_s,
    ROUND(SUM(duration_s) FILTER (WHERE run_outcome = 'failed'), 2) AS fail_time_s,
    ROUND(SUM(duration_s) FILTER (WHERE run_outcome = 'errored'), 2) AS error_time_s
FROM runs
{where}
GROUP BY part, station, phase, period_day
ORDER BY period_day
"""


# ---------------------------------------------------------------------------
# MeasurementsQuery
# ---------------------------------------------------------------------------


class MeasurementsQuery:
    """Read-only client over the runs DuckDB daemon's ``measurements`` view.

    The daemon exposes a single ``measurements`` view that unions all
    raw measurement parquet files. Methods on this class send SQL
    queries over Arrow Flight and return typed result rows.

    Usage::

        q = MeasurementsQuery()
        rows = q.yield_summary(part="PN-123", period="week")
        q.close()
    """

    def __init__(self, *, _data_dir: Path | str | None = None) -> None:
        data_dir = resolve_data_dir(_data_dir)
        self._runs_dir = data_dir / "runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)

        location = runs_duckdb_manager.acquire(self._runs_dir)
        self._flight = FlightQueryClient(
            location,
            "runs",
            reacquire=lambda: runs_duckdb_manager.acquire(self._runs_dir),
            label="MeasurementsQuery",
        )

    def _query_dicts(self, sql: str) -> list[dict[str, Any]]:
        """Execute SQL via Flight and return list of dicts."""
        return self._flight.query(sql)

    def close(self) -> None:
        """Release daemon ref and close Flight client."""
        self._flight.close()
        runs_duckdb_manager.release(self._runs_dir)

    def __enter__(self) -> MeasurementsQuery:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def yield_summary(
        self,
        *,
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        phase: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ) -> list[dict[str, Any]]:
        """Yield summary: FPY, final yield, run counts, duration stats.

        Returns one row per (part, station, phase, period).
        """
        where = _build_where(
            part=part,
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
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        phase: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        top_n: int = 10,
    ) -> list[dict[str, Any]]:
        """Pareto analysis: top failure modes by count.

        Returns one row per (part, station, step, measurement).
        """
        sql = _PARETO_SQL.format(
            and_clauses=_build_and_clauses(
                part=part,
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
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        phase: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        min_samples: int = 10,
    ) -> list[dict[str, Any]]:
        """Process capability (Cpk/Cp) per measurement.

        Returns one row per (part, station, measurement_name).
        """
        sql = _CPK_SQL.format(
            and_clauses=_build_and_clauses(
                part=part,
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
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        phase: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ) -> list[dict[str, Any]]:
        """Yield trend over time.

        Returns one row per (part, station, phase, period).
        """
        where = _build_where(
            part=part,
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
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        phase: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ) -> list[dict[str, Any]]:
        """Retest rates: how often UUTs require multiple attempts.

        Returns one row per (part, station, phase, period).
        """
        where = _build_where(
            part=part,
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
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        phase: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ) -> list[dict[str, Any]]:
        """Time lost to failures and errors.

        Returns one row per (part, station, phase, period).
        """
        where = _build_where(
            part=part,
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
    # Parametric viewer — generic Y/X query over measurements
    # ------------------------------------------------------------------

    def describe_columns(self) -> list[dict[str, str]]:
        """Return the measurements schema: ``[{column_name, column_type}, ...]``.

        Returns fixed columns from ``measurements_materialized`` plus dynamic
        column names discovered during ingest (from ``measurement_io_schema``).
        Dynamic columns are reported as ``DOUBLE`` so the explore page's
        classifier includes them as Y/X candidates; values are actually
        ``VARCHAR`` in the MAP, with ``TRY_CAST`` applied at query time.
        """
        fixed = self._query_dicts(
            "SELECT column_name, column_type"
            " FROM (DESCRIBE measurements_materialized)"
            " WHERE column_name NOT IN ('file_path', 'dynamic_attrs')"
        )
        dynamic = self._query_dicts(
            "SELECT DISTINCT column_name, 'DOUBLE' AS column_type FROM measurement_io_schema"
        )
        return [*fixed, *dynamic]

    def parametric(
        self,
        *,
        y: str,
        x: str,
        filters: FilterSet | None = None,
        group_by: str | None = None,
        chart_type: str = "scatter",
        bins: int = 30,
        limit: int = 5000,
        include_incomplete: bool = False,
    ) -> list[ParametricRow] | list[HistogramRow]:
        """Generic Y vs X query over measurements, optionally split by ``group_by``.

        Returns long-format rows. Shape depends on ``chart_type``:

        - ``scatter`` / ``line``: ``ParametricRow`` per measurement.
          ``line`` orders by X.
        - ``bar``: ``ParametricRow`` per (X, group) with ``y`` = AVG.
        - ``histogram``: ``HistogramRow`` per (bin, group) with ``y``
          = COUNT, ``x`` = bin midpoint. The ``x`` argument is ignored
          for histograms.

        ``filters`` is a validated ``FilterSet`` — multi-value, mixing
        string and enum facets plus optional date range. Column names
        are validated as bare identifiers, values escape via
        :func:`sql_escape`.

        ``include_incomplete`` (default ``False``) excludes
        measurements whose owning run has not finalized — same
        semantic as the other public methods. UI live views pass
        ``True`` to plot in-flight values.
        """
        y_col = _col_expr(y)
        x_col = _col_expr(x) if chart_type != "histogram" else None
        group_col = _col_expr(group_by, "VARCHAR") if group_by else None

        clauses = [f"{y_col} IS NOT NULL"]
        if x_col is not None:
            clauses.append(f"{x_col} IS NOT NULL")
        if not include_incomplete:
            clauses.append("run_outcome IS NOT NULL")
        clauses.extend(_filter_clauses(filters))
        where = " WHERE " + " AND ".join(clauses)

        group_expr = group_col if group_col else "''"
        group_clause = f", {group_col}" if group_col else ""

        if chart_type == "histogram":
            sql = f"""
            WITH stats AS (
                SELECT MIN({y_col}) AS lo, MAX({y_col}) AS hi
                FROM measurements{where}
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
                FROM measurements, stats{where}
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
            return [HistogramRow(**row) for row in self._query_dicts(sql)]

        if chart_type == "bar":
            assert x_col is not None
            sql = f"""
            SELECT
                {x_col} AS x,
                AVG({y_col}) AS y,
                {group_expr} AS "group"
            FROM measurements{where}
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
            FROM measurements{where}
            {order}
            LIMIT {int(limit)}
            """
        return [ParametricRow(**row) for row in self._query_dicts(sql)]

    def distinct_values(
        self,
        column: str,
        *,
        filters: FilterSet | None = None,
        exclude_self: bool = True,
        limit: int = 500,
    ) -> list[FacetOption]:
        """Return distinct values for ``column`` with their counts.

        With ``exclude_self=True`` (Tableau-style cross-filter), the
        column being enumerated is dropped from the WHERE clause —
        otherwise selecting one value would collapse the option list
        to that one value.

        Counts are aggregated so the UI can show "PN-100 (12,304)" —
        useful for spotting which buckets actually have data.
        """
        col = _safe_ident(column)
        scoped = _filters_excluding(filters, column) if exclude_self else filters
        clauses = [f"{col} IS NOT NULL", *_filter_clauses(scoped)]
        where = " WHERE " + " AND ".join(clauses)
        sql = f"""
        SELECT {col} AS value, COUNT(*) AS count
        FROM measurements{where}
        GROUP BY {col}
        ORDER BY count DESC, value ASC
        LIMIT {int(limit)}
        """
        return [
            FacetOption(value=str(row["value"]), count=int(row["count"]))
            for row in self._query_dicts(sql)
        ]

    def summary_counts(self, *, filters: FilterSet | None = None) -> SummaryCounts:
        """Cardinality stats for the filter section's badge.

        One round-trip; empty result on no-data is zeros across the
        board so the UI can render unconditionally.
        """
        clauses = _filter_clauses(filters)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"""
        SELECT
            COUNT(*) AS total_rows,
            COUNT(DISTINCT run_id) AS distinct_runs,
            COUNT(DISTINCT measurement_name) AS distinct_measurements,
            COUNT(DISTINCT COALESCE(uut_part_number, part_id, 'unknown')) AS distinct_parts
        FROM measurements{where}
        """
        rows = self._query_dicts(sql)
        if not rows:
            return SummaryCounts(
                total_rows=0,
                distinct_runs=0,
                distinct_measurements=0,
                distinct_parts=0,
            )
        return SummaryCounts(**rows[0])
