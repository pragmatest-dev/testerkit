"""Tests for RunStore — DuckDB-indexed query API over parquet files.

Uses the canonical singleton runs daemon (the only one this process
should ever talk to). Each fixture writes its synthetic parquets
under a unique subdirectory of the canonical runs dir with uuid4
``run_id`` / ``session_id`` so assertions filter cleanly past any
other tests' / users' runs in the shared store.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.data.ref import make_channel_uri
from litmus.data.results_dir import resolve_results_dir
from litmus.data.run_store import RunStore
from tests._step_sidecar import write_steps_sidecar


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


@pytest.fixture(scope="module")
def fixture_data() -> dict[str, str]:
    """Synthetic runs in the canonical store. Unique uuid identifiers."""
    session_id = str(uuid4())
    session_short = session_id[:8]
    run_001 = str(uuid4())
    run_002 = str(uuid4())

    canonical_runs = resolve_results_dir() / "runs" / "test-run-store"
    runs_dir = canonical_runs / "2026-03-01"
    runs_dir.mkdir(parents=True, exist_ok=True)

    uri = make_channel_uri("scope.ch1.waveform", session_id)

    pq1 = runs_dir / f"{run_001}_SN001.parquet"
    pq.write_table(
        pa.table(
            {
                "run_id": [run_001],
                "session_id": [session_id],
                "run_started_at": [_dt("2026-03-01T10:00:00Z")],
                "run_ended_at": [_dt("2026-03-01T10:05:00Z")],
                "run_outcome": ["passed"],
                "dut_serial": ["SN001"],
                "station_id": ["station-1"],
                "step_index": [0],
                "step_name": ["test_voltage"],
                "measurement_name": ["voltage"],
                "value": [3.3],
                "outcome": ["passed"],
                "units": ["V"],
                "limit_low": [3.1],
                "limit_high": [3.5],
                "nominal": [3.3],
                "out_waveform": [uri],
            }
        ),
        pq1,
    )
    write_steps_sidecar(
        pq1,
        run_id=run_001,
        session_id=session_id,
        started_at=_dt("2026-03-01T10:00:00Z"),
        ended_at=_dt("2026-03-01T10:05:00Z"),
        outcome="passed",
        dut_serial="SN001",
        station_id="station-1",
    )

    pq2 = runs_dir / f"{run_002}_SN002.parquet"
    pq.write_table(
        pa.table(
            {
                "run_id": [run_002],
                "session_id": [session_id],
                "run_started_at": [_dt("2026-03-01T11:00:00Z")],
                "run_ended_at": [_dt("2026-03-01T11:05:00Z")],
                "run_outcome": ["failed"],
                "dut_serial": ["SN002"],
                "station_id": ["station-1"],
                "step_index": [0],
                "step_name": ["test_voltage"],
                "measurement_name": ["voltage"],
                "value": [2.8],
                "outcome": ["failed"],
                "units": ["V"],
                "limit_low": [3.1],
                "limit_high": [3.5],
                "nominal": [3.3],
                "out_waveform": pa.array([None], type=pa.string()),
            }
        ),
        pq2,
    )
    write_steps_sidecar(
        pq2,
        run_id=run_002,
        session_id=session_id,
        started_at=_dt("2026-03-01T11:00:00Z"),
        ended_at=_dt("2026-03-01T11:05:00Z"),
        outcome="failed",
        dut_serial="SN002",
        station_id="station-1",
    )

    return {
        "session_id": session_id,
        "session_short": session_short,
        "run_001": run_001,
        "run_002": run_002,
        "pq1": str(pq1),
        "pq2": str(pq2),
    }


@pytest.fixture(scope="module")
def runs_store(fixture_data: dict[str, str]) -> Generator[RunStore]:
    """Canonical singleton RunStore — connects to the same daemon every other client uses."""
    store = RunStore()
    # Notify the canonical daemon of the synthetic parquets so it
    # ingests them into ``runs_persisted`` for the read-side tests.
    store.notify_new_run(Path(fixture_data["pq1"]))
    store.notify_new_run(Path(fixture_data["pq2"]))
    yield store
    store.close()


def test_get_run(runs_store: RunStore, fixture_data: dict[str, str]) -> None:
    """RunStore.get_run returns run details with prefix match."""
    run = runs_store.get_run(fixture_data["run_001"][:8])
    assert run is not None
    assert run.test_run_id == fixture_data["run_001"]
    assert run.dut_serial == "SN001"
    assert run.outcome == "passed"


def test_get_run_not_found(runs_store: RunStore) -> None:
    """RunStore.get_run returns None for unknown run_id."""
    assert runs_store.get_run("nonexistent-prefix-xxxxxxxx") is None


def test_find_run_file(runs_store: RunStore, fixture_data: dict[str, str]) -> None:
    """RunStore.find_run_file returns the parquet path."""
    f = runs_store.find_run_file(fixture_data["run_001"][:8])
    assert f is not None
    assert f.name == Path(fixture_data["pq1"]).name


def test_get_measurements(runs_store: RunStore, fixture_data: dict[str, str]) -> None:
    """RunStore.get_measurements returns measurement rows."""
    measurements = runs_store.get_measurements(fixture_data["run_001"][:8])
    assert len(measurements) == 1
    assert measurements[0]["measurement_name"] == "voltage"
    assert measurements[0]["value"] == 3.3


def test_find_channel_refs(runs_store: RunStore, fixture_data: dict[str, str]) -> None:
    """RunStore.find_channel_refs finds channel:// URIs in out_* columns."""
    refs = runs_store.find_channel_refs({fixture_data["session_short"]})
    assert any(
        r["channel_id"] == "scope.ch1.waveform"
        and r["session_short"] == fixture_data["session_short"]
        and r["col_name"] == "out_waveform"
        for r in refs
    ), f"expected scope.ch1.waveform ref for session {fixture_data['session_short']}, got {refs}"


