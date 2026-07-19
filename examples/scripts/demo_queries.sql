-- Demo DuckDB queries for TesterKit test results
-- Run from the examples/ directory:
--   uv run python scripts/demo_duckdb.py
--
-- Or paste individual queries into the DuckDB CLI:
--   duckdb -c "SELECT ..."

-- The data path (global TesterKit results directory)
-- SET VARIABLE results = '~/.local/share/testerkit/data/runs/**/*.parquet';

-- 1. Recent test runs: what ran, what passed?
SELECT
    uut_serial,
    station_id,
    run_outcome,
    COUNT(DISTINCT measurement_name) AS measurements,
    run_started_at::DATE AS date
FROM '~/.local/share/testerkit/data/runs/**/*.parquet'
GROUP BY run_id, uut_serial, station_id, run_outcome, run_started_at
ORDER BY run_started_at DESC
LIMIT 10;

-- 2. All measurements for a specific UUT
SELECT
    step_name,
    measurement_name,
    value,
    units,
    outcome,
    low_limit,
    high_limit
FROM '~/.local/share/testerkit/data/runs/**/*.parquet'
WHERE uut_serial = 'DEMO-PWR-001'
ORDER BY step_started_at, measurement_name;

-- 3. Pass rate by step (which tests fail most?)
SELECT
    step_name,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE outcome = 'pass') AS passed,
    ROUND(100.0 * COUNT(*) FILTER (WHERE outcome = 'pass') / COUNT(*), 1) AS pass_pct
FROM '~/.local/share/testerkit/data/runs/**/*.parquet'
GROUP BY step_name
ORDER BY pass_pct ASC;

-- 4. Measurement statistics (mean, stddev, min, max)
SELECT
    measurement_name,
    units,
    COUNT(*) AS n,
    ROUND(AVG(value), 4) AS mean,
    ROUND(STDDEV(value), 4) AS stddev,
    ROUND(MIN(value), 4) AS min_val,
    ROUND(MAX(value), 4) AS max_val
FROM '~/.local/share/testerkit/data/runs/**/*.parquet'
WHERE value IS NOT NULL
GROUP BY measurement_name, units
ORDER BY measurement_name;

-- 5. Cpk calculation (process capability)
SELECT
    measurement_name,
    ROUND(AVG(value), 4) AS mean,
    ROUND(STDDEV(value), 6) AS sigma,
    ROUND(MIN(high_limit), 4) AS usl,
    ROUND(MAX(low_limit), 4) AS lsl,
    CASE
        WHEN STDDEV(value) > 0 THEN
            ROUND((MIN(high_limit) - MAX(low_limit)) / (6 * STDDEV(value)), 2)
        ELSE NULL
    END AS cpk
FROM '~/.local/share/testerkit/data/runs/**/*.parquet'
WHERE value IS NOT NULL
  AND high_limit IS NOT NULL
  AND low_limit IS NOT NULL
GROUP BY measurement_name
HAVING COUNT(*) >= 3;
