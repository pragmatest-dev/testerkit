"""Opt-in **migrate** sink — rewrite an owned durable file forward to the current
schema, in place, via the same adapter the reader uses.

The re-index sink (the whitelist-dispatch reader at each store's ingest boundary)
is the *mandatory floor*: it projects any readable version forward at query time
and never touches the file. Migrate is the *optional* housekeeping layer on top —
"migration is re-index that also persists the adapter output" (§4 of
``schema-versioning-migration.md``). It exists so an operator can retire old-major
files (and their adapters) once they're rewritten, not because reading requires
it. Never a substitute for read-time projection: files in the wild are unreachable.

Both content kinds the adapters span are covered here:
- ``migrate_parquet_file`` — Arrow/parquet (runs). Events/channels are the same
  Arrow pattern over IPC; wired when a real adapter first needs them (YAGNI).
- ``migrate_sidecar_file`` — the FileStore JSON sidecar (Pydantic model).

Each is idempotent (a current-version file is a no-op) and raises
:class:`~testerkit.data.schema_dispatch.SchemaVersionRefused` for a file the current
build cannot read (absent/pre-1.0 or unknown) — the caller decides whether to
skip or surface it, exactly as the re-index sink does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from testerkit.data._atomic import atomic_write_table, atomic_write_text
from testerkit.data.files.models import FileArtifactMetadata
from testerkit.data.schema_dispatch import dispatch, stamp_from_arrow_metadata
from testerkit.data.schema_versions import CURRENT_SCHEMA_VERSION, SchemaStore


def migrate_parquet_file(store: SchemaStore, path: Path | str) -> bool:
    """Rewrite a parquet artifact forward to the current schema, atomically.

    Reads the footer stamp, applies the ``source -> current`` adapter, re-stamps
    the result to the current version, and atomically replaces the file. Returns
    ``True`` if it migrated, ``False`` if already current (no-op). Raises
    :class:`SchemaVersionRefused` for an unreadable version.
    """
    path = Path(path)
    parquet_file = pq.ParquetFile(str(path))
    stamp = stamp_from_arrow_metadata(parquet_file.schema_arrow.metadata)
    current = CURRENT_SCHEMA_VERSION[store]
    if stamp == current:
        return False
    adapter = dispatch(store, stamp)
    migrated = adapter(parquet_file.read()).replace_schema_metadata(
        {b"schema_version": current.encode()}
    )
    atomic_write_table(migrated, path)
    return True


def migrate_sidecar_file(path: Path | str) -> bool:
    """Rewrite a FileStore ``.meta.json`` sidecar forward to the current schema.

    Reads the raw JSON (so an absent stamp is detectable), applies the
    ``source -> current`` adapter to the ``FileArtifactMetadata`` model, re-stamps
    to the current version, and atomically replaces the sidecar. The blob is never
    touched (it is opaque user payload). Returns ``True`` if migrated, ``False`` if
    already current. Raises :class:`SchemaVersionRefused` for an unreadable version.
    """
    path = Path(path)
    raw = json.loads(path.read_text())
    current = CURRENT_SCHEMA_VERSION[SchemaStore.FILES]
    if raw.get("schema_version") == current:
        return False
    adapter = dispatch(SchemaStore.FILES, raw.get("schema_version"))
    migrated = adapter(FileArtifactMetadata.model_validate(raw))
    migrated = migrated.model_copy(update={"schema_version": current})
    atomic_write_text(migrated.model_dump_json(), path)
    return True
