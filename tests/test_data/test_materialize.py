"""Tests for copy-on-prune channel ref materialization."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
import pytest

from litmus.data.materialize import materialize_channel_refs
from litmus.data.ref import make_channel_uri


@pytest.fixture()
def results_tree(tmp_path: Path) -> Path:
    """Create a minimal results directory with parquet + channel arrow files.

    Arrow filenames follow ChannelStore convention: {channel_id}_{session_short}.arrow
    """
    results = tmp_path / "results"

    # Create a channel arrow file matching ChannelStore layout
    channel_dir = results / "channels" / "2026-03-01"
    channel_dir.mkdir(parents=True)

    session_id = "abcd1234-0000-0000-0000-000000000000"
    session_short = "abcd1234"
    channel_id = "scope.ch1.waveform"

    # ChannelStore arrow files include a timestamp column
    now = datetime.now(UTC)
    arrow_table = pa.table(
        {
            "timestamp": pa.array([now, now, now], type=pa.timestamp("us", tz="UTC")),
            "value": [1.0, 2.0, 3.0],
            "source_method": ["observe", "observe", "observe"],
        }
    )
    arrow_path = channel_dir / f"{channel_id}_{session_short}.arrow"
    writer = ipc.new_stream(arrow_path, arrow_table.schema)
    writer.write_table(arrow_table)
    writer.close()

    # Create a parquet file referencing that channel
    uri = make_channel_uri(channel_id, session_id)

    def _dt(iso: str) -> datetime:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))

    pq_table = pa.table(
        {
            "run_id": ["run1"],
            "session_id": [session_id],
            "run_started_at": [_dt("2026-03-01T10:00:00Z")],
            "run_ended_at": [_dt("2026-03-01T10:05:00Z")],
            "run_outcome": ["pass"],
            "dut_serial": ["SN001"],
            "station_id": ["station-1"],
            "step_index": [0],
            "measurement_name": ["voltage"],
            "out_waveform": [uri],
        }
    )
    runs_dir = results / "runs" / "2026-03-01"
    runs_dir.mkdir(parents=True)
    pq.write_table(pq_table, runs_dir / "test_run.parquet")

    return results


def test_materialize_rewrites_parquet(results_tree: Path) -> None:
    """Materializing channel refs rewrites parquet URIs to file:// refs."""
    channel_dir = results_tree / "channels" / "2026-03-01"
    count = materialize_channel_refs(results_tree, [channel_dir])

    assert count == 1

    # Read rewritten parquet
    pq_path = results_tree / "runs" / "2026-03-01" / "test_run.parquet"
    table = pq.read_table(pq_path)
    new_uri = table.column("out_waveform")[0].as_py()

    assert new_uri.startswith("file://_ref/")
    assert new_uri.endswith(".arrow")
    assert "abcd1234" in new_uri
    assert "scope.ch1.waveform" in new_uri

    # Verify the sidecar arrow file exists and contains correct data
    ref_dir = pq_path.parent / "test_run_ref"
    assert ref_dir.is_dir()
    arrow_files = list(ref_dir.glob("*.arrow"))
    assert len(arrow_files) == 1

    saved = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb")).read_all()
    assert saved.num_rows == 3
    assert saved.column("value").to_pylist() == [1.0, 2.0, 3.0]


def test_materialize_no_matching_refs(results_tree: Path) -> None:
    """No-op when channel dirs don't match any parquet refs."""
    fake_dir = results_tree / "channels" / "2099-01-01"
    fake_dir.mkdir(parents=True)
    count = materialize_channel_refs(results_tree, [fake_dir])
    assert count == 0


def test_materialize_preserves_non_channel_columns(results_tree: Path) -> None:
    """Non-channel columns are preserved through rewrite."""
    channel_dir = results_tree / "channels" / "2026-03-01"
    materialize_channel_refs(results_tree, [channel_dir])

    pq_path = results_tree / "runs" / "2026-03-01" / "test_run.parquet"
    table = pq.read_table(pq_path)
    assert table.column("run_id")[0].as_py() == "run1"
    assert table.column("measurement_name")[0].as_py() == "voltage"
