"""Terminal-fence replication readiness (GH-62 + GH-63).

Two write paths reach the events index — the live Flight put-hook and the
background IPC file-ingest — and after GH-63 they apply the SAME terminal fence:

* **Put path (GH-62):** the do_put ack now carries a per-batch
  :class:`BatchDisposition` (``inserted`` / ``deduped`` / ``rejected_ids``) instead
  of a fixed byte, so a replication client can advance its cursor on
  inserted/deduped only. A post-seal producer revival is rejected and reported; a
  ``replicated`` re-ingest of the same sealed session rides through.
* **File-ingest path (GH-63):** ingest applies the fence against the daemon's
  shared sealed set and feeds that set from any ``session.ended`` it reads — so a
  revival that reached an IPC file is not resurrected on a later re-derive, exactly
  as the put path would reject it.

The put-path test uses its own tmp daemon (like the write-resilience suite) so it
never disturbs the canonical daemon. The file-ingest tests drive
``_ingest_one_file`` against an in-memory DuckDB connection (like the
schema-dispatch suite) — no daemon, no shared data dir.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import pyarrow as pa
import pyarrow.flight as flight
import pyarrow.ipc as ipc

from testerkit.data import duckdb_manager
from testerkit.data._duckdb_daemon import _ensure_schema as _ensure_events_schema
from testerkit.data._duckdb_daemon import _ingest_one_file as _ingest_events_file
from testerkit.data._duckdb_flight_server import FlightPutStream
from testerkit.data.event_log import _IPC_SCHEMA, EVENT_LOG_SCHEMA_VERSION
from testerkit.data.events import (
    EVENT_CATALOG_VERSION,
    EVENT_CATALOG_VERSION_KEY,
    TYPED_PAYLOAD_COLUMNS,
)

# --------------------------------------------------------------------------- #
# Put path (GH-62) — disposition ack + live fence                             #
# --------------------------------------------------------------------------- #


def _put_batch(
    event_id: str, session_id: str, event_type: str, payload: str = "{}"
) -> pa.RecordBatch:
    """A one-row events batch carrying exactly the columns the insert selects."""
    now = datetime.now(UTC)
    cols: dict[str, pa.Array] = {
        "id": pa.array([event_id], pa.string()),
        "event_type": pa.array([event_type], pa.string()),
        "occurred_at": pa.array([now], pa.timestamp("us", tz="UTC")),
        "session_id": pa.array([session_id], pa.string()),
        "run_id": pa.array([None], pa.string()),
        "writer_key": pa.array(["w0"], pa.string()),
        "event_offset": pa.array([0], pa.int64()),
        "json": pa.array([payload], pa.string()),
    }
    for col in TYPED_PAYLOAD_COLUMNS:
        cols[col] = pa.array([None], pa.string())
    return pa.record_batch(cols)


def _query_event_ids(location: str) -> set[str]:
    client = flight.connect(location)
    try:
        table = client.do_get(flight.Ticket(b"events\0SELECT id FROM events")).read_all()
        return {row["id"] for row in table.to_pylist()}
    finally:
        client.close()


def _kill_daemon(events_dir: Path) -> None:
    import json
    import signal

    pid = json.loads((events_dir / "_duckdb.json").read_text())["pid"]
    os.kill(pid, signal.SIGKILL)
    try:
        os.waitpid(pid, 0)
    except (ChildProcessError, OSError):
        pass


def test_put_path_disposition_fences_producer_but_keeps_replicated(tmp_path: Path) -> None:
    """After a session seals, a post-seal producer write is rejected and reported
    in the ack disposition, while a ``replicated`` re-ingest of the same session
    rides through — and the ack counts distinguish inserted / rejected."""
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True)
    sid = str(uuid4())

    location = duckdb_manager.acquire(events_dir)
    put = FlightPutStream(
        location, "events", "events", reacquire=lambda: duckdb_manager.acquire(events_dir)
    )
    try:
        # Seal the session — session.ended is accepted and seals sid.
        put.write(_put_batch("seal", sid, "session.ended"))
        seal_disp = put.drain()
        assert len(seal_disp) == 1
        assert seal_disp[0].inserted == 1
        assert seal_disp[0].rejected_ids == []

        # Post-seal producer revival (rejected) then a replicated re-ingest (kept),
        # as two batches so each yields its own disposition in send order.
        put.write(_put_batch("revival", sid, "test.measurement"))
        put.write(_put_batch("repl", sid, "test.measurement", payload='{"replicated": true}'))
        disps = put.drain()
        assert len(disps) == 2

        revival_disp, repl_disp = disps
        assert revival_disp.inserted == 0
        assert revival_disp.rejected_ids == ["revival"]
        assert repl_disp.inserted == 1
        assert repl_disp.rejected_ids == []

        ids = _query_event_ids(location)
        assert "seal" in ids  # the sealing event landed
        assert "repl" in ids  # replicated re-ingest rode through
        assert "revival" not in ids  # post-seal producer revival was fenced
    finally:
        put.close()
        try:
            _kill_daemon(events_dir)
        except (FileNotFoundError, KeyError, ProcessLookupError):
            pass


# --------------------------------------------------------------------------- #
# File-ingest path (GH-63) — same fence, feeds the sealed set                  #
# --------------------------------------------------------------------------- #


def _events_ipc_file(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a schema-stamped events IPC file (so ingest's version dispatch
    accepts it). Each row dict carries id / session_id / event_type / json."""
    meta = {
        EVENT_CATALOG_VERSION_KEY: EVENT_CATALOG_VERSION.encode(),
        b"schema_version": EVENT_LOG_SCHEMA_VERSION.encode(),
    }
    schema = _IPC_SCHEMA.with_metadata(meta)
    now = datetime(2026, 7, 2, tzinfo=UTC)
    n = len(rows)
    data: dict[str, list[object]] = {name: [None] * n for name in schema.names}
    data["id"] = [r["id"] for r in rows]
    data["event_type"] = [r["event_type"] for r in rows]
    data["occurred_at"] = [now] * n
    data["received_at"] = [now] * n
    data["session_id"] = [r["session_id"] for r in rows]
    data["json"] = [r.get("json", "{}") for r in rows]
    table = pa.table(data, schema=schema)
    with pa.OSFile(str(path), "wb") as sink, ipc.new_stream(sink, schema) as writer:
        writer.write_table(table)


