"""Test --uut-part-number, --uut-revision, --uut-lot-number pytest options.

Inner pytest subprocesses inherit our ``TESTERKIT_HOME`` (set in ``conftest.py``)
so they write to the canonical singleton store. Each run is scoped by a unique
``_TESTERKIT_SESSION_ID`` driven into the subprocess, then read back through the
public ``RunsQuery`` — which resolves the data dir and reads through the daemon
(in-flight overlay + parquet). No parquet globbing, no mtime/serial guessing.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from testerkit.analysis.runs_query import RunRow, RunsQuery

pytest_plugins = ["pytester"]


def _run_for_session(session_id: str, *, timeout: float = 15.0) -> RunRow | None:
    """Poll ``RunsQuery`` for the run produced under ``session_id``.

    The runs daemon materializes asynchronously after the subprocess exits;
    poll until the run appears (or time out).
    """
    deadline = time.monotonic() + timeout
    q = RunsQuery()
    try:
        while time.monotonic() < deadline:
            runs = q.list_for_session(session_id)
            if runs:
                return runs[0]
            time.sleep(0.2)
    finally:
        q.close()
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


def test_uut_options_land_in_parquet(pytester_with_test, monkeypatch):
    """UUT part-number, revision, and lot flow through to the stored run."""
    session_id = str(uuid4())
    serial = f"SN-{uuid4().hex[:8]}"
    monkeypatch.setenv("_TESTERKIT_SESSION_ID", session_id)
    result = pytester_with_test.runpytest_subprocess(
        f"--uut-serial={serial}",
        "--uut-part-number=WIDGET-200",
        "--uut-revision=C",
        "--uut-lot-number=LOT-42",
        "--mock-instruments",
        "-q",
    )
    result.assert_outcomes(passed=1)

    run = _run_for_session(session_id)
    assert run is not None, f"no run materialized for session {session_id}"
    assert run.uut_serial_number == serial
    assert run.uut_part_number == "WIDGET-200"
    assert run.uut_revision == "C"
    assert run.uut_lot_number == "LOT-42"


def test_uut_options_default_to_none(pytester_with_test, monkeypatch):
    """UUT options default to None (serial itself defaults to UUT001)."""
    session_id = str(uuid4())
    monkeypatch.setenv("_TESTERKIT_SESSION_ID", session_id)
    result = pytester_with_test.runpytest_subprocess(
        "--mock-instruments",
        "-q",
    )
    result.assert_outcomes(passed=1)

    run = _run_for_session(session_id)
    assert run is not None, f"no run materialized for session {session_id}"
    assert run.uut_serial_number == "UUT001"  # default
    assert run.uut_part_number is None
    assert run.uut_revision is None
    assert run.uut_lot_number is None
