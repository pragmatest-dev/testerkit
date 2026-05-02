#!/usr/bin/env python3
"""Example queries for Litmus Parquet results using DuckDB.

Run from an examples tier directory:
    uv run python scripts/query_results.py           # Full report
    uv run python scripts/query_results.py summary   # Just summary stats
    uv run python scripts/query_results.py tests     # Results by test
    uv run python scripts/query_results.py recent    # Recent runs
    uv run python scripts/query_results.py failed    # Failed measurements
    uv run python scripts/query_results.py dist test_load_sweep    # Value histogram
    uv run python scripts/query_results.py cpk test_load_sweep     # Cpk analysis
    uv run python scripts/query_results.py conditions              # By conditions
    uv run python scripts/query_results.py export results.csv      # Export to CSV

DuckDB provides SQL queries over Parquet files with glob patterns,
making it easy to analyze test results across multiple runs.
"""

import duckdb

# Results directory (relative to cwd)
RESULTS_GLOB = "results/runs/**/*.parquet"


def results_table():
    """Get results as a DuckDB relation with unified schema."""
    return duckdb.sql(f"""
        SELECT * FROM read_parquet('{RESULTS_GLOB}', union_by_name=true)
    """)


def summary():
    """Overall test results summary."""
    print("=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)

    duckdb.sql(f"""
        SELECT
            COUNT(DISTINCT run_id) as total_runs,
            COUNT(*) as total_measurements,
            COUNT(*) FILTER (outcome = 'pass') as passed,
            COUNT(*) FILTER (outcome = 'fail') as failed,
            ROUND(100.0 * COUNT(*) FILTER (outcome = 'pass') / COUNT(*), 1) as pass_rate
        FROM read_parquet('{RESULTS_GLOB}', union_by_name=true)
    """).show()


def by_test():
    """Results grouped by test name."""
    print("\n" + "=" * 60)
    print("RESULTS BY TEST")
    print("=" * 60)

    duckdb.sql(f"""
        SELECT
            step_name,
            COUNT(*) as count,
            COUNT(*) FILTER (outcome = 'pass') as passed,
            COUNT(*) FILTER (outcome = 'fail') as failed,
            ROUND(AVG(value), 3) as avg_value,
            ROUND(MIN(value), 3) as min_value,
            ROUND(MAX(value), 3) as max_value
        FROM read_parquet('{RESULTS_GLOB}', union_by_name=true)
        WHERE step_name IS NOT NULL
        GROUP BY step_name
        ORDER BY count DESC
    """).show()


def recent_runs(limit: int = 10):
    """Most recent test runs."""
    print("\n" + "=" * 60)
    print(f"RECENT RUNS (last {limit})")
    print("=" * 60)

    duckdb.sql(f"""
        SELECT
            LEFT(run_id::VARCHAR, 8) as run_id,
            dut_serial,
            MIN(run_started_at) as started,
            COUNT(*) as measurements,
            COUNT(*) FILTER (outcome = 'fail') as failures
        FROM read_parquet('{RESULTS_GLOB}', union_by_name=true)
        GROUP BY run_id, dut_serial
        ORDER BY started DESC
        LIMIT {limit}
    """).show()


def failed_measurements():
    """All failed measurements with details."""
    print("\n" + "=" * 60)
    print("FAILED MEASUREMENTS")
    print("=" * 60)

    duckdb.sql(f"""
        SELECT
            step_name,
            measurement_name,
            ROUND(value, 4) as value,
            ROUND(low_limit, 4) as low,
            ROUND(high_limit, 4) as high,
            units,
            dut_serial,
            run_started_at::DATE as date
        FROM read_parquet('{RESULTS_GLOB}', union_by_name=true)
        WHERE outcome = 'fail'
        ORDER BY run_started_at DESC
        LIMIT 20
    """).show()


def value_distribution(test_name: str):
    """Value distribution for a specific test."""
    print("\n" + "=" * 60)
    print(f"VALUE DISTRIBUTION: {test_name}")
    print("=" * 60)

    duckdb.sql(f"""
        SELECT
            ROUND(value, 3) as value,
            COUNT(*) as count,
            REPEAT('█', (COUNT(*) * 20 / MAX(COUNT(*)) OVER())::INT) as histogram
        FROM read_parquet('{RESULTS_GLOB}', union_by_name=true)
        WHERE step_name = '{test_name}'
          AND value IS NOT NULL
        GROUP BY ROUND(value, 3)
        ORDER BY value
    """).show()


def cpk_analysis(test_name: str):
    """Process capability analysis for a test."""
    print("\n" + "=" * 60)
    print(f"PROCESS CAPABILITY: {test_name}")
    print("=" * 60)

    duckdb.sql(f"""
        WITH stats AS (
            SELECT
                AVG(value) as mean,
                STDDEV(value) as sigma,
                MIN(low_limit) as lsl,
                MAX(high_limit) as usl,
                COUNT(*) as n
            FROM read_parquet('{RESULTS_GLOB}', union_by_name=true)
            WHERE step_name = '{test_name}'
              AND value IS NOT NULL
              AND low_limit IS NOT NULL
              AND high_limit IS NOT NULL
        )
        SELECT
            ROUND(mean, 4) as mean,
            ROUND(sigma, 4) as sigma,
            ROUND(lsl, 4) as lsl,
            ROUND(usl, 4) as usl,
            n as sample_size,
            ROUND((usl - lsl) / (6 * sigma), 2) as cp,
            ROUND(LEAST((usl - mean), (mean - lsl)) / (3 * sigma), 2) as cpk
        FROM stats
        WHERE sigma > 0
    """).show()


def conditions_analysis():
    """Analyze results by test conditions (from in_* columns)."""
    print("\n" + "=" * 60)
    print("RESULTS BY CONDITIONS")
    print("=" * 60)

    # Check what in_* columns exist
    cols = duckdb.sql(f"""
        SELECT column_name
        FROM (DESCRIBE SELECT * FROM read_parquet('{RESULTS_GLOB}', union_by_name=true) LIMIT 1)
        WHERE column_name LIKE 'in_%'
    """).fetchall()

    if not cols:
        print("No condition columns (in_*) found")
        return

    print(f"Available condition columns: {[c[0] for c in cols]}")

    # Example: group by vin if it exists
    if any("in_vin" in c[0] for c in cols):
        print("\nResults by input voltage:")
        duckdb.sql(f"""
            SELECT
                in_vin as vin,
                COUNT(*) as measurements,
                ROUND(AVG(value), 3) as avg_value,
                COUNT(*) FILTER (outcome = 'fail') as failures
            FROM read_parquet('{RESULTS_GLOB}', union_by_name=true)
            WHERE in_vin IS NOT NULL
            GROUP BY in_vin
            ORDER BY in_vin
        """).show()


def export_to_csv(output_path: str = "results_export.csv"):
    """Export all results to CSV."""
    print(f"\nExporting to {output_path}...")

    duckdb.sql(f"""
        COPY (
            SELECT * FROM read_parquet('{RESULTS_GLOB}', union_by_name=true)
            ORDER BY run_started_at DESC
        ) TO '{output_path}' (HEADER, DELIMITER ',')
    """)

    print(f"Exported to {output_path}")


def interactive():
    """Launch interactive DuckDB shell."""
    print("\nLaunching interactive DuckDB shell...")
    print(f"Results loaded as: results (from {RESULTS_GLOB})")
    print("Example: SELECT * FROM results LIMIT 10;")
    print("Type .exit to quit\n")

    # Create a view for convenience
    query = f"SELECT * FROM read_parquet('{RESULTS_GLOB}', union_by_name=true)"
    duckdb.sql(f"CREATE OR REPLACE VIEW results AS {query}")

    # Start interactive mode
    import subprocess

    subprocess.run(["duckdb", "-cmd", f"CREATE VIEW results AS {query}"])


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "summary":
            summary()
        elif cmd == "tests":
            by_test()
        elif cmd == "recent":
            recent_runs()
        elif cmd == "failed":
            failed_measurements()
        elif cmd == "dist" and len(sys.argv) > 2:
            value_distribution(sys.argv[2])
        elif cmd == "cpk" and len(sys.argv) > 2:
            cpk_analysis(sys.argv[2])
        elif cmd == "conditions":
            conditions_analysis()
        elif cmd == "export":
            export_to_csv(sys.argv[2] if len(sys.argv) > 2 else "results_export.csv")
        elif cmd == "interactive":
            interactive()
        else:
            print(f"Unknown command: {cmd}")
            print("Available: summary, tests, recent, failed, dist <test>, cpk <test>,")
            print("           conditions, export, interactive")
    else:
        # Default: run all reports
        summary()
        by_test()
        recent_runs()
        failed_measurements()