def _ingested_ids(conn: duckdb.DuckDBPyConnection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM events").fetchall()}


def test_file_ingest_fences_presealed_producer_keeps_exempt(tmp_path: Path) -> None:
    """When the session is already sealed, file-ingest rejects the post-seal
    producer row and keeps the exempt rows (derived + replicated) — the SAME
    outcome the put path produces (GH-63 parity)."""
    conn = duckdb.connect()
    _ensure_events_schema(conn)
    sid = "S-INGEST"
    fpath = tmp_path / "sealed_session.arrow"
    _events_ipc_file(
        fpath,
        [
            {"id": "prod", "session_id": sid, "event_type": "test.measurement", "json": "{}"},
            {
                "id": "repl",
                "session_id": sid,
                "event_type": "test.measurement",
                "json": '{"replicated": true}',
            },
            {
                "id": "deriv",
                "session_id": sid,
                "event_type": "run.materialized",
                "json": '{"derived": true}',
            },
        ],
    )
    sealed = {sid}
    _ingest_events_file(conn, fpath, os.stat(fpath), sealed, threading.Lock())

    assert _ingested_ids(conn) == {"repl", "deriv"}  # "prod" fenced
    # ledger marks ok with the surviving row count, not quarantined.
    status = conn.execute(
        "SELECT status, row_count FROM _ingested WHERE path = ?", [str(fpath)]
    ).fetchone()
    assert status == ("ok", 2)
    conn.close()


def test_file_ingest_feeds_sealed_set(tmp_path: Path) -> None:
    """A ``session.ended`` read from an IPC file feeds the daemon's sealed set, so
    later writes to that session are fenced (the file-ingest half of the seal)."""
    conn = duckdb.connect()
    _ensure_events_schema(conn)
    sid = "S-FEED"
    fpath = tmp_path / "closing_session.arrow"
    _events_ipc_file(
        fpath,
        [
            {"id": "m1", "session_id": sid, "event_type": "test.measurement", "json": "{}"},
            {"id": "end", "session_id": sid, "event_type": "session.ended", "json": "{}"},
        ],
    )
    sealed: set[str] = set()
    _ingest_events_file(conn, fpath, os.stat(fpath), sealed, threading.Lock())

    # Not sealed when the batch was fenced → both rows land.
    assert _ingested_ids(conn) == {"m1", "end"}
    # …and the session is now sealed for subsequent writes.
    assert sid in sealed
    conn.close()


def test_file_ingest_without_sealed_state_skips_fence(tmp_path: Path) -> None:
    """Omitting the sealed state (an isolated caller that doesn't exercise the
    fence) ingests everything — the fence is opt-in via the shared set."""
    conn = duckdb.connect()
    _ensure_events_schema(conn)
    sid = "S-NOFENCE"
    fpath = tmp_path / "no_fence.arrow"
    _events_ipc_file(
        fpath,
        [{"id": "only", "session_id": sid, "event_type": "test.measurement", "json": "{}"}],
    )
    _ingest_events_file(conn, fpath, os.stat(fpath))  # no sealed / sealed_lock
    assert _ingested_ids(conn) == {"only"}
    conn.close()
