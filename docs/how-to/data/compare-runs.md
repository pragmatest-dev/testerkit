# Compare two runs

The Results view shows one run at a time. There is no built-in
side-by-side compare button — when you need to diff two runs
(known-good vs failing, before/after a fix, fixture A vs fixture B),
combine a two-tab browser pass with a parquet query.

## Prerequisites

- Both run IDs in hand. Get them from
  [`/results`](../../reference/operator-ui/results/list.md) by hovering
  the row, or from the CLI: `litmus runs`.
- `duckdb` installed for the parquet diff (optional but recommended).

## 1. Two tabs, side by side

Open each run in its own browser tab:

```
http://localhost:8000/results/<run_id_a>
http://localhost:8000/results/<run_id_b>
```

On the [Results detail](../../reference/operator-ui/results/detail.md)
page, both tabs show the same shape — overview card, step tree,
measurements table. Quickly scan:

- **Overview**: Outcome, started_at, duration, station, part
  revision. If the runs ran on different stations or part
  revisions, that's your difference.
- **Step tree**: which step has a different outcome? Click into the
  step on each tab to see the per-attempt measurements.
- **Measurements table**: sort by `step_path`, scan for rows where
  the value crossed the limit on one tab but not the other.

The two-tab scan is fastest for short runs you can eyeball. Once the
step tree is long enough that scanning is slow, jump to the parquet diff.

## 2. Diff measurements with DuckDB

For most diffs, [`MeasurementsQuery`](../../reference/data/query-api.md) filtered to each
`run_id` is enough. For a one-shot SQL diff, read the parquet directly: each row carries
its measurements nested inside it, so `UNNEST` them and join the two runs on `step_path`,
`vector_index`, and `measurement_name`. (Resolve `<data_dir>` from
[`ProjectConfig`](../../reference/configuration.md); the
[parquet schema](../../reference/data/parquet-schema.md) lists the columns.)

```bash
duckdb -c "
WITH a AS (
  SELECT step_path, vector_index, m.name AS measurement_name,
         m.value AS measurement_value, m.outcome AS measurement_outcome,
         m.limit_low, m.limit_high
  FROM read_parquet('<data_dir>/runs/**/*.parquet'), UNNEST(measurements) AS t(m)
  WHERE run_id = '<run_id_a>' AND record_type = 'vector'
),
b AS (
  SELECT step_path, vector_index, m.name AS measurement_name,
         m.value AS measurement_value, m.outcome AS measurement_outcome,
         m.limit_low, m.limit_high
  FROM read_parquet('<data_dir>/runs/**/*.parquet'), UNNEST(measurements) AS t(m)
  WHERE run_id = '<run_id_b>' AND record_type = 'vector'
)
SELECT
  COALESCE(a.step_path, b.step_path) AS step_path,
  COALESCE(a.measurement_name, b.measurement_name) AS measurement,
  a.measurement_value AS value_a,
  b.measurement_value AS value_b,
  a.measurement_outcome AS outcome_a,
  b.measurement_outcome AS outcome_b
FROM a FULL OUTER JOIN b
  USING (step_path, vector_index, measurement_name)
WHERE a.measurement_outcome IS DISTINCT FROM b.measurement_outcome
   OR a.measurement_value IS DISTINCT FROM b.measurement_value
   OR ABS(COALESCE(a.measurement_value, 0) - COALESCE(b.measurement_value, 0)) > 0.001
ORDER BY step_path, measurement_name;
"
```

Rows where one side is NULL are measurements that exist in one run
but not the other (one tested more, one tested less). Rows where
both sides are populated but the values diverge are where the
behavior changed — that's your diff.

Resolve `<data_dir>` from
[`ProjectConfig`](../../reference/configuration.md) or the
[Data stores](../../concepts/data/data-stores.md) page.

## 3. Compare environmental channels

If both runs share a session, channels are already comparable in
the same view. If they're from different sessions, open
[`/channels`](../../reference/operator-ui/channels/list.md), find each
session's channels, and click into each channel's detail view in
two tabs to compare time-series.

A common diff pattern: known-good run has a flat supply rail at
3.30 V; failing run has the same rail sagging to 3.10 V during the
failing step. That's environment, not the test logic.

## 4. Save the diff for the bug report

Pipe the DuckDB query above to CSV for attaching to the bug
report:

```bash
duckdb -c "...query above..." -csv > run_diff.csv
```

Or export both runs in full via
`litmus show <run_id> -f csv -o exports/` and diff the CSVs with
the tool of your choice.

## Related

- [Results — detail view](../../reference/operator-ui/results/detail.md) — the per-run view used in step 1
- [Channels reference](../../reference/operator-ui/channels/list.md) — the channel views used in step 3
- [Parquet schema](../../reference/data/parquet-schema.md) — the columns you can join on
- [Data stores](../../concepts/data/data-stores.md) — where the parquet files live
- [Find flaky tests](find-flaky-tests.md) — when the two runs are the same test on the same UUT