def test_find_channel_refs_no_match(runs_store: RunStore) -> None:
    """No refs returned for unknown session shorts."""
    refs = runs_store.find_channel_refs({"deadbeef"})
    assert refs == []


def test_ref_dir_for() -> None:
    """ref_dir_for returns the _ref sidecar path."""
    p = Path("/results/runs/2026-03-01/test_run.parquet")
    assert RunStore.ref_dir_for(p) == Path("/results/runs/2026-03-01/test_run_ref")


def test_notify_new_run(runs_store: RunStore) -> None:
    """notify_new_run pushes a file path to the daemon for immediate indexing."""
    canonical_runs = resolve_results_dir() / "runs" / "test-run-store"
    runs_dir = canonical_runs / "2026-03-08"
    runs_dir.mkdir(parents=True, exist_ok=True)

    run_id = str(uuid4())
    session_id = str(uuid4())
    pq_file = runs_dir / f"{run_id}_SN099.parquet"
    pq.write_table(
        pa.table(
            {
                "run_id": [run_id],
                "session_id": [session_id],
                "run_started_at": [_dt("2026-03-08T12:00:00Z")],
                "run_ended_at": [_dt("2026-03-08T12:01:00Z")],
                "run_outcome": ["passed"],
                "dut_serial": ["SN099"],
                "station_id": ["station-2"],
                "measurement_name": ["current"],
                "value": [1.5],
                "outcome": ["passed"],
            }
        ),
        pq_file,
    )
    write_steps_sidecar(
        pq_file,
        run_id=run_id,
        session_id=session_id,
        started_at=_dt("2026-03-08T12:00:00Z"),
        ended_at=_dt("2026-03-08T12:01:00Z"),
        outcome="passed",
        dut_serial="SN099",
        station_id="station-2",
    )

    runs_store.notify_new_run(pq_file)

    # The notify is idempotent + best-effort; verify the daemon picked
    # the parquet up by querying for the run_id.
    found = runs_store.get_run(run_id[:8])
    assert found is not None
    assert found.test_run_id == run_id
    assert found.dut_serial == "SN099"
