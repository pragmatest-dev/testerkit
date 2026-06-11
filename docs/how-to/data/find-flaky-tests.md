# Find flaky tests

A test is flaky if it sometimes passes and sometimes fails on the
same UUT under the same conditions. Real flakes hide one of three
things: a marginal limit, a measurement that depends on
uncontrolled environment, or a race in setup. This recipe walks
the operator UI and the parquet store to identify which.

## Prerequisites

- A few weeks of accumulated runs in the project's data dir (the
  retest signal needs repeated UUT serials across sessions to mean
  anything).
- `litmus serve` running on the bench.

## 1. Find the suspects in the Metrics → Retest tab

Open [`/metrics`](../../reference/operator-ui/metrics.md), click the
**Retest** tab. The chart shows the percentage of unique UUTs that
needed more than one attempt to clear the same step, bucketed by
period. The table below shows Period / Serials / Retested / Rate /
Avg retries.

High retest rates flag flaky tests OR marginal hardware. To narrow
to "is it the test", filter the same Metrics view by part or
station with the filter bar above the tabs and see whether the
spike follows the test, the station, or the part.

## 2. Pin the test that's flaking

The Retest tab is aggregate; for the specific test, open
[`/results`](../../reference/operator-ui/results/list.md). The list
doesn't text-filter by UUT serial, so sort by Started descending
and scan the UUT column for one of the affected serials. A flaky
test shows up as a serial that has both `passed` and `failed` rows
in its history without an obvious code change between them.

Click into a failing run. The
[Results detail](../../reference/operator-ui/results/detail.md) step
tree shows one row per `(step_path, vector_index)` regardless of
retry count; to see the individual attempts, jump to the parquet
query in the next step. Confirm the failing step's measurements
table: a borderline value just outside the limit is a marginal
limit; a wild value is environment or hardware.

## 3. Make the retry behaviour explicit

If the test is genuinely intermittent and you can't fix the root
cause yet, set an explicit retry policy with the
[`@pytest.mark.litmus_retry`](../../reference/pytest/markers.md#litmus_retry)
marker:

```python
@pytest.mark.litmus_retry(max_retries=2, delay=0.5, on=["AssertionError"])
def test_output_voltage(context, verify):
    ...
```

This translates to `pytest-rerunfailures` under the hood. Every
retry produces parquet rows with the same `vector_index` and an
incremented `vector_retry` — the operator UI's step tree counts
those and shows them as retries.

## 4. Confirm with a parquet query

To see every attempt for one (run, step, serial) combination
across the project, query the parquet store directly:

```bash
duckdb -c "
SELECT run_id, uut_serial, step_path, vector_index, vector_retry,
       measurement_outcome, measurement_value
FROM read_parquet('<data_dir>/runs/**/*.parquet')
WHERE step_path = 'test_output_voltage'
  AND uut_serial = 'DPB001-0001'
  AND record_type = 'measurement'
ORDER BY run_started_at DESC, vector_retry ASC
"
```

A row where `vector_retry` increments past 0 is a retried attempt.
A row where the final retry's `measurement_outcome` is `passed`
but earlier retries were `failed` is a real intermittent — the
unit is right, the test just had to try again. A row where every
retry of the same step on the same serial fails the same way is
not a flake at all; it's a deterministic failure.

Resolve `<data_dir>` from
[`ProjectConfig`](../../reference/configuration.md) or check the
[Three Stores](../../concepts/data/three-stores.md) page for the default
locations.

## 5. Cross-check the environment with channels

If the measurement is wild but the UUT is fine, the cause is
usually environmental. Open
[`/channels`](../../reference/operator-ui/channels/list.md), find the
session ID from the failing run's detail page, and look at any
power-rail, temperature, or supply-current channel logged during
that session. A 50 mV brown-out on the supply rail during the
failing window is a smoking gun.

## Related

- [Metrics — Retest tab](../../reference/operator-ui/metrics.md) — the chart used in step 1
- [Results — detail view](../../reference/operator-ui/results/detail.md) — the step tree used in step 2
- [`litmus_retry` marker](../../reference/pytest/markers.md#litmus_retry) — the retry policy in step 3
- [Parquet schema → Retries](../../reference/data/parquet-schema.md#retries) — `vector_retry` column semantics
- [Three stores](../../concepts/data/three-stores.md) — ParquetBackend + ChannelStore
- [Compare two runs](compare-runs.md) — what to do once you've narrowed it to two specific runs
