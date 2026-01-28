#!/usr/bin/env python3
"""
Litmus Demo Runner

Demonstrates the complete Litmus test flow:
1. Run tests with simulated instruments
2. Save results to Parquet
3. Query and display results

Usage:
    python run_demo.py
"""

import subprocess
import sys
from pathlib import Path

# Ensure we're in the demo directory
DEMO_DIR = Path(__file__).parent
RESULTS_DIR = DEMO_DIR / "results"


def run_tests():
    """Run the demo test suite."""
    print("=" * 60)
    print("LITMUS DEMO: Power Board Validation")
    print("=" * 60)
    print()
    print("Running tests with simulated instruments...")
    print()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "--dut-serial=DPB001-0001",
            "--station=demo_station_001",
            "--operator=Demo User",
            f"--results-dir={RESULTS_DIR}",
            "-v",
        ],
        cwd=DEMO_DIR,
    )

    return result.returncode == 0


def show_results():
    """Display the test results from Parquet."""
    print()
    print("=" * 60)
    print("TEST RESULTS")
    print("=" * 60)
    print()

    try:
        import pyarrow.parquet as pq

        # Find the most recent results
        test_runs_dir = RESULTS_DIR / "test_runs"
        measurements_dir = RESULTS_DIR / "measurements"

        if not test_runs_dir.exists():
            print("No results found. Run the tests first.")
            return

        # Read test runs
        print("Test Run Summary:")
        print("-" * 40)
        table = pq.read_table(str(test_runs_dir))
        for i in range(table.num_rows):
            print(f"  Run ID: {table.column('test_run_id')[i]}")
            print(f"  DUT: {table.column('dut_serial')[i]}")
            print(f"  Station: {table.column('station_id')[i]}")
            print(f"  Result: {table.column('outcome')[i]}")
            print(
                f"  Steps: {table.column('total_steps')[i]} total, "
                f"{table.column('failed_steps')[i]} failed"
            )
        print()

        # Read measurements
        print("Measurements:")
        print("-" * 40)
        table = pq.read_table(str(measurements_dir))
        print(f"{'Name':<30} {'Value':>10} {'Units':>6} {'Result':>8}")
        print("-" * 60)
        for i in range(table.num_rows):
            name = str(table.column("measurement_name")[i])
            value = table.column("value")[i].as_py()
            units = str(table.column("units")[i]) if table.column("units")[i].as_py() else ""
            result = str(table.column("outcome")[i])
            print(f"{name:<30} {value:>10.4f} {units:>6} {result:>8}")

    except ImportError:
        print("pyarrow not available for results display")
    except Exception as e:
        print(f"Error reading results: {e}")


def cleanup_results():
    """Remove previous results."""
    import shutil

    if RESULTS_DIR.exists():
        shutil.rmtree(RESULTS_DIR)


def main():
    """Run the complete demo."""
    # Clean up previous results
    cleanup_results()

    # Run tests
    success = run_tests()

    # Show results
    show_results()

    print()
    print("=" * 60)
    if success:
        print("DEMO COMPLETE - All tests passed!")
    else:
        print("DEMO COMPLETE - Some tests failed (see above)")
    print("=" * 60)
    print()
    print(f"Results saved to: {RESULTS_DIR}")
    print()
    print("To query results programmatically:")
    print("  import pyarrow.parquet as pq")
    print(f"  table = pq.read_table('{RESULTS_DIR}/measurements')")
    print("  print(table.to_pydict())")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
