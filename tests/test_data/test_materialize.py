"""Tests for copy-on-prune channel ref materialization.

Uses an isolated tree under ``tmp_path`` because
``materialize_channel_refs`` opens a ``RunStore`` on the
data_dir — spawning a fresh runs daemon there. To keep this
on the canonical singleton instead (~100 gRPC threads saved per
test), we create the fake results tree under the canonical
``runs/`` and ``channels/`` paths but namespaced by a per-test
UUID date directory. ``materialize_channel_refs(canonical, ...)``
uses the canonical runs daemon already alive in the test session.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
import pytest

from litmus.data.data_dir import resolve_data_dir
from litmus.data.materialize import materialize_channel_refs
from litmus.data.ref import make_channel_uri
from litmus.data.run_store import RunStore
from litmus.data.schemas import RUN_ROW_SCHEMA

# Resolved via repo's ``litmus.yaml`` → project-local store.
_CANONICAL_RESULTS = resolve_data_dir()


class _ResultsTree:
    """Bundle of paths for a per-test materialize fixture under canonical."""

    def __init__(self, root: Path, date_stem: str) -> None:
        self.root = root
        self.date_stem = date_stem
        self.channel_dir = root / "channels" / date_stem
        self.runs_dir = root / "runs" / date_stem
        self.parquet = self.runs_dir / "test_run.parquet"


@pytest.fixture()
def results_tree() -> Generator[_ResultsTree, None, None]:
    """Create channel + run files under the canonical results dir.

    Per-test isolation is via a unique date-stem directory
    (``2099-99-<uuid>`` ensures no collision with real run dates).
    The fixture returns paths into the canonical results root;
    ``materialize_channel_refs`` uses the canonical runs daemon
    that's already alive for the test session.
    """
    suffix = uuid4().hex[:8]
    date_stem = f"2099-99-{suffix}"
    tree = _ResultsTree(_CANONICAL_RESULTS, date_stem)
    tree.channel_dir.mkdir(parents=True, exist_ok=True)
    tree.runs_dir.mkdir(parents=True, exist_ok=True)

    session_id = f"{suffix}-0000-0000-0000-000000000000"
    session_short = suffix
    channel_id = f"scope.ch1.waveform.{suffix}"

    now = datetime.now(UTC)
    arrow_table = pa.table(
        {
            "timestamp": pa.array([now, now, now], type=pa.timestamp("us", tz="UTC")),
            "value": [1.0, 2.0, 3.0],
            "source_method": ["observe", "observe", "observe"],
        }
    )
    arrow_path = tree.channel_dir / f"{channel_id}_{session_short}.arrow"
    writer = ipc.new_stream(arrow_path, arrow_table.schema)
    writer.write_table(arrow_table)
    writer.close()

    uri = make_channel_uri(channel_id, session_id)

    def _dt(iso: str) -> datetime:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))

    populated: dict = {f.name: None for f in RUN_ROW_SCHEMA}
    populated.update(
        {
            "record_type": "measurement",
            "run_id": f"run-{suffix}",
            "session_id": session_id,
            "run_started_at": _dt("2026-03-01T10:00:00Z"),
            "run_ended_at": _dt("2026-03-01T10:05:00Z"),
            "run_outcome": "passed",
            "dut_serial": "SN001",
            "station_id": "station-1",
            "step_index": 0,
            "step_name": "test_voltage",
            "step_path": "test_voltage",
            "parent_path": "",
            "step_started_at": _dt("2026-03-01T10:00:00Z"),
            "step_ended_at": _dt("2026-03-01T10:05:00Z"),
            "step_outcome": "passed",
            "step_vector_count": 1,
            "vector_index": 0,
            "vector_retry": 0,
            "measurement_name": "voltage",
            "measurement_value": 3.3,
            "measurement_outcome": "passed",
        }
    )
    cols = {f.name: [populated[f.name]] for f in RUN_ROW_SCHEMA}
    cols["out_waveform"] = [uri]
    schema = pa.schema(list(RUN_ROW_SCHEMA) + [pa.field("out_waveform", pa.string())])
    pq.write_table(pa.table(cols, schema=schema), tree.parquet)

    # Index the test parquet into the canonical runs daemon so
    # ``materialize_channel_refs`` (which queries the index) can
    # find it. ``LITMUS_SKIP_DAEMON_NOTIFY`` only affects the
    # ParquetBackend.save_test_run path; calling notify directly
    # is fine and necessary for tests that exercise queries.
    notifier = RunStore()
    try:
        notifier.notify_new_run(tree.parquet)
    finally:
        notifier.close()

    yield tree

    import shutil

    shutil.rmtree(tree.channel_dir, ignore_errors=True)
    shutil.rmtree(tree.runs_dir, ignore_errors=True)


def test_materialize_rewrites_parquet(results_tree: _ResultsTree) -> None:
    """Materializing channel refs rewrites parquet URIs to file:// refs.

    Item 1d: refs now route through FileStore (canonical home), so
    the URI shape is ``file://{session_id}/{filename}`` instead of
    the legacy ``file://_ref/{filename}``.
    """
    count = materialize_channel_refs(results_tree.root, [results_tree.channel_dir])

    assert count == 1

    table = pq.read_table(results_tree.parquet)
    new_uri = table.column("out_waveform")[0].as_py()

    assert new_uri.startswith("file://")
    assert new_uri.endswith(".arrow")
    # session_short and channel_id both contain the per-test uuid suffix
    suffix = results_tree.date_stem.rsplit("-", 1)[-1]
    assert suffix in new_uri
    assert "scope.ch1.waveform" in new_uri
    # Per-parquet ``_ref/`` sidecar dir is NOT created post-1d —
    # materialized channel data lives in FileStore alongside other
    # session artifacts.
    legacy_sidecar = results_tree.parquet.parent / "test_run_ref"
    assert not legacy_sidecar.exists()

    # Verify the .arrow file landed in FileStore and contains correct data
    from litmus.data.files import get_filestore

    artifact_path = get_filestore().resolve_uri(new_uri)
    assert artifact_path is not None
    assert artifact_path.exists()

    saved = ipc.open_stream(pa.OSFile(str(artifact_path), "rb")).read_all()
    assert saved.num_rows == 3
    assert saved.column("value").to_pylist() == [1.0, 2.0, 3.0]


def test_materialize_no_matching_refs(results_tree: _ResultsTree) -> None:
    """No-op when channel dirs don't match any parquet refs."""
    fake_dir = results_tree.root / "channels" / f"2099-99-{uuid4().hex[:8]}-empty"
    fake_dir.mkdir(parents=True)
    try:
        count = materialize_channel_refs(results_tree.root, [fake_dir])
        assert count == 0
    finally:
        import shutil

        shutil.rmtree(fake_dir, ignore_errors=True)


def test_materialize_preserves_non_channel_columns(results_tree: _ResultsTree) -> None:
    """Non-channel columns are preserved through rewrite."""
    materialize_channel_refs(results_tree.root, [results_tree.channel_dir])

    table = pq.read_table(results_tree.parquet)
    suffix = results_tree.date_stem.rsplit("-", 1)[-1]
    assert table.column("run_id")[0].as_py() == f"run-{suffix}"
    assert table.column("measurement_name")[0].as_py() == "voltage"
