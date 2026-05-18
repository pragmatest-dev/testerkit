# Query API reference

The Query API is the public read path over Litmus's materialized parquet stores. Every operator page in the NiceGUI UI, every HTTP `GET` under `/api/runs`, `/api/steps`, `/api/measurements`, and every `litmus metrics …` CLI subcommand goes through it. Reach for these classes when you need analytics from Python — they handle DuckDB schema, filtering, and pagination so your code stays in Pydantic models instead of raw SQL.

Three classes, one per materialized table. All read through the runs daemon's DuckDB Flight server when one is running, falling back to direct parquet reads otherwise.

| Class | Table | Use for |
|---|---|---|
| [`RunsQuery`](#runsquery) | `runs` (one row per run) | Recent runs, per-run summary, run-level filters (phase, product, station, lot, outcome, date range) |
| [`StepsQuery`](#stepsquery) | `steps` (one row per pytest item × vector) | Step-level results, per-run step list, step-tree views, failure pareto by step |
| [`MeasurementsQuery`](#measurementsquery) | `measurements` (one row per measurement) | Yield, Cpk, retest rates, parametric histograms, time-loss analytics |

Open one with no args to read the active project's data dir; pass `_data_dir=<path>` to point elsewhere. Always close it (the daemon ref counts open clients):

```python
from litmus.analysis.runs_query import RunsQuery

with RunsQuery() as q:
    recent = q.list_recent(limit=20, outcome="failed")
    for r in recent:
        print(r.run_id, r.dut_serial, r.outcome)
```

Row records returned by these methods live in [models.md](models.md) — see `RunRow`, `StepRow`, `StepNode`. Filter shapes (`FilterSet`, `FacetSpec`, `FacetOption`) also have field tables there.

For low-level DuckDB queries against the parquet files directly, see [Querying events](../how-to/querying-events.md). The Query API is generally the better path — it shields you from schema renames and partition layout changes.

<!-- GENERATED:query-api-classes:start -->
## `RunsQuery` {#runsquery}

Read-only client over the runs daemon's ``runs`` table.

Source: `litmus.analysis.runs_query`. Import: `from litmus.analysis.runs_query import RunsQuery`.

### `RunsQuery.close` {#runsquery-close}

`close() → None`

Release daemon ref and close Flight client.

### `RunsQuery.list_recent` {#runsquery-list_recent}

`list_recent(limit: int = 50, *, offset: int = 0, include_incomplete: bool = False, phase: str | list[str] | None = None, product: str | list[str] | None = None, station: str | list[str] | None = None, lot: str | list[str] | None = None, outcome: str | list[str] | None = None, since: str | None = None, until: str | None = None) → list[RunRow]`

Return one page of recent runs, most recent first.

### `RunsQuery.get` {#runsquery-get}

`get(run_id: str) → RunRow | None`

Return one run by id-prefix (8-char) or ``None`` if not found.

### `RunsQuery.find_for_session` {#runsquery-find_for_session}

`find_for_session(session_id: str, *, include_incomplete: bool = False) → list[RunRow]`

Return all runs sharing a ``session_id`` (multi-DUT siblings).

### `RunsQuery.failure_pareto` {#runsquery-failure_pareto}

`failure_pareto(*, group_by: str = 'dut_part_number', top_n: int = 10, phase: str | list[str] | None = None, product: str | list[str] | None = None, station: str | list[str] | None = None, since: str | None = None, until: str | None = None) → list[dict[str, Any]]`

Pareto of failing runs grouped by ``group_by`` column.

### `RunsQuery.count` {#runsquery-count}

`count(*, include_incomplete: bool = False, phase: str | list[str] | None = None, product: str | list[str] | None = None, station: str | list[str] | None = None, lot: str | list[str] | None = None, outcome: str | list[str] | None = None, since: str | None = None, until: str | None = None) → int`

Total number of runs matching the same filters as :meth:`list_recent`.

### `RunsQuery.distinct_filter_values` {#runsquery-distinct_filter_values}

`distinct_filter_values() → dict[str, list[str]]`

Return distinct values for each filterable run column.

### `RunsQuery.count_by_outcome` {#runsquery-count_by_outcome}

`count_by_outcome() → dict[str, int]`

Return ``{outcome: count}`` over all runs.

### `RunsQuery.usage_stats` {#runsquery-usage_stats}

`usage_stats(by: str) → list[dict[str, Any]]`

Aggregate run stats grouped by a column, entirely in SQL.

### `RunsQuery.describe_columns` {#runsquery-describe_columns}

`describe_columns() → list[dict[str, str]]`

Return the ``runs`` table's columns: ``[{name, type}, ...]``.

## `StepsQuery` {#stepsquery}

Read-only client over the runs daemon's ``steps`` table.

Source: `litmus.analysis.steps_query`. Import: `from litmus.analysis.steps_query import StepsQuery`.

### `StepsQuery.close` {#stepsquery-close}

`close() → None`

Release daemon ref and close Flight client.

### `StepsQuery.list_for_run` {#stepsquery-list_for_run}

`list_for_run(run_id: str, *, include_incomplete: bool = False) → list[StepRow]`

Return every step row for a run, ordered by ``step_index``.

### `StepsQuery.failure_pareto` {#stepsquery-failure_pareto}

`failure_pareto(*, top_n: int = 10, phase: str | list[str] | None = None, product: str | list[str] | None = None, station: str | list[str] | None = None, since: str | None = None, until: str | None = None) → list[dict[str, Any]]`

Pareto of failing steps grouped by ``step_path``.

### `StepsQuery.list_for_session` {#stepsquery-list_for_session}

`list_for_session(session_id: str, *, include_incomplete: bool = False) → list[StepRow]`

Return every step row across every run sharing a ``session_id``.

### `StepsQuery.tree_for_run` {#stepsquery-tree_for_run}

`tree_for_run(run_id: str) → list[StepNode]`

Return the step tree for a run, built from ``step_path``.

### `StepsQuery.describe_columns` {#stepsquery-describe_columns}

`describe_columns() → list[dict[str, str]]`

Return the ``steps`` table's columns: ``[{name, type}, ...]``.

## `MeasurementsQuery` {#measurementsquery}

Read-only client over the runs DuckDB daemon's ``measurements`` view.

Source: `litmus.analysis.measurements_query`. Import: `from litmus.analysis.measurements_query import MeasurementsQuery`.

### `MeasurementsQuery.close` {#measurementsquery-close}

`close() → None`

Release daemon ref and close Flight client.

### `MeasurementsQuery.yield_summary` {#measurementsquery-yield_summary}

`yield_summary(*, product: str | list[str] | None = None, station: str | list[str] | None = None, phase: str | list[str] | None = None, since: str | None = None, until: str | None = None, period: str = 'day') → list[dict[str, Any]]`

Yield summary: FPY, final yield, run counts, duration stats.

### `MeasurementsQuery.pareto` {#measurementsquery-pareto}

`pareto(*, product: str | list[str] | None = None, station: str | list[str] | None = None, phase: str | list[str] | None = None, since: str | None = None, until: str | None = None, top_n: int = 10) → list[dict[str, Any]]`

Pareto analysis: top failure modes by count.

### `MeasurementsQuery.cpk` {#measurementsquery-cpk}

`cpk(*, product: str | list[str] | None = None, station: str | list[str] | None = None, phase: str | list[str] | None = None, since: str | None = None, until: str | None = None, min_samples: int = 10) → list[dict[str, Any]]`

Process capability (Cpk/Cp) per measurement.

### `MeasurementsQuery.trend` {#measurementsquery-trend}

`trend(*, product: str | list[str] | None = None, station: str | list[str] | None = None, phase: str | list[str] | None = None, since: str | None = None, until: str | None = None, period: str = 'day') → list[dict[str, Any]]`

Yield trend over time.

### `MeasurementsQuery.retest` {#measurementsquery-retest}

`retest(*, product: str | list[str] | None = None, station: str | list[str] | None = None, phase: str | list[str] | None = None, since: str | None = None, until: str | None = None, period: str = 'day') → list[dict[str, Any]]`

Retest rates: how often DUTs require multiple attempts.

### `MeasurementsQuery.time_loss` {#measurementsquery-time_loss}

`time_loss(*, product: str | list[str] | None = None, station: str | list[str] | None = None, phase: str | list[str] | None = None, since: str | None = None, until: str | None = None, period: str = 'day') → list[dict[str, Any]]`

Time lost to failures and errors.

### `MeasurementsQuery.describe_columns` {#measurementsquery-describe_columns}

`describe_columns() → list[dict[str, str]]`

Return the measurements schema: ``[{column_name, column_type}, ...]``.

### `MeasurementsQuery.parametric` {#measurementsquery-parametric}

`parametric(*, y: str, x: str, filters: FilterSet | None = None, group_by: str | None = None, chart_type: str = 'scatter', bins: int = 30, limit: int = 5000, include_incomplete: bool = False) → list[ParametricRow] | list[HistogramRow]`

Generic Y vs X query over measurements, optionally split by ``group_by``.

### `MeasurementsQuery.distinct_values` {#measurementsquery-distinct_values}

`distinct_values(column: str, *, filters: FilterSet | None = None, exclude_self: bool = True, limit: int = 500) → list[FacetOption]`

Return distinct values for ``column`` with their counts.

### `MeasurementsQuery.summary_counts` {#measurementsquery-summary_counts}

`summary_counts(*, filters: FilterSet | None = None) → SummaryCounts`

Cardinality stats for the filter section's badge.
<!-- GENERATED:query-api-classes:end -->

## DuckDB vs Query API

The Query API and raw DuckDB read the same parquet files. Pick the Query API when you want:

- typed result rows (`RunRow` / `StepRow` instead of `dict[str, Any]`);
- automatic schema drift handling (column renames update once, in the Query class);
- operator-facing identifiers handled (`product` filter → `dut_part_number`; `station` filter → `station_hostname`);
- daemon-served fast path when the runs daemon is up.

Drop to DuckDB when you need to:

- join across tables in a single SQL pass (e.g. `runs` ⨯ `measurements`);
- aggregate at granularities the Query API doesn't expose;
- script ad-hoc analytics where a Python class is overhead.

```python
import duckdb

# Bypass the Query API for a one-off join
duckdb.sql("""
    SELECT r.dut_part_number, COUNT(*) AS fails
    FROM read_parquet('data/runs/**/*.parquet') r
    JOIN read_parquet('data/measurements/**/*.parquet') m
      ON r.run_id = m.run_id
    WHERE m.outcome = 'failed'
    GROUP BY 1
    ORDER BY 2 DESC
""").df()
```

## See also

- [Models](models.md) — `RunRow`, `StepRow`, `StepNode`, `FilterSet`, `FacetSpec`, `FacetOption`, `SummaryCounts`, `ParametricRow`, `HistogramRow`
- [Three stores](../concepts/three-stores.md) — what feeds these tables
- [Parquet schema](parquet-schema.md) — the column shape underneath
- [API reference](api.md) — HTTP endpoints that wrap the Query API
- [Querying events](../how-to/querying-events.md) — raw DuckDB recipes
- [CLI reference](cli.md) — `litmus metrics …` subcommands (CLI surface for `MeasurementsQuery`)
