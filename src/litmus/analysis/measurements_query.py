"""Read-only query client over the runs DuckDB daemon.

Each method sends SQL to the daemon over Arrow Flight against the
``measurements`` view (the ``measurements_materialized`` table plus the
in-flight overlay). Dynamic input/output fields are read by anchoring on
the core ``measurements`` view and LEFT JOINing the typed
``measurements_dynamic`` EAV table per referenced field. Returns typed
rows (long-format, aggregated, or schema descriptions).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from litmus.analysis.measurement_facets import (
    ColumnSchema,
    DynamicFieldDescriptor,
    FacetOption,
    FieldRef,
    FieldRole,
    FilterSet,
    FixedColumnDescriptor,
    HistogramRow,
    LimitBandRow,
    ParametricRow,
    ParetoRow,
    PpkRow,
    RetestRow,
    SummaryCounts,
    TimeLossRow,
    TrendRow,
    YieldRow,
)
from litmus.data import runs_duckdb_manager
from litmus.data._flight_query import FlightQueryClient
from litmus.data._sql_helpers import sql_escape
from litmus.data.data_dir import resolve_data_dir

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str) -> str:
    """Reject any identifier that isn't a bare SQL column name."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"invalid column identifier: {name!r}")
    return name


# The natural vector identity shared by the core ``measurements`` view (aliased
# ``m``) and the EAV ``measurements_dynamic`` table. ``vector_retry`` is
# NULL-bearing so the join uses IS NOT DISTINCT FROM.
_VECTOR_KEY = (
    "{a}.run_id = m.run_id"
    " AND {a}.step_index = m.step_index"
    " AND {a}.vector_index = m.vector_index"
    " AND {a}.vector_retry IS NOT DISTINCT FROM m.vector_retry"
)

# Fixed infrastructure columns that a bare string selector resolves to
# directly, bypassing the FieldRef.measurement() default.
_FIXED_COLUMNS: frozenset[str] = frozenset(
    {
        "vector_index",
        "vector_retry",
        "step_index",
        "run_started_at",
        "run_ended_at",
        "step_started_at",
        "step_ended_at",
        "measurement_timestamp",
        "limit_low",
        "limit_high",
        "limit_nominal",
        "measurement_value",
        "measurement_name",
        "measurement_outcome",
        "measurement_unit",
        "run_outcome",
        "step_outcome",
        "vector_outcome",
        "uut_serial",
        "uut_part_number",
        "uut_revision",
        "uut_lot_number",
        "test_phase",
        "step_name",
        "step_path",
        "limit_comparator",
        "uut_pin",
    }
)

# Registry of operator-meaningful fixed columns exposed as plot axes by
# describe_columns(). Excludes identity/admin columns (UUIDs, file_path, etc.).
_PLOTTABLE_FIXED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_started_at", "TIMESTAMPTZ"),
    ("run_ended_at", "TIMESTAMPTZ"),
    ("step_started_at", "TIMESTAMPTZ"),
    ("step_ended_at", "TIMESTAMPTZ"),
    ("measurement_timestamp", "TIMESTAMPTZ"),
    ("vector_index", "BIGINT"),
    ("vector_retry", "BIGINT"),
    ("step_index", "INTEGER"),
    ("measurement_value", "DOUBLE"),
    ("measurement_name", "VARCHAR"),
    ("measurement_outcome", "VARCHAR"),
    ("measurement_unit", "VARCHAR"),
    ("run_outcome", "VARCHAR"),
    ("step_outcome", "VARCHAR"),
    ("vector_outcome", "VARCHAR"),
    ("limit_low", "DOUBLE"),
    ("limit_high", "DOUBLE"),
    ("limit_nominal", "DOUBLE"),
    ("limit_comparator", "VARCHAR"),
    ("uut_serial", "VARCHAR"),
    ("uut_part_number", "VARCHAR"),
    ("uut_pin", "VARCHAR"),
    ("test_phase", "VARCHAR"),
    ("step_name", "VARCHAR"),
)

