"""Test --uut-part-number, --uut-revision, --uut-lot-number pytest options.

Inner pytest invocations inherit our ``LITMUS_HOME`` (set in
``conftest.py``) so they write to the canonical singleton
data_dir — no per-test ``--data-dir`` override. Per-test
isolation is by unique ``--uut-serial``; we read back the
parquet by filtering on that.
"""

from pathlib import Path
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from litmus.data.data_dir import resolve_data_dir

pytest_plugins = ["pytester"]


# Resolved via the repo's ``litmus.yaml`` → project-local store.
_CANONICAL_RESULTS = resolve_data_dir()


def _find_parquet_by_serial(uut_serial_number: str, *, timeout: float = 15.0) -> Path | None:
    """Find the most recent run parquet under canonical for ``uut_serial_number``.

    Polls because the runs daemon writes parquets asynchronously after
    receiving ``RunEnded`` from the test process. The subprocess exits
    before the daemon finishes materializing.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = list(_CANONICAL_RESULTS.glob(f"runs/**/*_{uut_serial_number}.parquet"))
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)
        time.sleep(0.2)
    return None


def _find_parquet_since(start_mtime: float, *, timeout: float = 15.0) -> Path | None:
    """Find the most recent run parquet under canonical written after ``start_mtime``.

    Polls — see :func:`_find_parquet_by_serial`.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = [
            p
            for p in _CANONICAL_RESULTS.glob("runs/**/*.parquet")
            if p.stat().st_mtime > start_mtime
        ]
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)
        time.sleep(0.2)
    return None


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
def test_dummy(context, measure):
    measure("dummy", 1.0)
""")
    return pytester


def test_uut_options_land_in_parquet(pytester_with_test):
    """UUT part-number, revision, and lot flow through to Parquet."""
    serial = f"SN-{uuid4().hex[:8]}"
    result = pytester_with_test.runpytest_subprocess(
        f"--uut-serial={serial}",
        "--uut-part-number=WIDGET-200",
        "--uut-revision=C",
        "--uut-lot-number=LOT-42",
        "--mock-instruments",
        "-q",
    )
    result.assert_outcomes(passed=1)

    parquet = _find_parquet_by_serial(serial)
    assert parquet is not None, f"No parquet for {serial}"

    table = pq.read_table(parquet)
    row = table.to_pylist()[0]

    assert row["uut_serial_number"] == serial
    assert row["uut_part_number"] == "WIDGET-200"
    assert row["uut_revision"] == "C"
    assert row["uut_lot_number"] == "LOT-42"


def test_uut_options_default_to_none(pytester_with_test):
    """UUT options default to None when not provided."""
    import time

    # No ``--uut-serial`` override → exercises the default. Scope the
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

    assert row["uut_serial_number"] == "UUT001"  # default
    assert row.get("uut_part_number") is None
    assert row.get("uut_revision") is None
    assert row.get("uut_lot_number") is None
