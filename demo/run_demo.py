#!/usr/bin/env python3
"""
Litmus Demo Runner

Demonstrates the complete Litmus test flow:
1. Run tests with simulated instruments
2. Save results to Parquet
3. Query and display results

Usage:
    python run_demo.py                    # Run @litmus_test examples
    python run_demo.py --pure-pytest      # Run pure pytest examples
    python run_demo.py --architect        # Run TestHarness/@measure examples
    python run_demo.py --all              # Run all examples
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Ensure we're in the demo directory
DEMO_DIR = Path(__file__).parent
RESULTS_DIR = DEMO_DIR / "results"


def run_tests(test_file: str = "test_power_board.py"):
    """Run the demo test suite."""
    print("=" * 70)
    print(f"LITMUS DEMO: Running {test_file}")
    print("=" * 70)
    print()
    print("Running tests with simulated instruments...")
    print()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            f"tests/{test_file}",
            "--dut-serial=DPB001-0001",
            "--station=demo_station_001",
            "--simulate",
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
    print("=" * 70)
    print("TEST RESULTS")
    print("=" * 70)
    print()

    try:
        import pyarrow.parquet as pq

        # Find the most recent results
        test_runs_dir = RESULTS_DIR / "test_runs"
        measurements_dir = RESULTS_DIR / "measurements"
        vectors_dir = RESULTS_DIR / "vectors"

        if not test_runs_dir.exists():
            print("No results found. Run the tests first.")
            return

        # Read test runs
        print("Test Run Summary:")
        print("-" * 40)
        table = pq.read_table(str(test_runs_dir))
        for i in range(min(table.num_rows, 3)):  # Show last 3 runs
            print(f"  Run ID: {str(table.column('test_run_id')[i])[:8]}...")
            print(f"  DUT: {table.column('dut_serial')[i]}")
            print(f"  Station: {table.column('station_id')[i]}")
            print(f"  Result: {table.column('outcome')[i]}")
            print(
                f"  Steps: {table.column('total_steps')[i]} total, "
                f"{table.column('failed_steps')[i]} failed"
            )
            print()

        # Read vectors (test conditions)
        if vectors_dir.exists():
            print("Test Vectors (conditions):")
            print("-" * 40)
            vtable = pq.read_table(str(vectors_dir))
            print(f"  Total vectors executed: {vtable.num_rows}")
            print()

        # Read measurements
        print("Measurements (latest 10):")
        print("-" * 70)
        table = pq.read_table(str(measurements_dir))
        print(f"{'Name':<35} {'Value':>12} {'Units':>6} {'Result':>8}")
        print("-" * 70)
        rows_to_show = min(table.num_rows, 10)
        for i in range(rows_to_show):
            name = str(table.column("measurement_name")[i])
            value = table.column("value")[i].as_py()
            units = str(table.column("units")[i]) if table.column("units")[i].as_py() else ""
            result = str(table.column("outcome")[i])
            # Truncate long names
            if len(name) > 32:
                name = name[:29] + "..."
            print(f"{name:<35} {value:>12.4f} {units:>6} {result:>8}")

        if table.num_rows > rows_to_show:
            print(f"  ... and {table.num_rows - rows_to_show} more measurements")

    except ImportError:
        print("pyarrow not available for results display")
        print("Install with: pip install pyarrow")
    except Exception as e:
        print(f"Error reading results: {e}")


def cleanup_results():
    """Remove previous results."""
    import shutil

    if RESULTS_DIR.exists():
        shutil.rmtree(RESULTS_DIR)


def main():
    """Run the complete demo."""
    parser = argparse.ArgumentParser(description="Litmus Demo Runner")
    parser.add_argument(
        "--pure-pytest",
        action="store_true",
        help="Run pure pytest examples (without @litmus_test)",
    )
    parser.add_argument(
        "--architect",
        action="store_true",
        help="Run TestHarness/@measure/@litmus_step examples",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all examples",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Don't clean up previous results",
    )
    args = parser.parse_args()

    # Clean up previous results
    if not args.no_cleanup:
        cleanup_results()

    # Run tests
    success = True

    if args.all:
        success = run_tests("test_power_board.py") and success
        print()
        success = run_tests("test_pure_pytest.py") and success
        print()
        success = run_tests("test_architect.py") and success
    elif args.pure_pytest:
        success = run_tests("test_pure_pytest.py")
    elif args.architect:
        success = run_tests("test_architect.py")
    else:
        success = run_tests("test_power_board.py")

    # Show results
    show_results()

    print()
    print("=" * 70)
    if success:
        print("DEMO COMPLETE - All tests passed!")
    else:
        print("DEMO COMPLETE - Some tests failed (see above)")
    print("=" * 70)
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