assert all(  # noqa: S101
    name in _FIXED_COLUMNS for name, _ in _PLOTTABLE_FIXED_COLUMNS
), "Drift: _PLOTTABLE_FIXED_COLUMNS contains names absent from _FIXED_COLUMNS"


def _resolve_selector(selector: str | FieldRef) -> str | FieldRef:
    """Normalize a bare string or FieldRef.

    Returns a bare ``str`` when the selector names a fixed infrastructure
    column (caller uses ``m.<col>`` directly). Returns a ``FieldRef`` for
    real EAV/measurement fields.
    """
    if isinstance(selector, str):
        if selector in _FIXED_COLUMNS:
            return selector
        return FieldRef.measurement(selector)
    return selector


def _eav_typed_expr(ref: FieldRef, alias: str) -> str:
    """Typed SQL expression selecting the correct value_* column from an EAV alias.

    When ``value_type`` is ``None`` (field absent from ``measurements_dynamic``),
    returns a typed-NULL expression so callers' ``IS NOT NULL`` filters yield
    empty results rather than a SQL type error.
    """
    vt = ref.value_type or ""
    if vt == "scalar:bool":
        return f"{alias}.value_bool"
    if vt == "scalar:int":
        return f"CAST({alias}.value_int AS DOUBLE)"
    if vt == "scalar:float":
        return f"{alias}.value_double"
    if vt == "scalar:datetime":
        return f"{alias}.value_timestamp"
    if vt in ("list", "dict"):
        return f"{alias}.value_json"
    if not vt:
        return "CAST(NULL AS DOUBLE)"
    return f"{alias}.value_text"


class _EAVJoins:
    """Collects EAV joins for input/output FieldRefs and emits their SQL.

    Also accumulates measurement-name predicates for MEASUREMENT-role
    FieldRefs so ``parametric(y="v_rail")`` scopes to that measurement.
    """

    def __init__(self) -> None:
        self._joins: list[tuple[str, FieldRef]] = []  # (alias, ref)
        self._seen: dict[tuple[str, str, str | None], str] = {}
        self._meas_name_predicates: list[str] = []

    def register(self, ref: FieldRef) -> str:
        """Register a FieldRef and return its join alias."""
        key = (ref.role.value, ref.name, ref.value_type)
        existing = self._seen.get(key)
        if existing is not None:
            return existing
        alias = f"eav_{len(self._joins)}"
        self._joins.append((alias, ref))
        self._seen[key] = alias
        return alias

    def add_meas_name_predicate(self, name: str) -> None:
        """Record a measurement_name scoping predicate for a MEASUREMENT FieldRef."""
        pred = f"m.measurement_name = '{sql_escape(name)}'"
        if pred not in self._meas_name_predicates:
            self._meas_name_predicates.append(pred)

    def meas_name_clauses(self) -> list[str]:
        """Return accumulated measurement_name predicates for the WHERE clause."""
        return list(self._meas_name_predicates)

    def join_sql(self) -> str:
        clauses = []
        for alias, ref in self._joins:
            vkey = _VECTOR_KEY.format(a=alias)
            role_pred = f" AND {alias}.role = '{sql_escape(ref.role.value)}'"
            name_pred = f" AND {alias}.name = '{sql_escape(ref.name)}'"
            vtype_pred = (
                f" AND {alias}.value_type = '{sql_escape(ref.value_type)}'"
                if ref.value_type
                else ""
            )
            clauses.append(
                f" LEFT JOIN measurements_dynamic {alias} ON {vkey}"
                f"{role_pred}{name_pred}{vtype_pred}"
            )
        return "".join(clauses)


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
    """Build SQL ``AND`` clauses for inline queries (pareto, ppk).

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
        # from (``station_hostname`` first; see ``get_runs_filter_options``
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

_PPK_SQL = """
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
    END AS pp,
    CASE WHEN STDDEV_SAMP(measurement_value) > 0
         AND (MIN(limit_low) IS NOT NULL OR MAX(limit_high) IS NOT NULL)
        THEN ROUND(LEAST(
            COALESCE((MAX(limit_high) - AVG(measurement_value))
                     / (3 * STDDEV_SAMP(measurement_value)), 1e9),
            COALESCE((AVG(measurement_value) - MIN(limit_low))
                     / (3 * STDDEV_SAMP(measurement_value)), 1e9)
        ), 3)
    END AS ppk
