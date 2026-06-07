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
    uri VARCHAR,
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


def ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(CATALOG_DDL)


def catalog_row(
    *,
    uri: str,
    session_id: str,
    name: str,
    path: Path,
    meta: FileArtifactMetadata,
    created_at: datetime,
) -> dict[str, Any]:
    """Build one catalog row from sidecar metadata + on-disk location."""
    return {
        "uri": uri,
        "session_id": session_id,
        "name": name,
        "path": str(path),
        "mime": meta.mime,
        "extension": meta.extension,
        "size_bytes": meta.size_bytes,
        "instrument_role": meta.instrument_role,
        "resource": meta.resource,
        "created_at": created_at,
        "attributes": json.dumps(meta.attributes),
    }


def scan_sidecars(conn: duckdb.DuckDBPyConnection, files_dir: Path) -> int:
    """Rebuild the catalog by scanning every sidecar under ``files_dir``.

    Layout: ``{files_dir}/{date}/{session_id}/{filename}`` with a
    ``{filename}.meta.json`` sidecar alongside. Returns the row count.
    """
    rows: list[dict[str, Any]] = []
    for sidecar in files_dir.glob(f"*/*/*{_SIDECAR_SUFFIX}"):
        blob = sidecar.with_name(sidecar.name[: -len(_SIDECAR_SUFFIX)])
        if not blob.exists():
            continue
        try:
            meta = FileArtifactMetadata.model_validate_json(sidecar.read_text())
            created_at = datetime.fromtimestamp(blob.stat().st_mtime, tz=UTC)
        except (OSError, ValueError):
            continue
        session_id = blob.parent.name
        name = blob.name
        rows.append(
            catalog_row(
                uri=f"file://{session_id}/{name}",
                session_id=session_id,
                name=name,
                path=blob,
                meta=meta,
                created_at=created_at,
            )
        )
    if rows:
        tbl = pa.Table.from_pylist(rows, schema=CATALOG_ARROW_SCHEMA)
        conn.register("_scan", tbl)
        conn.execute("INSERT INTO file_catalog SELECT * FROM _scan")
        conn.unregister("_scan")
    return len(rows)
