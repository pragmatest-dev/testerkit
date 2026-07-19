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
- `testerkit serve` running on the bench.

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
tree shows one row per step, collapsing retries; to see the
individual attempts, jump to the parquet query in the next step. Confirm the failing step's measurements
table: a borderline value just outside the limit is a marginal
limit; a wild value is environment or hardware.

## 3. Make the retry behaviour explicit

When the hardware is genuinely non-deterministic — a measurement with
irreducible jitter, not a bug you haven't found yet — make the retries
explicit and auditable with the
[`@pytest.mark.testerkit_retry`](../../reference/pytest/markers.md#testerkit_retry)
marker, so every attempt is recorded rather than hidden:

```python
@pytest.mark.testerkit_retry(max_retries=2, delay=0.5, on=["AssertionError"])
def test_output_voltage(context, verify):
    ...
```

TesterKit reruns the test up to `max_retries` times on the listed
exceptions and records every attempt with an incremented
`vector_retry` — so the step tree and the query in the next step
show them as separate retries rather than hiding them.

## 4. Confirm with a parquet query

To see every attempt for one (run, step, serial) combination
across the project, query the parquet store directly. Resolve
`<data_dir>` from your project's `testerkit.yaml`
([`ProjectConfig`](../../reference/configuration.md); see also
[Data stores](../../concepts/data/data-stores.md)):

```bash
duckdb -c "
SELECT run_id, uut_serial_number, step_path, vector_index, vector_retry,
       m.outcome AS measurement_outcome, m.value AS measurement_value
FROM read_parquet('<data_dir>/runs/**/*.parquet'), UNNEST(measurements) AS t(m)
WHERE step_path = 'test_output_voltage'
  AND uut_serial_number = 'DPB001-0001'
  AND record_type = 'vector'
ORDER BY run_started_at DESC, vector_retry ASC
"
```

A row where `vector_retry` increments past 0 is a retried attempt.
A row where the final retry's `measurement_outcome` is `passed`
but earlier retries were `failed` is a real intermittent — the
unit is right, the test just had to try again. A row where every
retry of the same step on the same serial fails the same way is
not a flake at all; it's a deterministic failure.

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
- [`testerkit_retry` marker](../../reference/pytest/markers.md#testerkit_retry) — the retry policy in step 3
- [Parquet schema → Retries](../../reference/data/parquet-schema.md#retries) — `vector_retry` column semantics
- [Data stores](../../concepts/data/data-stores.md) — EventStore, ChannelStore, FileStore, RunStore
- [Compare two runs](compare-runs.md) — what to do once you've narrowed it to two specific runs
