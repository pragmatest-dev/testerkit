"""Files catalog — warm DuckDB index over FileStore sidecar metadata.

The catalog daemon owns this index so the UI / API / materializer query
it instead of walking the date-partitioned tree per call (req 2). It is
a derived cache: the on-disk blobs and their ``.meta.json`` sidecars are
the durable truth, so the catalog is built in-memory and rebuilt by
scanning sidecars on every daemon start, then kept warm by a do_put from
``FileStore.write`` after each artifact lands durably.

Mirrors the channels Opt-1 model (daemon indexes producer files; live
writes push; restart rebuilds from disk). Per the plan, the low-level
``FileStore`` walk stays as a fallback until Phase E removes it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from litmus.data.files.models import FileArtifactMetadata

_SIDECAR_SUFFIX = ".meta.json"

CATALOG_DDL = """
CREATE TABLE IF NOT EXISTS file_catalog (
    uri VARCHAR PRIMARY KEY,
    session_id VARCHAR,
    name VARCHAR,
    path VARCHAR,
    mime VARCHAR,
    extension VARCHAR,
    size_bytes BIGINT,
    instrument_role VARCHAR,
    resource VARCHAR,
    created_at TIMESTAMPTZ,
    attributes VARCHAR
)
"""

# ``uri`` is the natural key (``file://{session}/{name}`` is 1:1 with a
# blob), so the catalog is its own ingest ledger: the startup scan skips
# any sidecar whose uri is already present (incremental — no rebuild from
# every sidecar), and the live ``do_put`` from ``FileStore.write`` upserts.
# A file rewritten under the same uri refreshes its row instead of
# duplicating it (the old plain INSERT double-counted).
_CATALOG_COLUMNS = (
    "uri",
    "session_id",
    "name",
    "path",
    "mime",
    "extension",
    "size_bytes",
    "instrument_role",
    "resource",
    "created_at",
    "attributes",
)
_UPSERT_SQL = (
    "INSERT INTO file_catalog SELECT * FROM {src} "
    "ON CONFLICT (uri) DO UPDATE SET "
    + ", ".join(f"{c}=excluded.{c}" for c in _CATALOG_COLUMNS if c != "uri")
)

CATALOG_ARROW_SCHEMA = pa.schema(
    [
        ("uri", pa.utf8()),
        ("session_id", pa.utf8()),
        ("name", pa.utf8()),
        ("path", pa.utf8()),
        ("mime", pa.utf8()),
        ("extension", pa.utf8()),
        ("size_bytes", pa.int64()),
        ("instrument_role", pa.utf8()),
        ("resource", pa.utf8()),
        ("created_at", pa.timestamp("us", tz="UTC")),
        ("attributes", pa.utf8()),
    ]
)


# Ephemeral live-stream frame notifications. NOT a durable event (the
# committed design keeps stream events lifecycle-only — StreamStarted /
# StreamEnded — to avoid flooding the EventStore at kHz/30 fps rates).
# Frames ride a fan-out-only Flight db so live consumers get a no-poll
# signal to range-read the new byte window of a still-growing artifact.
FRAMES_DB = "file_frames"

FRAME_ARROW_SCHEMA = pa.schema(
    [
        ("stream_id", pa.utf8()),
        ("uri", pa.utf8()),
        ("byte_offset", pa.int64()),
        ("length", pa.int64()),
    ]
)


def ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Idempotently align the on-disk catalog schema (additive open)."""
    conn.execute(CATALOG_DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_catalog_created ON file_catalog(created_at)")


def upsert_rows(conn: duckdb.DuckDBPyConnection, table: pa.Table) -> None:
    """Upsert catalog rows by ``uri`` (idempotent for scan + live do_put)."""
    if table.num_rows == 0:
        return
    conn.register("_upsert", table)
    conn.execute(_UPSERT_SQL.format(src="_upsert"))
    conn.unregister("_upsert")


def catalog_row(
    *,
    uri: str,
    session_id: str,
    name: str,
    key: str,
    meta: FileArtifactMetadata,
    created_at: datetime,
) -> dict[str, Any]:
    """Build one catalog row from sidecar metadata + backend key.

    ``key`` is the backend-relative physical locator
    (``{date}/{session_id}/{filename}``) — NOT an absolute path — so the
    catalog stays backend-agnostic and a resolve hands back a key the
    blob backend (local or remote) can read.
    """
    return {
        "uri": uri,
        "session_id": session_id,
        "name": name,
        "path": key,
        "mime": meta.mime,
        "extension": meta.extension,
        "size_bytes": meta.size_bytes,
        "instrument_role": meta.instrument_role,
        "resource": meta.resource,
        "created_at": created_at,
        "attributes": json.dumps(meta.attributes),
    }


def scan_sidecars(conn: duckdb.DuckDBPyConnection, files_dir: Path) -> int:
    """Fold sidecars not yet cataloged into the (persistent) catalog.

    Layout: ``{files_dir}/{date}/{session_id}/{filename}`` with a
    ``{filename}.meta.json`` sidecar alongside. **Incremental**: a sidecar
    whose uri is already in the catalog is skipped, so a daemon restart
    reads only new sidecars rather than rebuilding from every one. Returns
    the count of newly-ingested rows.
    """
    known = {row[0] for row in conn.execute("SELECT uri FROM file_catalog").fetchall()}
    rows: list[dict[str, Any]] = []
    for sidecar in files_dir.glob(f"*/*/*{_SIDECAR_SUFFIX}"):
        blob = sidecar.with_name(sidecar.name[: -len(_SIDECAR_SUFFIX)])
        if not blob.exists():
            continue
        session_id = blob.parent.name
        name = blob.name
        uri = f"file://{session_id}/{name}"
        if uri in known:
            continue
        try:
            meta = FileArtifactMetadata.model_validate_json(sidecar.read_text())
            created_at = datetime.fromtimestamp(blob.stat().st_mtime, tz=UTC)
        except (OSError, ValueError):
            continue
        rows.append(
            catalog_row(
                uri=uri,
                session_id=session_id,
                name=name,
                key=f"{blob.parent.parent.name}/{session_id}/{name}",
                meta=meta,
                created_at=created_at,
            )
        )
    if rows:
        upsert_rows(conn, pa.Table.from_pylist(rows, schema=CATALOG_ARROW_SCHEMA))
    return len(rows)
