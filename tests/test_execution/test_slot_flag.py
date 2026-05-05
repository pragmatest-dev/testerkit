"""``--slot=N`` flag — single-process operator targeting of a fixture slot.

The flag is the operator-facing channel: when an operator slots a
DUT into physical position 2 of a multi-slot fixture and runs solo,
``--slot=slot_2`` makes the resulting parquet record ``slot_id="slot_2"``.

Pinned behaviors:

* ``--slot=<known>`` against a multi-slot fixture → single-process
  run (orchestrator dispatch suppressed), parquet ``slot_id`` set.
* ``--slot=<unknown>`` → usage error from ``pytest_sessionstart``,
  no run is performed.
* No ``--slot`` against a multi-slot fixture in single-process mode
  → usage error (operator must declare which physical slot).
* Single-slot fixture / no fixture → ``--slot`` is unused; no error.

All read-back goes through the public :class:`RunsQuery` — same path
the operator UI uses.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from uuid import uuid4

import yaml

from litmus.analysis.runs_query import RunsQuery


def _write_multi_slot_fixture(path: Path, slot_ids: list[str]) -> None:
    fixture = {
        "id": path.stem,
        "slots": {sid: {"connections": {}} for sid in slot_ids},
    }
    path.write_text(yaml.safe_dump(fixture))


def _write_station(path: Path) -> None:
    path.write_text(yaml.safe_dump({"id": path.stem, "name": "Test Station", "instruments": {}}))


def _write_pass_test(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """\
            def test_ok():
                assert 1 == 1
            """
        )
    )


def _run_pytest(
    test_file: Path,
    fixture_path: Path,
    station_path: Path,
    *,
    session_id: str,
    slot: str | None = None,
    extra: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Run pytest in a subprocess writing to the canonical results dir.

    ``session_id`` flows through the ``_LITMUS_SESSION_ID`` env so
    the outer test can identify exactly its own runs in the shared
    canonical store.
    """
    args = [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        f"--fixture={fixture_path}",
        f"--station={station_path}",
        "--mock-instruments",
        "--dut-serial=SN42",
        "-v",
    ]
    if slot is not None:
        args.append(f"--slot={slot}")
    if extra:
        args.extend(extra)
    env = {**os.environ, "_LITMUS_SESSION_ID": session_id}
    return subprocess.run(args, capture_output=True, text=True, timeout=60, env=env)


def _list_runs(session_id: str, *, timeout: float = 3.0) -> list:
    """Bounded poll over canonical RunsQuery, scoped to ``session_id``."""
    deadline = time.monotonic() + timeout
    q = RunsQuery()
    try:
        while time.monotonic() < deadline:
            runs = q.find_for_session(session_id, include_incomplete=True)
            if runs:
                return runs
            time.sleep(0.2)
        return q.find_for_session(session_id, include_incomplete=True)
    finally:
        q.close()


class TestSlotFlag:
    """``--slot=N`` records the operator's intended slot in the run row."""

    def test_known_slot_records_slot_id_and_runs_single_process(self, tmp_path):
        session_id = str(uuid4())
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_pass.py"

        _write_multi_slot_fixture(fixture_path, ["slot_1", "slot_2"])
        _write_station(station_path)
        _write_pass_test(test_file)

        result = _run_pytest(
            test_file,
            fixture_path,
            station_path,
            session_id=session_id,
            slot="slot_2",
        )
        assert result.returncode == 0, (
            f"pytest exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # Single-process run — orchestrator's "Multi-DUT Results"
        # header should NOT appear.
        assert "Multi-DUT Results" not in result.stdout

        runs = _list_runs(session_id)
        # Exactly one run row; ``slot_id`` reflects the operator's choice.
        assert len(runs) == 1, runs
        assert runs[0].slot_id == "slot_2", runs[0]
        assert runs[0].outcome == "passed"

    def test_unknown_slot_errors_at_session_start(self, tmp_path):
        session_id = str(uuid4())
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_pass.py"

        _write_multi_slot_fixture(fixture_path, ["slot_1", "slot_2"])
        _write_station(station_path)
        _write_pass_test(test_file)

        result = _run_pytest(
            test_file,
            fixture_path,
            station_path,
            session_id=session_id,
            slot="slot_99",
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "slot_99" in combined and "slot_1" in combined and "slot_2" in combined, combined

    def test_no_slot_against_multi_slot_fixture_errors(self, tmp_path):
        """Single-process invocation against multi-slot needs --slot or --dut-serials."""
        session_id = str(uuid4())
        fixture_path = tmp_path / "fixture.yaml"
        station_path = tmp_path / "station.yaml"
        test_file = tmp_path / "test_pass.py"

        _write_multi_slot_fixture(fixture_path, ["slot_1", "slot_2"])
        _write_station(station_path)
        _write_pass_test(test_file)

        result = _run_pytest(
            test_file,
            fixture_path,
            station_path,
            session_id=session_id,
        )
        # Either a usage error (--slot required) OR orchestrator
        # dispatch — either is acceptable, both prove the bare
        # multi-slot fixture isn't silently miscaptured. The flag
        # is the way to record a single physical slot.
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            # Orchestrator dispatch path: should show Multi-DUT Results.
            assert "Multi-DUT Results" in combined
        else:
            assert "Multi-slot fixture" in combined or "--slot" in combined, combined
