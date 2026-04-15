"""Tests for RunStore — DuckDB-indexed query API over parquet files."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.data.ref import make_channel_uri
from litmus.data.run_store import RunStore


@pytest.fixture(scope="module")
def runs_store(tmp_path_factory: pytest.TempPathFactory) -> Generator[RunStore]:
    """Single RunStore shared across all tests in this module."""
    results = tmp_path_factory.mktemp("run_store") / "results"
    runs_dir = results / "runs" / "2026-03-01"
    runs_dir.mkdir(parents=True)

    session_id = "abcd1234-0000-0000-0000-000000000000"
    uri = make_channel_uri("scope.ch1.waveform", session_id)

    pq1 = runs_dir / "20260301T100000Z_SN001.parquet"
    table = pa.table(
        {
            "run_id": ["run-001-abc"],
            "session_id": [session_id],
            "run_started_at": ["2026-03-01T10:00:00Z"],
            "run_ended_at": ["2026-03-01T10:05:00Z"],
            "run_outcome": ["pass"],
            "dut_serial": ["SN001"],
            "station_id": ["station-1"],
            "measurement_name": ["voltage"],
            "value": [3.3],
            "outcome": ["pass"],
            "out_waveform": [uri],
        }
    )
    pq.write_table(table, pq1)

    pq2 = runs_dir / "20260301T110000Z_SN002.parquet"
    table2 = pa.table(
        {
            "run_id": ["run-002-def"],
            "session_id": [session_id],
            "run_started_at": ["2026-03-01T11:00:00Z"],
            "run_ended_at": ["2026-03-01T11:05:00Z"],
            "run_outcome": ["fail"],
            "dut_serial": ["SN002"],
            "station_id": ["station-1"],
            "measurement_name": ["voltage"],
            "value": [2.8],
            "outcome": ["fail"],
            "out_waveform": [None],
        }
    )
    pq.write_table(table2, pq2)

    store = RunStore(_results_dir=results)

    # Parquet files exist before daemon started, so they're already indexed
    # via the bulk rebuild on daemon startup.

    yield store
    store.close()


def test_list_runs(runs_store: RunStore) -> None:
    """RunStore.list_runs returns indexed runs sorted by time."""
    runs = runs_store.list_runs()
    assert len(runs) >= 2
    # Most recent first
    ids = [r["test_run_id"] for r in runs]
    assert "run-002-def" in ids
    assert "run-001-abc" in ids
    idx_002 = ids.index("run-002-def")
    idx_001 = ids.index("run-001-abc")
    assert idx_002 < idx_001  # run-002 is more recent


def test_get_run(runs_store: RunStore) -> None:
    """RunStore.get_run returns run details with prefix match."""
    run = runs_store.get_run("run-001-")
    assert run is not None
    assert run["test_run_id"] == "run-001-abc"
    assert run["dut_serial"] == "SN001"
    assert run["outcome"] == "pass"


def test_get_run_not_found(runs_store: RunStore) -> None:
    """RunStore.get_run returns None for unknown run_id."""
    assert runs_store.get_run("nonexistent") is None


def test_find_run_file(runs_store: RunStore) -> None:
    """RunStore.find_run_file returns the parquet path."""
    f = runs_store.find_run_file("run-001-")
    assert f is not None
    assert f.name == "20260301T100000Z_SN001.parquet"


def test_get_measurements(runs_store: RunStore) -> None:
    """RunStore.get_measurements returns measurement rows."""
    measurements = runs_store.get_measurements("run-001-")
    assert len(measurements) == 1
    assert measurements[0]["measurement_name"] == "voltage"
    assert measurements[0]["value"] == 3.3


def test_find_channel_refs(runs_store: RunStore) -> None:
    """RunStore.find_channel_refs finds channel:// URIs in out_* columns."""
    refs = runs_store.find_channel_refs({"abcd1234"})
    assert len(refs) == 1
    assert refs[0]["channel_id"] == "scope.ch1.waveform"
    assert refs[0]["session_short"] == "abcd1234"
    assert refs[0]["col_name"] == "out_waveform"
    assert refs[0]["row_idx"] == 0


def test_find_channel_refs_no_match(runs_store: RunStore) -> None:
    """No refs returned for unknown session shorts."""
    refs = runs_store.find_channel_refs({"deadbeef"})
    assert refs == []


def test_ref_dir_for() -> None:
    """ref_dir_for returns the _ref sidecar path."""
    p = Path("/results/runs/2026-03-01/test_run.parquet")
    assert RunStore.ref_dir_for(p) == Path("/results/runs/2026-03-01/test_run_ref")


def test_notify_new_run(tmp_path: Path) -> None:
    """notify_new_run pushes a file path to the daemon for immediate indexing."""
    results = tmp_path / "results"
    runs_dir = results / "runs" / "2026-03-08"
    runs_dir.mkdir(parents=True)

    pq_file = runs_dir / "20260308T120000Z_SN099.parquet"
    table = pa.table(
        {
            "run_id": ["run-099-xyz"],
            "session_id": ["sess-099"],
            "run_started_at": ["2026-03-08T12:00:00Z"],
            "run_ended_at": ["2026-03-08T12:01:00Z"],
            "run_outcome": ["pass"],
            "dut_serial": ["SN099"],
            "station_id": ["station-2"],
            "measurement_name": ["current"],
            "value": [1.5],
            "outcome": ["pass"],
        }
    )
    pq.write_table(table, pq_file)

    store = RunStore(_results_dir=results)
    try:
        # File exists before daemon start, so it's already indexed.
        # But let's also test notify_new_run for a second file.
        pq_file2 = runs_dir / "20260308T130000Z_SN100.parquet"
        table2 = pa.table(
            {
                "run_id": ["run-100-abc"],
                "session_id": ["sess-100"],
                "run_started_at": ["2026-03-08T13:00:00Z"],
                "run_ended_at": ["2026-03-08T13:01:00Z"],
                "run_outcome": ["fail"],
                "dut_serial": ["SN100"],
                "station_id": ["station-2"],
                "measurement_name": ["current"],
                "value": [0.5],
                "outcome": ["fail"],
            }
        )
        pq.write_table(table2, pq_file2)

        store.notify_new_run(pq_file2)

        runs = store.list_runs()
        ids = [r["test_run_id"] for r in runs]
        assert "run-099-xyz" in ids
        assert "run-100-abc" in ids
    finally:
        store.close()
