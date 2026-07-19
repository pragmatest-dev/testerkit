"""Layer 1 — frozen golden corpus, one dir per shipped schema epoch.

One real artifact per store, committed as bytes under ``golden/schema_v0_1/``
(the 0.1 baseline). A new ``golden/schema_v<epoch>/`` is frozen each time the
schema epoch bumps; prior-epoch dirs are kept, not regenerated. Two jobs:

- **Drift-pin (this epoch):** the current build reads each golden file, it still
  carries this epoch's stamp, and it projects to the current shape. An
  accidental breaking change to the current schema fails HERE, now — before it
  ships.
- **Break-safe apparatus fixture (next epoch):** once the epoch bumps, this
  frozen prior-epoch artifact is the REAL input — not a synthetic version
  string — proving a new-epoch daemon meeting old-epoch bytes does the correct
  thing: a clean quarantine by default (never a crash-loop), or a read-time
  adapt if that epoch deliberately shipped an adapter. This is the crash-safety
  the epoch strategy exists to battle-test
  (``docs/_internal/explorations/pre-1.0-epoch-strategy.md``).

Each epoch's files are generated from that epoch's live schemas into its golden
dir on first run if absent (then committed); thereafter they are read-only
fixtures. Channels is a follow-on (its per-channel descriptor schema needs the
store writer).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
import pytest

from testerkit.data.event_log import _IPC_SCHEMA
from testerkit.data.files.models import FileArtifactMetadata
from testerkit.data.schema_dispatch import dispatch, stamp_from_arrow_metadata
from testerkit.data.schema_versions import SchemaStore
from testerkit.data.schemas import RUN_ROW_SCHEMA

GOLDEN = Path(__file__).parent / "golden" / "schema_v0_1"


def _gen_runs(path: Path) -> None:
    row: dict[str, object] = {f.name: None for f in RUN_ROW_SCHEMA}
    row.update(
        record_type="run",
        run_id="GOLDEN-RUN",
        session_id="GOLDEN-SESSION",
        run_started_at=datetime(2026, 7, 2, tzinfo=UTC),
        run_ended_at=datetime(2026, 7, 2, tzinfo=UTC),
        run_outcome="passed",
    )
    # RUN_ROW_SCHEMA carries the current (0.1) stamp in its metadata.
    pq.write_table(pa.table({k: [v] for k, v in row.items()}, schema=RUN_ROW_SCHEMA), path)


def _gen_events(path: Path) -> None:
    row: dict[str, object] = {name: None for name in _IPC_SCHEMA.names}
    row.update(
        id="GOLDEN-EVT",
        event_type="RunStarted",
        occurred_at=datetime(2026, 7, 2, tzinfo=UTC),
        received_at=datetime(2026, 7, 2, tzinfo=UTC),
        session_id="GOLDEN-SESSION",
        run_id="GOLDEN-RUN",
        writer_key="w0",
        event_offset=0,
        json='{"event_type": "RunStarted"}',
    )
    # _IPC_SCHEMA carries both stamps (schema_version + event_catalog_version).
    table = pa.table({name: [row[name]] for name in _IPC_SCHEMA.names}, schema=_IPC_SCHEMA)
    with ipc.new_stream(pa.OSFile(str(path), "wb"), table.schema) as writer:
        writer.write_table(table)


def _gen_files(path: Path) -> None:
    # schema_version defaults to the current version.
    meta = FileArtifactMetadata(mime="text/plain", extension="txt", size_bytes=3)
    path.write_text(meta.model_dump_json())


_GENERATORS = {
    "runs.parquet": _gen_runs,
    "events.arrow": _gen_events,
    "files.meta.json": _gen_files,
}


@pytest.fixture(scope="session", autouse=True)
def _ensure_golden() -> None:
    """Materialize any missing golden file from the current-baseline (0.1)
    schema, so this epoch's corpus is generated once and committed. Present files
    are never overwritten."""
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, generate in _GENERATORS.items():
        target = GOLDEN / name
        if not target.exists():
            generate(target)


def test_runs_golden_is_0_1_and_reads_to_current_shape() -> None:
    pf = pq.ParquetFile(str(GOLDEN / "runs.parquet"))
    stamp = stamp_from_arrow_metadata(pf.schema_arrow.metadata)
    assert stamp == "0.1"  # frozen at the 0.1 epoch
    dispatch(SchemaStore.RUNS, stamp)  # current build accepts it (no raise)
    assert {f.name for f in RUN_ROW_SCHEMA} <= set(pf.schema_arrow.names)


def test_events_golden_carries_both_0_1_stamps() -> None:
    reader = ipc.open_stream(pa.OSFile(str(GOLDEN / "events.arrow"), "rb"))
    meta = reader.schema.metadata
    envelope = stamp_from_arrow_metadata(meta)
    catalog = stamp_from_arrow_metadata(meta, key=b"event_catalog_version")
    assert envelope == "0.1"  # frozen at the 0.1 epoch
    assert catalog == "0.1"
    dispatch(SchemaStore.EVENTS_ENVELOPE, envelope)
    dispatch(SchemaStore.EVENT_CATALOG, catalog)


def test_files_golden_is_0_1() -> None:
    raw = json.loads((GOLDEN / "files.meta.json").read_text())
    assert raw["schema_version"] == "0.1"  # frozen at the 0.1 epoch
    dispatch(SchemaStore.FILES, raw["schema_version"])
    # Re-validates against the current model (shape pin).
    assert FileArtifactMetadata.model_validate(raw).schema_version == "0.1"
