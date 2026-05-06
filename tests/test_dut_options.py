"""Test --dut-part-number, --dut-revision, --dut-lot-number pytest options.

Inner pytest invocations inherit our ``LITMUS_HOME`` (set in
``conftest.py``) so they write to the canonical singleton
results_dir — no per-test ``--results-dir`` override. Per-test
isolation is by unique ``--dut-serial``; we read back the
parquet by filtering on that.
"""

from pathlib import Path
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from litmus.data.results_dir import resolve_results_dir

pytest_plugins = ["pytester"]


# Resolved via the repo's ``litmus.yaml`` → project-local store.
_CANONICAL_RESULTS = resolve_results_dir()


def _find_parquet_by_serial(dut_serial: str) -> Path | None:
    """Find the most recent run parquet under canonical for ``dut_serial``."""
    matches = list(_CANONICAL_RESULTS.glob(f"runs/**/*_{dut_serial}.parquet"))
    matches = [m for m in matches if not m.stem.endswith("_steps")]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def _find_parquet_since(start_mtime: float) -> Path | None:
    """Find the most recent run parquet under canonical written after ``start_mtime``."""
    matches = [
        p
        for p in _CANONICAL_RESULTS.glob("runs/**/*.parquet")
        if not p.stem.endswith("_steps") and p.stat().st_mtime > start_mtime
    ]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


@pytest.fixture
def pytester_with_test(pytester):
    """Create a minimal test file and station config."""
    pytester.makefile(
        ".yaml",
        station="""
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
def test_dummy(context, logger):
    logger.measure("dummy", 1.0)
""")
    return pytester


def test_dut_options_land_in_parquet(pytester_with_test):
    """DUT part-number, revision, and lot flow through to Parquet."""
    serial = f"SN-{uuid4().hex[:8]}"
    result = pytester_with_test.runpytest_subprocess(
        f"--dut-serial={serial}",
        "--dut-part-number=WIDGET-200",
        "--dut-revision=C",
        "--dut-lot-number=LOT-42",
        "--mock-instruments",
        "-q",
    )
    result.assert_outcomes(passed=1)

    parquet = _find_parquet_by_serial(serial)
    assert parquet is not None, f"No parquet for {serial}"

    table = pq.read_table(parquet)
    row = table.to_pylist()[0]

    assert row["dut_serial"] == serial
    assert row["dut_part_number"] == "WIDGET-200"
    assert row["dut_revision"] == "C"
    assert row["dut_lot_number"] == "LOT-42"


def test_dut_options_default_to_none(pytester_with_test):
    """DUT options default to None when not provided."""
    import time

    # No ``--dut-serial`` override → exercises the default. Scope the
    # parquet lookup by mtime since we can't filter on a known serial.
    start = time.time()
    result = pytester_with_test.runpytest_subprocess(
        "--mock-instruments",
        "-q",
    )
    result.assert_outcomes(passed=1)

    parquet = _find_parquet_since(start)
    assert parquet is not None

    table = pq.read_table(parquet)
    row = table.to_pylist()[0]

    assert row["dut_serial"] == "DUT001"  # default
    assert row.get("dut_part_number") is None
    assert row.get("dut_revision") is None
    assert row.get("dut_lot_number") is None
