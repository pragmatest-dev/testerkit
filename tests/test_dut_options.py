"""Test --dut-part-number, --dut-revision, --dut-lot pytest options."""

import pyarrow.parquet as pq
import pytest

pytest_plugins = ["pytester"]


@pytest.fixture
def pytester_with_test(pytester):
    """Create a minimal test file and station config."""
    pytester.makefile(
        ".yaml",
        station="""
station:
  id: station
  name: Test Station
instruments: {}
""",
    )
    pytester.mkdir("stations")
    # Move the yaml into stations/
    import shutil
    shutil.move(
        str(pytester.path / "station.yaml"),
        str(pytester.path / "stations" / "station.yaml"),
    )

    pytester.makepyfile("""
from litmus.execution import litmus_test

@litmus_test
def test_dummy(context):
    return 1.0
""")
    return pytester


def test_dut_options_land_in_parquet(pytester_with_test):
    """DUT part-number, revision, and lot flow through to Parquet."""
    result = pytester_with_test.runpytest_subprocess(
        "--dut-serial=SN-999",
        "--dut-part-number=WIDGET-200",
        "--dut-revision=C",
        "--dut-lot=LOT-42",
        "--mock-instruments",
        f"--results-dir={pytester_with_test.path / 'results'}",
        "-q",
    )
    result.assert_outcomes(passed=1)

    # Find the parquet file
    parquet_files = list(pytester_with_test.path.glob("results/runs/**/*.parquet"))
    assert parquet_files, "No parquet file generated"

    table = pq.read_table(parquet_files[0])
    row = table.to_pylist()[0]

    assert row["dut_serial"] == "SN-999"
    assert row["dut_part_number"] == "WIDGET-200"
    assert row["dut_revision"] == "C"
    assert row["dut_lot_number"] == "LOT-42"


def test_dut_options_default_to_none(pytester_with_test):
    """DUT options default to None when not provided."""
    result = pytester_with_test.runpytest_subprocess(
        "--mock-instruments",
        f"--results-dir={pytester_with_test.path / 'results'}",
        "-q",
    )
    result.assert_outcomes(passed=1)

    parquet_files = list(pytester_with_test.path.glob("results/runs/**/*.parquet"))
    assert parquet_files

    table = pq.read_table(parquet_files[0])
    row = table.to_pylist()[0]

    assert row["dut_serial"] == "DUT001"  # default
    assert row.get("dut_part_number") is None
    assert row.get("dut_revision") is None
    assert row.get("dut_lot_number") is None
