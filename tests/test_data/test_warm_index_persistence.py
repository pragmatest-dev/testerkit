"""On-disk warm index (#232): persistent + incremental, no rebuild, no dup.

Channels and files keep their warm index in an on-disk ``_index.duckdb``
(like events/runs), brought current by an INCREMENTAL scan — only sources
not already ingested are read. Reopening the index must NOT re-ingest
already-recorded segments/sidecars (no duplicate rows) and must pick up
only what's new.

These exercise the index at the store / catalog layer directly (no daemon
process, no Flight, no threads) so they're fast and pid-cheap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb

from litmus.data.channels.models import ChannelSample, sample_to_batch
from litmus.data.channels.store import ChannelStore
from litmus.data.files.catalog import ensure_schema, scan_sidecars, upsert_rows
from litmus.data.files.store import FileStore


def _count(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    return int(conn.execute(sql).fetchall()[0][0])


def _channel_rows(index_path: Path) -> int:
    conn = duckdb.connect(str(index_path))
    try:
        return _count(conn, "SELECT count(*) FROM channel_index")
    finally:
        conn.close()


class TestChannelsPersistentIndex:
    def test_reopen_is_incremental_no_duplicate(self, tmp_path: Path) -> None:
        # Producer writes two closed segments (flush_threshold=1 → one per write).
        producer = ChannelStore(tmp_path, uuid4(), flush_threshold=1)
        producer.open()
        producer.write("dmm.dc_voltage", 1.0)
        producer.write("dmm.dc_voltage", 2.0)
        producer.close()

        # First index open scans both segments.
        ix1 = ChannelStore(tmp_path, uuid4(), index=True)
        ix1.open()
        assert ix1.query("dmm.dc_voltage").num_rows == 2
        ix1.close()

        index_path = tmp_path / "channels" / "_index.duckdb"
        assert index_path.exists(), "index must persist to disk"
        assert _channel_rows(index_path) == 2

        # Reopen: the ledger already has both segments, so the scan adds
        # nothing — the count stays 2 (a rebuild-from-all would double it).
        ix2 = ChannelStore(tmp_path, uuid4(), index=True)
        ix2.open()
        assert ix2.query("dmm.dc_voltage").num_rows == 2
        ix2.close()
        assert _channel_rows(index_path) == 2

        # A new segment is folded in incrementally on the next open.
        producer2 = ChannelStore(tmp_path, uuid4(), flush_threshold=1)
        producer2.open()
        producer2.write("dmm.dc_voltage", 3.0)
        producer2.close()

        ix3 = ChannelStore(tmp_path, uuid4(), index=True)
        ix3.open()
        assert ix3.query("dmm.dc_voltage").num_rows == 3
        ix3.close()

    def test_live_overlay_unions_with_durable_index(self, tmp_path: Path) -> None:
        # A live ingest_batch row (overlay) and a segment-scanned row both
        # show up in one query, exactly once each.
        producer = ChannelStore(tmp_path, uuid4(), flush_threshold=1)
        producer.open()
        producer.write("scope.ch1", 1.0)
        producer.close()

        ix = ChannelStore(tmp_path, uuid4(), index=True)
        ix.open()
        sample = ChannelSample(
            channel_id="scope.ch1",
            value=2.0,
            received_at=datetime.now(UTC),
            session_id=uuid4().hex,
            source_method="test",
        )
        ix.ingest_batch("scope.ch1", sample_to_batch(sample))
        assert ix.query("scope.ch1").num_rows == 2  # 1 durable + 1 overlay
        ix.close()


class TestFilesPersistentCatalog:
    def test_scan_is_incremental_no_duplicate(self, tmp_path: Path) -> None:
        store = FileStore(data_dir=tmp_path)
        sid = uuid4().hex
        store.write("a.bin", b"aaa", session_id=sid)
        store.write("b.bin", b"bbb", session_id=sid)

        files_dir = tmp_path / "files"
        index_path = files_dir / "_index.duckdb"

        conn1 = duckdb.connect(str(index_path))
        ensure_schema(conn1)
        assert scan_sidecars(conn1, files_dir) == 2
        assert _count(conn1, "SELECT count(*) FROM file_catalog") == 2
        conn1.close()

        # Reopen: both sidecars are already cataloged → 0 new, count stays 2.
        conn2 = duckdb.connect(str(index_path))
        ensure_schema(conn2)
        assert scan_sidecars(conn2, files_dir) == 0
        assert _count(conn2, "SELECT count(*) FROM file_catalog") == 2

        # A new file is folded in incrementally.
        store.write("c.bin", b"ccc", session_id=sid)
        assert scan_sidecars(conn2, files_dir) == 1
        assert _count(conn2, "SELECT count(*) FROM file_catalog") == 3
        conn2.close()

    def test_upsert_by_uri_is_idempotent(self, tmp_path: Path) -> None:
        store = FileStore(data_dir=tmp_path)
        sid = uuid4().hex
        store.write("dup.bin", b"first", session_id=sid)

        files_dir = tmp_path / "files"
        conn = duckdb.connect(str(files_dir / "_index.duckdb"))
        ensure_schema(conn)
        scan_sidecars(conn, files_dir)
        before = _count(conn, "SELECT count(*) FROM file_catalog")

        # Re-push the same uri row: upsert refreshes in place, never dups.
        row = conn.execute("SELECT * FROM file_catalog LIMIT 1").to_arrow_table()
        upsert_rows(conn, row)
        after = _count(conn, "SELECT count(*) FROM file_catalog")
        assert after == before == 1
        conn.close()
