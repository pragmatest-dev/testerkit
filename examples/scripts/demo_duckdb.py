"""Demo: DuckDB queries on Litmus test results.

Run from anywhere:
    uv run python examples/scripts/demo_duckdb.py

Shows SQL analytics on Parquet test results — no database server needed.
"""

import duckdb

from litmus.data.data_dir import resolve_data_dir

results = resolve_data_dir()
parquet = f"{results}/runs/**/*.parquet"
db = duckdb.connect()


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


# 1. Recent runs
section("Recent test runs")
print(
    db.sql(f"""
    SELECT
        uut_serial,
        station_id,
        run_outcome,
        COUNT(DISTINCT measurement_name) AS measurements,
        run_started_at::DATE AS date
    FROM "{parquet}"
    GROUP BY run_id, uut_serial, station_id, run_outcome, run_started_at
    ORDER BY run_started_at DESC
    LIMIT 10
""")
)

# 2. Measurement statistics
section("Measurement statistics")
print(
    db.sql(f"""
    SELECT
        measurement_name,
        unit,
        COUNT(*) AS n,
        ROUND(AVG(value), 4) AS mean,
        ROUND(STDDEV(value), 4) AS stddev,
        ROUND(MIN(value), 4) AS min_val,
        ROUND(MAX(value), 4) AS max_val
    FROM "{parquet}"
    WHERE value IS NOT NULL
    GROUP BY measurement_name, unit
    ORDER BY measurement_name
""")
)

# 3. Pass rate by step
section("Pass rate by step")
print(
    db.sql(f"""
    SELECT
        step_name,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE outcome = 'pass') AS passed,
        ROUND(100.0 * COUNT(*) FILTER (WHERE outcome = 'pass') / COUNT(*), 1)
            AS pass_pct
    FROM "{parquet}"
    GROUP BY step_name
    ORDER BY pass_pct ASC
""")
)

# 4. Cpk (process capability) — only for measurements with limits
section("Process capability (Cpk)")
print(
    db.sql(f"""
    SELECT
        measurement_name,
        ROUND(AVG(value), 4) AS mean,
        ROUND(STDDEV(value), 6) AS sigma,
        ROUND(MIN(low_limit), 4) AS lsl,
        ROUND(MIN(high_limit), 4) AS usl,
        CASE WHEN STDDEV(value) > 0 THEN
            ROUND(
                LEAST(
                    (MIN(high_limit) - AVG(value)) / (3 * STDDEV(value)),
                    (AVG(value) - MAX(low_limit)) / (3 * STDDEV(value))
                ), 2)
        ELSE NULL END AS cpk
    FROM "{parquet}"
    WHERE value IS NOT NULL
      AND high_limit IS NOT NULL
      AND low_limit IS NOT NULL
    GROUP BY measurement_name
    HAVING COUNT(*) >= 3
""")
)

# 5. Full traceability for one measurement
section("Full traceability (last measurement)")
print(
    db.sql(f"""
    SELECT
        measurement_name, value, unit, outcome,
        uut_serial, station_id, git_commit,
        meas_instrument, meas_instrument_channel,
        instr_serial, instr_cal_due,
        run_started_at
    FROM "{parquet}"
    ORDER BY run_started_at DESC
    LIMIT 1
""")
)
