# Query API reference

The Query API is the public read path over Litmus's materialized parquet stores. The operator-UI results, explore, and metrics pages read through it, the HTTP routes `/api/runs`, `/api/runs/{run_id}/steps`, and `/api/metrics/*` wrap it, and every `litmus metrics …` CLI subcommand goes through it. Reach for these classes when you need analytics from Python — they handle the DuckDB Flight connection, schema, filtering, and pagination so your code stays in Pydantic models instead of raw SQL.

Three classes, one per materialized table. Every call goes through the runs DuckDB Flight daemon; constructing a query client calls `runs_duckdb_manager.acquire()`, which spawns the daemon if it isn't already running and force-restarts it if its Flight server isn't responding (`src/litmus/data/runs_duckdb_manager.py:35-72`).

| Class | Table | Use for |
|---|---|---|
| [`RunsQuery`](#runsquery) | `runs` (one row per run) | Recent runs, per-run summary, run-level filters (phase, product, station, lot, outcome, date range) |
| [`StepsQuery`](#stepsquery) | `steps` (one row per pytest item × vector, plus container rows for class- and module-scoped step paths) | Step-level results, per-run step list, step-tree views, failure pareto by step |
| [`MeasurementsQuery`](#measurementsquery) | `measurements` view | Yield, Cpk, retest rates, parametric histograms, time-loss analytics |

Open one with no args to read the active project's data dir — resolution is `_data_dir=<path>` arg → project `litmus.yaml` → `LITMUS_HOME` env → `platformdirs.user_data_dir("litmus")` (`src/litmus/data/data_dir.py`). Pass `_data_dir=<path>` to point elsewhere. Always close it (the daemon ref-counts open clients):

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

## When the Query API doesn't cover what you need

The three classes above expose the methods the UI, HTTP routes, and `litmus metrics` CLI use. If you hit a query the Query API doesn't have, the right move is to add the method to the class so every consumer benefits — not to drop to raw SQL inside your test code. File the gap; the surface is meant to grow.

For one-off ad-hoc exploration outside production code, raw DuckDB recipes live on [Querying events](../how-to/querying-events.md), which also covers the on-disk parquet layout and the `record_type` discriminator that lets one file carry run / step / measurement rows.

## See also

- [Models](models.md) — `RunRow`, `StepRow`, `StepNode`, `FilterSet`, `FacetSpec`, `FacetOption`, `SummaryCounts`, `ParametricRow`, `HistogramRow`
- [Three stores](../concepts/three-stores.md) — what feeds these tables
- [Parquet schema](parquet-schema.md) — the column shape underneath
- [API reference](api.md) — HTTP endpoints that wrap the Query API
- [Querying events](../how-to/querying-events.md) — raw DuckDB recipes
- [CLI reference](cli.md) — `litmus metrics …` subcommands (CLI surface for `MeasurementsQuery`)
