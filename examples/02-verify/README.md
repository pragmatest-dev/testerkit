# Stage 2 — `verify` + Parquet logging

Same tests, same DUT. The only change: `assert` becomes
`verify(name, value, limit=...)`, and every measurement is written
to a Parquet row with units, limits, and outcome.

## Diff from stage 1

- Added dep: `litmus-test` (the Litmus platform)
- Replaced `assert 3.2 <= v <= 3.4` with `verify("v_rail", v, limit=V_RAIL)`
- Added `from litmus.config.test_config import Limit` and one inline `Limit(...)` at module level

The `conftest.py` fixture is unchanged.

## Run it

```bash
cd examples/02-verify
uv run pytest -v
```

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