FROM measurements
WHERE record_type = 'measurement' AND measurement_value IS NOT NULL
    {and_clauses}
GROUP BY part, station, measurement_name
HAVING COUNT(*) >= {min_samples}
ORDER BY ppk ASC NULLS LAST
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

    Construct once and reuse — no explicit close needed::

        q = MeasurementsQuery()
        rows = q.yield_summary(part="PN-123", period="week")

    Or use as a context manager for deterministic cleanup::

        with MeasurementsQuery() as q:
            rows = q.yield_summary(part="PN-123", period="week")
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
    ) -> list[YieldRow]:
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
        return [YieldRow(**r) for r in self._query_dicts(sql)]

    def pareto(
        self,
        *,
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        phase: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        top_n: int = 10,
    ) -> list[ParetoRow]:
        """Failure pareto analysis: top failure modes by count.

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
        return [ParetoRow(**r) for r in self._query_dicts(sql)]

    def ppk(
        self,
        field: str | FieldRef | None = None,
        *,
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        phase: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        min_samples: int = 10,
    ) -> list[PpkRow]:
        """Process performance (Ppk/Pp) per measurement.

        ``field`` selects which measurement to scope — a bare string or
        ``FieldRef.measurement(...)``. Passing a non-measurement FieldRef
        is an error (outputs have no limits). ``None`` includes all
        measurements (existing behavior).

        Returns one row per (part, station, measurement_name).
        """
        name_clause = ""
        if field is not None:
            resolved = _resolve_selector(field)
            if isinstance(resolved, str):
                # Fixed column — treat as a measurement_name filter
                name_clause = f"\n    AND measurement_name = '{sql_escape(resolved)}'"
            else:
                ref = resolved
                if ref.role is not FieldRole.MEASUREMENT:
                    raise ValueError(
                        f"ppk() requires a measurement FieldRef; got role={ref.role.value!r}. "
                        "Outputs and inputs have no limits — use histogram() for distributions."
                    )
                name_clause = f"\n    AND measurement_name = '{sql_escape(ref.name)}'"
        base_and = _build_and_clauses(
            part=part,
            station=station,
            phase=phase,
            since=since,
            until=until,
        )
        sql = _PPK_SQL.format(
            and_clauses=base_and + name_clause,
            min_samples=int(min_samples),
        )
        return [PpkRow(**r) for r in self._query_dicts(sql)]

    def trend(
        self,
        *,
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        phase: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ) -> list[TrendRow]:
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
        return [TrendRow(**r) for r in self._query_dicts(sql)]

    def retest(
        self,
        *,
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        phase: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ) -> list[RetestRow]:
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
        return [RetestRow(**r) for r in self._query_dicts(sql)]

    def time_loss(
        self,
        *,
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        phase: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ) -> list[TimeLossRow]:
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
        return [TimeLossRow(**r) for r in self._query_dicts(sql)]

    # ------------------------------------------------------------------
    # Parametric viewer — generic Y/X query over measurements
    # ------------------------------------------------------------------

    def describe_columns(self) -> ColumnSchema:
        """Return the plottable column schema — curated fixed columns plus role-keyed fields.

        Fixed columns are operator-meaningful (excludes identity/admin columns).
        Dynamic fields are sourced from the ``measurement_io_schema`` catalog and
        grouped by ``(role, name)`` with their observed ``value_type`` set.
        """
        fixed = [
            FixedColumnDescriptor(name=name, column_type=col_type)
            for name, col_type in _PLOTTABLE_FIXED_COLUMNS
        ]
        raw_fields = self._query_dicts(
            "SELECT role, name, value_type FROM measurement_io_schema"
            " WHERE role IS NOT NULL AND name IS NOT NULL"
        )
        by_key: dict[tuple[str, str], list[str]] = {}
        for row in raw_fields:
            key = (str(row["role"]), str(row["name"]))
            vt = str(row["value_type"]) if row.get("value_type") else ""
            by_key.setdefault(key, [])
            if vt and vt not in by_key[key]:
                by_key[key].append(vt)
        fields = [
            DynamicFieldDescriptor(role=FieldRole(role), name=name, value_types=vts)
            for (role, name), vts in by_key.items()
        ]
        return ColumnSchema(fixed=fixed, fields=fields)

    def _resolve_eav_field(
        self,
        selector: str | FieldRef,
        joins: _EAVJoins,
        filters: FilterSet | None = None,
    ) -> str:
        """Resolve a selector to a SQL column expression, registering any EAV join.

        A bare ``str`` from ``_resolve_selector`` is a fixed infrastructure
        column — returned as ``m.<col>`` directly. A ``FieldRef`` with role
        MEASUREMENT maps to ``m.measurement_value`` and registers a
        ``measurement_name`` predicate on ``joins`` so the query scopes to
        that measurement (e.g. ``parametric(y="v_rail")`` returns only v_rail
        rows). A bare fixed column resolved as a plain ``str`` gets no
        predicate. Input/output FieldRefs get a typed EAV join; type
        coherence is checked when ``value_type`` is not specified.

        ``filters`` is forwarded to ``_resolve_value_type`` so ambiguity is
        judged within the caller's active filter scope, not globally.
        """
        resolved = _resolve_selector(selector)
        if isinstance(resolved, str):
            _safe_ident(resolved)
            return f"m.{resolved}"

        ref = resolved
        if ref.role is FieldRole.MEASUREMENT:
            joins.add_meas_name_predicate(ref.name)
            return "m.measurement_value"

        # input/output: check type coherence if value_type not specified
        if ref.value_type is None:
            vt = self._resolve_value_type(ref, filters=filters)
            ref = FieldRef(role=ref.role, name=ref.name, value_type=vt)

        alias = joins.register(ref)
        return _eav_typed_expr(ref, alias)

    def _resolve_value_type(
        self,
        ref: FieldRef,
        filters: FilterSet | None = None,
    ) -> str | None:
        """Resolve the value_type for a (role, name) pair within the active filter scope.

        Returns ``None`` when the field has no rows in ``measurements_dynamic``
        within the filtered scope (the query will yield empty results — correct
        absence-as-empty behaviour). Raises ``ValueError`` when multiple
        value_types are present within the scope (ambiguity requires the caller
        to supply an explicit ``value_type=`` on ``FieldRef``).

        ``filters`` scopes the lookup via a join on ``measurements`` so a field
        that is mixed-type globally but uniform within the user's active filter
        (e.g. a date range or part filter) resolves cleanly without raising.
        """
        filter_clauses = _filter_clauses(filters)
        if filter_clauses:
            filter_join = f" JOIN measurements m ON {_VECTOR_KEY.format(a='measurements_dynamic')}"
            filter_where = " AND ".join(filter_clauses)
            sql = (
                f"SELECT value_type, COUNT(*) AS cnt"
                f" FROM measurements_dynamic{filter_join}"
                f" WHERE measurements_dynamic.role = '{sql_escape(ref.role.value)}'"
                f"   AND measurements_dynamic.name = '{sql_escape(ref.name)}'"
                f"   AND {filter_where}"
                f" GROUP BY value_type"
            )
        else:
            sql = (
                f"SELECT value_type, COUNT(*) AS cnt"
                f" FROM measurements_dynamic"
                f" WHERE role = '{sql_escape(ref.role.value)}'"
                f"   AND name = '{sql_escape(ref.name)}'"
                f" GROUP BY value_type"
            )
        rows = self._query_dicts(sql)
        if not rows:
            return None
        if len(rows) == 1:
            vt = rows[0]["value_type"]
            return str(vt) if vt else None
        breakdown = ", ".join(f"{r['value_type']} ({r['cnt']})" for r in rows)
        raise ValueError(
            f"{ref.role.value} {ref.name!r} has {len(rows)} value_types in scope: "
            f"{breakdown}. Specify value_type= on FieldRef to disambiguate."
        )

    def parametric(
        self,
        *,
        y: str | FieldRef,
        x: str | FieldRef,
        filters: FilterSet | None = None,
        group_by: str | FieldRef | None = None,
        limit: int = 5000,
        include_incomplete: bool = False,
    ) -> list[ParametricRow]:
        """Y vs X scatter/line points over measurements, optionally split by ``group_by``.

        Returns ``ParametricRow`` per measurement. Scatter vs line is a render
        choice — pass the same points to either. ``group_by`` accepts a bare
        string (fixed column name) or a ``FieldRef`` for EAV role fields.
        ``y`` and ``x`` accept a bare string (measurement shorthand) or an
        explicit ``FieldRef``.

        ``include_incomplete`` (default ``False``) excludes measurements whose
        owning run has not finalized. UI live views pass ``True``.
        """
        joins = _EAVJoins()
        y_col = self._resolve_eav_field(y, joins, filters=filters)
        x_col = self._resolve_eav_field(x, joins, filters=filters)
        group_col: str | None = None
        if group_by is not None:
            if isinstance(group_by, FieldRef):
                group_col = self._resolve_eav_field(group_by, joins, filters=filters)
            else:
                _safe_ident(group_by)
                group_col = f"m.{group_by}"

        clauses = [f"{y_col} IS NOT NULL", f"{x_col} IS NOT NULL"]
        if not include_incomplete:
            clauses.append("m.run_outcome IS NOT NULL")
        clauses.extend(joins.meas_name_clauses())
        clauses.extend(_filter_clauses(filters))
        where = " WHERE " + " AND ".join(clauses)
        frm = f"measurements m{joins.join_sql()}"
        group_expr = group_col if group_col else "''"
        sql = f"""
        SELECT
            {x_col} AS x,
            {y_col} AS y,
            {group_expr} AS "group"
        FROM {frm}{where}
        LIMIT {int(limit)}
        """
        return [ParametricRow(**row) for row in self._query_dicts(sql)]

    def histogram(
        self,
        *,
        field: str | FieldRef,
        bins: int = 30,
        group_by: str | FieldRef | None = None,
        filters: FilterSet | None = None,
    ) -> list[HistogramRow]:
        """Distribution of one field's values, bucketed into ``bins`` bins.

        Returns one ``HistogramRow`` per (bin, group) with ``y`` = count and
        ``x`` = bin midpoint. ``field`` accepts a bare string (measurement
        shorthand) or an explicit ``FieldRef``. ``group_by`` accepts a bare
        string (fixed column name) or a ``FieldRef`` for EAV role fields.
        """
        joins = _EAVJoins()
        field_col = self._resolve_eav_field(field, joins, filters=filters)
        group_col: str | None = None
        if group_by is not None:
            if isinstance(group_by, FieldRef):
                group_col = self._resolve_eav_field(group_by, joins, filters=filters)
            else:
                _safe_ident(group_by)
                group_col = f"m.{group_by}"

        clauses = [f"{field_col} IS NOT NULL", "m.run_outcome IS NOT NULL"]
        clauses.extend(joins.meas_name_clauses())
        clauses.extend(_filter_clauses(filters))
        where = " WHERE " + " AND ".join(clauses)
        frm = f"measurements m{joins.join_sql()}"
        group_expr = group_col if group_col else "''"

        sql = f"""
        WITH stats AS (
            SELECT MIN({field_col}) AS lo, MAX({field_col}) AS hi
            FROM {frm}{where}
        ),
        bucketed AS (
            SELECT
                LEAST(
                    CAST(FLOOR(({field_col} - stats.lo)
                        / NULLIF((stats.hi - stats.lo) / {int(bins)}, 0)) AS INTEGER),
                    {int(bins) - 1}
                ) AS bin,
                stats.lo AS lo, stats.hi AS hi,
                {group_expr} AS "group"
            FROM {frm}, stats{where}
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

    def latest_run_limits(
        self,
        *,
        x: str | FieldRef,
        filters: FilterSet | None = None,
    ) -> list[LimitBandRow]:
        """Limit envelope from the most recent run, keyed by the chart's X.

        Meaningful only when ``filters`` scope to a single
        ``measurement_name`` (the caller checks). Picks the latest
        finalized run that carries limits for the scoped rows, then
        returns its ``(x, limit_low, limit_high)`` — one row per distinct
        X, ordered by X. A condition-indexed limit renders as a step
        band; a constant limit collapses to a flat one.
        """
        joins = _EAVJoins()
        x_col = self._resolve_eav_field(x, joins, filters=filters)
        clauses = [f"{x_col} IS NOT NULL", "m.run_outcome IS NOT NULL"]
        clauses.extend(joins.meas_name_clauses())
        clauses.extend(_filter_clauses(filters))
        where = " WHERE " + " AND ".join(clauses)
        frm = f"measurements m{joins.join_sql()}"
        sql = f"""
        WITH latest AS (
            SELECT m.run_id AS run_id
            FROM {frm}{where}
              AND (m.limit_low IS NOT NULL OR m.limit_high IS NOT NULL)
            ORDER BY m.run_started_at DESC
            LIMIT 1
        )
        SELECT
            {x_col} AS x,
            MAX(m.limit_low) AS low,
            MAX(m.limit_high) AS high
        FROM {frm}{where}
          AND m.run_id = (SELECT run_id FROM latest)
        GROUP BY {x_col}
        ORDER BY {x_col}
        """
        return [LimitBandRow(**row) for row in self._query_dicts(sql)]

    def distinct_values(
        self,
        column: str,
        *,
        role: FieldRole | str | None = None,
        filters: FilterSet | None = None,
        exclude_self: bool = True,
        limit: int = 500,
    ) -> list[FacetOption]:
        """Return distinct values for ``column`` with their counts.

        With ``exclude_self=True`` (Tableau-style cross-filter), the
        column being enumerated is dropped from the WHERE clause —
        otherwise selecting one value would collapse the option list
        to that one value.

        ``role`` filters the ``measurements_dynamic`` EAV table when
        ``column`` is ``"name"`` — use it to enumerate only input or
        output field names.

        Counts are aggregated so the UI can show "PN-100 (12,304)" —
        useful for spotting which buckets actually have data.
        """
        col = _safe_ident(column)
        if role is not None:
            role_val = FieldRole(role) if isinstance(role, str) else role
            scoped = _filters_excluding(filters, column) if exclude_self else filters
            filter_clauses = _filter_clauses(scoped)
            role_clause = f" AND role = '{sql_escape(role_val.value)}'"
            clauses = [f"{col} IS NOT NULL", *filter_clauses]
            # measurements_dynamic has no run-level columns; join measurements
            # to apply FilterSet predicates when filters are present.
            join_sql = (
                f" JOIN measurements m ON {_VECTOR_KEY.format(a='measurements_dynamic')}"
                if filter_clauses
                else ""
            )
            dyn_where = " WHERE " + " AND ".join(clauses) + role_clause
            sql = f"""
            SELECT {col} AS value, COUNT(*) AS count
            FROM measurements_dynamic{join_sql}{dyn_where}
            GROUP BY {col}
            ORDER BY count DESC, value ASC
            LIMIT {int(limit)}
            """
        else:
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
