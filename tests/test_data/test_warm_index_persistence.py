"""On-disk warm index (#232): persistent + incremental, no rebuild, no dup.

Channels and files keep their warm index in an on-disk, content-addressed
``_index.<fp>.duckdb`` (like events/runs, #53/#64), brought current by an
INCREMENTAL scan — only sources not already ingested are read. Reopening the
index must NOT re-ingest already-recorded segments/sidecars (no duplicate
rows) and must pick up only what's new.

These exercise the index at the store / catalog layer directly (no daemon
process, no Flight, no threads) so they're fast and pid-cheap.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import pyarrow as pa
import pyarrow.ipc as ipc

from testerkit.data import _index_epoch
from testerkit.data.channels.index import _projection_fingerprint as _channel_fingerprint
from testerkit.data.channels.models import CHANNEL_SCHEMA_VERSION, ChannelSample, sample_to_batch
from testerkit.data.channels.store import ChannelStore
from testerkit.data.files.catalog import (
    _projection_fingerprint as _files_fingerprint,
)
from testerkit.data.files.catalog import (
    ensure_schema,
    scan_sidecars,
    upsert_rows,
)
from testerkit.data.files.models import FILE_METADATA_SCHEMA_VERSION
from testerkit.data.files.store import FileStore


def _channel_index_path(channels_dir: Path) -> Path:
    """The content-addressed index path a fresh channels store would open."""
    return channels_dir / _index_epoch.index_file_name(_channel_fingerprint())


def _files_index_path(files_dir: Path) -> Path:
    """The content-addressed catalog path a fresh files daemon would open."""
    return files_dir / _index_epoch.index_file_name(_files_fingerprint())


def _mark_build_complete(conn: duckdb.DuckDBPyConnection, fingerprint: str) -> None:
    """Stamp a hand-seeded stale table as a COMPLETE prior build at
    *fingerprint* so :func:`_index_epoch.open_index` treats the next open as
    a normal reopen (idempotent ``ensure_schema`` ALTER-reconcile) rather
    than an interrupted-build self-heal (discard + rebuild from durable
    segments/sidecars) — the scenario these staleness tests target."""
    _index_epoch.stamp_index_meta(
        conn, testerkit_version="0.0.0-test-stale", schema_version="0.1", fingerprint=fingerprint
    )


def _count(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    return int(conn.execute(sql).fetchall()[0][0])


def _write_pre_removal_channel_segment(seg_dir: Path, channel_id: str, session_short: str) -> None:
    """Write a scalar channel segment shaped like every version before this
    change: it carries the (now-removed) ``machine_id`` column. New code must
    still open + index it — the extra column is silently ignored, never a
    ``BinderException`` or a column-count crash."""
    now = datetime.now(UTC)
    schema = pa.schema(
        [
            pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("sampled_at", pa.timestamp("us", tz="UTC"), nullable=True),
            pa.field("value", pa.float64()),
            pa.field("source_method", pa.utf8()),
            pa.field("session_id", pa.utf8()),
            pa.field("machine_id", pa.utf8()),
            pa.field("sample_offset", pa.int64()),
        ],
        metadata={b"schema_version": CHANNEL_SCHEMA_VERSION.encode()},
    )
    table = pa.table(
        {
            "received_at": [now],
            "sampled_at": [None],
            "value": [3.3],
            "source_method": ["legacy"],
            "session_id": [f"{session_short}-legacy-session"],
            "machine_id": ["11111111-1111-4111-8111-111111111111"],
            "sample_offset": [0],
        },
        schema=schema,
    )
    path = seg_dir / f"{channel_id}_{session_short}.arrow"
    with pa.OSFile(str(path), "wb") as sink, ipc.new_stream(sink, schema) as writer:
        writer.write_table(table)


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

        index_path = _channel_index_path(tmp_path / "channels")
        assert index_path.exists(), "index must persist to disk"
        assert index_path.name != "_index.duckdb", "must be content-addressed, not fixed-name"
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

    def test_reads_pre_removal_segment_carrying_machine_id_column(self, tmp_path: Path) -> None:
        """Backward-compat: a segment written before machine_id was removed
        from CHANNELS still opens + queries cleanly under a fresh index — no
        ``BinderException``, no column-count crash. The extra column is
        dropped, not surfaced."""
        seg_dir = tmp_path / "channels" / "2026-01-01"
        seg_dir.mkdir(parents=True)
        _write_pre_removal_channel_segment(seg_dir, "dmm.dc_voltage", "abcd1234")

        store = ChannelStore(tmp_path, uuid4(), index=True)
        store.open()
        try:
            result = store.query("dmm.dc_voltage")
        finally:
            store.close()

        assert result.num_rows == 1
        assert "machine_id" not in result.column_names

    def test_stale_index_extra_column_reconciled_no_crash(self, tmp_path: Path) -> None:
        """Regression for the 0.4.0→0.5.0 upgrade crash: an on-disk index
        file written by a different code version carries an extra
        ``machine_id`` column (0.5.0's shipped shape) that current code no
        longer writes. Without the column-explicit inserts in
        ``ChannelIndex._insert_index_rows`` / ``insert_live_columnar``, the
        positional ``INSERT INTO channel_index SELECT * FROM _incoming``
        raises ``duckdb.BinderException: table channel_index has 9 columns
        but 8 values were supplied`` the moment ``_scan_disk`` tries to fold
        the segment below into the stale table. The fix leaves the orphan
        column in place (additive reconcile, not a rebuild) and simply
        ignores it via the by-name insert.

        This is the ALTER-based reconcile *within* one fingerprinted file
        (see ``ChannelIndex._ensure_schema``'s docstring) — the stale table
        is pre-seeded at the CURRENT fingerprint's path, standing in for a
        stale build of the identically-shaped file (an in-place code edit
        before the fingerprint was recomputed), not a genuine schema fork
        (which the epoch mechanism handles by opening a different file
        entirely — see ``test_channels_index_epoch.py``).
        """
        # Producer writes one real segment — the durable truth the index
        # must be able to fold in even though its on-disk table is stale.
        producer = ChannelStore(tmp_path, uuid4(), flush_threshold=1)
        producer.open()
        producer.write("dmm.dc_voltage", 1.0)
        producer.close()

        # Simulate a stale on-disk index from a different code version: a
        # channel_index table with an extra machine_id column, matching the
        # real 0.4.0-vs-0.5.0 drift (9 columns vs the current 8). Pre-seeded
        # at the path this run's fingerprint will resolve to.
        channels_dir = tmp_path / "channels"
        channels_dir.mkdir(parents=True, exist_ok=True)
        index_path = _channel_index_path(channels_dir)
        stale_conn = duckdb.connect(str(index_path))
        try:
            stale_conn.execute(
                """
                CREATE TABLE channel_index (
                    channel_id VARCHAR,
                    session_id VARCHAR,
                    received_at TIMESTAMPTZ,
                    sampled_at TIMESTAMPTZ,
                    source_method VARCHAR,
                    sample_interval DOUBLE,
                    value VARCHAR,
                    sample_offset BIGINT,
                    machine_id VARCHAR
                )
                """
            )
            _mark_build_complete(stale_conn, _channel_fingerprint())
        finally:
            stale_conn.close()

        # Opening a fresh index over the stale on-disk table must reconcile
        # in place (no drop, no full rebuild) and serve the query normally —
        # no BinderException, no manual `data/channels` clearing.
        store = ChannelStore(tmp_path, uuid4(), index=True)
        store.open()
        try:
            result = store.query("dmm.dc_voltage")
        finally:
            store.close()

        assert result.num_rows == 1
        assert "machine_id" not in result.column_names

        # Additive reconcile, not a rebuild: the orphan column is still on
        # disk — only the by-name insert (and the query's named projection)
        # ignore it.
        verify_conn = duckdb.connect(str(index_path))
        try:
            cols = {
                row[1]
                for row in verify_conn.execute("PRAGMA table_info('channel_index')").fetchall()
            }
            assert "machine_id" in cols
        finally:
            verify_conn.close()

    def test_stale_index_missing_column_reconciled_no_crash(self, tmp_path: Path) -> None:
        """A ``channel_index`` missing a current column (e.g. reopening an
        even-older index predating ``sample_offset``) is reconciled
        additively: ``ChannelIndex._ensure_schema``'s ``ALTER TABLE
        channel_index ADD COLUMN IF NOT EXISTS`` adds the missing column, and
        open()/query() proceed without a ``duckdb.BinderException`` from the
        underlying column-count mismatch a raw ``CREATE TABLE IF NOT EXISTS``
        (which leaves an existing table untouched) would otherwise leave in
        place — no drop, no full rebuild.
        """
        producer = ChannelStore(tmp_path, uuid4(), flush_threshold=1)
        producer.open()
        producer.write("dmm.dc_voltage", 1.0)
        producer.close()

        channels_dir = tmp_path / "channels"
        channels_dir.mkdir(parents=True, exist_ok=True)
        index_path = _channel_index_path(channels_dir)
        stale_conn = duckdb.connect(str(index_path))
        try:
            stale_conn.execute(
                """
                CREATE TABLE channel_index (
                    channel_id VARCHAR,
                    session_id VARCHAR,
                    received_at TIMESTAMPTZ,
                    sampled_at TIMESTAMPTZ,
                    source_method VARCHAR,
                    sample_interval DOUBLE,
                    value VARCHAR
                )
                """
            )
            _mark_build_complete(stale_conn, _channel_fingerprint())
        finally:
            stale_conn.close()

        store = ChannelStore(tmp_path, uuid4(), index=True)
        store.open()
        try:
            result = store.query("dmm.dc_voltage")
        finally:
            store.close()

        assert result.num_rows == 1

        verify_conn = duckdb.connect(str(index_path))
        try:
            cols = {
                row[1]
                for row in verify_conn.execute("PRAGMA table_info('channel_index')").fetchall()
            }
            assert "sample_offset" in cols
        finally:
            verify_conn.close()


class TestFilesPersistentCatalog:
    def test_scan_is_incremental_no_duplicate(self, tmp_path: Path) -> None:
        store = FileStore(_data_dir=tmp_path)
        sid = uuid4().hex
        store.write("a.bin", b"aaa", session_id=sid)
        store.write("b.bin", b"bbb", session_id=sid)

        files_dir = tmp_path / "files"
        index_path = _files_index_path(files_dir)

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
        store = FileStore(_data_dir=tmp_path)
        sid = uuid4().hex
        store.write("dup.bin", b"first", session_id=sid)

        files_dir = tmp_path / "files"
        conn = duckdb.connect(str(_files_index_path(files_dir)))
        ensure_schema(conn)
        scan_sidecars(conn, files_dir)
        before = _count(conn, "SELECT count(*) FROM file_catalog")

        # Re-push the same uri row: upsert refreshes in place, never dups.
        row = conn.execute("SELECT * FROM file_catalog LIMIT 1").to_arrow_table()
        upsert_rows(conn, row)
        after = _count(conn, "SELECT count(*) FROM file_catalog")
        assert after == before == 1
        conn.close()

    def test_scans_pre_removal_sidecar_carrying_machine_id_field(self, tmp_path: Path) -> None:
        """Backward-compat: a sidecar written before machine_id was removed
        from FILES still scans into a fresh catalog cleanly — no crash, no
        column-count mismatch. The leftover field is dropped, not surfaced."""
        files_dir = tmp_path / "files"
        session_dir = files_dir / "2026-01-01" / "legacy-session"
        session_dir.mkdir(parents=True)
        (session_dir / "note.bin").write_bytes(b"hello")
        sidecar = {
            "schema_version": FILE_METADATA_SCHEMA_VERSION,
            "mime": "application/octet-stream",
            "extension": ".bin",
            "size_bytes": 5,
            "attributes": {},
            "instrument_role": "",
            "resource": "",
            "run_id": None,
            "machine_id": "11111111-1111-4111-8111-111111111111",
        }
        (session_dir / "note.bin.meta.json").write_text(json.dumps(sidecar))

        conn = duckdb.connect(str(_files_index_path(files_dir)))
        ensure_schema(conn)
        try:
            assert scan_sidecars(conn, files_dir) == 1
            assert _count(conn, "SELECT count(*) FROM file_catalog") == 1
            cols = {row[1] for row in conn.execute("PRAGMA table_info('file_catalog')").fetchall()}
            assert "machine_id" not in cols
        finally:
            conn.close()
