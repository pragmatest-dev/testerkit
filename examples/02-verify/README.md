# Stage 2 — `verify` + Parquet logging

`assert` becomes `verify(name, value, limit=...)`, and every
measurement is written to a Parquet row with units, limits, and
outcome. Drivers and the mock pattern from stage 1 carry forward
unchanged.

## Diff from stage 1

- Added dep: `litmus-test` (the Litmus platform)
- Replaced `assert 3.2 <= v <= 3.4` with `verify("v_rail", v, limit=V_RAIL)`
- Added `from litmus.models.test_config import Limit` and one inline
  `Limit(...)` at module level
- Added `test_intermittent_glitch` decorated with
  `@pytest.mark.litmus_retry(max_attempts=3, delay=0.05)` — retry
  on transient failures (VISA timeouts, instrument-not-ready blips)

The `drivers/` folder, the conditional-mock fixture (`mock_instruments`
flag → `Mock(cls, ...)` else real driver), and `pytest.ini`'s
`--mock-instruments` default are all carried over from stage 1
unchanged.

## Run it

```bash
cd examples/02-verify
uv run pytest -v
```

## Mocks need return values

`Mock(DMM, measure_dc_voltage=3.31)` is doing the load-bearing work
in mock mode: the test passes because `dmm.measure_dc_voltage()`
returns `3.31`, inside the `V_RAIL` limit. Flip the kwarg to `4.0`
and the same test fails. There's no magic — every passing hardware
test without a bench has explicit return values somewhere; here
they're in `conftest.py`. Stage 5 moves them to station YAML so
ops can edit values without touching Python.

## What changed in the report

`pytest -v` still prints pass/fail, but now a Parquet log next to
your results captures every reading:

```bash
uv run litmus runs        # list recent runs
uv run litmus show <id>   # show the measurement rows for one run
```

You can query the log with DuckDB:

```sql
SELECT name, value, low_limit, high_limit, outcome
FROM measurements
WHERE run_id = '...'
```

## `litmus_retry` — transient failures

Real benches misbehave. VISA timeouts, instrument-not-ready blips,
thermal-soak races. `litmus_retry` declares a retry budget per test:

```python
@pytest.mark.litmus_retry(max_attempts=3, delay=0.05)
def test_intermittent_glitch(verify, psu, dmm): ...
```

The demo's `test_intermittent_glitch` raises `OSError` on the first
attempt (a module-level counter forces it). `pytest -v` shows
`RERUN` then `PASSED` — the marker actually fires.

`max_attempts` is total attempts (1 = no retry); `delay` is seconds
between attempts; optional `on=[ExceptionName, ...]` narrows which
exceptions trigger a retry. Translates to
`@pytest.mark.flaky(reruns=N-1, reruns_delay=...)` so
pytest-rerunfailures owns the rerun loop. OpenHTF / unittest
wrappers map to their native retry primitives.

Stage 4 shows the same marker in the sibling sidecar.

## Why this shape

`verify()` is one verb. It does three things that always go together
for hardware test:

1. Log the value
2. Compare it to a limit
3. Raise on fail so pytest marks the test as failed

If you only want to record a value without a check
(characterization), pass no limit — the row is still logged with
outcome `DONE`.

## The gap this stage leaves

The `Limit(...)` object is **inline in Python code**. Changing a
limit means editing test source and re-running the whole suite. Stage
3 moves limits to a pytest marker so you can tune them without
touching the function body.
