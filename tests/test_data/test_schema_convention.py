"""No-unstamped-write convention (§5.1) — every durable write path must carry its
version stamp, and every store's public stamp must match the central registry.

This is the guard that keeps a future write path (a new store, a refactor) from
silently regressing to unstamped — which the read-time dispatch would then refuse
at ingest. It is structural (asserts the stamp is wired into each write schema),
so it needs no daemon.
"""

from __future__ import annotations

import json

from litmus.data.channels.models import CHANNEL_SCHEMA_VERSION
from litmus.data.event_log import _IPC_SCHEMA, EVENT_LOG_SCHEMA_VERSION
from litmus.data.events import EVENT_CATALOG_VERSION
from litmus.data.files.models import FILE_METADATA_SCHEMA_VERSION, FileArtifactMetadata
from litmus.data.schema_versions import CURRENT_SCHEMA_VERSION, SchemaStore
from litmus.data.schemas import RUN_ROW_SCHEMA, SCHEMA_VERSION, _build_write_schema


def test_every_store_stamp_matches_the_registry() -> None:
    """Each store's public stamp constant is sourced from the one registry — no
    store can drift from `CURRENT_SCHEMA_VERSION`."""
    assert SCHEMA_VERSION == CURRENT_SCHEMA_VERSION[SchemaStore.RUNS]
    assert EVENT_LOG_SCHEMA_VERSION == CURRENT_SCHEMA_VERSION[SchemaStore.EVENTS_ENVELOPE]
    assert EVENT_CATALOG_VERSION == CURRENT_SCHEMA_VERSION[SchemaStore.EVENT_CATALOG]
    assert CHANNEL_SCHEMA_VERSION == CURRENT_SCHEMA_VERSION[SchemaStore.CHANNELS]
    assert FILE_METADATA_SCHEMA_VERSION == CURRENT_SCHEMA_VERSION[SchemaStore.FILES]


def test_runs_write_schemas_carry_the_stamp() -> None:
    """Both runs at-rest schema paths (the canonical schema and the dynamic
    `_build_write_schema`) stamp their metadata, so every table built from them is
    stamped by construction — fixtures included."""
    assert RUN_ROW_SCHEMA.metadata[b"schema_version"] == SCHEMA_VERSION.encode()
    dynamic = _build_write_schema([{"record_type": "run", "run_id": "r"}])
    assert dynamic.metadata[b"schema_version"] == SCHEMA_VERSION.encode()


def test_events_ipc_schema_carries_both_coordinates() -> None:
    """Events stamps both coordinates on every IPC file (envelope + catalog)."""
    assert _IPC_SCHEMA.metadata[b"schema_version"] == EVENT_LOG_SCHEMA_VERSION.encode()
    assert _IPC_SCHEMA.metadata[b"event_catalog_version"] == EVENT_CATALOG_VERSION.encode()


def test_files_sidecar_serializes_the_stamp() -> None:
    """The FileStore sidecar carries its stamp in the serialized JSON."""
    meta = FileArtifactMetadata(mime="text/plain", extension="txt", size_bytes=1)
    assert json.loads(meta.model_dump_json())["schema_version"] == FILE_METADATA_SCHEMA_VERSION
