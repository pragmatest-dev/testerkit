"""Schema-version dispatch seam + the deferrable-vs-permanent refusal fix (#43).

Two layers:
- ``dispatch()`` classification: known → identity; a *newer* version → deferrable
  refusal; absent / older / unparseable → permanent refusal.
- runs ingest ledger behaviour: a current file ingests, an absent file is
  permanently quarantined, a *newer* file is DEFERRED (left un-ledgered) and then
  HEALS — a newer daemon that knows the version re-reads and ingests it, instead
  of the file being permanently invisible.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
import pytest

from testerkit.data import schema_dispatch, schema_versions
from testerkit.data._duckdb_daemon import _ensure_schema as _ensure_events_schema
from testerkit.data._duckdb_daemon import _ingest_one_file as _ingest_events_file
from testerkit.data._runs_duckdb_daemon import _ensure_schema, _ingest_one_file
from testerkit.data.channels.index import ChannelIndex
from testerkit.data.event_log import _IPC_SCHEMA, EVENT_LOG_SCHEMA_VERSION
from testerkit.data.events import EVENT_CATALOG_VERSION, EVENT_CATALOG_VERSION_KEY
from testerkit.data.schema_dispatch import SchemaVersionRefused, dispatch
from testerkit.data.schema_versions import SchemaStore
from testerkit.data.schemas import RUN_ROW_SCHEMA


def _write_run_parquet(path: Path, *, version: str | None) -> None:
    """Write a minimal single-run parquet stamped with *version* (None strips the
    stamp entirely, simulating a pre-1.0 / unstamped artifact)."""
    row: dict[str, object] = {f.name: None for f in RUN_ROW_SCHEMA}
    row["record_type"] = "run"
    row["run_id"] = "R-DISPATCH"
    row["session_id"] = "S-DISPATCH"
    row["run_started_at"] = datetime(2026, 7, 2, tzinfo=UTC)
    row["run_ended_at"] = datetime(2026, 7, 2, tzinfo=UTC)
    row["run_outcome"] = "passed"
    table = pa.table({k: [v] for k, v in row.items()}, schema=RUN_ROW_SCHEMA)
    metadata = {} if version is None else {b"schema_version": version.encode()}
    pq.write_table(table.replace_schema_metadata(metadata), path)


def _runs_count(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute("SELECT count(*) FROM runs_materialized").fetchone()
    return row[0] if row else 0


def _ledger_status(conn: duckdb.DuckDBPyConnection, path: Path) -> list[tuple]:
    return conn.execute("SELECT status FROM _ingested WHERE path = ?", [str(path)]).fetchall()


def _scalar_count(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return row[0] if row else 0


class TestDispatchClassification:
    def test_known_version_returns_identity(self) -> None:
        adapter = dispatch(
            SchemaStore.RUNS, schema_versions.CURRENT_SCHEMA_VERSION[SchemaStore.RUNS]
        )
        sentinel = object()
        assert adapter(sentinel) is sentinel

    def test_register_adapter_self_wires_the_version(self, monkeypatch) -> None:
        # Registering an adapter must ALSO make its version dispatchable — else
        # the adapter is dead (dispatch refuses before reaching it). Isolate the
        # mutable registry so register_adapter's global mutation is undone.
        store = SchemaStore.RUNS
        monkeypatch.setitem(
            schema_dispatch._ADAPTERS, store, dict(schema_dispatch._ADAPTERS[store])
        )
        monkeypatch.setitem(
            schema_versions.KNOWN_SCHEMA_VERSIONS,
            store,
            schema_versions.KNOWN_SCHEMA_VERSIONS[store],
        )
        with pytest.raises(SchemaVersionRefused):
            dispatch(store, "0.9")  # unknown before registering
        schema_dispatch.register_adapter(store, "0.9", lambda t: t)
        assert dispatch(store, "0.9")("x") == "x"  # now dispatchable, returns the adapter

    @pytest.mark.parametrize("version", ["2.0", "1.5", "10.0"])
    def test_newer_version_is_deferrable(self, version: str) -> None:
        with pytest.raises(SchemaVersionRefused) as excinfo:
            dispatch(SchemaStore.RUNS, version)
        assert excinfo.value.deferrable is True

    @pytest.mark.parametrize("version", [None, "0.0", "junk"])
    def test_absent_older_or_unparseable_is_permanent(self, version: str | None) -> None:
        with pytest.raises(SchemaVersionRefused) as excinfo:
            dispatch(SchemaStore.RUNS, version)
        assert excinfo.value.deferrable is False


class TestRunsDeferralAndHealing:
    @pytest.fixture
    def conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        c = duckdb.connect()
        _ensure_schema(c)
        yield c
        c.close()

    def test_current_version_ingests(self, conn, tmp_path: Path) -> None:
        p = tmp_path / "current.parquet"
        _write_run_parquet(p, version=schema_versions.CURRENT_SCHEMA_VERSION[SchemaStore.RUNS])
        _ingest_one_file(conn, p, os.stat(p))
        assert _runs_count(conn) == 1
        assert _ledger_status(conn, p) == [("ok",)]

    def test_absent_stamp_is_permanently_quarantined(self, conn, tmp_path: Path) -> None:
        p = tmp_path / "absent.parquet"
        _write_run_parquet(p, version=None)
        _ingest_one_file(conn, p, os.stat(p))
        assert _runs_count(conn) == 0
        # Permanent: ledgered quarantined so it is not re-attempted (regenerate).
        assert _ledger_status(conn, p) == [("quarantined",)]

    def test_newer_stamp_is_deferred_not_quarantined(self, conn, tmp_path: Path) -> None:
        p = tmp_path / "future.parquet"
        _write_run_parquet(p, version="2.0")
        _ingest_one_file(conn, p, os.stat(p))
        assert _runs_count(conn) == 0
        # The crux of #43: a newer file is NOT ledgered, so it stays re-attemptable.
        assert _ledger_status(conn, p) == []

    def test_deferred_file_heals_on_a_newer_daemon(self, conn, tmp_path: Path, monkeypatch) -> None:
        p = tmp_path / "future.parquet"
        _write_run_parquet(p, version="2.0")

        # Older daemon: 2.0 is unknown → deferred, invisible, but not lost.
        _ingest_one_file(conn, p, os.stat(p))
        assert _runs_count(conn) == 0
        assert _ledger_status(conn, p) == []

        # A newer daemon knows 2.0 (identity adapter here). Same on-disk file.
        monkeypatch.setitem(
            schema_versions.KNOWN_SCHEMA_VERSIONS,
            SchemaStore.RUNS,
            schema_versions.KNOWN_SCHEMA_VERSIONS[SchemaStore.RUNS] | {"2.0"},
        )
        _ingest_one_file(conn, p, os.stat(p))
        # Healed — the previously-deferred file is now ingested.
        assert _runs_count(conn) == 1
        assert _ledger_status(conn, p) == [("ok",)]


def _write_events_ipc(path: Path, *, envelope_version: str | None) -> None:
    """Write a one-row events IPC segment stamped with *envelope_version* (None
    strips the envelope stamp — a pre-1.0 file). Catalog stamp stays current so
    the ENVELOPE coordinate is what the test exercises."""
    meta: dict[bytes, bytes] = {EVENT_CATALOG_VERSION_KEY: EVENT_CATALOG_VERSION.encode()}
    if envelope_version is not None:
        meta[b"schema_version"] = envelope_version.encode()
    schema = _IPC_SCHEMA.with_metadata(meta)
    # A minimally valid row (the events table has NOT NULL id/event_type/occurred_at)
    # so a current-version file actually ingests; the deferral/absent tests refuse
    # before the insert, so the row content is irrelevant to them.
    now = datetime(2026, 7, 2, tzinfo=UTC)
    row: dict[str, list[object]] = {name: [None] for name in schema.names}
    row["id"] = ["E-CURRENT"]
    row["event_type"] = ["RunStarted"]
    row["occurred_at"] = [now]
    row["received_at"] = [now]
    row["session_id"] = ["S"]
    table = pa.table(row, schema=schema)
    with pa.OSFile(str(path), "wb") as sink, ipc.new_stream(sink, schema) as writer:
        writer.write_table(table)


class TestEventsDeferralAndQuarantine:
    @pytest.fixture
    def conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        c = duckdb.connect()
        _ensure_events_schema(c)
        yield c
        c.close()

    def test_newer_envelope_is_deferred_not_quarantined(self, conn, tmp_path: Path) -> None:
        p = tmp_path / "future.arrow"
        _write_events_ipc(p, envelope_version="2.0")
        _ingest_events_file(conn, p, os.stat(p))
        assert _scalar_count(conn, "SELECT count(*) FROM events") == 0
        assert _ledger_status(conn, p) == []  # #43: newer → un-ledgered, re-attemptable

    def test_absent_envelope_is_permanently_quarantined(self, conn, tmp_path: Path) -> None:
        p = tmp_path / "absent.arrow"
        _write_events_ipc(p, envelope_version=None)
        _ingest_events_file(conn, p, os.stat(p))
        assert _scalar_count(conn, "SELECT count(*) FROM events") == 0
        assert _ledger_status(conn, p) == [("quarantined",)]  # pre-1.0 → permanent skip

    def test_current_envelope_ingests(self, conn, tmp_path: Path) -> None:
        # Positive control: a current-version file DOES land, so the deferral /
        # quarantine assertions above aren't passing for the wrong reason (e.g. an
        # unreadable file that never reaches dispatch).
        p = tmp_path / "current.arrow"
        _write_events_ipc(p, envelope_version=EVENT_LOG_SCHEMA_VERSION)
        _ingest_events_file(conn, p, os.stat(p))
        assert _ledger_status(conn, p) == [("ok",)]


def _write_channel_segment(path: Path, *, version: str | None) -> None:
    """Write a minimal channel IPC segment stamped with *version* (None strips
    the stamp). Content is irrelevant — dispatch refuses on the stamp before the
    descriptor/rows are read."""
    meta = {} if version is None else {b"schema_version": version.encode()}
    schema = pa.schema([("session_id", pa.utf8()), ("value", pa.utf8())], metadata=meta)
    table = pa.table({"session_id": ["S"], "value": ["1"]}, schema=schema)
    with pa.OSFile(str(path), "wb") as sink, ipc.new_stream(sink, schema) as writer:
        writer.write_table(table)


class TestChannelsDeferral:
    def test_newer_segment_is_deferred_not_indexed(self, tmp_path: Path) -> None:
        channels_dir = tmp_path / "channels"
        seg_dir = channels_dir / "2026-07-02"
        seg_dir.mkdir(parents=True)
        # Filename must match the channel-segment pattern: <channel_id>_<8 hex>.
        _write_channel_segment(seg_dir / "temp_abcd1234.arrow", version="2.0")
        index = ChannelIndex(channels_dir)
        index.open()  # runs _scan_disk over the segment
        try:
            db = index._index_db
            assert db is not None
            assert _scalar_count(db, "SELECT count(*) FROM channel_index") == 0
            # Presence-only ledger: NOT recorded, so it is re-read next scan (#43).
            assert _scalar_count(db, "SELECT count(*) FROM _ingested") == 0
        finally:
            index.close()

    def test_current_version_segment_is_indexed(self, tmp_path: Path) -> None:
        # Positive control: a current-version segment DOES index + ledger, so the
        # deferral test's zeros above prove the refusal short-circuited the ingest,
        # not that _scan_disk silently failed to reach the dispatch call.
        channels_dir = tmp_path / "channels"
        seg_dir = channels_dir / "2026-07-02"
        seg_dir.mkdir(parents=True)
        _write_channel_segment(
            seg_dir / "temp_abcd1234.arrow",
            version=schema_versions.CURRENT_SCHEMA_VERSION[SchemaStore.CHANNELS],
        )
        index = ChannelIndex(channels_dir)
        index.open()
        try:
            db = index._index_db
            assert db is not None
            assert _scalar_count(db, "SELECT count(*) FROM channel_index") == 1
            assert _scalar_count(db, "SELECT count(*) FROM _ingested") == 1
        finally:
            index.close()
