"""Replication surface round-trip (GH-65).

``read_segments`` reads a bench's WAL past a cursor; ``ingest_replicated`` marks
the events replicated, dual-writes them to the receiver's WAL, and do_puts them
into the receiver's events daemon, returning a disposition. Together they are the
bench→server replication path.

Integration tests use their own tmp events daemon via the sanctioned
``duckdb_manager.acquire(tmp_path/...)`` idiom, killed on teardown so the isolated
daemon never accumulates against the canonical one.
"""

from __future__ import annotations

import json
import os
import signal
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.flight as flight
import pyarrow.ipc as ipc

from testerkit.data import duckdb_manager
from testerkit.replication import (
    EVENT_WAL_SCHEMA,
    _stamp_replicated,
    ingest_replicated,
    read_segments,
)


def _wal_table(rows: list[dict[str, object]]) -> pa.Table:
    """Build an events table matching EVENT_WAL_SCHEMA from row dicts carrying
    id / writer_key / event_offset / session_id / event_type / json."""
    now = datetime(2026, 9, 13, tzinfo=UTC)
    n = len(rows)
    data: dict[str, list[object]] = {name: [None] * n for name in EVENT_WAL_SCHEMA.names}
    data["id"] = [r["id"] for r in rows]
    data["event_type"] = [r.get("event_type", "test.measurement") for r in rows]
    data["occurred_at"] = [now] * n
    data["received_at"] = [now] * n
    data["session_id"] = [r["session_id"] for r in rows]
    data["writer_key"] = [r["writer_key"] for r in rows]
    data["event_offset"] = [r["event_offset"] for r in rows]
    data["json"] = [r.get("json", "{}") for r in rows]
    return pa.table(data, schema=EVENT_WAL_SCHEMA)


def _write_segment(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pa.OSFile(str(path), "wb") as sink, ipc.new_stream(sink, EVENT_WAL_SCHEMA) as w:
        w.write_table(table)


def _query_event_ids(location: str) -> set[str]:
    client = flight.connect(location)
    try:
        table = client.do_get(flight.Ticket(b"events\0SELECT id FROM events")).read_all()
        return {row["id"] for row in table.to_pylist()}
    finally:
        client.close()


def _kill_daemon(events_dir: Path) -> None:
    try:
        pid = json.loads((events_dir / "_duckdb.json").read_text())["pid"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ChildProcessError, OSError):
        pass


# --------------------------------------------------------------------------- #
# Unit — no daemon                                                             #
# --------------------------------------------------------------------------- #


def test_stamp_replicated_sets_flag_preserving_payload() -> None:
    tbl = _wal_table(
        [
            {
                "id": "e0",
                "writer_key": "w0",
                "event_offset": 0,
                "session_id": "s1",
                "json": '{"event_type": "test.measurement", "value": 3}',
            },
        ]
    )
    stamped = _stamp_replicated(tbl)
    obj = json.loads(stamped.column("json").to_pylist()[0])
    assert obj["replicated"] is True
    assert obj["value"] == 3  # existing payload preserved


def test_read_segments_filters_by_cursor(tmp_path: Path) -> None:
    events_dir = tmp_path / "events"
    _write_segment(
        events_dir / "2026-09-13" / "seg0.arrow",
        _wal_table(
            [
                {"id": "e0", "writer_key": "w0", "event_offset": 0, "session_id": "s1"},
                {"id": "e1", "writer_key": "w0", "event_offset": 1, "session_id": "s1"},
                {"id": "e2", "writer_key": "w0", "event_offset": 2, "session_id": "s1"},
            ]
        ),
    )
    # No cursor → everything.
    all_rows = read_segments(events_dir)
    assert all_rows is not None
    assert all_rows.column("id").to_pylist() == ["e0", "e1", "e2"]
    # Cursor past offset 0 → only later offsets for that writer.
    after = read_segments(events_dir, cursor={"w0": 0})
    assert after is not None
    assert after.column("id").to_pylist() == ["e1", "e2"]
    # Cursor at the end → nothing new.
    assert read_segments(events_dir, cursor={"w0": 2}) is None


def test_read_segments_empty_dir_is_none(tmp_path: Path) -> None:
    (tmp_path / "events").mkdir()
    assert read_segments(tmp_path / "events") is None


# --------------------------------------------------------------------------- #
# Integration — isolated receiving daemon                                      #
# --------------------------------------------------------------------------- #


def test_roundtrip_read_then_ingest_is_exactly_once(tmp_path: Path) -> None:
    sid = str(uuid4())
    src = tmp_path / "a" / "events"
    _write_segment(
        src / "2026-09-13" / "seg0.arrow",
        _wal_table(
            [
                {
                    "id": "e0",
                    "writer_key": "w0",
                    "event_offset": 0,
                    "session_id": sid,
                    "event_type": "run.started",
                    "json": '{"event_type": "run.started"}',
                },
                {
                    "id": "e1",
                    "writer_key": "w0",
                    "event_offset": 1,
                    "session_id": sid,
                    "event_type": "test.measurement",
                    "json": '{"event_type": "test.measurement"}',
                },
            ]
        ),
    )
    new = read_segments(src)
    assert new is not None and new.num_rows == 2

    dst = tmp_path / "b"
    dst_events = dst / "events"
    try:
        disp = ingest_replicated(dst, new)
        assert disp.inserted == 2
        assert disp.rejected_ids == []

        location = duckdb_manager.acquire(dst_events)
        assert _query_event_ids(location) == {"e0", "e1"}

        # Re-ingest the same events → all deduped, none inserted (exactly-once).
        disp2 = ingest_replicated(dst, new)
        assert disp2.inserted == 0
        assert disp2.deduped == 2
        assert disp2.rejected_ids == []
    finally:
        _kill_daemon(dst_events)


def test_ingest_replicated_rides_the_terminal_fence(tmp_path: Path) -> None:
    """Replicated events for an already-sealed session ride the fence (they are
    re-ingests, not post-seal producer revival) — this is what the ``replicated``
    stamp buys, end to end through ingest_replicated."""
    sid = str(uuid4())
    dst = tmp_path / "b"
    dst_events = dst / "events"
    try:
        # Seal the session on the receiver: ingest a batch ending it.
        seal = _wal_table(
            [
                {
                    "id": "s-end",
                    "writer_key": "w0",
                    "event_offset": 0,
                    "session_id": sid,
                    "event_type": "session.ended",
                    "json": '{"event_type": "session.ended"}',
                },
            ]
        )
        assert ingest_replicated(dst, seal).inserted == 1

        # A later replicated event for the sealed session must still land.
        late = _wal_table(
            [
                {
                    "id": "late-m",
                    "writer_key": "w0",
                    "event_offset": 1,
                    "session_id": sid,
                    "event_type": "test.measurement",
                    "json": '{"event_type": "test.measurement"}',
                },
            ]
        )
        disp = ingest_replicated(dst, late)
        assert disp.rejected_ids == []
        assert disp.inserted == 1

        location = duckdb_manager.acquire(dst_events)
        assert {"s-end", "late-m"} <= _query_event_ids(location)
    finally:
        _kill_daemon(dst_events)
