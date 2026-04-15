"""Persistent upload queue backed by DuckDB.

Tracks upload attempts so failed uploads can be retried instead of silently lost.
The manifest lives at ``results/_uploads.duckdb``.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from litmus.models.project import OutputConfig

# -- Status constants (used in SQL and Python) --
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


class UploadRow(BaseModel):
    """A single row from the upload queue."""

    id: int
    local_path: str
    transport: str
    status: str
    attempts: int
    last_error: str | None = None
    created_at: str
    updated_at: str


def _db_path(results_dir: str = "results") -> Path:
    return Path(results_dir) / "_uploads.duckdb"


@contextmanager
def _db_connection(results_dir: str = "results") -> Generator[Any, None, None]:
    """Context manager for a DuckDB connection with schema setup."""
    import duckdb

    db = _db_path(results_dir)
    db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE SEQUENCE IF NOT EXISTS uploads_seq START 1")
        # DDL doesn't support parameterized defaults, so interpolation is correct here
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY DEFAULT nextval('uploads_seq'),
                local_path VARCHAR NOT NULL,
                transport VARCHAR NOT NULL,
                config_json VARCHAR NOT NULL,
                status VARCHAR DEFAULT '{STATUS_PENDING}',
                attempts INTEGER DEFAULT 0,
                last_error VARCHAR,
                created_at VARCHAR,
                updated_at VARCHAR
            )
        """)
        yield con
    finally:
        con.close()


def _update_status(
    con: Any, row_id: int, new_status: str, error: str | None = None
) -> None:
    """Update an upload row's status and optional error. Increments attempts only on failure."""
    now = datetime.now(UTC).isoformat()
    if new_status == STATUS_FAILED:
        con.execute(
            "UPDATE uploads SET status = ?, attempts = attempts + 1, "
            "last_error = ?, updated_at = ? WHERE id = ?",
            [new_status, error, now, row_id],
        )
    else:
        con.execute(
            "UPDATE uploads SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
            [new_status, error, now, row_id],
        )


def enqueue(
    local_path: Path,
    transport_name: str,
    config: OutputConfig,
    results_dir: str = "results",
) -> int:
    """Insert a pending upload row. Returns the row id."""
    with _db_connection(results_dir) as con:
        now = datetime.now(UTC).isoformat()
        config_json = json.dumps(config.model_dump())
        row = con.execute(
            """
            INSERT INTO uploads
                (local_path, transport, config_json, status, attempts, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            RETURNING id
            """,
            [str(local_path), transport_name, config_json, STATUS_PENDING, now, now],
        ).fetchone()
        return row[0] if row else 0


def drain(results_dir: str = "results", max_attempts: int = 3) -> int:
    """Process all pending/failed rows. Returns count of successfully uploaded items."""
    from litmus.data.transports._base import get_transport

    with _db_connection(results_dir) as con:
        rows = con.execute(
            "SELECT id, local_path, transport, config_json FROM uploads "
            "WHERE status IN (?, ?) AND attempts < ? ORDER BY id",
            [STATUS_PENDING, STATUS_FAILED, max_attempts],
        ).fetchall()

        success_count = 0
        for row_id, local_path, transport_name, config_json in rows:
            try:
                transport = get_transport(transport_name)
                config = OutputConfig(**json.loads(config_json))
                transport.send(Path(local_path), config)
                _update_status(con, row_id, STATUS_DONE)
                success_count += 1
            except Exception as exc:
                _update_status(con, row_id, STATUS_FAILED, error=str(exc))
                warnings.warn(
                    f"Upload failed for {local_path} → {transport_name}: {exc}",
                    stacklevel=2,
                )
        return success_count


def status(results_dir: str = "results") -> list[UploadRow]:
    """Return all upload rows."""
    with _db_connection(results_dir) as con:
        rows = con.execute(
            "SELECT id, local_path, transport, status, attempts, last_error, "
            "created_at, updated_at FROM uploads ORDER BY id"
        ).fetchall()
        return [
            UploadRow(
                id=r[0],
                local_path=r[1],
                transport=r[2],
                status=r[3],
                attempts=r[4],
                last_error=r[5],
                created_at=r[6],
                updated_at=r[7],
            )
            for r in rows
        ]


def clear_done(results_dir: str = "results") -> int:
    """Remove completed entries. Returns count removed."""
    with _db_connection(results_dir) as con:
        count_row = con.execute(
            "SELECT count(*) FROM uploads WHERE status = ?", [STATUS_DONE]
        ).fetchone()
        count = count_row[0] if count_row else 0
        con.execute("DELETE FROM uploads WHERE status = ?", [STATUS_DONE])
        return count
